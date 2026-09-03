"""증권사 리포트 수집.

- 네이버 금융 리서치(종목분석): URL 쿼리에 종목코드가 들어있어 매핑이 정확하다.
- 한경컨센서스: 전 증권사 집계. 종목코드가 없어 제목 기반 매핑 + 귀속검증이 필요하다.

저작권: 리포트 원문(PDF)은 저장/재배포하지 않는다.
목록의 제목·증권사만 사용하고 원문은 링크로만 연결한다.

크롤링 방어(부정여론봇 이식): 셀렉터 배열 폴백 + 재시도 + 랜덤 지연 + 헬스체크.
단일 셀렉터를 박아두면 사이트 개편 당일부터 조용히 0건이 된다.
"""
import re

from src import crawl

# 폴백 순서대로 시도. 위쪽이 현재 구조, 아래쪽은 구/대체 구조.
NAVER_ROW_SELECTORS = [
    "table.type_1 tr",
    "table.type_1 tbody tr",
    ".box_type_m table tr",
]
HK_ROW_SELECTORS = [
    "table.table_style01 tbody tr",
    "table tbody tr",
    ".table_wrap tbody tr",
]


def fetch_naver(limit: int = 12) -> list[dict]:
    url = "https://finance.naver.com/research/company_list.naver"
    out = []
    soup = crawl.get_soup(url, encoding="euc-kr")
    if soup is None:
        crawl.report("naver_research", 0, limit)
        return out

    for tr in crawl.select_rows(soup, NAVER_ROW_SELECTORS):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        a_stock, a_title = tds[0].find("a"), tds[1].find("a")
        if not (a_stock and a_title):
            continue

        m = re.search(r"code=(\d{6})", a_stock.get("href", ""))
        if not m:
            continue

        name = a_stock.get_text(strip=True)
        title = a_title.get_text(strip=True)
        out.append({
            "id": "naver-" + re.sub(r"\W", "", a_title.get("href", ""))[-24:],
            "kind": "research",
            "stock_code": m.group(1),
            "stock_name": name,
            "title": title,
            "facts": (
                f"종목: {name} ({m.group(1)})\n"
                f"리포트 제목: {title}\n"
                f"발간: {tds[2].get_text(strip=True)} / {tds[4].get_text(strip=True)}\n"
                f"※ 제목 외 본문 수치는 미제공. 목표주가·투자의견을 추정하지 말 것."
            ),
            "src": "https://finance.naver.com" + a_title.get("href", ""),
        })
        if len(out) >= limit:
            break

    crawl.report("naver_research", len(out), limit)
    crawl.sleep_jitter()
    return out


def fetch_hankyung(limit: int = 8) -> list[dict]:
    url = "https://consensus.hankyung.com/analysis/list?skinType=business"
    out = []
    soup = crawl.get_soup(url)
    if soup is None:
        crawl.report("hankyung", 0, limit)
        return out

    for tr in crawl.select_rows(soup, HK_ROW_SELECTORS):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        a = tr.find("a")
        title = a.get_text(strip=True) if a else ""
        if not title:
            continue

        out.append({
            "id": "hk-" + re.sub(r"\W", "", title)[:24],
            "kind": "research",
            "stock_code": None,          # entity.verify_attribution 으로 후처리
            "stock_name": None,
            "title": title,
            "facts": (
                f"리포트 제목: {title}\n"
                f"작성: {tds[-2].get_text(strip=True)} / {tds[-1].get_text(strip=True)}\n"
                f"※ 제목 외 본문 수치는 미제공."
            ),
            "src": "https://consensus.hankyung.com" + (a.get("href") or ""),
        })
        if len(out) >= limit:
            break

    crawl.report("hankyung", len(out), limit)
    crawl.sleep_jitter()
    return out


def fetch(limit: int = 16) -> list[dict]:
    n = int(limit * 0.7)
    return (fetch_naver(n) + fetch_hankyung(limit - n))[:limit]
