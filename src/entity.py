"""종목 귀속(attribution) 검증.

인사이트봇 FALSE_POSITIVES.md 2026-08-02 사례를 이 봇에 그대로 이식한다.
  증상: 「두나무, 디지털 금융 연합 구축」 기사에 company_name=삼성증권 8.2점
  원인: ALIAS "삼성"→삼성증권 이 그룹명 단독 언급까지 승격시킴. 검증 절차 부재

이 봇의 tickers.resolve() 는 정확히 같은 함정에 빠져 있다.
최장일치 정규식만 쓰므로 "대상 기업 실적 점검" 이라는 제목이
상장사 '대상'(001680)으로 매핑되어 엉뚱한 종목방으로 배포된다.

원칙 (인사이트봇 2026-08-24 교훈):
  판정은 fail-open. 기본은 주체로 인정하고, 부수 신호가 명확할 때만 드롭한다.
  반대로 설계했더니 조사가 생략된 제목이 과잉 드롭됐다는 실측이 있다.
"""
import re

# 일반명사·그룹명과 충돌하는 종목명.
# 단독 등장만으로는 인정하지 않고 '주체 신호'가 함께 있어야 한다.
AMBIGUOUS_NAMES = {
    "대상", "동원", "한국", "우리", "미래", "삼성", "현대", "한화", "효성",
    "태영", "동양", "대한", "신라", "부산", "경동", "일신", "central",
    "아이티", "지어소프트", "이엔", "에스엘", "케이씨", "디아이",
}

# 종목명이 '기사의 주체'임을 시사하는 패턴 (종목명 바로 뒤)
_SUBJECT_AFTER = re.compile(
    r"^\s*(?:,|은|는|이|가|의|에|을|를|와|과|㈜|\(|\d{6}|주가|실적|공시|목표|"
    r"[가-힣]{0,3}(?:증자|계약|배당|합병|분할|인수|취득|공급|수주|출시|발표|체결))"
)

# 부수 언급 신호 — 이게 붙어 있으면 그 종목은 기사의 주체가 아니다
_INCIDENTAL_BEFORE = re.compile(
    r"(?:대비|비교|경쟁사|피어|peer|밸류체인|공급망|협력사|납품처|고객사|"
    r"수혜주|관련주|테마|계열|그룹|모회사|자회사|지분|보유)\s*[,·]?\s*$"
)
_INCIDENTAL_AFTER = re.compile(
    r"^\s*(?:등|외|과|와)\s*(?:[가-힣A-Za-z0-9]+\s*)?(?:종목|주|기업|사)?[,·]"
    r"|^\s*(?:수혜|관련|테마|납품|협력|계열)"
)


def _surface_re(name: str) -> re.Pattern:
    """종목명 표기 변형 매칭. 한글 경계를 지켜 부분일치를 막는다."""
    esc = re.escape(name)
    return re.compile(rf"(?<![가-힣A-Za-z0-9]){esc}(?![가-힣A-Za-z0-9])")


def verify_attribution(name: str, title: str, facts: str = "") -> tuple[bool, str]:
    """(통과여부, 사유). 실패 시 호출부는 종목 태그를 떼고 테마글로 강등한다."""
    if not name:
        return False, "종목명 없음"

    rgx = _surface_re(name)
    m = rgx.search(title or "")

    if not m:
        # 제목에 없고 본문에만 있으면 주체로 보기 어렵다
        if facts and rgx.search(facts):
            return False, f"제목미등장({name})"
        return False, f"미등장({name})"

    # 모호한 이름은 주체 신호가 반드시 필요
    if name in AMBIGUOUS_NAMES:
        after = (title or "")[m.end():m.end() + 8]
        if not _SUBJECT_AFTER.match(after):
            return False, f"모호명단독({name})"

    return True, ""


def is_incidental(name: str, title: str, facts: str = "") -> bool:
    """부수 언급이면 True. fail-open — 확실할 때만 True 를 낸다."""
    text = f"{title}\n{facts}"
    rgx = _surface_re(name)

    found = False
    for m in rgx.finditer(text):
        found = True
        before = text[max(0, m.start() - 16):m.start()]
        after = text[m.end():m.end() + 16]
        if _INCIDENTAL_BEFORE.search(before) or _INCIDENTAL_AFTER.match(after):
            continue
        return False        # 부수 신호가 아닌 등장이 하나라도 있으면 주체
    return found            # 전부 부수 신호일 때만 True
