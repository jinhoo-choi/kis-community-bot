"""Anthropic Claude 프로바이더.

대량 비실시간 작업이므로 Message Batches API 를 기본으로 쓴다.
문서: https://docs.claude.com/en/docs/build-with-claude/batch-processing
"""
import time
import concurrent.futures as cf

import config
from src.llm.base import Provider, GenResult

# 모델이 은퇴하면 단일 문자열은 그날 파이프라인을 죽인다.
# 404/not_found/deprecated 계열 오류에서만 다음 후보로 승격한다.
_RETIRED = ("not_found", "404", "deprecated", "does not exist",
            "is not supported", "NOT_FOUND", "unsupported")


def _ival(obj, name: str) -> int:
    try:
        return int(getattr(obj, name, 0) or 0)
    except (TypeError, ValueError):
        return 0


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
    return GenResult(
        txt, provider, str(getattr(message, "model", "") or fallback_model),
        ok=bool(txt),
        input_tokens=uncached + cache_read + cache_write,
        output_tokens=_ival(usage, "output_tokens"),
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        sources=srcs,
        tier="paid", service_tier=service_tier, billing_mode=billing_mode,
    )


class ClaudeProvider(Provider):
    name = "claude"

    def __init__(self, api_key: str, model: str, use_batch: bool = True):
        self.model = model
        self.use_batch = use_batch
        self._client = None
        self._no_temp = False
        if api_key:
            import anthropic
            from anthropic import Anthropic
            self._client = Anthropic(api_key=api_key)
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

    def _create(self, system, user, temperature, max_tokens):
        """설치된 SDK 가 temperature 를 안 받는 경우가 있어(실측) 방어적으로 호출한다."""
        kw = dict(model=self.model, max_tokens=max_tokens, system=system,
                  messages=[{"role": "user", "content": user}])
        # Sonnet 5는 비기본 sampling parameter를 거부하고 adaptive thinking이 기본이다.
        # JSON 심사는 짧고 결정적이어야 하므로 thinking을 끄고 temperature를 보내지 않는다.
        if self.model == "claude-sonnet-5":
            # 심사 프롬프트의 82%(약 2,016토큰)는 매번 같은 규칙 텍스트다.
            # Sonnet 5 의 캐싱 최소 길이(1,024토큰)를 넘으므로 system 을 캐시한다.
            # 캐시 읽기는 입력 단가의 0.1배라 심사 입력비가 약 70% 준다(#127 기준
            # 심사 65회 $0.320 → 약 $0.084). 심사 결과는 바뀌지 않는다 — 같은 입력이다.
            # Haiku 4.5(생성)는 최소 길이가 4,096토큰인데 입력이 3,836토큰이라
            # 캐싱되지 않는다. 그쪽에 걸면 쓰기 할증만 생길 수 있어 걸지 않는다.
            kw["system"] = [{"type": "text", "text": system,
                             "cache_control": {"type": "ephemeral"}}]
            return self._client.messages.create(
                thinking={"type": "disabled"}, **kw)
        if self._no_temp:                 # 한 번 확인했으면 매번 재시도하지 않는다
            return self._client.messages.create(**kw)
        try:
            return self._client.messages.create(temperature=temperature, **kw)
        except TypeError as e:
            if "temperature" not in str(e):
                raise
            print(f"[claude] SDK 가 temperature 미지원 → 제외하고 재호출 ({e})")
            self._no_temp = True
            return self._client.messages.create(**kw)

    def generate(self, system, user, temperature=1.0, max_tokens=700) -> GenResult:
        try:
            r = self._create(system, user, temperature, max_tokens)
            return _message_result(r, self.name, self.model)
        except Exception as e:
            msg = str(e)
            if self._promote(msg):
                result = self.generate(system, user, temperature, max_tokens)
                result.attempts += 1
                return result
            return GenResult("", self.name, self.model, ok=False, error=msg[:200])

    def search(self, system: str, user: str, max_tokens: int = 700) -> GenResult:
        """웹 검색 도구를 붙여 호출한다. 보강(enrich) 전용.

        Gemini 그라운딩이 하던 일을 대신한다. 검색 결과 블록에서 근거 URL 을
        모아 GenResult.sources 에 담는다 — 보강은 근거 없는 내용을 쓰지 않는다.
        """
        try:
            r = self._client.messages.create(
                model=self.model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": user}],
                tools=[{"type": "web_search_20250305", "name": "web_search",
                        "max_uses": 3}],
            )
            return _message_result(r, self.name, self.model)
        except Exception as e:
            return GenResult("", self.name, self.model, ok=False, error=str(e)[:200])

    def generate_many(self, jobs, temperature=1.0, max_tokens=700,
                      poll_sec=10, timeout_sec=300) -> list[GenResult]:
        if not self.use_batch or len(jobs) < 15:
            return self._sync_many(jobs, temperature, max_tokens)

        # temperature 는 요청마다 다를 수 있다(배치 API 는 요청별 params 를 받는다).
        temps = temperature if isinstance(temperature, list) else [temperature] * len(jobs)
        try:
            reqs = [{
                "custom_id": f"j{i}",
                "params": {
                    "model": self.model, "max_tokens": max_tokens, "system": s,
                    "messages": [{"role": "user", "content": u}],
                    **({"thinking": {"type": "disabled"}}
                       if self.model == "claude-sonnet-5"
                       else ({} if self._no_temp else {"temperature": temps[i]})),
                },
            } for i, (s, u) in enumerate(jobs)]

            batch = self._client.messages.batches.create(requests=reqs)
            print(f"[claude] batch {batch.id} 제출 ({len(reqs)}건)")
            _t0 = time.time()

            waited = 0
            while waited < timeout_sec:
                if self._client.messages.batches.retrieve(batch.id).processing_status == "ended":
                    break
                time.sleep(poll_sec)
                waited += poll_sec
            else:
                # 취소하지 않고 동기로 폴백하면, 배치는 뒤에서 끝까지 처리돼
                # 배치분과 동기분이 둘 다 과금된다. 폴백 전에 반드시 취소한다.
                # 대기 상한은 5분 — 08:00 발송이 목표라 오래 기다리지 않는다.
                try:
                    self._client.messages.batches.cancel(batch.id)
                    print(f"[claude] batch {batch.id} {timeout_sec}s 초과 → 취소")
                except Exception as ce:
                    print(f"[claude] ⚠ batch 취소 실패(이중 과금 가능): {ce}")
                raise TimeoutError(f"batch timeout {timeout_sec}s")

            print(f"[claude] batch {batch.id} 완료 {time.time() - _t0:.0f}초")
            got = {}
            for res in self._client.messages.batches.results(batch.id):
                if res.result.type == "succeeded":
                    m = res.result.message
                    got[res.custom_id] = _message_result(
                        m, self.name, self.model, billing_mode="batch")

            return [got.get(f"j{i}") or GenResult(
                        "", self.name, self.model, ok=False,
                        error="batch result missing", billing_mode="batch")
                    for i in range(len(jobs))]
        except Exception as e:
            print(f"[claude] batch 실패 → 동기 폴백: {e}")
            return self._sync_many(jobs, temperature, max_tokens)

    def _sync_many(self, jobs, temperature, max_tokens) -> list[GenResult]:
        """같은 실행에서 결과가 필요한 작업을 제한된 동시성으로 처리한다."""
        if not jobs:
            return []
        workers = min(max(1, config.CLAUDE_SYNC_WORKERS), len(jobs))
        temps = temperature if isinstance(temperature, list) else [temperature] * len(jobs)
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(lambda jt: self.generate(
                jt[0][0], jt[0][1], jt[1], max_tokens), zip(jobs, temps)))
