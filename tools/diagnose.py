"""크롤링 진단. Actions 에서 실행해 결과를 레포에 커밋한다.

dry-run 에서 드러난 문제를 실제 HTML/응답으로 확인하기 위한 도구.
  1. 한경컨센서스 테이블 구조 (제목-종목 어긋남 원인)
  2. pykrx 대체 후보 (KRX 계정 없이 전일 시세 확보)
  3. 정책 소스 대체 후보 (korea.kr 해외 IP 타임아웃)
"""
import json
import re
import sys
import traceback

import requests
from bs4 import BeautifulSoup

H = {"User-Agent": "Mozilla/5.0 (compatible; kis-community-bot/1.0)",
     "Accept-Language": "ko-KR,ko;q=0.9"}
OUT = []


def log(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    OUT.append(line)


def sec(t):
    log("\n" + "=" * 66)
    log(t)
    log("=" * 66)


# ────────────────────────────────────────────────
def diag_hankyung():
    sec("1. 한경컨센서스 테이블 구조")
    url = "https://consensus.hankyung.com/analysis/list?skinType=business"
    try:
        r = requests.get(url, headers=H, timeout=20)
        log(f"HTTP {r.status_code} / {len(r.text)} bytes")
        soup = BeautifulSoup(r.text, "html.parser")

        for ti, tb in enumerate(soup.select("table")):
            heads = [th.get_text(strip=True) for th in tb.select("thead th, tr th")]
            rows = tb.select("tbody tr") or tb.select("tr")
            log(f"\n[table {ti}] class={tb.get('class')} rows={len(rows)}")
            log(f"  헤더: {heads}")
            for ri, tr in enumerate(rows[:3]):
                tds = tr.find_all("td")
                if not tds:
                    continue
                log(f"  ── row {ri} (td {len(tds)}개)")
                for ci, td in enumerate(tds):
                    a = td.find("a")
                    log(f"     td[{ci}] text={td.get_text(strip=True)[:50]!r}"
                        + (f" href={a.get('href')[:60]!r}" if a and a.get("href") else ""))
    except Exception:
        log("실패:\n" + traceback.format_exc()[-600:])


# ────────────────────────────────────────────────
def diag_market():
    sec("2. 전일 시세 소스 후보 (pykrx 대체)")

    # 후보 A: 네이버 금융 시세 API (sise.naver.com)
    try:
        u = ("https://api.finance.naver.com/siseJson.naver"
             "?symbol=005930&requestType=1&startTime=20260825&endTime=20260903&timeframe=day")
        r = requests.get(u, headers=H, timeout=15)
        log(f"[A] naver siseJson  HTTP {r.status_code}  {r.text[:220]}")
    except Exception as e:
        log(f"[A] naver siseJson 실패: {e}")

    # 후보 B: 네이버 금융 거래대금 상위 페이지
    try:
        u = "https://finance.naver.com/sise/sise_quant.naver?sosok=0"
        r = requests.get(u, headers=H, timeout=15)
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("table.type_2 tr")
        got = 0
        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) < 10:
                continue
            a = tds[1].find("a")
            if not a:
                continue
            m = re.search(r"code=(\d{6})", a.get("href", ""))
            if m and got < 3:
                log(f"[B] {a.get_text(strip=True)} {m.group(1)} "
                    f"종가={tds[2].get_text(strip=True)} 등락={tds[4].get_text(strip=True)} "
                    f"거래대금={tds[7].get_text(strip=True)}")
            if m:
                got += 1
        log(f"[B] naver sise_quant  HTTP {r.status_code}  파싱 {got}건")
    except Exception as e:
        log(f"[B] naver sise_quant 실패: {e}")

    # 후보 C: 상장종목 리스트 (pykrx 대체)
    for name, u in [
        ("KRX 상장법인목록",
         "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"),
        ("네이버 종목 시세 KOSPI",
         "https://finance.naver.com/sise/sise_market_sum.naver?sosok=0&page=1"),
    ]:
        try:
            r = requests.get(u, headers=H, timeout=20)
            log(f"[C] {name}  HTTP {r.status_code}  {len(r.content)} bytes")
        except Exception as e:
            log(f"[C] {name} 실패: {type(e).__name__}")


# ────────────────────────────────────────────────
def diag_policy():
    sec("3. 정책 소스 후보 (korea.kr 대체)")
    cands = [
        ("korea.kr RSS",        "https://www.korea.kr/rss/policy.xml"),
        ("기재부 보도자료",      "https://www.moef.go.kr/nw/nes/nesdta.do?menuNo=4020100"),
        ("금융위 보도자료",      "https://www.fsc.go.kr/no010101"),
        ("한국은행 보도자료",    "https://www.bok.or.kr/portal/bbs/P0000559/list.do?menuNo=200690"),
        ("산업부 보도자료",      "https://www.motie.go.kr/kor/article/ATCLc01234567/list"),
        ("금감원 보도자료",      "https://www.fss.or.kr/fss/bbs/B0000188/list.do?menuNo=200218"),
        ("연합뉴스 경제 RSS",    "https://www.yna.co.kr/rss/economy.xml"),
        ("한경 정책 RSS",        "https://rss.hankyung.com/feed/economy.xml"),
    ]
    for name, u in cands:
        try:
            r = requests.get(u, headers=H, timeout=12)
            body = r.text[:120].replace("\n", " ")
            log(f"  {name:20s} HTTP {r.status_code}  {len(r.content):>7} bytes  {body[:70]!r}")
        except Exception as e:
            log(f"  {name:20s} 실패 {type(e).__name__}")


if __name__ == "__main__":
    diag_hankyung()
    diag_market()
    diag_policy()
    with open("data/diagnose.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    print("\n→ data/diagnose.txt 기록 완료")
