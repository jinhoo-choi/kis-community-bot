"""LLM 프로바이더 공통 인터페이스.

Claude / Gemini 를 동일 시그니처로 호출하기 위한 얇은 어댑터.
generator 는 이 인터페이스만 알고 있으면 되므로 프로바이더 교체·추가가 자유롭다.
"""
from dataclasses import dataclass


@dataclass
class GenResult:
    text: str
    provider: str
    model: str
    ok: bool = True
    error: str = ""


class Provider:
    name = "base"

    def generate(self, system: str, user: str, temperature: float = 1.0,
                 max_tokens: int = 700) -> GenResult:
        raise NotImplementedError

    def generate_many(self, jobs: list[tuple[str, str]], **kw) -> list[GenResult]:
        """[(system, user), ...] → 결과 리스트. 기본은 순차 호출."""
        return [self.generate(s, u, **kw) for s, u in jobs]

    def available(self) -> bool:
        return False

    # 모델 은퇴 감지용. 폴백이 발생하면 여기에 기록되어 run_stats 로 넘어간다.
    fallbacks: list = None
