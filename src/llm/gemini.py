"""Google Gemini 프로바이더.

모델(2026년 기준):
  gemini-3.5-flash        : GA(2026-05-19). gemini-flash-latest 가 가리키는 모델
  gemini-3.1-flash-lite   : GA(2026-05-07). 속도·비용 최적화 → 심사(judge)용
  gemini-3-pro-preview    : 최고 추론 품질

참고: 2026년 6월부터 Interactions API 가 권장 인터페이스이나,
기존 generateContent 는 레거시로 분류되었을 뿐 계속 완전히 지원되므로
안정성을 위해 여기서는 generateContent(SDK)를 사용한다.
문서: https://ai.google.dev/gemini-api/docs/models
"""
import concurrent.futures as cf

from src.llm.base import Provider, GenResult


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, api_key: str, model: str, grounding: bool = False):
        self.model = model
        self.grounding = grounding
        self._client = None
        self._types = None
        if api_key:
            from google import genai
            from google.genai import types
            self._client = genai.Client(api_key=api_key)
            self._types = types

    def available(self) -> bool:
        return self._client is not None

    def _config(self, system, temperature, max_tokens):
        t = self._types
        kw = dict(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        if self.grounding:
            # Google 검색 그라운딩 — 사실 보강 단계에서만 켠다
            kw["tools"] = [t.Tool(google_search=t.GoogleSearch())]
        return t.GenerateContentConfig(**kw)

    def generate(self, system, user, temperature=1.0, max_tokens=700) -> GenResult:
        try:
            r = self._client.models.generate_content(
                model=self.model,
                contents=user,
                config=self._config(system, temperature, max_tokens),
            )
            return GenResult((r.text or "").strip(), self.name, self.model)
        except Exception as e:
            return GenResult("", self.name, self.model, ok=False, error=str(e)[:200])

    def generate_many(self, jobs, temperature=1.0, max_tokens=700,
                      workers: int = 6) -> list[GenResult]:
        """Gemini 는 배치 대신 소규모 병렬 호출. RPM 초과를 피해 workers 를 낮게 유지."""
        out = [None] * len(jobs)
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            fut = {
                ex.submit(self.generate, s, u, temperature, max_tokens): i
                for i, (s, u) in enumerate(jobs)
            }
            for f in cf.as_completed(fut):
                out[fut[f]] = f.result()
        return out
