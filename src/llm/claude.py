"""Anthropic Claude 프로바이더.

대량 비실시간 작업이므로 Message Batches API 를 기본으로 쓴다.
문서: https://docs.claude.com/en/docs/build-with-claude/batch-processing
"""
import time

import config
from src.llm.base import Provider, GenResult

# 모델이 은퇴하면 단일 문자열은 그날 파이프라인을 죽인다.
# 404/not_found/deprecated 계열 오류에서만 다음 후보로 승격한다.
_RETIRED = ("not_found", "404", "deprecated", "does not exist",
            "is not supported", "NOT_FOUND", "unsupported")


class ClaudeProvider(Provider):
    name = "claude"

    def __init__(self, api_key: str, model: str, use_batch: bool = True):
        self.model = model
        self.use_batch = use_batch
        self._client = None
        if api_key:
            from anthropic import Anthropic
            self._client = Anthropic(api_key=api_key)

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

    def generate(self, system, user, temperature=1.0, max_tokens=700) -> GenResult:
        try:
            r = self._client.messages.create(
                model=self.model, max_tokens=max_tokens, temperature=temperature,
                system=system, messages=[{"role": "user", "content": user}],
            )
            txt = "".join(b.text for b in r.content if b.type == "text")
            return GenResult(txt.strip(), self.name, self.model)
        except Exception as e:
            msg = str(e)
            if self._promote(msg):
                return self.generate(system, user, temperature, max_tokens)
            return GenResult("", self.name, self.model, ok=False, error=msg[:200])

    def generate_many(self, jobs, temperature=1.0, max_tokens=700,
                      poll_sec=20, timeout_sec=1800) -> list[GenResult]:
        if not self.use_batch or len(jobs) < 5:
            return [self.generate(s, u, temperature, max_tokens) for s, u in jobs]

        try:
            reqs = [{
                "custom_id": f"j{i}",
                "params": {
                    "model": self.model, "max_tokens": max_tokens,
                    "temperature": temperature, "system": s,
                    "messages": [{"role": "user", "content": u}],
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
                raise TimeoutError("batch timeout")

            got = {}
            for res in self._client.messages.batches.results(batch.id):
                if res.result.type == "succeeded":
                    m = res.result.message
                    got[res.custom_id] = "".join(b.text for b in m.content if b.type == "text")

            return [GenResult(got.get(f"j{i}", "").strip(), self.name, self.model,
                              ok=bool(got.get(f"j{i}")))
                    for i in range(len(jobs))]
        except Exception as e:
            print(f"[claude] batch 실패 → 동기 폴백: {e}")
            return [self.generate(s, u, temperature, max_tokens) for s, u in jobs]
