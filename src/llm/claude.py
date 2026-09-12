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

    def generate_many(self, jobs, temperature=1.0, max_tokens=700,
                      poll_sec=15, timeout_sec=600) -> list[GenResult]:
        if not self.use_batch or len(jobs) < 15:
            return self._sync_many(jobs, temperature, max_tokens)

        try:
            reqs = [{
                "custom_id": f"j{i}",
                "params": {
                    "model": self.model, "max_tokens": max_tokens, "system": s,
                    "messages": [{"role": "user", "content": u}],
                    **({"thinking": {"type": "disabled"}}
                       if self.model == "claude-sonnet-5"
                       else ({} if self._no_temp else {"temperature": temperature})),
                },
            } for i, (s, u) in enumerate(jobs)]

            batch = self._client.messages.batches.create(requests=reqs)
            print(f"[claude] batch {batch.id} 제출 ({len(reqs)}건)")

            waited = 0
            while waited < timeout_sec:
                if self._client.messages.batches.retrieve(batch.id).processing_status == "ended":
                    break
                time.sleep(poll_sec)
                waited += poll_sec
            else:
                # 30분을 기다리느니 동기로 다시 도는 편이 빠르다
                raise TimeoutError(f"batch timeout {timeout_sec}s")

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
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(lambda job: self.generate(
                job[0], job[1], temperature, max_tokens), jobs))
