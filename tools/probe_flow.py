"""네이버 수급(외국인·기관 순매매) 페이지 구조 확인.

특징주가 심사에서 '데이터 나열에 그쳐 정보 가치가 낮음'으로 깎인다(실측 98건).
등락률·거래대금은 앱 시세 화면에 이미 있는 값이라 새 정보가 아니기 때문이다.
외국인·기관 순매수는 시세창에 없는 값이라 점수가 다를 수 있다.

URL 과 컬럼은 추측하지 않는다. 후보를 전부 때려보고 응답을 덤프한다.
과거 KIND 에서 td[2]를 td[1]로 잡아 2743종목 중 3개만 파싱된 적이 있다.
"""
import sys

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, ".")

OUT = []
H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}

CANDIDATES = [
    ("외인기관 순매매 상위 KOSPI",
     "https://finance.naver.com/sise/sise_deal_rank.naver?sosok=01"),
    ("외인기관 순매매 상위 KOSDAQ",
     "https://finance.naver.com/sise/sise_deal_rank.naver?sosok=02"),
    ("투자자별 매매동향",
     "https://finance.naver.com/sise/investorDealTrendDay.naver"),
    ("외국인 매매 상위",
     "https://finance.naver.com/sise/sise_deal_rank.naver"),
    # 위 프로브에서 확보한 실제 iframe 주소. investor_gubun 값은 미확인이라
    # 외국인(9000)과 그 옆 값을 같이 때려 기관 코드를 찾는다.
    ("외국인 순매수 KOSPI",
     "https://finance.naver.com/sise/sise_deal_rank_iframe.naver"
     "?sosok=01&investor_gubun=9000&type=buy"),
    ("외국인 순매도 KOSPI",
     "https://finance.naver.com/sise/sise_deal_rank_iframe.naver"
     "?sosok=01&investor_gubun=9000&type=sell"),
    ("기관 추정 1000 KOSPI",
     "https://finance.naver.com/sise/sise_deal_rank_iframe.naver"
     "?sosok=01&investor_gubun=1000&type=buy"),
    ("기관 추정 8000 KOSPI",
     "https://finance.naver.com/sise/sise_deal_rank_iframe.naver"
     "?sosok=01&investor_gubun=8000&type=buy"),
    ("외국인 순매수 KOSDAQ",
     "https://finance.naver.com/sise/sise_deal_rank_iframe.naver"
     "?sosok=02&investor_gubun=9000&type=buy"),
    ("시가총액 상위 KOSPI",
     "https://finance.naver.com/sise/sise_market_sum.naver?sosok=0"),
    ("거래상위 KOSPI(기준 확인용)",
     "https://finance.naver.com/sise/sise_quant.naver?sosok=0"),
]


def log(s: str = "") -> None:
    print(s)
    OUT.append(s)


def probe(label: str, url: str) -> None:
    log(f"\n── [{label}] ──")
    log(f"  {url}")
    try:
        r = requests.get(url, headers=H, timeout=20)
        r.encoding = "euc-kr"
        log(f"  HTTP {r.status_code} / {len(r.text):,}bytes")
        if r.status_code != 200:
            return
        soup = BeautifulSoup(r.text, "html.parser")

        # 200 인데 표가 안 잡히면 프레임 안에 있는 경우다. 실제로 sise_deal_rank
        # 가 그랬다. 프레임 주소를 먼저 찍어 다음 프로브 대상을 만든다.
        frames = [f.get("src") for f in soup.find_all(["frame", "iframe"])
                  if f.get("src")]
        if frames:
            log(f"  프레임: {frames[:6]}")
        if not soup.find_all("th"):
            log(f"  th 없음 — 본문 앞부분: {soup.get_text(' ', strip=True)[:200]}")

        # 표가 여러 개일 수 있다. 표별로 헤더와 첫 행을 각각 본다.
        for ti, table in enumerate(soup.find_all("table")[:4]):
            th = [t.get_text(strip=True) for t in table.find_all("th")]
            th = [x for x in th if x]
            if not th:
                continue
            log(f"  표{ti} 헤더({len(th)}): {th[:14]}")
            shown = 0
            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                cells = [c.get_text(strip=True) for c in tds]
                cells = [c for c in cells if c]
                if len(cells) < 4:
                    continue
                log(f"    행: {cells[:12]}")
                shown += 1
                if shown >= 2:
                    break
    except Exception as ex:
        log(f"  실패 {type(ex).__name__} {ex}")


def main() -> None:
    for label, url in CANDIDATES:
        probe(label, url)
    with open("data/flow_probe.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    print("\n[probe] data/flow_probe.txt 저장")


if __name__ == "__main__":
    main()
