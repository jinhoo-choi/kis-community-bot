"""증권사 리포트 수집.

- 네이버 금융 리서치(종목분석): URL 쿼리에 종목코드가 들어있어 매핑이 정확하다.
- 한경컨센서스: 전 증권사 집계. 종목코드가 없어 제목 기반 매핑이 필요하다.

저작권: 리포트 원문(PDF)은 저장/재배포하지 않는다.
목록의 제목·증권사·요약 스니펫만 사용하고 원문은 링크로만 연결한다.
"""
import re
import time
import requests
from bs4 import BeautifulSoup

from config import REQUEST_DELAY, USER_AGENT

H = {"User-Agent": USER_AGENT}


def fetch_naver(limit: int = 12) -> list[dict]:
    """네이버 금융 > 리서치 > 종목분석 리포트."""
    url = "https://finance.naver.com/research/company_list.naver"
    out = []
    try:
        r = requests.get(url, headers=H, timeout=15)
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")

        for tr in soup.select("table.type_1 tr"):
            tds = tr.find_all("td")
            if len(tds) < 5:
                continue
            a_stock = tds[0].find("a")
            a_title = tds[1].find("a")
            if not (a_stock and a_title):
                continue

            m = re.search(r"code=(\d{6})", a_stock.get("href", ""))
            if not m:
                continue

            out.append({
                "id": "naver-" + re.sub(r"\W", "", a_title.get("href", ""))[-24:],
                "kind": "research",
                "stock_code": m.group(1),
                "stock_name": a_stock.get_text(strip=True),
                "title": a_title.get_text(strip=True),
                "facts": (
                    f"종목: {a_stock.get_text(strip=True)} ({m.group(1)})\n"
                    f"리포트 제목: {a_title.get_text(strip=True)}\n"
                    f"발간: {tds[2].get_text(strip=True)} / {tds[4].get_text(strip=True)}\n"
                    f"※ 제목 외 본문 수치는 미제공. 목표주가·투자의견을 추정하지 말 것."
                ),
                "src": "https://finance.naver.com" + a_title.get("href", ""),
            })
            if len(out) >= limit:
                break
    except Exception as e:
        print(f"[naver] 실패: {e}")

    time.sleep(REQUEST_DELAY)
    print(f"[naver] {len(out)}건 수집")
    return out


def fetch_hankyung(limit: int = 8) -> list[dict]:
    """한경컨센서스 기업 리포트 목록. 종목코드는 tickers.py 에서 후처리 매핑."""
    url = "https://consensus.hankyung.com/analysis/list?skinType=business"
    out = []
    try:
        r = requests.get(url, headers=H, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        for tr in soup.select("table tbody tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            a = tr.find("a")
            if not a:
                continue
            title = a.get_text(strip=True)
            if not title:
                continue

            out.append({
                "id": "hk-" + re.sub(r"\W", "", title)[:24],
                "kind": "research",
                "stock_code": None,          # 후처리 매핑 대상
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
    except Exception as e:
        print(f"[hankyung] 실패: {e}")

    time.sleep(REQUEST_DELAY)
    print(f"[hankyung] {len(out)}건 수집")
    return out


def fetch(limit: int = 16) -> list[dict]:
    items = fetch_naver(int(limit * 0.7)) + fetch_hankyung(limit - int(limit * 0.7))
    return items[:limit]
