"""전일 시세 → 특징주 카드.

pykrx 를 쓰지 않는다. 2026년 기준 pykrx 는 KRX_ID/KRX_PW 계정을 요구하며
GitHub Actions 에서 `KRX 로그인 실패` 로 전건 실패했다 (dry-run 실측).

대체: 네이버 금융 거래대금 상위 페이지 (sise_quant).
진단 결과 HTTP 200 / 83건 파싱 확인. 종가·등락률·거래대금이 한 페이지에 다 있다.
단 ETF·ETN·인버스가 상위를 점유하므로 종목명 필터가 필수다.
여기서 나온 수치는 실측치이므로 프롬프트 facts 에 그대로 넣어도 환각 위험이 없다.
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

# ETF/ETN/스팩/리츠/우선주 제외 — 커뮤니티 종목글 대상이 아니다
_EXCLUDE = re.compile(
    r"KODEX|TIGER|KBSTAR|ARIRANG|HANARO|KOSEF|SOL |ACE |PLUS |RISE |WON |"
    r"인버스|레버리지|선물|스팩|리츠$|우$|우[ABC]$|\d+호$"
)


def _num(s: str) -> float | None:
    s = re.sub(r"[^\d.\-+]", "", s or "")
    try:
        return float(s)
    except ValueError:
        return None


def _last_trading_day() -> str:
    d = datetime.now(KST) - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def fetch(limit: int = 12) -> list[dict]:
    day = _last_trading_day()
    rows = []

    for market, url in URLS:
        soup = crawl.get_soup(url, encoding="euc-kr")
        if soup is None:
            continue
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
            rate = _num(tds[4].get_text())
            amount = _num(tds[7].get_text())      # 거래대금(백만원)
            if None in (close, rate, amount):
                continue

            rows.append({
                "code": m.group(1), "name": name, "market": market,
                "close": close, "rate": rate, "amount": amount,
            })
        crawl.sleep_jitter()

    if not rows:
        crawl.report("market", 0, limit, "네이버 시세 페이지 로드/파싱 실패")
        return []

    # 거래대금이 충분한 종목 중 등락률 절댓값이 큰 순
    rows = [r for r in rows if r["amount"] >= 30_000]        # 300억원 이상
    rows.sort(key=lambda r: abs(r["rate"]), reverse=True)

    out = []
    for r in rows[:limit]:
        direction = "상승" if r["rate"] > 0 else "하락"
        out.append({
            "id": f"flow-{day.replace('-','')}-{r['code']}",
            "kind": "flow",
            "stock_code": r["code"],
            "stock_name": r["name"],
            "title": f"{r['name']} 전일 {r['rate']:.2f}% {direction}",
            "facts": (
                f"기준일: {day}\n"
                f"종목: {r['name']} ({r['code']}, {r['market']})\n"
                f"종가: {int(r['close']):,}원\n"
                f"등락률: {r['rate']:.2f}%\n"
                f"거래대금: {r['amount']/100:,.0f}억원\n"
                f"※ 등락 사유는 데이터에 없음. 원인을 추측해 단정하지 말 것."
            ),
            "src": f"https://finance.naver.com/item/main.naver?code={r['code']}",
        })

    crawl.report("market", len(out), limit, "거래대금 기준 미달 또는 파싱 실패")
    return out
