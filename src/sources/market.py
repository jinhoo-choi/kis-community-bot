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
from src import crawl, facts

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


SISE_JSON = ("https://api.finance.naver.com/siseJson.naver"
             "?symbol={code}&requestType=1&startTime={s}&endTime={e}&timeframe=day")


FRGN_URL = "https://finance.naver.com/item/frgn.naver?code={code}"


def _add_flow(r: dict):
    """외국인·기관 순매매를 붙인다.

    '왜 올랐는지'를 추정하지 않고도 관찰 가능한 사실을 늘리는 안전한 방법이다.
    네이버 종목별 외국인·기관 페이지에서 최근 1일치만 읽는다.
    """
    soup = crawl.get_soup(FRGN_URL.format(code=r["code"]), encoding="euc-kr")
    if soup is None:
        return
    try:
        for tr in soup.select("table.type2 tr"):
            tds = tr.find_all("td")
            if len(tds) < 9:
                continue
            def _n(i):
                t = tds[i].get_text(strip=True).replace(",", "")
                return int(t) if re.fullmatch(r"[-+]?\d+", t) else None
            close, inst, frgn = _n(1), _n(5), _n(6)
            if close is None or (inst is None and frgn is None):
                continue
            # 순매매 '수량'이므로 종가를 곱해 금액으로 환산한다 (코드가 계산)
            if inst is not None:
                r["inst_net"] = inst * close
            if frgn is not None:
                r["frgn_net"] = frgn * close
            break
    except Exception:
        pass


def _add_history(r: dict):
    """네이버 siseJson 으로 20일 평균 거래대금·5일 수익률·장중 고저를 붙인다.
    원인 추정이 아니라 정형 수치라 안전하면서 콘텐츠 variation 을 크게 늘린다."""
    import json
    from datetime import datetime, timedelta
    try:
        end = datetime.now(KST)
        beg = end - timedelta(days=45)
        url = SISE_JSON.format(code=r["code"], s=beg.strftime("%Y%m%d"),
                               e=end.strftime("%Y%m%d"))
        txt = crawl.requests.get(url, headers=crawl.HEADERS, timeout=12).text
        rows = json.loads(txt.replace("'", '"'))[1:]      # [0] 은 헤더
        if len(rows) < 6:
            return
        # [날짜, 시가, 고가, 저가, 종가, 거래량, 외국인소진율]
        last = rows[-1]
        r["high"], r["low"] = int(last[2]), int(last[3])
        if last[1]:
            r["from_open"] = (int(last[4]) - int(last[1])) / int(last[1]) * 100
        if len(rows) >= 6 and int(rows[-6][4]):
            r["ret5"] = (int(last[4]) - int(rows[-6][4])) / int(rows[-6][4]) * 100
        # 거래량 기준 배수 (거래대금 대신 거래량으로 계산 — siseJson 에 금액이 없다)
        r["open"] = int(last[1]) if last[1] else None
        # 목록 페이지 종가를 덮어쓰지 않고 따로 둔다. 두 값이 어긋나면 파싱 오류다.
        r["close_hist"] = int(last[4]) if last[4] else None
        vols = [int(x[5]) for x in rows[-21:-1] if x[5]]
        if vols and int(last[5]):
            avg = sum(vols) / len(vols)
            if avg > 0:
                r["vol_x"] = int(last[5]) / avg
    except Exception:
        pass


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

    # 입력이 종가·등락률·거래대금 3개뿐이면 아무리 축을 늘려도
    # 표현법만 30가지지 콘텐츠는 3가지다 (외부 검토 지적).
    # 원인 추정 없이 안전하게 늘릴 수 있는 정형 지표를 붙인다.
    for r in rows[:limit]:
        _add_history(r)
        _add_flow(r)
        crawl.sleep_jitter(0.4, 0.9)

    out = []
    for r in rows[:limit]:
        # 불변식 위반은 게시 대상이 아니라 수집 버그다. 조용히 통과시키지 않는다.
        bad = facts.sanity_errors(r)
        if bad:
            print(f"[market] ⚠ {r['name']} 제외 — {', '.join(bad)}")
            continue
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
                + "".join(f"{lbl}\n" for lbl in facts.evaluate(r))
                + "※ '평가' 항목은 코드가 계산한 관찰 결과다. 그대로 인용하되 원인으로 해석하지 말 것.\n"
                + "※ 등락 사유는 데이터에 없음. 원인을 추측해 단정하지 말 것."
            ),
            "src": f"https://finance.naver.com/item/main.naver?code={r['code']}",
        })

    # 조건(거래대금·등락률)을 만족하는 종목이 없는 날은 정상적인 0건이다
    crawl.report("market", len(out), limit if rows else 0, "시세 파싱 실패")
    return out
