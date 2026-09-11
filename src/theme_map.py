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
# 키워드 일괄 차단은 과했다 (외부 검토 지적).
# 상장사가 직접 공장을 착공·준공했다면 그 종목방에서 중요한 소재다.
# 차단 기준은 키워드가 아니라 '종목과 사건의 직접 연결성'이어야 한다.
#
#   DIRECT_COMPANY : 상장사 본인이 당사자 → 본문에 종목명 허용
#   SECTOR_PROXY   : 산업·정책 일반 → 섹터 대표주 방에 게시, 본문 종목 언급 금지
#   NO_BOARD       : 어느 종목방에도 맞지 않음 → 생성 안 함
#
# 지역 행사·박람회는 '주최가 지자체이고 상장사는 참가자'인 경우가 대부분이라
# proxy 로도 부적합하다. 다만 상장사가 당사자로 확인되면 DIRECT 로 살린다.
PROXY_UNFIT_RE = re.compile(
    r"채용|취업|일자리|박람회|전시회|축제|공모전|설명회|간담회|위촉|봉사|캠페인|표창|수상"
)


def classify(item: dict, table: dict) -> tuple[str, str]:
    """(mapping_type, 종목명). 종목과 사건의 직접 연결성으로 판정한다."""
    title = item.get("title", "")

    # 상장사 본인이 당사자로 제목에 등장하면 DIRECT
    for name in sorted(table, key=len, reverse=True):
        if len(name) < 2:
            continue
        if re.search(rf"(?<![가-힣A-Za-z0-9]){re.escape(name)}(?![가-힣A-Za-z0-9])", title):
            return "DIRECT_COMPANY", name

    if PROXY_UNFIT_RE.search(title):
        return "NO_BOARD", ""
    return "SECTOR_PROXY", ""


# 특정 회사가 주어인 기사를 다른 회사의 종목방에 붙이면 안 된다.
# 실측: '신한은행, 퇴직연금형 개인 투자용 국채 판매' 기사가
# '금융|은행' 키워드로 KB금융 종목방에 배정됐다. 경쟁사 뉴스다.
_COMPANY_IN_TITLE = re.compile(
    r"[가-힣A-Za-z]{2,10}(은행|증권|화재|생명|카드|캐피탈|자산운용|손보|"
    r"전자|중공업|건설|제약|바이오|텔레콤|화학|에너지)")


def _other_company_subject(item: dict, assigned: str) -> str:
    """제목의 주어가 배정 종목과 다른 회사면 그 이름을 돌려준다."""
    title = item.get("title", "")
    m = _COMPANY_IN_TITLE.search(title)
    if not m:
        return ""
    found = m.group(0)
    # 회사명 뒤에 따옴표가 오면 기사 주어가 아니라 인용 출처다.
    # 실측: 한투증권 "하반기 환율 전망치 1,380원" -> 환율 기사이지 증권사 기사가 아니다.
    # 이걸 경쟁사로 보고 배정을 취소해 종목이 안 붙었다.
    tail = title[m.end():m.end() + 4].lstrip()
    if tail and tail[0] in "\"'\u201c\u2018":
        return ""
    # 배정 종목과 같은 계열이면 문제없다 (신한은행 <-> 신한지주)
    stem = re.sub(r"(지주|금융|홀딩스)$", "", assigned)
    return "" if (stem and stem in found) or found in assigned else found


# 비상장 자회사가 주어인 기사가 많다. 상장 지주사로 올려 붙인다.
# 실측: '신한은행', '우리은행' 기사가 배정 취소돼 종목방에 못 갔다.
_LISTED_FORMS = ("{stem}지주", "{stem}금융지주", "{stem}금융", "{stem}홀딩스",
                 "{stem}", "{stem}증권", "{stem}화재", "{stem}생명")


def _subject_ticker(subject: str, table: dict) -> str:
    """기사 주어 회사에 대응하는 상장 종목명. 없으면 빈 문자열."""
    if subject in table:
        return subject
    stem = re.sub(r"(은행|증권|화재|생명|카드|캐피탈|자산운용|손보)$", "", subject)
    if not stem:
        return ""
    for form in _LISTED_FORMS:
        cand = form.format(stem=stem)
        if cand in table:
            return cand
    # 표기가 조금 달라도 stem 으로 시작하는 금융 지주를 찾는다
    # (하나은행 -> 하나금융지주)
    for nm in table:
        if nm.startswith(stem) and re.search(r"(지주|금융|홀딩스)$", nm):
            return nm
    return ""


def assign(item: dict) -> bool:
    """테마·정책 항목에 게시할 종목을 배정한다. 배정했으면 True."""
    from src import tickers

    if item.get("stock_code"):
        return False
    table = tickers.listed()
    if not table:
        return False

    kind, name = classify(item, table)
    item["board_mapping"] = kind
    if kind == "NO_BOARD":
        item["no_stock_fit"] = True
        return False
    if kind == "DIRECT_COMPANY":
        # 회사가 당사자이므로 본문에 종목명을 써도 된다
        item["stock_code"] = table[name]
        item["stock_name"] = name
        item["board"] = "stock"
        return True
    text = f"{item.get('title','')} {item.get('facts','')[:400]}"
    cands = [n for n in _pick_names(text) if n in table]
    if not cands:
        cands = [n for n in FALLBACK if n in table]
    if not cands:
        return False

    name = random.choice(cands)
    other = _other_company_subject(item, name)
    if other:
        # 경쟁사 종목방에 붙이면 안 되지만, 배정을 포기하면 종목방에 못 간다.
        # 기사 주어의 상장 종목(또는 지주사)을 찾아 그쪽에 붙인다.
        mapped = _subject_ticker(other, table)
        if mapped:
            print(f"[theme] 주어 '{other}' → 상장 '{mapped}' 로 배정 변경")
            name = mapped
        else:
            # 상장사를 못 찾으면 그 회사와 무관한 다른 후보로 돌린다
            alt = [c for c in cands
                   if c != name and not _other_company_subject(item, c)]
            if alt:
                name = random.choice(alt)
            else:
                item["no_stock_fit"] = True
                item["board_mapping"] = "NO_BOARD"
                print(f"[theme] 배정 취소 — 주어 '{other}' 상장 매칭 실패: "
                      f"{item.get('title', '')[:40]}")
                return False
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
