"""종목 매핑. 원칙: LLM이 만든 종목코드는 절대 신뢰하지 않는다.
반드시 KRX 실제 상장 리스트와 대조해서 존재하는 것만 통과시킨다.
"""
import functools
import re


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
    # 짧은 이름의 오탐(예: '한국')을 막기 위해 긴 이름부터 검사
    for name in sorted(table, key=len, reverse=True):
        if len(name) < 2:
            continue
        if re.search(rf"(?<![가-힣A-Za-z]){re.escape(name)}(?![가-힣A-Za-z])", title):
            item["stock_code"] = table[name]
            item["stock_name"] = name
            return item

    item["kind"] = "theme" if item["kind"] == "research" else item["kind"]
    item["board"] = "free"
    return item


def board_of(item: dict) -> str:
    return "stock" if item.get("stock_code") else "free"
