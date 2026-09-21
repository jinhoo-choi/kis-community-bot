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


API_CANDIDATES = [
    # 리서치에서 front-api 가 살아 있는 것을 확인했다. 수급도 있을 수 있다.
    "https://m.stock.naver.com/api/stock/{code}/trend",
    "https://m.stock.naver.com/api/stock/{code}/investor",
    "https://m.stock.naver.com/api/stock/{code}/foreignInstitution",
    "https://api.stock.naver.com/stock/{code}/trend",
    "https://api.stock.naver.com/stock/{code}/investor",
    "https://m.stock.naver.com/front-api/stock/trend?stockCode={code}",
    "https://m.stock.naver.com/front-api/external/chart/domestic/investor"
    "?stockCode={code}",
]

POLICY_FEED_CANDIDATES = [
    ("연합 전체", "https://www.yna.co.kr/rss/all.xml"),
    ("연합 뉴스", "https://www.yna.co.kr/rss/news.xml"),
    ("연합 시장", "https://www.yna.co.kr/rss/market.xml"),
    ("연합 정치", "https://www.yna.co.kr/rss/politics.xml"),
    ("한국은행", "https://www.bok.or.kr/portal/bbs/B0000338/rss.do?menuNo=200761"),
    ("금융감독원", "https://www.fss.or.kr/fss/bbs/B0000188/rss.do"),
    ("KDI", "https://www.kdi.re.kr/rss/rss_news.jsp"),
    ("구글뉴스 korea.kr",
     "https://news.google.com/rss/search?q=site:korea.kr&hl=ko&gl=KR&ceid=KR:ko"),
    ("구글뉴스 금융위",
     "https://news.google.com/rss/search?q=%EA%B8%88%EC%9C%B5%EC%9C%84%EC%9B%90"
     "%ED%9A%8C+%EB%B0%9C%ED%91%9C&hl=ko&gl=KR&ceid=KR:ko"),
]


def probe_stock_api(codes=("005930", "042660")) -> None:
    """종목별 수급을 주는 API 후보 탐색.

    실측 #127: item/frgn.naver 는 table.type2 가 0개이고 세 종목 응답이
    138,559bytes 로 동일하다. 순위 페이지는 HTTP 410(Gone).
    소스가 사라진 것이라 파서 수정으로는 못 고친다. 대체를 찾거나 걷어낸다.
    """
    log("\n── A. 종목별 수급 API 후보 ──")
    for tmpl in API_CANDIDATES:
        for code in codes[:1]:
            url = tmpl.format(code=code)
            try:
                r = requests.get(url, headers={
                    **H, "Accept": "application/json",
                    "Referer": "https://m.stock.naver.com/"}, timeout=12)
                head = (r.text or "")[:180].replace("\n", " ")
                log(f"  {r.status_code} {url}")
                if r.status_code == 200:
                    try:
                        d = r.json()
                        keys = list(d)[:12] if isinstance(d, dict) else f"list[{len(d)}]"
                        log(f"      keys: {keys}")
                        log(f"      head: {head[:150]}")
                    except Exception:
                        log(f"      (JSON 아님) {head[:120]}")
            except Exception as e:
                log(f"  ERR {url} — {e}")


def probe_policy_feeds() -> None:
    """정책 RSS 대체 후보. korea.kr 계열은 해외 IP 에서 0건이다(실측 #127)."""
    log("\n── B. 정책 RSS 후보 ──")
    for name, url in POLICY_FEED_CANDIDATES:
        try:
            r = requests.get(url, headers=H, timeout=15)
            n = 0
            titles = []
            if r.status_code == 200:
                soup = BeautifulSoup(r.content, "xml")
                items = soup.find_all("item")
                n = len(items)
                titles = [(i.title.get_text(strip=True) if i.title else "")[:44]
                          for i in items[:3]]
            log(f"  {r.status_code} {n:>3}건  {name}")
            for t in titles:
                log(f"        · {t}")
        except Exception as e:
            log(f"  ERR {name} — {e}")


def probe_frgn_detail(codes=("005930", "000660", "042660")) -> None:
    """종목별 외국인·기관 페이지(item/frgn.naver) 구조 덤프.

    실측 #127: 200종목 전건 파싱 실패(수급 파싱 0/200). _add_flow 는
    table.type2 의 tr 에서 td 9개 이상을 찾아 td[1]=종가, td[5]=기관,
    td[6]=외국인으로 읽는데, 한 컬럼이라도 어긋나면 전건 조용히 실패한다.
    추측하지 않고 실제 표를 덤프한다.
    """
    log("\n── 0. 종목별 외국인·기관 상세 (item/frgn.naver) ──")
    for code in codes:
        url = f"https://finance.naver.com/item/frgn.naver?code={code}"
        try:
            r = requests.get(url, headers=H, timeout=15)
            r.encoding = "euc-kr"
            log(f"  [{code}] HTTP {r.status_code} / {len(r.content):,}bytes")
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            tables = soup.select("table.type2")
            log(f"    table.type2 {len(tables)}개")
            for ti, tb in enumerate(tables):
                rows = tb.select("tr")
                wide = [tr for tr in rows if len(tr.find_all("td")) >= 9]
                log(f"    [{ti}] tr {len(rows)}개 / td9+ {len(wide)}개")
                heads = [th.get_text(strip=True)
                         for th in tb.select("th")][:12]
                if heads:
                    log(f"        th: {heads}")
                for tr in wide[:2]:
                    tds = [td.get_text(strip=True)
                           for td in tr.find_all("td")]
                    log(f"        td({len(tds)}): {tds}")
        except Exception as e:
            log(f"  [{code}] 실패: {e}")
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
    probe_stock_api()
    probe_policy_feeds()
    probe_frgn_detail()
    for label, url in CANDIDATES:
        probe(label, url)
    with open("data/flow_probe.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    print("\n[probe] data/flow_probe.txt 저장")


if __name__ == "__main__":
    main()
