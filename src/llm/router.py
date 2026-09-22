"""프로바이더 인스턴스 관리 + 역할별 라우팅.

역할 분담 (2026-09-23 부터 Claude 단독)
  enrich  : Claude 웹 검색 — 빈약한 facts 를 검색으로 확인된 사실로 보강
  write   : Claude Haiku (배치)
  judge   : Claude Sonnet — 작성 모델과 다른 모델로 채점(자기 글 자기 채점 편향 제거)

Gemini 를 걷어낸 근거 (실측)
  - 2026-09-23 결제/할당량 오류로 Gemini 전건 실패. 그날도 발송 50건 유지
  - 생성 단가: Gemini Flash 건당 약 $0.0044 vs Haiku 배치 약 $0.0014
  - 글 다양성: Claude 단독 유사쌍 0.24% vs 혼합 0.41~0.82%
  - 심사: Gemini 가 근거 없는 '차익실현' 을 사실성 5점으로 통과시킨 이력
장애 대비는 문장틀 예비(reserve)가 맡는다. Gemini 는 이미 그 역할이 아니었다.
"""
import functools

import config
from src.llm.claude import ClaudeProvider


@functools.lru_cache(maxsize=1)
def writers() -> dict:
    p = {}
    c = ClaudeProvider(config.ANTHROPIC_API_KEY, config.CLAUDE_MODEL, config.USE_BATCH)
    if c.available():
        p["claude"] = c
    if not p:
        raise RuntimeError("사용 가능한 LLM 프로바이더가 없습니다. API 키를 확인하세요.")
    return p


@functools.lru_cache(maxsize=1)
def enricher():
    """검색 보강용. Claude 웹 검색 도구를 쓴다.

    종전에는 Gemini 그라운딩이었는데 2026-09-23 결제/할당량 오류로 전건
    실패했고(보강 0건), 그날 발송은 50건을 유지했다. Gemini 생성은 Haiku 보다
    건당 3배 비쌌고(약 $0.0044 vs 배치 $0.0014), 글 다양성도 더 나빴다
    (유사쌍 혼합 0.41~0.82% vs Claude 단독 0.24%). Gemini 를 걷어낸다.
    """
    c = ClaudeProvider(config.ANTHROPIC_API_KEY, config.CLAUDE_MODEL, use_batch=False)
    return c if c.available() else None


@functools.lru_cache(maxsize=1)
def judges() -> dict:
    """심사는 저비용 모델로."""
    p = {}
    c = ClaudeProvider(config.ANTHROPIC_API_KEY, config.CLAUDE_JUDGE_MODEL, use_batch=False)
    if c.available():
        p["claude"] = c
    # 작성(Haiku)과 다른 Sonnet 으로 심사한다. 같은 모델 자기심사는 하지 않는다.
    if (c.available()
            and config.CLAUDE_BACKUP_JUDGE_MODEL != config.CLAUDE_JUDGE_MODEL):
        backup = ClaudeProvider(config.ANTHROPIC_API_KEY,
                                config.CLAUDE_BACKUP_JUDGE_MODEL, use_batch=False)
        if backup.available():
            p["claude_backup"] = backup
    return p


def split_by_ratio(items: list) -> dict[str, list]:
    """작성 물량을 프로바이더별로 배분. 프로바이더가 하나뿐이면 전부 몰아준다."""
    # Gemini quota circuit breaker 가 동작했으면 이후 작성 물량은 Claude로 몰린다.
    w = {n: p for n, p in writers().items() if p.available()}
    if not w:
        raise RuntimeError("사용 가능한 LLM 프로바이더가 없습니다.")
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
    j = {n: p for n, p in judges().items() if p.available()}
    # 실발송 검증에서 Gemini judge가 근거 없는 '차익실현'을 factual 5점으로
    # 통과시키고 정상 리서치 문장을 fatal로 오판했다. 작성 Haiku와 다른 모델인
    # Sonnet 5를 먼저 쓰고, 실패할 때 Gemini/Haiku로 내려간다.
    preferred = ["claude_backup", "claude"]
    return next((n for n in preferred if n in j and n != writer), None)
