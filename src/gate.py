"""수집 직후 하드 게이트.

원칙(리스크봇에서 이식): **구조적 규칙은 확률적 AI 판단보다 항상 선행한다.**
지금까지는 정규식 필터가 '생성 후'에만 돌았다. 즉 상장폐지 심사 중인 종목에 대해
AI가 글을 다 쓰고 나서 거르는 구조였다. 게이트를 앞으로 당긴다.

리스크봇과 방향이 다른 점:
  리스크봇은 오탐 > 미탐 (놓치면 손실) → CRITICAL_KW bypass 로 무조건 통과시킴
  이 봇은 미탐 > 오탐 (애매하면 안 쓰는 게 이득) → bypass 없음. 걸리면 전부 배제
"""
import functools
import re

from src import angles

# tier 1 — 사안 자체가 커뮤니티 AI 게시에 부적합
# 투자자 손실·수사·상장 지위와 직결된 건 사람이 판단해야 한다.
TIER1_LEGAL = [
    "상장폐지", "상장적격성", "관리종목", "거래정지", "매매거래 정지",
    "회생절차", "파산", "부도", "감사의견 거절", "의견거절",
    "횡령", "배임", "분식회계", "불성실공시", "과징금",
    "압수수색", "구속기소", "검찰 수사", "검찰 압수",
]

# tier 2 — 이해상충. 자사 및 계열 관련 종목은 AI가 논평하지 않는다.
TIER2_CONFLICT_CODES = {
    "071050",   # 한국금융지주
    "071055",   # 한국금융지주우
}
TIER2_CONFLICT_KW = [
    "한국투자증권", "한국금융지주", "한국투자", "카카오뱅크",
]

# 타 운용사·타 증권사 상품 홍보. 자사 커뮤니티에 옮길 수 없다.
# 텔레그램 운용사 채널 소스를 붙이면서 실제로 필요해졌다.
TIER2_RIVAL_PRODUCT = [
    "TIGER", "KODEX", "TIMEFOLIO", "KOSEF", "ARIRANG", "HANARO",
    "순자산총액", "설정액", "총보수", "분배금 지급", "신규 상장 ETF",
]

# tier 3 — 투기·선동 소지
TIER3_SPECULATIVE = [
    "정치테마", "대선", "총선", "테마주", "작전", "세력",
    "투자경고", "투자위험", "단기과열", "이상급등",
    # 2026-09-03 실측: 연합뉴스 RSS 에서 정치 인물 기사가 유입됨.
    # 증권사 커뮤니티에 AI가 정치 소재 글을 쓰는 것은 어떤 톤이든 위험하다.
    "추경", "국회", "여당", "야당", "대통령실", "국정감사", "청문회",
    "탄핵", "특검", "선거",
]

# tier 4 — 정보량이 없어 글이 될 수 없는 형식 공시
TIER4_NOISE = [
    "기타경영사항", "주주총회소집", "임원ㆍ주요주주", "주식등의대량보유",
    "일괄신고", "증권발행실적",
    # '기재정정'·'첨부정정'을 뺐다. 게이트 차단 22건 중 10건이 이 규칙이었다.
    # 정정 공시에서 수치를 뽑을 수단이 없던 시절의 규칙인데, 지금은
    # 조회창 180일 확대(dart_detail)와 공급계약 원문 파서로 수치가 붙는다.
    # 수치가 안 붙는 정정 건은 has_substance 가 tier5 로 걸러낸다.
    # 제목으로 한 번, 내용으로 또 한 번 막을 이유가 없다.
]


@functools.lru_cache(maxsize=64)
def _pat(kw: str) -> re.Pattern:
    """한글 왼쪽 경계를 요구한다.

    단순 `in` 연산은 부분일치로 오탐한다 — '법무부도 이전' 이 '부도' 에 걸려
    정책 기사가 차단된 사례(2026-09-03). 한글은 어절 경계가 없으므로
    앞에 한글이 붙어 다른 낱말을 이루는 경우를 배제한다.
    """
    return re.compile(rf"(?<![가-힣]){re.escape(kw)}")


def _hit(text: str, kws: list[str]) -> str | None:
    for k in kws:
        if _pat(k).search(text):
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

    k = _hit(f"{text} {item.get('facts','')[:300]}", TIER2_RIVAL_PRODUCT)
    if k:
        return True, f"tier2:타사상품({k})"

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


# 글이 되려면 제목 말고 '말할 수 있는 사실'이 있어야 한다.
# 리포트 제목만 있는 항목으로 억지로 글을 쓰면 내용 없는 글이 나온다 (실측:
# "리포트 제목은 'Never Stop Rising'이다" 수준의 글이 배포됨).
_SUBSTANCE = re.compile(
    r"\d[\d,]*\s*(원|%|억|조|배|건|주)"      # 구체 수치
    r"|적정가격|투자의견|등락률|거래대금|종가"     # 정형 필드
    r"|\[검색으로 확인된 배경\]"                 # enrich 성공
    r"|DART 정형 데이터"                        # 공시 상세 보강 성공
    r"|답변 성격"                               # 거래소 조회공시
)


# 리포트는 별도 기준을 쓴다.
# 리포트 제목만으로는 "본문 읽어보세요" 수준의 글밖에 안 나온다 (실측:
# "리포트의 구체적인 분석 내용이 어떻게 펼쳐지는지 확인해 보고 싶으신 분들이…").
# 검색으로 얻은 '회사 배경'은 리포트 내용이 아니므로 글감으로 치지 않는다.
_RESEARCH_SUBSTANCE = re.compile(r"제시 적정가격|투자의견")

# 정책 글은 수치가 아니라 '무엇이 어떻게 바뀌는가'가 글감이다.
# 수치를 요구했더니 26건 수집분에서 4건만 통과했다 (실측).
# 대신 요지가 실제 내용을 담고 있는지를 본다 — 제목 재탕은 걸러야 한다.
_POLICY_MIN_DESC = 120


def has_substance(item: dict) -> bool:
    facts = item.get("facts", "")
    # 주석(※)과 메타 줄을 뺀 실질 내용만 본다
    core = "\n".join(l for l in facts.splitlines()
                      if l.strip() and not l.strip().startswith("※"))
    if item.get("kind") == "research":
        return bool(_RESEARCH_SUBSTANCE.search(core))
    if item.get("kind") in ("policy", "theme"):
        desc = "".join(l.split(":", 1)[-1] for l in core.splitlines()
                       if l.startswith("요지"))
        title = "".join(l.split(":", 1)[-1] for l in core.splitlines()
                        if l.startswith("제목"))
        # 요지가 제목을 그대로 옮긴 것이면 글감이 아니다
        if desc.strip() and desc.strip() != title.strip():
            return len(desc.strip()) >= _POLICY_MIN_DESC or bool(_SUBSTANCE.search(core))
        return bool(_SUBSTANCE.search(core))
    return bool(_SUBSTANCE.search(core))


def apply(items: list[dict]) -> tuple[list[dict], list[tuple[str, str]]]:
    passed, blocked = [], []
    for it in items:
        ex, why = is_hard_excluded(it)
        if ex:
            blocked.append((it.get("id", "?"), why))
        elif not has_substance(it):
            blocked.append((it.get("id", "?"), "tier5:글감부족"))
        elif it.get("no_stock_fit"):
            # 커뮤니티에 종목방만 있는데 어떤 종목방에도 맞지 않는 소재다
            blocked.append((it.get("id", "?"), "tier5:게시판없음"))
        elif not angles.available(it):
            # 강조할 각도가 하나도 없으면 어떤 페르소나를 줘도 글이 되지 않는다.
            blocked.append((it.get("id", "?"), "tier5:앵글없음"))
        else:
            passed.append(it)

    if blocked:
        from collections import Counter
        c = Counter(w.split(":")[0] for _, w in blocked)
        print(f"[gate] 차단 {len(blocked)}건 {dict(c)}")
    return passed, blocked
