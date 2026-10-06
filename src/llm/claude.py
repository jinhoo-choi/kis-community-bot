"""Anthropic Claude 프로바이더.

대량 비실시간 작업이므로 Message Batches API 를 기본으로 쓴다.
문서: https://docs.claude.com/en/docs/build-with-claude/batch-processing
"""
import hashlib
import inspect
import json
import os
import time
import concurrent.futures as cf

import config
from src.llm import base, budget
from src.llm.base import Provider, GenResult, record_usage

# 모델이 은퇴하면 단일 문자열은 그날 파이프라인을 죽인다.
# 404/not_found/deprecated 계열 오류에서만 다음 후보로 승격한다.
_RETIRED = ("not_found", "404", "deprecated", "does not exist",
            "is not supported", "NOT_FOUND", "unsupported")


def _ival(obj, name: str) -> int:
    try:
        return int(getattr(obj, name, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _unclear(e: Exception) -> str:
    """응답을 못 받은 채 끊긴 호출은 처리·과금 여부를 알 수 없다."""
    if budget.active() and "unconfirmed" in budget.summary()["stop_reason"]:
        return "unconfirmed"
    name = type(e).__name__
    return ("unconfirmed" if name in ("APITimeoutError", "APIConnectionError",
                                       "ReadTimeout", "ConnectionError", "TimeoutError")
            else "estimated")


def _job_hash(model, system, user, temperature, max_tokens) -> str:
    raw = json.dumps([model, system, user, temperature, max_tokens],
                     ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _load_state() -> dict:
    try:
        with open(config.BATCH_STATE_PATH, encoding="utf-8") as f:
            st = json.load(f)
        if not isinstance(st, dict) or any(
                not isinstance(rec, dict) or not isinstance(rec.get("jobs"), dict)
                for rec in st.values()):
            raise ValueError("invalid batch checkpoint")
        return st
    except FileNotFoundError:
        return {}
    except Exception as exc:
        if budget.active():
            budget.block("legacy batch checkpoint unreadable")
            raise budget.BudgetDenied("legacy batch checkpoint unreadable") from exc
        return {}


def _save_state(st: dict) -> None:
    """배치 상태를 원자적으로 저장한다. 정리 끝난 지 7일 지난 기록은 버린다."""
    now = time.time()
    st = {k: v for k, v in st.items()
          if not (v.get("status") in ("collected", "abandoned")
                  and now - v.get("created", now) > 7 * 86400)}
    if not st:
        # 복원할 배치가 없으면 파일을 남기지 않는다(테스트·빈 실행의 부산물 방지).
        if os.path.exists(config.BATCH_STATE_PATH):
            os.remove(config.BATCH_STATE_PATH)
        return
    try:
        os.makedirs(os.path.dirname(config.BATCH_STATE_PATH) or ".", exist_ok=True)
        tmp = config.BATCH_STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, config.BATCH_STATE_PATH)
    except Exception as e:
        print(f"[claude] 배치 상태 저장 실패: {e}")


def _find_reusable(st: dict, model: str, hashes: list) -> str:
    """같은 작업을 모두 담은, 아직 정리되지 않은 배치. 있으면 재제출하지 않는다."""
    want = set(hashes)
    for bid, rec in st.items():
        if (not bid.startswith("pending-") and rec.get("model") == model
                and rec.get("status") not in ("collected", "abandoned")
                and want <= set(rec.get("jobs", {}).values())):
            return bid
    return ""


def _message_result(message, provider: str, fallback_model: str,
                    billing_mode: str = "standard") -> GenResult:
    """Anthropic message의 실제 usage를 공통 결과로 정규화한다."""
    txt = "".join(b.text for b in message.content if b.type == "text").strip()
    # 웹 검색 도구를 쓴 응답은 근거 URL 이 검색 결과 블록에 들어온다.
    # 보강은 '검색으로 확인된 사실' 만 쓰므로 근거가 없으면 채택하지 않는다.
    srcs = []
    for b in message.content:
        if getattr(b, "type", "") != "web_search_tool_result":
            continue
        for it in (getattr(b, "content", None) or []):
            u = getattr(it, "url", None)
            if u:
                srcs.append({"url": u, "title": getattr(it, "title", "") or ""})
    usage = getattr(message, "usage", None)
    cache_read = _ival(usage, "cache_read_input_tokens")
    cache_write = _ival(usage, "cache_creation_input_tokens")
    uncached = _ival(usage, "input_tokens")
    service_tier = str(getattr(usage, "service_tier", "standard") or "standard")
    cache_1h = _ival(getattr(usage, "cache_creation", None), "ephemeral_1h_input_tokens")
    numeric = [getattr(usage, k, None) for k in ("input_tokens", "output_tokens")]
    numeric += [getattr(usage, k, 0) for k in
                ("cache_read_input_tokens", "cache_creation_input_tokens")]
    numeric += [getattr(getattr(usage, "cache_creation", None),
                        "ephemeral_1h_input_tokens", 0)]
    valid_usage = all(isinstance(v, int) and not isinstance(v, bool) and v >= 0
                      for v in numeric) and cache_1h <= cache_write
    return GenResult(
        txt, provider, str(getattr(message, "model", "") or fallback_model),
        ok=bool(txt),
        input_tokens=uncached + cache_read + cache_write,
        output_tokens=_ival(usage, "output_tokens"),
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        cache_write_1h_tokens=cache_1h,
        sources=srcs,
        # 웹 검색은 토큰과 별도로 1회당 과금된다($10/1,000). 종전엔 집계 누락.
        grounding_queries=_ival(getattr(usage, "server_tool_use", None), "web_search_requests"),
        tier="paid", service_tier=service_tier, billing_mode=billing_mode,
        request_id=str(getattr(message, "id", "") or ""),
        cost_status="estimated" if valid_usage else "unconfirmed",
    )


class ClaudeProvider(Provider):
    # SDK 1.8.0 의 messages.create 는 temperature 를 받지 않는다. 한 번 확인하면
    # 모든 인스턴스가 공유한다. 인스턴스별 플래그였을 때, 생성용 프로바이더만
    # 폴백을 밟고 보강용 프로바이더는 매번 TypeError 로 죽었다
    # (실측 #145: enrich 14건 전건 실패, 공시 발송 0건).
    _no_temp = False
    name = "claude"

    def __init__(self, api_key: str, model: str, use_batch: bool = True):
        self.model = model
        self.use_batch = use_batch
        self._client = None
        if api_key:
            import anthropic
            from anthropic import Anthropic
            self._client = Anthropic(api_key=api_key, max_retries=0)
            print(f"[claude] SDK {getattr(anthropic, '__version__', '?')}")

    def available(self) -> bool:
        return self._client is not None

    def _promote(self, err: str) -> bool:
        """모델 은퇴로 보이면 다음 후보로 교체. 교체했으면 True."""
        if not any(k in err for k in _RETIRED):
            return False
        cands = config.CLAUDE_CANDIDATES
        try:
            nxt = cands[cands.index(self.model) + 1]
        except (ValueError, IndexError):
            return False
        print(f"[{self.name}] 모델 은퇴 감지: {self.model} -> {nxt}")
        self.fallbacks = (self.fallbacks or []) + [f"{self.model}->{nxt}"]
        self.model = nxt
        return True

    def _budget_create(self, **kw):
        """Every paid synchronous SDK call passes one atomic reservation."""
        token = None
        # A signature binding failure is provably before dispatch. Runtime
        # TypeError can also be a response-decoding failure, so never waive it.
        inspect.signature(self._client.messages.create).bind(**kw)
        if budget.active():
            if budget.stopped():
                raise budget.BudgetDenied(budget.summary()["stop_reason"])
            if kw.get("tools"):
                # Server search can inject an unbounded result context. Its request
                # cannot be priced before execution, so the bounded profile forbids it.
                raise budget.BudgetDenied("search disabled by bounded API budget")
            try:
                count_kw = {k: kw[k] for k in ("model", "system", "messages", "thinking")
                            if k in kw}
                count = self._client.messages.count_tokens(**count_kw).input_tokens
            except Exception as exc:
                budget.block("input token counting unavailable")
                raise budget.BudgetDenied("input token counting unavailable") from exc
            # Conservatively cover even a future explicit 1-hour cache boundary.
            one_hour = '\"ttl\": \"1h\"' in json.dumps(kw.get("system"))
            token = budget.reserve(kw["model"], count, kw["max_tokens"],
                                   cache_multiplier=2.0 if one_hour else 1.25)
        try:
            message = self._client.messages.create(**kw)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            # Local signature errors and rejected requests are not billable. Every
            # other failure is uncertain; automatic SDK retries are disabled.
            budget.fail(token, known_unbilled=status in (400, 401, 403, 404, 413, 422, 429))
            raise
        try:
            budget.settle(token, _message_result(message, self.name, self.model))
        except Exception:
            budget.fail(token)
            raise
        return message

    def _create(self, system, user, temperature, max_tokens):
        """설치된 SDK 가 temperature 를 안 받는 경우가 있어(실측) 방어적으로 호출한다."""
        kw = dict(model=self.model, max_tokens=max_tokens, system=system,
                  messages=[{"role": "user", "content": user}])
        # Sonnet 5는 비기본 sampling parameter를 거부하고 adaptive thinking이 기본이다.
        # JSON 심사는 짧고 결정적이어야 하므로 thinking을 끄고 temperature를 보내지 않는다.
        if isinstance(system, list):
            # 이미 블록 단위로 캐시 경계가 지정된 프롬프트다. 그대로 보낸다.
            pass
        elif self.model == "claude-sonnet-5":
            # 심사 프롬프트의 82%(약 2,016토큰)는 매번 같은 규칙 텍스트다.
            # Sonnet 5 의 캐싱 최소 길이(1,024토큰)를 넘으므로 system 을 캐시한다.
            # 캐시 읽기는 입력 단가의 0.1배라 심사 입력비가 약 70% 준다(#127 기준
            # 심사 65회 $0.320 → 약 $0.084). 심사 결과는 바뀌지 않는다 — 같은 입력이다.
            # Haiku 4.5(생성)는 최소 길이가 4,096토큰인데 입력이 3,836토큰이라
            # 캐싱되지 않는다. 그쪽에 걸면 쓰기 할증만 생길 수 있어 걸지 않는다.
            kw["system"] = [{"type": "text", "text": system,
                             "cache_control": {"type": "ephemeral"}}]
        if self.model == "claude-sonnet-5":
            return self._budget_create(
                thinking={"type": "disabled"}, **kw)
        return self._call_with_temp(kw, temperature)

    def _call_with_temp(self, kw: dict, temperature: float):
        """temperature 를 붙여 보고, SDK 가 못 받으면 빼고 다시 부른다."""
        if ClaudeProvider._no_temp:       # 한 번 확인했으면 매번 재시도하지 않는다
            return self._budget_create(**kw)
        try:
            return self._budget_create(temperature=temperature, **kw)
        except TypeError as e:
            if budget.stopped() or "temperature" not in str(e):
                raise
            print(f"[claude] SDK 가 temperature 미지원 → 제외하고 재호출 ({e})")
            ClaudeProvider._no_temp = True
            return self._budget_create(**kw)

    def generate(self, system, user, temperature=1.0, max_tokens=700) -> GenResult:
        try:
            r = self._create(system, user, temperature, max_tokens)
            return _message_result(r, self.name, self.model)
        except budget.BudgetDenied as e:
            return GenResult("", self.name, self.model, ok=False, error=str(e),
                             attempts=0, cost_status="not_sent")
        except Exception as e:
            msg = str(e)
            if self._promote(msg):
                result = self.generate(system, user, temperature, max_tokens)
                result.attempts += 1
                return result
            return GenResult("", self.name, self.model, ok=False, error=msg[:200],
                             cost_status=_unclear(e))

    def _prewarm(self, jobs) -> None:
        """고정부를 캐시에 미리 적재한다. 실패해도 조용히 넘어간다(캐시는 최적화일 뿐)."""
        sysblk = jobs[0][0] if jobs else None
        if not isinstance(sysblk, list) or not sysblk:
            return
        try:
            r = self._budget_create(
                model=self.model, max_tokens=0, system=[sysblk[0]],
                messages=[{"role": "user", "content": "warmup"}])
            # 본문 없는 정상 응답이다. 캐시 쓰기 할증이 붙으므로 별도 역할로 원장에 남긴다
            # (10-06 실측: 예열 2회 약 $0.0064 가 원장에서 빠져 있었다).
            res = _message_result(r, self.name, self.model)
            res.ok = True
            record_usage(res, "prewarm", "prewarm")
            u = getattr(r, "usage", None)
            print(f"[claude] 캐시 예열 고정부 {getattr(u, 'input_tokens', 0):,}토큰 "
                  f"(최소 4,096) / write {getattr(u, 'cache_creation_input_tokens', 0):,}"
                  f" / read {getattr(u, 'cache_read_input_tokens', 0):,}")
        except budget.BudgetDenied as e:
            record_usage(GenResult("", self.name, self.model, ok=False,
                                   error=str(e), attempts=0, cost_status="not_sent"),
                         "prewarm", "prewarm")
        except Exception as e:
            print(f"[claude] 캐시 예열 실패(무시): {type(e).__name__}")
            record_usage(GenResult("", self.name, self.model, ok=False,
                                   error=str(e)[:200], cost_status=_unclear(e)),
                         "prewarm", "prewarm")

    def search(self, system: str, user: str, temperature: float = 1.0,
               max_tokens: int = 700) -> GenResult:
        """웹 검색 도구를 붙여 호출한다. 보강(enrich) 전용.

        Gemini 그라운딩이 하던 일을 대신한다. 검색 결과 블록에서 근거 URL 을
        모아 GenResult.sources 에 담는다 — 보강은 근거 없는 내용을 쓰지 않는다.
        """
        try:
            kw = dict(model=self.model, max_tokens=max_tokens, system=system,
                      messages=[{"role": "user", "content": user}],
                      tools=[{"type": "web_search_20250305", "name": "web_search",
                              # 3→1: 결과가 입력 토큰으로 붙어 호출당 ~3만 토큰.
                              # 10-01 실측 보강 $0.46(비용 44%)에 발송 기여 0~5건.
                              "max_uses": 1}])
            r = self._call_with_temp(kw, temperature)
            return _message_result(r, self.name, self.model)
        except budget.BudgetDenied as e:
            return GenResult("", self.name, self.model, ok=False, error=str(e),
                             attempts=0, cost_status="not_sent")
        except Exception as e:
            return GenResult("", self.name, self.model, ok=False, error=str(e)[:200],
                             cost_status=_unclear(e))

    def generate_many(self, jobs, temperature=1.0, max_tokens=700,
                      poll_sec=10, timeout_sec=None) -> list[GenResult]:
        timeout_sec = config.BATCH_TIMEOUT_SEC if timeout_sec is None else timeout_sec
        n = len(jobs)
        # temperature 는 요청마다 다를 수 있다(배치 API 는 요청별 params 를 받는다).
        temps = temperature if isinstance(temperature, list) else [temperature] * n
        # 논리 작업 ID = 요청 내용 해시. 재시작 후 같은 작업이면 같은 배치를 찾는다.
        hashes = [_job_hash(self.model, s, u, temps[i], max_tokens)
                  for i, (s, u) in enumerate(jobs)]
        cids = [f"j{i}_{h}" for i, h in enumerate(hashes)]
        out: list = [None] * n

        st = _load_state()
        reuse = _find_reusable(st, self.model, hashes) if hashes else ""
        # 이전 실행의 배치를 먼저 정리한다. 같은 작업 결과면 재사용, 아니면 비용만 원장에.
        found = self._recover(st, skip="" if budget.active() else reuse, want=set(hashes))
        # 동기·소량 모드도 잔여 배치를 확인한다. 처리 여부 불명인 같은 작업은
        # 새 배치나 동기로 다시 제출하지 않고, 무관한 신규 작업만 진행한다.
        for bid, rec in st.items():
            if bid == reuse or bid not in base.PENDING_BATCHES:
                continue
            for cid, h in rec.get("jobs", {}).items():
                if h in hashes and h not in found:
                    found[h] = GenResult(
                        "", self.name, rec.get("model", self.model), ok=False,
                        error="previous batch unresolved", billing_mode="batch",
                        cost_status="unconfirmed", batch_id=bid, custom_id=cid)
        for i, h in enumerate(hashes):
            if h in found:
                out[i] = found.pop(h)
        todo = [i for i in range(n) if out[i] is None]
        if not todo:
            _save_state(st)
            return out

        if budget.active():
            # Reconcile old work above, but do not create fresh asynchronous jobs
            # whose outcome can outlive this run's bounded ledger.
            if base.PENDING_BATCHES:
                budget.block("unresolved legacy batch")
            _save_state(st)
            return self._fill_sync(out, todo, jobs, temps, max_tokens)

        if reuse:
            batch_id = reuse
            print(f"[claude] 기존 batch {batch_id} 재사용 — 재제출 안 함")
        elif not self.use_batch or len(todo) < 15:
            _save_state(st)
            return self._fill_sync(out, todo, jobs, temps, max_tokens)
        else:
            # 배치는 요청이 동시에 처리된다. 캐시 항목은 첫 응답이 시작돼야 쓸 수 있어
            # (문서: 병렬 요청은 첫 응답을 기다려야 적중), 예열 없이 보내면 60건이
            # 전부 미스가 되고 쓰기 할증만 문다. 배치 밖에서 max_tokens=0 으로 먼저
            # 고정부를 적재한다(배치 안에서는 max_tokens=0 이 거부된다).
            self._prewarm([jobs[i] for i in todo])
            reqs = [{
                "custom_id": cids[i],
                "params": {
                    "model": self.model, "max_tokens": max_tokens, "system": jobs[i][0],
                    "messages": [{"role": "user", "content": jobs[i][1]}],
                    **({"thinking": {"type": "disabled"}}
                       if self.model == "claude-sonnet-5"
                       else ({} if ClaudeProvider._no_temp
                             else {"temperature": temps[i]})),
                },
            } for i in todo]
            # 제출 전에 논리 작업을 기록한다. 제출 직후 죽어도 무엇을 보냈는지 남는다.
            pre = f"pending-{base.RUN_ID[0]}-{time.time_ns()}"
            st[pre] = {"run_id": base.RUN_ID[0], "model": self.model,
                       "created": time.time(), "status": "preparing",
                       "jobs": {cids[i]: hashes[i] for i in todo}, "collected": []}
            _save_state(st)
            try:
                batch = self._client.messages.batches.create(requests=reqs)
            except Exception as e:
                if _unclear(e) == "unconfirmed":
                    # 서버가 접수한 뒤 응답만 유실됐을 수 있다. 준비 기록을
                    # 보존하고 같은 작업의 동기 폴백·다음 실행 재제출을 막는다.
                    st[pre]["status"] = "submission_unknown"
                    _save_state(st)
                    base.PENDING_BATCHES[pre] = len(todo)
                    for i in todo:
                        out[i] = GenResult(
                            "", self.name, self.model, ok=False,
                            error=f"batch submission unresolved: {e}"[:200],
                            billing_mode="batch", cost_status="unconfirmed",
                            batch_id=pre, custom_id=cids[i])
                    print(f"[claude] batch 제출 응답 불명 — {len(todo)}건 재호출 보류")
                    return out
                # 처리 불명이 아닌 제출 오류는 기존 동기 폴백을 유지한다.
                print(f"[claude] batch 제출 실패 → 동기: {e}")
                st.pop(pre, None)
                _save_state(st)
                return self._fill_sync(out, todo, jobs, temps, max_tokens)
            batch_id = batch.id
            st[batch_id] = dict(st.pop(pre), status="submitted")
            _save_state(st)
            print(f"[claude] batch {batch_id} 제출 ({len(reqs)}건)")

        rec = st[batch_id]
        _t0 = time.time()
        status = self._wait(batch_id, timeout_sec, poll_sec, st)
        if status != "ended":
            # 취소 요청은 취소 완료가 아니다. 이미 처리 중인 요청은 끝까지 처리·과금된다.
            # 종료를 확인한 뒤 성공분은 회수하고, 처리되지 않은 것만 다시 만든다.
            try:
                self._client.messages.batches.cancel(batch_id)
                rec["status"] = "canceling"
                print(f"[claude] batch {batch_id} {timeout_sec}s 초과 → 취소 요청")
            except Exception as ce:
                print(f"[claude] ⚠ batch 취소 요청 실패: {ce}")
            _save_state(st)
            # 이번 실행의 나머지 묶음은 동기로 처리한다(아침 마감).
            self.use_batch = False
            status = self._wait(batch_id, config.BATCH_CANCEL_WAIT_SEC, poll_sec, st)
        print(f"[claude] batch {batch_id} 상태 {status} {time.time() - _t0:.0f}초")

        prev = set(rec.get("collected", []))     # 이전 실행이 이미 회수(원장 기록)한 결과
        got = self._results(batch_id, rec) if status == "ended" else {}
        if status == "ended":
            base.PENDING_BATCHES.pop(batch_id, None)
            if rec["status"] != "collected":
                base.PENDING_BATCHES[batch_id] = len(set(rec["jobs"]) - set(got))
        for cid in prev & set(got):
            kind, res = got[cid]
            if kind == "succeeded":
                # 내용은 다시 쓰되 비용은 이미 기록됐다. 두 번 합산하지 않는다.
                res.input_tokens = res.output_tokens = 0
                res.cache_read_tokens = res.cache_write_tokens = res.cache_write_1h_tokens = 0
                res.attempt_type = "batch_reuse"
        # 재사용한 배치는 custom_id 순번이 다를 수 있어 작업 해시로 맞춘다.
        bcid = {h: c for c, h in rec["jobs"].items()}
        retry = []
        for i in todo:
            kind, res = got.get(bcid.get(hashes[i], cids[i]), (None, None))
            if kind == "succeeded":
                out[i] = res
            elif kind in ("canceled", "expired") or (
                    kind == "errored" and res != "invalid_request_error"):
                retry.append(i)          # 처리되지 않았다(과금 없음). 필요한 것만 다시
            elif kind == "errored":
                out[i] = GenResult("", self.name, self.model, ok=False,
                                   error=f"batch errored: {res}", billing_mode="batch",
                                   batch_id=batch_id,
                                   custom_id=bcid.get(hashes[i], cids[i]))
            else:
                # 종료를 확인 못 했거나 결과를 못 받았다. 처리·과금 여부를 모르므로
                # 0원 처리도, 전량 재호출도 하지 않는다. 다음 실행이 결과를 회수한다.
                out[i] = GenResult("", self.name, self.model, ok=False,
                                   error=f"batch unresolved ({status})",
                                   billing_mode="batch", cost_status="unconfirmed",
                                   batch_id=batch_id,
                                   custom_id=bcid.get(hashes[i], cids[i]))
        _save_state(st)
        if retry:
            print(f"[claude] batch 미처리 {len(retry)}건만 동기 재시도")
        return self._fill_sync(out, retry, jobs, temps, max_tokens,
                               attempt_type="batch_retry")

    def _fill_sync(self, out, idx, jobs, temps, max_tokens, attempt_type=""):
        if idx:
            res = self._sync_many([jobs[i] for i in idx], [temps[i] for i in idx],
                                  max_tokens)
            for i, r in zip(idx, res):
                if attempt_type:
                    r.attempt_type = attempt_type
                out[i] = r
        return out

    def _wait(self, batch_id, limit, poll_sec, st) -> str:
        """limit 초 안에 ended 를 확인하면 'ended'. 조회 오류는 상한 안에서만 재시도."""
        deadline = time.monotonic() + max(0, limit)
        status = "unknown"
        while True:
            try:
                status = self._client.messages.batches.retrieve(batch_id).processing_status
                st[batch_id]["status"] = status
            except Exception as e:
                print(f"[claude] batch 조회 실패(재시도): {type(e).__name__}")
            if status == "ended" or time.monotonic() >= deadline:
                return status
            time.sleep(poll_sec)

    def _results(self, batch_id, rec, tries=3) -> dict:
        """custom_id → (결과 유형, GenResult|오류유형). 다운로드가 끊기면 결과 조회만 재시도한다."""
        got = {}
        for t in range(tries):
            try:
                for r in self._client.messages.batches.results(batch_id):
                    if r.custom_id in got:
                        continue
                    kind = r.result.type
                    if kind == "succeeded":
                        g = _message_result(r.result.message, self.name, self.model,
                                            billing_mode="batch")
                        g.batch_id, g.custom_id = batch_id, r.custom_id
                        got[r.custom_id] = (kind, g)
                    else:
                        err = getattr(getattr(getattr(r.result, "error", None), "error",
                                              None), "type", "") or ""
                        got[r.custom_id] = (kind, err)
                break
            except Exception as e:
                print(f"[claude] batch 결과 조회 중단 {t + 1}/{tries}: {type(e).__name__}")
        rec["collected"] = sorted(got)
        rec["status"] = ("collected" if set(rec["jobs"]) <= set(got)
                         else "ended_uncollected")
        return got

    def _recover(self, st, skip, want) -> dict:
        """상태 파일의 이전 배치를 정리한다. 반환: 작업 해시 → 재사용 가능한 성공 결과.

        같은 작업이 아닌 성공 결과는 이미 과금된 것이므로 원장에 'orphan_batch' 로
        한 번만 남긴다. 아직 끝나지 않은 배치는 비용 미확정으로 표시하고 기다리지 않는다.
        """
        found = {}
        for bid, rec in list(st.items()):
            if bid == skip or rec.get("status") in ("collected", "abandoned"):
                continue
            if bid.startswith("pending-"):
                # 제출 직전 기록만 있고 batch_id 가 없다. 제출 여부를 알 수 없다.
                base.PENDING_BATCHES[bid] = len(rec.get("jobs", {}))
                print(f"[claude] ⚠ 제출 여부 불명 배치 기록 {bid} — 비용 미확정")
                continue
            try:
                status = self._client.messages.batches.retrieve(bid).processing_status
            except Exception as e:
                print(f"[claude] 이전 batch {bid} 조회 실패: {type(e).__name__}")
                base.PENDING_BATCHES[bid] = len(rec.get("jobs", {}))
                continue
            if status != "ended":
                rec["status"] = status
                base.PENDING_BATCHES[bid] = len(rec.get("jobs", {}))
                print(f"[claude] 이전 batch {bid} {status} — 비용 미확정, 대기 안 함")
                continue
            base.PENDING_BATCHES.pop(bid, None)
            done = set(rec.get("collected", []))
            got = self._results(bid, rec)
            for cid, (kind, res) in got.items():
                if kind != "succeeded":
                    continue
                if cid not in done:
                    budget.account_recovered(res)
                h = rec["jobs"].get(cid)
                if h in want and h not in found:
                    if cid in done:
                        res.input_tokens = res.output_tokens = 0
                        res.cache_read_tokens = res.cache_write_tokens = res.cache_write_1h_tokens = 0
                        res.attempt_type = "batch_reuse"
                    found[h] = res           # 같은 작업: 다시 만들지 않는다
                elif cid not in done:
                    record_usage(res, "write", "orphan_batch")
            if rec["status"] != "collected":
                base.PENDING_BATCHES[bid] = len(set(rec["jobs"]) - set(got))
        return found

    def _sync_many(self, jobs, temperature, max_tokens) -> list[GenResult]:
        """같은 실행에서 결과가 필요한 작업을 제한된 동시성으로 처리한다."""
        if not jobs:
            return []
        workers = min(max(1, config.CLAUDE_SYNC_WORKERS), len(jobs))
        if budget.active():
            workers = min(workers, 2)
        temps = temperature if isinstance(temperature, list) else [temperature] * len(jobs)
        # One useful response primes the shared cache; avoids six cold writes and
        # an extra max_tokens=0 prewarm. Same prompt and generated content contract.
        first = ([self.generate(jobs[0][0], jobs[0][1], temps[0], max_tokens)]
                 if budget.active() else [])
        offset = len(first)
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            return first + list(ex.map(lambda jt: self.generate(
                jt[0][0], jt[0][1], jt[1], max_tokens), zip(jobs[offset:], temps[offset:])))
