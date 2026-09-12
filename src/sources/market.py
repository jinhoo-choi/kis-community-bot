"""전일 특징주 카드.

pykrx 는 쓰지 않는다. 2026년 기준 KRX_ID/KRX_PW 계정을 요구해
GitHub Actions 에서 `KRX 로그인 실패` 로 전건 실패했다 (dry-run 실측).

대체: 네이버 금융 거래대금 상위(sise_quant). 종가·등락률·거래대금이
한 페이지에 모두 있어 종목별 개별 호출이 필요 없다.
진단에서 HTTP 200 / 83건 파싱 확인.

주의: 상위권을 ETF·인버스가 점유하므로 반드시 걸러낸다.
      (진단 실측: 1~3위가 KODEX 200선물인버스2X, KODEX 인버스, TIGER 200선물인버스2X)
"""
import concurrent.futures as cf
import json
import os
import re
from datetime import datetime, timedelta

import config
from config import KST
from src import crawl, facts, tickers

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
    r"파워|스팩|리츠$|제\d+호|인버스|레버리지|ETN|ETF|"
    # 우선주: 서울식품우가 상승률 1위(+29.86%)로 올라온다. 종목방 대상이 아니다.
    # (?<!대) 는 '미래에셋대우' 류 보통주 오제외 방지.
    r"(?<!대)우[A-C]?$"
)

MIN_TURNOVER_EOK = 150      # 실측 결과 300억 기준에서 6건만 통과해 완화
MIN_ABS_CHANGE = 1.5        # 실측 결과 2.0% 기준에서 물량 부족

# 큰 등락은 거래대금이 작아도 커뮤니티가 이야기한다. 다만 유동성이 너무 얕으면
# 글감으로도 위험하므로 하한은 둔다.
BIG_MOVE_PCT = 5.0
BIG_MOVE_MIN_EOK = 30


def _last_trading_day() -> str:
    """순위 페이지가 지금 보여주는 데이터의 기준일.

    무조건 '어제' 로 잡으면 장 마감 후 수집 시 하루가 밀린다.
    실측(#80): 09-08 16:06 에 수집한 09-08 종가 데이터를 '9월 7일' 로 적었다.
    마감(15:30) 이후에는 당일이 기준일이다.
    """
    now = datetime.now(KST)
    d = now if (now.weekday() < 5 and _after_close(now)) else now - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


# 순위 페이지는 장 시작 전에 비어 있다(실측 #81). 06시 실행을 살리려면
# 마감 후에 받아둔 결과를 재사용해야 한다. 기준일이 같을 때만 쓴다.
_CACHE = "data/market_cache.json"


def _after_close(now=None) -> bool:
    now = now or datetime.now(KST)
    return now.hour * 60 + now.minute >= config.MARKET_CLOSE_MIN


def _cache_save(day: str, items: list[dict]) -> None:
    if len(items) < 10:
        return
    # 장중에 저장하면 오늘 장중 데이터가 '어제 확정치' 로 둔갑한다.
    # 실측 확인: 09:09 에 _last_trading_day() 는 09-08 을 주는데
    # 페이지는 09-09 장중 데이터를 준다.
    if not (datetime.now(KST).weekday() < 5 and _after_close()):
        print("[market] 장 마감 전이라 캐시를 저장하지 않는다 "
              "(장중 데이터가 전일 확정치로 둔갑한다)")
        return
    try:
        os.makedirs("data", exist_ok=True)
        with open(_CACHE, "w", encoding="utf-8") as f:
            json.dump({"day": day, "items": items}, f, ensure_ascii=False)
        print(f"[market] 캐시 저장 {len(items)}건 (기준일 {day})")
    except Exception as e:
        print(f"[market] 캐시 저장 실패: {e}")


def _cache_load(day: str) -> list[dict]:
    try:
        with open(_CACHE, encoding="utf-8") as f:
            c = json.load(f)
    except Exception:
        return []
    if c.get("day") != day:
        print(f"[market] 캐시 기준일 불일치 (캐시 {c.get('day')} vs 필요 {day})")
        return []
    return c.get("items") or []


def _num(s: str) -> float:
    try:
        return float(re.sub(r"[^\d.\-]", "", s or "") or 0)
    except ValueError:
        return 0.0


SISE_JSON = ("https://api.finance.naver.com/siseJson.naver"
             "?symbol={code}&requestType=1&startTime={s}&endTime={e}&timeframe=day")


FRGN_URL = "https://finance.naver.com/item/frgn.naver?code={code}"


# 수급 상위 페이지. 프로브(data/flow_probe.txt)로 확인한 실제 iframe 주소다.
# 목록 페이지는 프레임 껍데기라 th 가 하나도 없다.
#   헤더 ['종목명', '수량', '금액', '당일거래량']
#   행   ['우리금융지주', '13,052', '431,651', '18,186,793']
# 금액 단위는 확정되지 않았다(천원/백만원 모두 자기정합적). 그래서 순위만 쓴다.
# 순위는 시세 화면에 없는 사실이라 그 자체로 정보가 된다.
_RANK_URL = ("https://finance.naver.com/sise/sise_deal_rank_iframe.naver"
             "?sosok={sosok}&investor_gubun=9000&type={type}")


def flow_ranks() -> dict:
    """종목명 -> 수급 순위 문구. 종목코드가 없어 이름으로 맞춘다."""
    out = {}
    for sosok in ("01", "02"):
        for typ, label in (("buy", "순매수"), ("sell", "순매도")):
            soup = crawl.get_soup(_RANK_URL.format(sosok=sosok, type=typ),
                                  encoding="euc-kr")
            if soup is None:
                continue
            # 한 페이지에 표가 2쌍 들어있다(외국인/기관). 첫 표만 쓴다.
            table = soup.find("table")
            if table is None:
                continue
            rank = 0
            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) < 4:
                    continue
                name = tds[0].get_text(strip=True)
                if not name:
                    continue
                rank += 1
                if rank <= 10:
                    out.setdefault(name, f"외국인 {label} 상위 {rank}위")
            crawl.sleep_jitter(0.3, 0.7)
    return out


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
        # 장중에 부르면 rows[-1] 이 '오늘 진행 중' 데이터다. 그러면 순위 페이지의
        # 실시간 값과 함께 미확정 수치가 '전일 확정치' 로 나간다.
        # 기준일과 일치하는 행을 찾아 쓴다. 없으면 마지막 확정일을 쓴다.
        want = _last_trading_day().replace("-", "")
        idx = next((i for i, x in enumerate(rows) if str(x[0]) == want), None)
        if idx is None:
            # 오늘 행이 섞여 있으면 그 앞 행이 직전 거래일이다
            idx = len(rows) - (2 if str(rows[-1][0]) > want else 1)
        if idx < 5:
            return
        rows = rows[:idx + 1]
        last = rows[-1]
        # 전일 확정치로 목록 페이지의 실시간 값을 덮어쓴다
        r["day_used"] = str(last[0])
        if last[4]:
            r["close"] = float(last[4])
        prev = int(rows[-2][4]) if len(rows) >= 2 and rows[-2][4] else 0
        if prev:
            r["pct"] = (int(last[4]) - prev) / prev * 100
        if last[5] and last[4]:
            r["eok"] = int(last[5]) * int(last[4]) / 1e8
            r["eok_approx"] = True
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
    # 실패하면 무엇을 봤는지 남긴다. 별도 프로브를 돌려 구조를 알아내는 것보다
    # 실행 로그가 바로 알려주는 편이 빠르다 (프로브가 세 번 밀렸다).
    seen = [[th.get_text(strip=True) for th in t.find_all("th")]
            for t in soup.find_all("table")]
    print(f"[market] 발견된 헤더: {[h for h in seen if h][:3]}")
    return {}


def _daily_rows(code: str, name: str, day: str) -> dict | None:
    """siseJson 으로 한 종목의 기준일 확정 시세를 만든다.

    네이버가 'Npay 증권' 으로 개편되면서 표 기반 순위 페이지가 사라졌다
    (121KB 응답에 th 0개). front-api 랭킹 엔드포인트는 후보 6종 전부 404다.
    siseJson 은 살아있고 날짜 지정이 되므로 랭킹을 직접 만든다.
    실행 시각과 무관하고 장중에도 전일 확정치를 준다.
    """
    end = datetime.now(KST)
    beg = end - timedelta(days=12)
    try:
        url = SISE_JSON.format(code=code, s=beg.strftime("%Y%m%d"),
                               e=end.strftime("%Y%m%d"))
        txt = crawl.requests.get(url, headers=crawl.HEADERS, timeout=10).text
        rows = json.loads(txt.replace("'", '"'))[1:]
    except Exception:
        return None
    want = day.replace("-", "")
    idx = next((i for i, r in enumerate(rows) if str(r[0]) == want), None)
    if idx is None or idx < 1:
        return None
    cur, prev = rows[idx], rows[idx - 1]
    if not (cur[4] and prev[4] and cur[5]):
        return None
    close, pclose, vol = int(cur[4]), int(prev[4]), int(cur[5])
    return {
        "code": code, "name": name, "market": "",
        "close": float(close),
        "pct": (close - pclose) / pclose * 100,
        "eok": vol * close / 1e8,
        "eok_approx": True,          # 거래량x종가. siseJson 에 금액이 없다
        "day_used": want,
    }


def _rank_from_daily(day: str, limit: int) -> list[dict]:
    """상장 전종목의 기준일 시세를 모아 랭킹을 만든다."""
    table = tickers.listed()
    if len(table) < 500:
        print(f"[market] 상장사 목록 {len(table)}종목 — 랭킹 생성 스킵")
        return []
    items = list(table.items())
    print(f"[market] siseJson 랭킹 생성 — {len(items)}종목 조회")
    out = []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for r in ex.map(lambda kv: _daily_rows(kv[1], kv[0], day), items):
            if r:
                out.append(r)
    print(f"[market] 확정 시세 {len(out)}종목 수집 (기준일 {day})")
    # 거래대금 상위 + 등락률 상위/하위를 합쳐 후보를 만든다
    by_eok = sorted(out, key=lambda r: -r["eok"])[:limit]
    by_up = sorted(out, key=lambda r: -r["pct"])[:limit]
    by_down = sorted(out, key=lambda r: r["pct"])[:limit]
    seen, merged = set(), []
    for r in by_eok + by_up + by_down:
        if r["code"] not in seen:
            seen.add(r["code"])
            merged.append(r)
    return merged


def fetch(limit: int = 12) -> list[dict]:
    day = _last_trading_day()
    rows, ok = [], 0

    for market, url in URLS:
        soup = crawl.get_soup(url, encoding="euc-kr")
        if soup is None:
            continue
        ok += 1

        col = _col_map(soup)
        # 상승/하락률 페이지에는 거래대금 컬럼이 없다 (실측: 거래량만 있음).
        # 현재가 x 거래량으로 대체한다 — 당일 평균단가가 아니라 종가 기준이라
        # 정확한 거래대금은 아니지만, 유동성 하한 판정에는 충분하다.
        need = {"종목명", "현재가", "등락률"}
        if "거래대금" not in col and "거래량" not in col:
            need = need | {"거래대금"}
        if not need <= set(col):
            # 어떤 컬럼이 없는지 남긴다. '인식 실패'만으로는 원인을 못 좁힌다.
            print(f"[market] ⚠ 컬럼 부족 {sorted(need - set(col))} "
                  f"/ 발견 {sorted(col)} — {url}")
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
            if "거래대금" in col:
                eok = _num(cell("거래대금")) / 100          # 백만원 -> 억원
                approx = False
            else:
                # 종가 x 거래량. 당일 평균단가가 아니라 종가 기준이라 실제와 다르다.
                # 상한가 종목일수록 과대 계상된다. 판정용으로만 쓰고 게시글엔 넣지 않는다.
                vol = _num(cell("거래량"))
                eok = (close * vol / 1e8) if (close and vol) else None
                approx = True

            if close is None or change_pct is None or eok is None:
                continue
            big = abs(change_pct) >= BIG_MOVE_PCT and eok >= BIG_MOVE_MIN_EOK
            usual = eok >= MIN_TURNOVER_EOK and abs(change_pct) >= MIN_ABS_CHANGE
            if not (big or usual):
                continue

            rows.append({
                "code": m.group(1), "name": name, "market": market,
                "close": close, "pct": change_pct, "eok": eok,
                "eok_approx": approx,
            })
            n_page += 1
        if n_page == 0:
            print(f"[market] ⚠ 0건 파싱 — {url}")
        crawl.sleep_jitter()

    # 순위 페이지가 개편으로 죽으면(th 0개) 전종목 일별시세로 랭킹을 직접 만든다.
    # 아이템 생성 루프 앞에 둬야 뒤 단계가 그대로 처리한다.
    if not rows:
        rows = _rank_from_daily(day, max(limit, 60))
        if rows:
            ok = 1

    if ok == 0 or not rows:
        crawl.report("market", 0, limit, "네이버 시세 페이지 로드 실패")
        return []

    dedup = {}
    for r in rows:                       # 거래대금 상위와 등락률 상위에 같은 종목이 겹친다
        old = dedup.get(r["code"])       # 정확한 거래대금을 근사치로 덮어쓰지 않는다
        if old is None or (old.get("eok_approx") and not r.get("eok_approx")):
            dedup[r["code"]] = r
    rows = list(dedup.values())
    # 등락 크기만으로 고르면 저유동 소형주가 앞을 채운다. 거래대금을 함께 본다.
    rows.sort(key=lambda r: (abs(r["pct"]) * min(r["eok"], 1000)), reverse=True)
    print(f"[market] 후보 {len(rows)}종목 (중복 제거 후)")

    # 입력이 종가·등락률·거래대금 3개뿐이면 아무리 축을 늘려도
    # 표현법만 30가지지 콘텐츠는 3가지다 (외부 검토 지적).
    # 원인 추정 없이 안전하게 늘릴 수 있는 정형 지표를 붙인다.
    ranks = flow_ranks()
    if ranks:
        print(f"[market] 수급 상위 {len(ranks)}종목 확보")
    # 종목당 요청 2회를 직렬로 돌면서 0.4~0.9초씩 쉬고 있었다.
    # limit 이 250이면 보강만 250 x (2요청 + 0.65초) 로 8분 넘게 걸린다.
    # siseJson 랭킹(2,632종목)을 8워커로 이미 병렬 처리하고 있으므로
    # 같은 방식으로 맞춘다. 워커 수를 두면 서버 부담도 일정하다.
    def _enrich_one(r: dict) -> None:
        _add_history(r)
        _add_flow(r)
        if r["name"] in ranks:
            r["flow_rank"] = ranks[r["name"]]

    targets = rows[:limit]
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(_enrich_one, targets))

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
                + f"종목: {r['name']} ({r['code']}"
                + (f", {r['market']})\n" if r.get("market") else ")\n")
                + f"종가: {int(r['close']):,}원\n"
                f"등락률: {r['pct']:.2f}%\n"
                + ("" if r.get("eok_approx")
                   else f"거래대금: {r['eok']:,.0f}억원\n")
                + (f"{r['flow_rank']}\n" if r.get("flow_rank") else "")
                + "".join(f"{lbl}\n" for lbl in facts.evaluate(r))
                + "※ '평가' 항목은 코드가 계산한 관찰 결과다. 그대로 인용하되 원인으로 해석하지 말 것.\n"
                + "※ 등락 사유는 데이터에 없음. 원인을 추측해 단정하지 말 것."
            ),
            "src": f"https://finance.naver.com/item/main.naver?code={r['code']}",
        })

    # 조건(거래대금·등락률)을 만족하는 종목이 없는 날은 정상적인 0건이다.
    # 다만 rows 자체가 비면 정상이 아니다. 실측(#81, 08:30 KST): 순위 페이지가
    # 장 시작 전에는 헤더만 있고 데이터 행이 없다. 이걸 '정상 0건'으로 넘기는
    # 바람에 특징주가 통째로 빠진 채 발송 3건으로 끝났고 경보도 안 떴다.
    if not rows:
        cached = _cache_load(day)
        if cached:
            print(f"[market] 순위 페이지가 비어 캐시 {len(cached)}건 사용 "
                  f"(기준일 {day})")
            crawl.report("market", len(cached), limit, "")
            return cached[:limit]
        h = datetime.now(KST).hour
        why = ("장 시작 전이라 순위 페이지가 비어 있고 캐시도 없다 "
               "(장 마감 후 캐시 적재 필요)"
               if h < 9 else "시세 파싱 실패 — 페이지 구조 변경 의심")
        crawl.report("market", 0, limit, why)
    else:
        _cache_save(day, out)
        crawl.report("market", len(out), limit, "조건 충족 종목 없음")
    return out
