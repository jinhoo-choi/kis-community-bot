"""프로바이더 인스턴스 관리 + 역할별 라우팅.

역할 분담
  enrich  : Gemini (검색 그라운딩) — 빈약한 facts 를 사실로 보강
  write   : Claude / Gemini 병렬 — 같은 톤이라도 모델이 다르면 문체 지문이 갈린다
  judge   : 교차 심사 — 작성자와 다른 프로바이더가 채점(자기 글 자기 채점 편향 제거)
"""
import functools

import config
from src.llm.claude import ClaudeProvider
from src.llm.gemini import GeminiProvider


@functools.lru_cache(maxsize=1)
def writers() -> dict:
    p = {}
    c = ClaudeProvider(config.ANTHROPIC_API_KEY, config.CLAUDE_MODEL, config.USE_BATCH)
    g = GeminiProvider(config.GEMINI_API_KEY, config.GEMINI_MODEL)
    if c.available():
        p["claude"] = c
    if g.available():
        p["gemini"] = g
    if not p:
        raise RuntimeError("사용 가능한 LLM 프로바이더가 없습니다. API 키를 확인하세요.")
    return p


@functools.lru_cache(maxsize=1)
def enricher():
    """검색 그라운딩용. Gemini 만 지원하며 없으면 None (보강 단계 스킵)."""
    g = GeminiProvider(config.GEMINI_API_KEY, config.GEMINI_ENRICH_MODEL, grounding=True)
    return g if g.available() else None


@functools.lru_cache(maxsize=1)
def judges() -> dict:
    """심사는 저비용 모델로."""
    p = {}
    c = ClaudeProvider(config.ANTHROPIC_API_KEY, config.CLAUDE_JUDGE_MODEL, use_batch=False)
    g = GeminiProvider(config.GEMINI_API_KEY, config.GEMINI_JUDGE_MODEL)
    if c.available():
        p["claude"] = c
    if g.available():
        p["gemini"] = g
    return p


def split_by_ratio(items: list) -> dict[str, list]:
    """작성 물량을 프로바이더별로 배분. 프로바이더가 하나뿐이면 전부 몰아준다."""
    w = writers()
    names = list(w.keys())
    if len(names) == 1:
        return {names[0]: items}

    ratio = config.WRITER_RATIO
    total = sum(ratio.get(n, 0) for n in names) or 1
    out, idx = {}, 0
    for i, n in enumerate(names):
        if i == len(names) - 1:
            out[n] = items[idx:]
        else:
            k = round(len(items) * ratio.get(n, 0) / total)
            out[n] = items[idx:idx + k]
            idx += k
    return out


def cross_judge_for(writer: str) -> str | None:
    """작성자와 다른 프로바이더를 심사자로 지정. 없으면 None."""
    j = judges()
    other = [n for n in j if n != writer]
    return other[0] if other else None
