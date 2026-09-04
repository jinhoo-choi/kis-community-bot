"""KIND 조회공시 구조 프로브.

조회공시(현저한 시황변동 / 풍문·보도)는 거래소 공시라 DART 에 없고 KIND 에만 있다.
지금 파이프라인의 최대 약점 — 특징주 글이 "왜 올랐는지 모른다"로 끝나는 문제 —
를 메울 수 있는 유일한 공식 확정 정보다. 회사가 직접 답변하기 때문이다.

엔드포인트 구조를 모르므로 후보를 여러 개 두드려 보고 실제 응답을 덤프한다.
"""
import re
import traceback

import requests
from bs4 import BeautifulSoup

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
     "Accept-Language": "ko-KR,ko;q=0.9",
     "Referer": "https://kind.krx.co.kr/disclosure/inquirydisclosure.do"}

OUT = []


def log(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    OUT.append(line)


CANDIDATES = [
    ("A. 조회공시 메인(GET)",
     "GET", "https://kind.krx.co.kr/disclosure/inquirydisclosure.do",
     {"method": "searchInquiryDisclosureMain"}),

    ("B. 조회공시 서브(POST)",
     "POST", "https://kind.krx.co.kr/disclosure/inquirydisclosure.do",
     {"method": "searchInquiryDisclosureSub", "currentPageSize": "15",
      "pageIndex": "1", "forward": "inquirydisclosure_sub", "searchMode": "",
      "orderMode": "0", "orderStat": "D"}),

    ("C. 오늘의 공시(POST)",
     "POST", "https://kind.krx.co.kr/disclosure/todaydisclosure.do",
     {"method": "searchTodayDisclosureSub", "currentPageSize": "15",
      "pageIndex": "1", "orderMode": "0", "orderStat": "D",
      "forward": "todaydisclosure_sub", "marketType": ""}),

    ("D. 전체공시 검색(POST)",
     "POST", "https://kind.krx.co.kr/disclosure/searchtotalinfo.do",
     {"method": "searchTotalInfoSub", "currentPageSize": "15", "pageIndex": "1",
      "forward": "searchtotalinfo_sub", "searchCorpName": "", "reportNm": "조회공시"}),
]


def dump(soup, tag):
    rows = soup.select("table tr")
    log(f"  <tr> {len(rows)}개")
    heads = [th.get_text(strip=True) for th in soup.select("th")][:12]
    if heads:
        log(f"  헤더: {heads}")
    shown = 0
    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 2 or shown >= 3:
            continue
        cells = [re.sub(r"\s+", " ", td.get_text(" ", strip=True))[:45] for td in tds]
        log(f"  row{shown}: {cells}")
        a = tr.find("a", href=True)
        if a:
            log(f"        href: {a['href'][:90]}")
        shown += 1
    if not shown:
        log(f"  본문 앞부분: {soup.get_text(' ', strip=True)[:200]!r}")


def raw_dump(s):
    """작동이 확인된 엔드포인트에서 행 원본 HTML 을 떠서
    종목코드·공시링크 추출 경로를 찾는다."""
    log("\n" + "=" * 66)
    log("행 원본 HTML 덤프 + 조회공시 필터 테스트")
    log("=" * 66)
    try:
        r = s.post("https://kind.krx.co.kr/disclosure/todaydisclosure.do",
                   data={"method": "searchTodayDisclosureSub", "currentPageSize": "100",
                         "pageIndex": "1", "orderMode": "0", "orderStat": "D",
                         "forward": "todaydisclosure_sub", "marketType": ""},
                   timeout=25)
        soup = BeautifulSoup(r.text, "html.parser")
        rows = [tr for tr in soup.select("tr") if tr.find_all("td")]
        log(f"총 {len(rows)}행 (currentPageSize=100)")

        if rows:
            log("\n[행 원본 HTML]")
            log(str(rows[0])[:1200])

        log("\n[조회공시 관련 행]")
        hit = 0
        for tr in rows:
            txt = tr.get_text(" ", strip=True)
            if re.search(r"조회공시|시황변동|풍문", txt):
                tds = [re.sub(r"\s+", " ", td.get_text(" ", strip=True))[:50]
                       for td in tr.find_all("td")]
                log(f"  {tds}")
                log(f"    RAW: {str(tr)[:500]}")
                hit += 1
                if hit >= 3:
                    break
        log(f"  조회공시 행 {hit}건")

        codes = re.findall(r"(?:isurCd|codeNm|스크립트)?['\"]?(\d{6})['\"]", str(rows[:3]))
        log(f"\n6자리 코드 후보: {codes[:6]}")
        acpt = re.findall(r"acptno=?['\"]?(\d{10,})", str(rows[:3]))
        log(f"acptno 후보: {acpt[:4]}")
    except Exception:
        log("실패:\n" + traceback.format_exc()[-400:])


def main():
    log("=" * 66)
    log("KIND 조회공시 엔드포인트 프로브")
    log("=" * 66)
    s = requests.Session()
    s.headers.update(H)
    # 세션 쿠키 확보
    try:
        s.get("https://kind.krx.co.kr/disclosure/inquirydisclosure.do", timeout=20)
    except Exception:
        pass

    for name, method, url, data in CANDIDATES:
        log(f"\n[{name}]")
        log(f"  {method} {url}")
        try:
            r = (s.post(url, data=data, timeout=25) if method == "POST"
                 else s.get(url, params=data, timeout=25))
            log(f"  HTTP {r.status_code}  {len(r.content)} bytes  ct={r.headers.get('Content-Type','')[:40]}")
            if r.status_code == 200 and r.content:
                soup = BeautifulSoup(r.text, "html.parser")
                dump(soup, name)
                if "조회공시" in r.text:
                    log("  ✔ 본문에 '조회공시' 문자열 존재")
                for kw in ("시황변동", "풍문", "답변"):
                    if kw in r.text:
                        log(f"  ✔ '{kw}' 존재")
        except Exception:
            log("  실패:\n" + traceback.format_exc()[-400:])

    raw_dump(s)

    with open("data/kind_probe.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    log("\n→ data/kind_probe.txt 기록")


if __name__ == "__main__":
    main()
