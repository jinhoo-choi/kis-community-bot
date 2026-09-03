"""전일 특징주 카드.

pykrx 는 쓰지 않는다. 2026년 기준 KRX_ID/KRX_PW 계정을 요구해
GitHub Actions 에서 `KRX 로그인 실패` 로 전건 실패했다 (dry-run 실측).

대체: 네이버 금융 거래대금 상위(sise_quant). 종가·등락률·거래대금이
한 페이지에 모두 있어 종목별 개별 호출이 필요 없다.
진단에서 HTTP 200 / 83건 파싱 확인.

주의: 상위권을 ETF·인버스가 점유하므로 반드시 걸러낸다.
      (진단 실측: 1~3위가 KODEX 200선물인버스2X, KODEX 인버스, TIGER 200선물인버스2X)
"""
import re
from datetime import datetime, timedelta

from config import KST
from src import crawl

URLS = [
    ("KOSPI",  "https://finance.naver.com/sise/sise_quant.naver?sosok=0"),
    ("KOSDAQ", "https://finance.naver.com/sise/sise_quant.naver?sosok=1"),
]

ROW_SELECTORS = ["table.type_2 tr", "table.type_2 tbody tr", "div.box_type_l table tr"]

# ETF/ETN/스팩/리츠 제외 — 커뮤니티 종목글 대상이 아니다
_EXCLUDE = re.compile(
    r"KODEX|TIGER|KBSTAR|ARIRANG|HANARO|KOSEF|SOL |ACE |PLUS |RISE |TIMEFOLIO|"
    r"파워|스팩|리츠$|제\d+호|인버스|레버리지"
)

MIN_TURNOVER_EOK = 150      # 실측 결과 300억 기준에서 6건만 통과해 완화
MIN_ABS_CHANGE = 1.5        # 실측 결과 2.0% 기준에서 물량 부족


def _last_trading_day() -> str:
    d = datetime.now(KST) - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _num(s: str) -> float:
    try:
        return float(re.sub(r"[^\d.\-]", "", s or "") or 0)
    except ValueError:
        return 0.0


def fetch(limit: int = 12) -> list[dict]:
    day = _last_trading_day()
    rows, ok = [], 0

    for market, url in URLS:
        soup = crawl.get_soup(url, encoding="euc-kr")
        if soup is None:
            continue
        ok += 1

        for tr in crawl.select_rows(soup, ROW_SELECTORS):
            tds = tr.find_all("td")
            if len(tds) < 10:
                continue
            a = tds[1].find("a")
            if not a:
                continue
            m = re.search(r"code=(\d{6})", a.get("href", ""))
            if not m:
                continue

            name = a.get_text(strip=True)
            if _EXCLUDE.search(name):
                continue

            close = _num(tds[2].get_text())
            change_pct = _num(tds[4].get_text(strip=True).replace("%", ""))
            if "하락" in tds[3].get_text() or tds[3].find("span", class_="tah p11 nv01"):
                change_pct = -abs(change_pct)
            turnover = _num(tds[7].get_text())      # 백만원 단위
            eok = turnover / 100

            if eok < MIN_TURNOVER_EOK or abs(change_pct) < MIN_ABS_CHANGE:
                continue

            rows.append({
                "code": m.group(1), "name": name, "market": market,
                "close": close, "pct": change_pct, "eok": eok,
            })
        crawl.sleep_jitter()

    if ok == 0:
        crawl.report("market", 0, limit, "네이버 시세 페이지 로드 실패")
        return []

    rows.sort(key=lambda r: abs(r["pct"]), reverse=True)

    out = []
    for r in rows[:limit]:
        direction = "상승" if r["pct"] > 0 else "하락"
        out.append({
            "id": f"flow-{day}-{r['code']}",
            "kind": "flow",
            "stock_code": r["code"],
            "stock_name": r["name"],
            "title": f"{r['name']} 전일 {abs(r['pct']):.2f}% {direction}",
            "facts": (
                f"기준일: {day}\n"
                f"종목: {r['name']} ({r['code']}, {r['market']})\n"
                f"종가: {int(r['close']):,}원\n"
                f"등락률: {r['pct']:.2f}%\n"
                f"거래대금: {r['eok']:,.0f}억원\n"
                f"※ 등락 사유는 데이터에 없음. 원인을 추측해 단정하지 말 것."
            ),
            "src": f"https://finance.naver.com/item/main.naver?code={r['code']}",
        })

    # 조건(거래대금·등락률)을 만족하는 종목이 없는 날은 정상적인 0건이다
    crawl.report("market", len(out), limit if rows else 0, "시세 파싱 실패")
    return out
