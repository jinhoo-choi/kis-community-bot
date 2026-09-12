"""네이버 리서치 / KIND 조회공시 장애 원인 확인.

둘 다 매 실행마다 0건으로 경고가 뜬다.
  [crawl] ⚠ naver_research 수집 0건 — 셀렉터 개편 의심
  [crawl] ⚠ kind_inquiry 수집 0건 — KIND 제목 구조 변경 의심

네이버 금융이 개편되면서 표 기반 페이지가 사라진 전례가 있으므로
같은 원인인지, 아니면 다른 경로가 있는지 응답으로 확인한다.
"""
import re
import sys

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, ".")

OUT = []
H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}


def log(s: str = "") -> None:
    print(s)
    OUT.append(s)


def probe_research() -> None:
    log("\n══ 1. 네이버 리서치 ══")
    for url in ("https://finance.naver.com/research/company_list.naver",
                "https://m.stock.naver.com/investment/research/company"):
        try:
            r = requests.get(url, headers=H, timeout=20)
        except Exception as e:
            log(f"  [ERR] {type(e).__name__} {url}")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        log(f"\n  {url}")
        log(f"  HTTP {r.status_code} / {len(r.content):,}bytes "
            f"/ table={len(soup.find_all('table'))} th={len(soup.find_all('th'))}")
        # 기존 파서가 쓰던 셀렉터
        log(f"  table.type_1 tr = {len(soup.select('table.type_1 tr'))}")
        links = soup.select("a[href*='company_read']")
        log(f"  company_read 링크 = {len(links)}")
        for tbl in soup.find_all("table")[:3]:
            th = [t.get_text(strip=True) for t in tbl.find_all("th")]
            th = [x for x in th if x]
            if th:
                log(f"  표 헤더: {th[:10]}")
                for tr in tbl.find_all("tr")[:3]:
                    c = [x.get_text(" ", strip=True) for x in tr.find_all("td")]
                    if len(c) >= 3:
                        log(f"    행: {c[:8]}")
                        break
        if not soup.find_all("table"):
            txt = soup.get_text(" ", strip=True)[:200]
            log(f"  본문 앞부분: {txt}")


def probe_kind() -> None:
    log("\n══ 2. KIND 조회공시 ══")
    url = "https://kind.krx.co.kr/disclosure/todaydisclosure.do"
    from src.sources import kind_inquiry as K
    log(f"  payload keys = {list(K.PAYLOAD)}")
    s = requests.Session()
    s.headers.update({**H, "Referer": "https://kind.krx.co.kr/"})
    try:
        s.get("https://kind.krx.co.kr/disclosure/todaydisclosure.do", timeout=20)
        r = s.post(url, data=K.PAYLOAD, timeout=25)
        log(f"  HTTP {r.status_code} / {len(r.content):,}bytes "
            f"/ ct={r.headers.get('content-type', '')[:40]}")
        soup = BeautifulSoup(r.text, "html.parser")
        log(f"  table={len(soup.find_all('table'))} tr={len(soup.find_all('tr'))}")
        th = [t.get_text(strip=True) for t in soup.find_all("th")]
        log(f"  헤더: {[x for x in th if x][:12]}")
        shown = 0
        for tr in soup.find_all("tr"):
            c = [x.get_text(" ", strip=True) for x in tr.find_all("td")]
            if len(c) >= 3:
                log(f"    행: {c[:8]}")
                shown += 1
                if shown >= 3:
                    break
        if not soup.find_all("tr"):
            log(f"  본문 앞부분: {soup.get_text(' ', strip=True)[:300]}")
        # 조회공시 키워드가 응답에 있는지
        for kw in ("조회공시", "答辯", "답변", "현저한"):
            if kw in r.text:
                log(f"  키워드 '{kw}' 발견")
    except Exception as e:
        log(f"  [ERR] {type(e).__name__} {e}")


def main() -> None:
    probe_research()
    probe_kind()
    with open("data/src2_probe.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    print("\n[probe] data/src2_probe.txt 저장")


if __name__ == "__main__":
    main()
