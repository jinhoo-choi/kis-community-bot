"""네이버 리서치 / 한경컨센서스 상세 페이지 구조 확인.

목록은 '제목 + 증권사'만 준다. 그래서 목표주가·투자의견을 Gemini 검색
그라운딩으로 알아내고 있는데, 1회 약 35원이 든다 (실청구 역산).
상세 페이지에 그 값이 그냥 있다면 그라운딩이 통째로 불필요해진다.

인덱스는 추측하지 않는다. 과거 KIND 에서 td[2]를 td[1]로 잡아
2743종목 중 3개만 파싱된 적이 있다. 원문을 덤프해 근거를 만든다.
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


def probe_naver_detail(n: int = 3) -> None:
    """목록에서 상세 링크를 따라가 본문에 수치가 있는지 본다."""
    log("\n── 1. 네이버 리서치 상세 ──")
    lst = "https://finance.naver.com/research/company_list.naver"
    r = requests.get(lst, headers=H, timeout=20)
    r.encoding = "euc-kr"
    soup = BeautifulSoup(r.text, "html.parser")
    log(f"  목록 HTTP {r.status_code}")

    links = []
    for tr in soup.select("table.type_1 tr"):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        a = tds[1].find("a")
        if a and a.get("href"):
            links.append((tds[0].get_text(strip=True),
                          a.get_text(strip=True), a["href"]))
    log(f"  목록에서 상세 링크 {len(links)}건 확보")

    for name, title, href in links[:n]:
        url = href if href.startswith("http") else \
            "https://finance.naver.com/research/" + href.lstrip("/")
        log(f"\n  [{name}] {title[:40]}")
        log(f"    URL {url}")
        try:
            d = requests.get(url, headers=H, timeout=20)
            d.encoding = "euc-kr"
            ds = BeautifulSoup(d.text, "html.parser")
            log(f"    HTTP {d.status_code} / {len(d.text):,}bytes")

            # 본문 후보 블록을 통째로 본다 (셀렉터를 미리 정하지 않는다)
            for sel in ["div.view_cnt", "td.view_cnt", "div.box_type_m",
                        "div.section", "table.type_1"]:
                el = ds.select_one(sel)
                if el:
                    txt = el.get_text(" ", strip=True)
                    log(f"    [{sel}] {len(txt)}자 → {txt[:220]}")

            body = ds.get_text(" ", strip=True)
            for label in ["목표주가", "적정주가", "적정가격", "투자의견",
                          "TP", "Buy", "매수", "현재가"]:
                for m in re.finditer(label, body):
                    seg = body[max(0, m.start() - 30):m.start() + 60]
                    log(f"    ※ '{label}' 주변: {seg}")
                    break

            pdf = [a["href"] for a in ds.find_all("a", href=True)
                   if ".pdf" in a["href"].lower()]
            log(f"    PDF 링크: {pdf[:2] if pdf else '없음'}")
        except Exception as ex:
            log(f"    실패 {type(ex).__name__} {ex}")


def probe_hk_detail(n: int = 2) -> None:
    log("\n── 2. 한경컨센서스 목록 컬럼 ──")
    url = ("https://consensus.hankyung.com/analysis/list"
           "?sdate=&edate=&report_type=CO")
    try:
        r = requests.get(url, headers={"User-Agent": H["User-Agent"]}, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        log(f"  HTTP {r.status_code} / {len(r.text):,}bytes")
        th = [t.get_text(strip=True) for t in soup.find_all("th")]
        log(f"  헤더: {[x for x in th if x][:12]}")
        cnt = 0
        for tr in soup.select("table tbody tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
            if not any(cells):
                continue
            log(f"  행: {cells[:10]}")
            cnt += 1
            if cnt >= n:
                break
    except Exception as ex:
        log(f"  실패 {type(ex).__name__} {ex}")


def main() -> None:
    probe_naver_detail()
    probe_hk_detail()
    with open("data/research_probe.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    print("\n[probe] data/research_probe.txt 저장")


if __name__ == "__main__":
    main()
