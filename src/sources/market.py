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

# 거래대금 상위만 보면 하루 8건이 한계다 (실측). 발송 목표를 맞추려면
# 유일하게 탄력적인 소스가 특징주다. 등락률 상·하위를 함께 본다.
# 물량보다 선별에서 이득이 크다 — 공급 8건에 슬롯 6건이면 고를 여지가 없다.
URLS = [
    ("KOSPI",  "https://finance.naver.com/sise/sise_quant.naver?sosok=0"),
    ("KOSDAQ", "https://finance.naver.com/sise/sise_quant.naver?sosok=1"),
    ("KOSPI",  "https://finance.naver.com/sise/sise_rise.naver?sosok=0"),
    ("KOSDAQ", "https://finance.naver.com/sise/sise_rise.naver?sosok=1"),
    ("KOSPI",  "https://finance.naver.com/sise/sise_fall.naver?sosok=0"),
    ("KOSDAQ", "https://finance.naver.com/sise/sise_fall.naver?sosok=1"),
]

ROW_SELECTORS = ["table.type_2 tr", "table.type_2 tbody tr", "div.box_type_l table tr"]

# ETF/ETN/스팩/리츠 제외 — 커뮤니티 종목글 대상이 아니다
_EXCLUDE = re.compile(
    r"KODEX|TIGER|KBSTAR|ARIRANG|HANARO|KOSEF|SOL |ACE |PLUS |RISE |TIMEFOLIO|"
    r"파워|스팩|리츠$|제\d+호|인버스|레버리지"
)

MIN_TURNOVER_EOK = 150      # 실측 결과 300억 기준에서 6건만 통과해 완화
MIN_ABS_CHANGE = 1.5        # 실측 결과 2.0% 기준에서 물량 부족

# 큰 등락은 거래대금이 작아도 커뮤니티가 이야기한다. 다만 유동성이 너무 얕으면
# 글감으로도 위험하므로 하한은 둔다.
BIG_MOVE_PCT = 5.0
BIG_MOVE_MIN_EOK = 30


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
        r["prev_close"] = int(rows[-2][4]) if len(rows) >= 2 and rows[-2][4] else None
        vols = [int(x[5]) for x in rows[-21:-1] if x[5]]
        if vols and int(last[5]):
            avg = sum(vols) / len(vols)
            if avg > 0:
                r["vol_x"] = int(last[5]) / avg
    except Exception:
        pass


def _col_map(soup) -> dict:
    """헤더 텍스트 -> 컬럼 인덱스.

    sise_quant 와 sise_rise/fall 은 컬럼이 다르다. 인덱스를 고정하면
    KIND 때처럼 조용히 틀린 값을 읽는다 (td[2]를 td[1]로 잡아
    2743종목 중 3개만 파싱됐다). 이름으로 잡으면 페이지가 달라도 안전하고,
    네이버가 컬럼을 바꿔도 0건으로 즉시 드러난다.
    """
    for tbl in soup.find_all("table"):
        heads = [th.get_text(strip=True) for th in tbl.find_all("th")]
        if "종목명" in heads and "등락률" in heads:
            return {h: i for i, h in enumerate(heads) if h}
    return {}


def fetch(limit: int = 12) -> list[dict]:
    day = _last_trading_day()
    rows, ok = [], 0

    for market, url in URLS:
        soup = crawl.get_soup(url, encoding="euc-kr")
        if soup is None:
            continue
        ok += 1

        col = _col_map(soup)
        if not {"종목명", "현재가", "등락률", "거래대금"} <= set(col):
            print(f"[market] ⚠ 컬럼 구조 인식 실패 — {url}")
            continue

        n_page = 0
        for tr in crawl.select_rows(soup, ROW_SELECTORS):
            tds = tr.find_all("td")
            if len(tds) <= max(col.values()):
                continue

            def cell(name: str) -> str:
                return tds[col[name]].get_text(strip=True)

            a = tds[col["종목명"]].find("a")
            if not a:
                continue
            m = re.search(r"code=(\d{6})", a.get("href", ""))
            if not m:
                continue

            name = a.get_text(strip=True)
            if _EXCLUDE.search(name):
                continue

            close = _num(cell("현재가"))
            raw_pct = cell("등락률")
            change_pct = _num(raw_pct.replace("%", ""))
            # 등락률 셀에 부호가 없는 페이지가 있어 전일비로 방향을 확인한다
            if raw_pct.startswith("-") or (
                    "전일비" in col and "하락" in tds[col["전일비"]].get_text()):
                change_pct = -abs(change_pct)
            eok = _num(cell("거래대금")) / 100      # 백만원 -> 억원

            if close is None or change_pct is None or eok is None:
                continue
            big = abs(change_pct) >= BIG_MOVE_PCT and eok >= BIG_MOVE_MIN_EOK
            usual = eok >= MIN_TURNOVER_EOK and abs(change_pct) >= MIN_ABS_CHANGE
            if not (big or usual):
                continue

            rows.append({
                "code": m.group(1), "name": name, "market": market,
                "close": close, "pct": change_pct, "eok": eok,
            })
            n_page += 1
        if n_page == 0:
            print(f"[market] ⚠ 0건 파싱 — {url}")
        crawl.sleep_jitter()

    if ok == 0:
        crawl.report("market", 0, limit, "네이버 시세 페이지 로드 실패")
        return []

    dedup = {}
    for r in rows:                       # 거래대금 상위와 등락률 상위에 같은 종목이 겹친다
        dedup[r["code"]] = r
    rows = list(dedup.values())
    # 등락 크기만으로 고르면 저유동 소형주가 앞을 채운다. 거래대금을 함께 본다.
    rows.sort(key=lambda r: (abs(r["pct"]) * min(r["eok"], 1000)), reverse=True)
    print(f"[market] 후보 {len(rows)}종목 (중복 제거 후)")

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
