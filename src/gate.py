"""수집 직후 하드 게이트.

원칙(리스크봇에서 이식): **구조적 규칙은 확률적 AI 판단보다 항상 선행한다.**
지금까지는 정규식 필터가 '생성 후'에만 돌았다. 즉 상장폐지 심사 중인 종목에 대해
AI가 글을 다 쓰고 나서 거르는 구조였다. 게이트를 앞으로 당긴다.

리스크봇과 방향이 다른 점:
  리스크봇은 오탐 > 미탐 (놓치면 손실) → CRITICAL_KW bypass 로 무조건 통과시킴
  이 봇은 미탐 > 오탐 (애매하면 안 쓰는 게 이득) → bypass 없음. 걸리면 전부 배제
"""
import re

# tier 1 — 사안 자체가 커뮤니티 AI 게시에 부적합
# 투자자 손실·수사·상장 지위와 직결된 건 사람이 판단해야 한다.
TIER1_LEGAL = [
    "상장폐지", "상장적격성", "관리종목", "거래정지", "매매거래 정지",
    "회생절차", "파산", "부도", "감사의견", "의견거절", "한정",
    "횡령", "배임", "분식회계", "불성실공시", "벌금", "과징금",
    "검찰", "압수수색", "기소", "구속", "고발", "제재",
]

# tier 2 — 이해상충. 자사 및 계열 관련 종목은 AI가 논평하지 않는다.
TIER2_CONFLICT_CODES = {
    "071050",   # 한국금융지주
    "071055",   # 한국금융지주우
}
TIER2_CONFLICT_KW = [
    "한국투자증권", "한국금융지주", "한국투자", "카카오뱅크",
]

# tier 3 — 투기·선동 소지
TIER3_SPECULATIVE = [
    "정치테마", "대선", "총선", "테마주", "작전", "세력",
    "투자경고", "투자위험", "단기과열", "이상급등",
]

# tier 4 — 정보량이 없어 글이 될 수 없는 형식 공시
TIER4_NOISE = [
    "기타경영사항", "주주총회소집", "임원ㆍ주요주주", "주식등의대량보유",
    "정정신고", "첨부정정", "기재정정", "일괄신고", "증권발행실적",
]


def _hit(text: str, kws: list[str]) -> str | None:
    for k in kws:
        if k in text:
            return k
    return None


def is_hard_excluded(item: dict) -> tuple[bool, str]:
    """(배제여부, 사유). AI 호출 이전에 반드시 통과시킨다."""
    text = f"{item.get('title','')} {item.get('stock_name','') or ''}"

    if item.get("stock_code") in TIER2_CONFLICT_CODES:
        return True, "tier2:자사계열종목"

    k = _hit(text, TIER2_CONFLICT_KW)
    if k:
        return True, f"tier2:이해상충({k})"

    k = _hit(text, TIER1_LEGAL)
    if k:
        return True, f"tier1:법적사안({k})"

    k = _hit(text, TIER3_SPECULATIVE)
    if k:
        return True, f"tier3:투기소지({k})"

    k = _hit(text, TIER4_NOISE)
    if k:
        return True, f"tier4:정보없음({k})"

    # 제목이 너무 짧으면 글감이 되지 않는다
    if len(re.sub(r"\W", "", item.get("title", ""))) < 6:
        return True, "tier4:제목부족"

    return False, ""


def apply(items: list[dict]) -> tuple[list[dict], list[tuple[str, str]]]:
    passed, blocked = [], []
    for it in items:
        ex, why = is_hard_excluded(it)
        if ex:
            blocked.append((it.get("id", "?"), why))
        else:
            passed.append(it)

    if blocked:
        from collections import Counter
        c = Counter(w.split(":")[0] for _, w in blocked)
        print(f"[gate] 차단 {len(blocked)}건 {dict(c)}")
    return passed, blocked
