"""정책·테마 글의 게시 종목 배정.

커뮤니티에 종목방만 있어서 테마글도 어딘가에는 올라가야 한다.
다만 아무 종목방에나 올리면 두 가지 문제가 생긴다.
  1) 무관한 방에 올라가면 스팸으로 보인다
  2) '이 정책이 이 종목에 호재'라는 암시가 되어 투자권유로 오인될 수 있다

그래서 두 가지를 지킨다.
  - 키워드로 관련 섹터를 찾아 그 대표주에 배정한다 (무작위는 최후 수단)
  - **본문에서는 종목명을 언급하지 않는다.** 종목방은 게시 위치일 뿐이고
    글 내용은 산업·정책 사실에 머문다. facts 에 이 지시를 주입한다.
"""
import random
import re

# 섹터 키워드 → 대표주(시총 상위). 코드는 tickers.listed() 로 조회한다.
SECTORS = [
    (r"반도체|메모리|HBM|파운드리|소부장|웨이퍼", ["삼성전자", "SK하이닉스"]),
    (r"배터리|이차전지|전기차|양극재|음극재", ["LG에너지솔루션", "삼성SDI", "POSCO홀딩스"]),
    (r"자동차|모빌리티|완성차|부품", ["현대차", "기아"]),
    (r"바이오|제약|의약품|임상|헬스케어|의료기기", ["삼성바이오로직스", "셀트리온"]),
    (r"방산|항공|우주|무기|防", ["한화에어로스페이스", "현대로템"]),
    (r"조선|해운|선박|LNG운반", ["HD한국조선해양", "삼성중공업"]),
    (r"원전|전력|에너지|발전|송전", ["두산에너빌리티", "한국전력"]),
    (r"금융|은행|대출|서민|가계부채|금리", ["KB금융", "신한지주"]),
    (r"증권|자본시장|공모|상장", ["미래에셋증권", "삼성증권"]),
    (r"인터넷|플랫폼|포털|광고|커머스", ["NAVER", "카카오"]),
    (r"게임|콘텐츠|엔터|웹툰", ["크래프톤", "엔씨소프트"]),
    (r"철강|화학|소재|정유", ["POSCO홀딩스", "LG화학"]),
    (r"건설|부동산|주택|인프라|SOC", ["현대건설", "삼성물산"]),
    (r"통신|5G|네트워크", ["SK텔레콤", "KT"]),
    (r"식품|유통|소비|물가|장바구니|추석", ["CJ제일제당", "이마트"]),
    (r"AI|인공지능|데이터|클라우드", ["삼성전자", "NAVER"]),
]

# 섹터 매칭 실패 시 쓰는 대형주 풀
FALLBACK = ["삼성전자", "SK하이닉스", "현대차", "KB금융", "NAVER",
            "삼성바이오로직스", "LG에너지솔루션", "기아"]

NOTE = (
    "※ 이 글은 종목방에 게시되지만 특정 종목에 대한 글이 아니다.\n"
    "※ 본문에서 종목명이나 종목코드를 절대 언급하지 말 것. "
    "산업·정책 사실만 쓰고, 수혜주나 관련주를 지목하지 말 것."
)


def _pick_names(text: str) -> list[str]:
    for pat, names in SECTORS:
        if re.search(pat, text, re.I):
            return names
    return FALLBACK


# 종목방에 올리면 부자연스러운 유형. 지역 행사·채용·수상 같은 건
# 어떤 종목방에 넣어도 맞지 않는다 (실측: 원주 의료기기 채용행사 -> 셀트리온).
NOT_STOCK_RE = re.compile(
    r"채용|취업|일자리|박람회|전시회|축제|행사|협약식|간담회|표창|수상|"
    r"위촉|개소|착공|준공|봉사|캠페인|공모전|설명회"
)


def assign(item: dict) -> bool:
    """테마·정책 항목에 게시할 종목을 배정한다. 배정했으면 True."""
    from src import tickers

    if item.get("stock_code"):
        return False
    if NOT_STOCK_RE.search(item.get("title", "")):
        item["no_stock_fit"] = True
        return False
    table = tickers.listed()
    if not table:
        return False

    text = f"{item.get('title','')} {item.get('facts','')[:400]}"
    cands = [n for n in _pick_names(text) if n in table]
    if not cands:
        cands = [n for n in FALLBACK if n in table]
    if not cands:
        return False

    name = random.choice(cands)
    item["stock_code"] = table[name]
    item["stock_name"] = name
    item["board"] = "stock"
    item["theme_assigned"] = True
    # 본문에 종목을 쓰지 않도록 지시를 주입한다
    item["facts"] = item["facts"].rstrip() + "\n" + NOTE
    return True


def assign_all(items: list[dict]) -> int:
    n = sum(1 for it in items
            if it.get("kind") in ("policy", "theme", "poll") and assign(it))
    if n:
        print(f"[theme] 테마글 {n}건에 게시 종목 배정")
    return n
