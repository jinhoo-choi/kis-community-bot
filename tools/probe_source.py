"""시세 소스 복구 탐색 — 네이버 개편 대응.

네이버 금융이 'Npay 증권' 으로 개편되면서 표 기반 페이지가 사라졌다.
  sise_quant.naver  -> 121KB 응답인데 <th> 0개
  research/company_list -> 상세 링크 0건
기존 파서가 전부 무효다. 특징주가 물량의 8할이라 복구가 최우선이다.

탐색 순서
  1. 개편 페이지의 JS 번들에서 API 경로를 직접 추출 (토론방 본문 때 통한 방법)
  2. front-api 랭킹 엔드포인트 후보 타격
  3. KRX 전종목 시세 (전일자 날짜 지정 조회 가능 = 실행 시각 무관)

주소를 추측해 파서를 쓰지 않는다. 응답으로 확인한 것만 쓴다.
"""
import json
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, ".")

OUT = []
H = {"User-Agent": "Mozilla/5.0",
     "Referer": "https://finance.naver.com/"}


def log(s: str = "") -> None:
    print(s)
    OUT.append(s)


def probe_new_naver() -> None:
    log("\n══ 1. 개편 페이지 구조 ══")
    for url in ("https://m.stock.naver.com/domestic/capitalization/KOSPI",
                "https://finance.naver.com/sise/sise_quant.naver?sosok=0"):
        try:
            r = requests.get(url, headers=H, timeout=20)
        except Exception as e:
            log(f"  [ERR] {type(e).__name__} {url}")
            continue
        log(f"\n  {url}")
        log(f"  HTTP {r.status_code} / {len(r.content):,}bytes")
        soup = BeautifulSoup(r.text, "html.parser")
        log(f"  th={len(soup.find_all('th'))} table={len(soup.find_all('table'))}")

        nd = soup.find("script", id="__NEXT_DATA__")
        if nd and nd.string:
            try:
                j = json.loads(nd.string)
                log(f"  buildId={j.get('buildId')} page={j.get('page')}")
            except Exception:
                pass

        srcs = [x["src"] for x in soup.find_all("script", src=True)
                if "_next/static" in x["src"] or ".js" in x["src"]]
        log(f"  JS 청크 {len(srcs)}개")
        found = set()
        for js in srcs[:20]:
            ju = js if js.startswith("http") else \
                ("https://m.stock.naver.com" + js if js.startswith("/") else "")
            if not ju:
                continue
            try:
                t = requests.get(ju, headers=H, timeout=20).text
            except Exception:
                continue
            for m in re.finditer(r"url:\s*[\"'`]([^\"'`]{4,80})[\"'`]", t):
                p = m.group(1)
                if re.search(r"rank|sise|stock|item|quant|price|trade", p, re.I):
                    found.add(p)
        for f in sorted(found)[:40]:
            log(f"    url: {f}")


def probe_front_api() -> None:
    log("\n══ 2. front-api 랭킹 후보 ══")
    base = "https://m.stock.naver.com/front-api"
    hdr = {**H, "Accept": "application/json",
           "Referer": "https://m.stock.naver.com/"}
    cands = [
        "/stock/ranking/domestic/tradingValue?page=1&pageSize=50",
        "/stock/ranking/domestic/rise?page=1&pageSize=50",
        "/ranking/domestic/tradingValue?page=1&pageSize=50",
        "/marketIndex/ranking/tradingValue",
        "/stocks/ranking?type=tradingValue&market=KOSPI",
        "/v1/stock/ranking/tradingValue",
    ]
    for c in cands:
        try:
            r = requests.get(base + c, headers=hdr, timeout=15)
            ct = r.headers.get("content-type", "")[:24]
            log(f"  [{r.status_code}] {ct:24} {c}")
            if r.status_code == 200 and "json" in ct:
                log(f"    → {r.text[:400]}")
        except Exception as e:
            log(f"  [ERR] {type(e).__name__} {c}")
        time.sleep(0.3)


def probe_sisejson() -> None:
    """기존에 쓰던 일별시세 API 가 아직 살아있는지."""
    log("\n══ 3. siseJson 생존 확인 ══")
    u = ("https://api.finance.naver.com/siseJson.naver?symbol=005930"
         "&requestType=1&startTime=20260901&endTime=20260911&timeframe=day")
    try:
        r = requests.get(u, headers=H, timeout=15)
        log(f"  [{r.status_code}] {len(r.text)}자 → {r.text[:220]}")
    except Exception as e:
        log(f"  [ERR] {type(e).__name__}")


def probe_krx() -> None:
    """KRX 전종목 시세. 날짜 지정이라 실행 시각과 무관하다."""
    log("\n══ 4. KRX 전종목 시세 ══")
    otp_url = "http://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd"
    api_url = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
    hdr = {**H, "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/"}
    # 전종목 시세(MDCSTAT01501)
    params = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
        "mktId": "ALL", "trdDd": "20260910",
        "share": "1", "money": "1", "csvxls_isNo": "false",
    }
    try:
        r = requests.post(api_url, data=params, headers=hdr, timeout=20)
        ct = r.headers.get("content-type", "")[:24]
        log(f"  [{r.status_code}] {ct:24} getJsonData")
        log(f"    → {r.text[:400]}")
    except Exception as e:
        log(f"  [ERR] {type(e).__name__} {e}")
    try:
        r2 = requests.get(otp_url, params={**params, "name": "fileDown",
                                           "url": params["bld"]},
                          headers=hdr, timeout=20)
        log(f"  [OTP {r2.status_code}] {r2.text[:120]}")
    except Exception as e:
        log(f"  [ERR OTP] {type(e).__name__}")


def main() -> None:
    probe_new_naver()
    probe_front_api()
    probe_sisejson()
    probe_krx()
    with open("data/source_probe.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    print("\n[probe] data/source_probe.txt 저장")


if __name__ == "__main__":
    main()
