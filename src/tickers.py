"""종목 매핑. 원칙: LLM이 만든 종목코드는 절대 신뢰하지 않는다.
반드시 KRX 실제 상장 리스트와 대조해서 존재하는 것만 통과시킨다.
"""
import functools
import re

from src import entity


@functools.lru_cache(maxsize=1)
def listed() -> dict[str, str]:
    """{종목명: 종목코드}"""
    try:
        from pykrx import stock
        return {
            stock.get_market_ticker_name(t): t
            for t in stock.get_market_ticker_list(market="ALL")
        }
    except Exception as e:
        print(f"[tickers] 상장리스트 로드 실패: {e}")
        return {}


def resolve(item: dict) -> dict:
    """stock_code 가 없으면 제목에서 최장일치 종목명을 찾아 채운다.
    실패하면 kind='theme' 로 강등해 종목방이 아닌 자유게시판으로 보낸다.
    """
    if item.get("stock_code"):
        return item

    table = listed()
    title = item.get("title", "")
    facts = item.get("facts", "")

    # 긴 이름부터 검사하되, 매칭됐다고 바로 확정하지 않는다.
    # 인사이트봇 2026-08-02 오탐(그룹명 단독 언급이 계열사로 승격)과 같은 함정이라
    # 반드시 귀속검증을 통과해야 종목 태그를 붙인다.
    for name in sorted(table, key=len, reverse=True):
        if len(name) < 2:
            continue
        if not re.search(rf"(?<![가-힣A-Za-z]){re.escape(name)}(?![가-힣A-Za-z])", title):
            continue

        ok, why = entity.verify_attribution(name, title, facts)
        if not ok:
            item.setdefault("attr_reject", []).append(why)
            continue
        if entity.is_incidental(name, title, facts):
            item.setdefault("attr_reject", []).append(f"부수언급({name})")
            continue

        item["stock_code"] = table[name]
        item["stock_name"] = name
        return item

    # 귀속검증 실패 → 종목방이 아닌 자유게시판으로 강등
    item["kind"] = "theme" if item["kind"] == "research" else item["kind"]
    item["board"] = "free"
    return item


def board_of(item: dict) -> str:
    return "stock" if item.get("stock_code") else "free"
