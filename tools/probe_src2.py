"""네이버 리서치 / KIND 조회공시 장애 원인 확인.

둘 다 매 실행마다 0건으로 경고가 뜬다.
  [crawl] ⚠ naver_research 수집 0건 — 셀렉터 개편 의심
  [crawl] ⚠ kind_inquiry 수집 0건 — KIND 제목 구조 변경 의심

네이버 금융이 개편되면서 표 기반 페이지가 사라진 전례가 있으므로
같은 원인인지, 아니면 다른 경로가 있는지 응답으로 확인한다.
"""
import re
import sys

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, ".")

OUT = []
H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}


def log(s: str = "") -> None:
    print(s)
    OUT.append(s)


def probe_research() -> None:
    log("\n══ 1. 네이버 리서치 ══")
    for url in ("https://finance.naver.com/research/company_list.naver",
                "https://m.stock.naver.com/investment/research/company"):
        try:
            r = requests.get(url, headers=H, timeout=20)
        except Exception as e:
            log(f"  [ERR] {type(e).__name__} {url}")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        log(f"\n  {url}")
        log(f"  HTTP {r.status_code} / {len(r.content):,}bytes "
            f"/ table={len(soup.find_all('table'))} th={len(soup.find_all('th'))}")
        # 기존 파서가 쓰던 셀렉터
        log(f"  table.type_1 tr = {len(soup.select('table.type_1 tr'))}")
        links = soup.select("a[href*='company_read']")
        log(f"  company_read 링크 = {len(links)}")
        for tbl in soup.find_all("table")[:3]:
            th = [t.get_text(strip=True) for t in tbl.find_all("th")]
            th = [x for x in th if x]
            if th:
                log(f"  표 헤더: {th[:10]}")
                for tr in tbl.find_all("tr")[:3]:
                    c = [x.get_text(" ", strip=True) for x in tr.find_all("td")]
                    if len(c) >= 3:
                        log(f"    행: {c[:8]}")
                        break
        if not soup.find_all("table"):
            txt = soup.get_text(" ", strip=True)[:200]
            log(f"  본문 앞부분: {txt}")


def probe_research_api() -> None:
    """개편 페이지의 리서치 API 를 번들에서 찾는다.

    토론방 본문을 찾을 때 통한 방법이다. base 는 front-api 로 확인됐다.
    """
    log("\n══ 1b. 리서치 API 탐색 ══")
    page = "https://m.stock.naver.com/investment/research/company"
    r = requests.get(page, headers=H, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")

    nd = soup.find("script", id="__NEXT_DATA__")
    bid = ""
    if nd and nd.string:
        import json as _j
        try:
            j = _j.loads(nd.string)
            bid = j.get("buildId", "")
            log(f"  buildId={bid} page={j.get('page')}")
        except Exception:
            pass

    srcs = [x["src"] for x in soup.find_all("script", src=True)
            if "_next/static" in x["src"]]
    log(f"  JS 청크 {len(srcs)}개")
    paths = set()
    for js in srcs[:30]:
        ju = js if js.startswith("http") else "https://m.stock.naver.com" + js
        try:
            t = requests.get(ju, headers=H, timeout=20).text
        except Exception:
            continue
        for m in re.finditer(r"url:\s*[\"'`]([^\"'`]{4,90})[\"'`]", t):
            pth = m.group(1)
            if re.search(r"research|report|analysis|consensus", pth, re.I):
                paths.add(pth)
        for m in re.finditer(r"[\"'`](/research[a-zA-Z0-9/_\-{}$.]*)[\"'`]", t):
            paths.add(m.group(1))
    for pth in sorted(paths)[:30]:
        log(f"    path: {pth}")

    # stockList/list 가 400 이다 = 경로는 맞고 파라미터가 빠졌다.
    log("  --- 파라미터 조합 ---")
    base0 = "https://m.stock.naver.com/front-api"
    hdr0 = {**H, "Accept": "application/json", "Referer": page}
    combos = [
        ("/research/stockList", {"page": 1, "pageSize": 20}),
        ("/research/stockList", {"page": 1, "size": 20}),
        ("/research/stockList", {"pageNo": 1, "pageSize": 20}),
        ("/research/stockList", {"category": "company", "page": 1, "pageSize": 20}),
        ("/research/list", {"category": "company", "page": 1, "pageSize": 20}),
        ("/research/list", {"researchType": "company", "page": 1, "pageSize": 20}),
        ("/research/list", {"type": "stock", "page": 1, "pageSize": 20}),
        ("/research/stockEnd", {"researchId": 95624}),
        ("/research/end", {"researchId": 95624}),
    ]
    for c, prm in combos:
        try:
            rr = requests.get(base0 + c, params=prm, headers=hdr0, timeout=12)
            log(f"    [{rr.status_code}] {c} {prm}")
            if rr.status_code == 200:
                log(f"      → {rr.text[:420]}")
        except Exception as e:
            log(f"    [ERR] {type(e).__name__} {c}")

    # 목록에서 researchId 를 얻어 상세에서 목표가/투자의견을 찾는다.
    # 게이트가 '제시 적정가격|투자의견' 을 요구하므로 이게 없으면 제목뿐이라
    # 통과율이 낮다. 기존 네이버 경로의 가치가 여기 있었다.
    log("  --- 상세(목표가) 탐색 ---")
    base1 = "https://m.stock.naver.com/front-api"
    hdr1 = {**H, "Accept": "application/json", "Referer": page}
    try:
        lst = requests.get(base1 + "/research/list",
                           params={"category": "company", "page": 1, "pageSize": 5},
                           headers=hdr1, timeout=12).json()["result"]
    except Exception as e:
        log(f"    목록 실패 {e}")
        lst = []
    if lst:
        it = lst[0]
        rid = it["researchId"]
        log(f"    대상 researchId={rid} {it['itemName']} / {it.get('endUrl')}")
        for c, prm in [("/research/end", {"researchId": rid, "category": "company"}),
                       ("/research/end", {"id": rid, "category": "company"}),
                       ("/research/company/" + str(rid), {}),
                       ("/research/detail", {"researchId": rid}),
                       ("/research/stockEnd", {"researchId": rid,
                                               "itemCode": it.get("itemCode")})]:
            try:
                rr = requests.get(base1 + c, params=prm, headers=hdr1, timeout=12)
                log(f"    [{rr.status_code}] {c} {prm}")
                if rr.status_code == 200:
                    log(f"      → {rr.text[:500]}")
            except Exception as e:
                log(f"    [ERR] {type(e).__name__} {c}")
        # endUrl 페이지 자체도 본다
        eu = it.get("endUrl")
        if eu:
            try:
                rr = requests.get(eu, headers=H, timeout=15)
                sp = BeautifulSoup(rr.text, "html.parser")
                txt = sp.get_text(" ", strip=True)
                log(f"    endUrl HTTP {rr.status_code} / 텍스트 {len(txt)}자")
                for kw in ("목표가", "목표주가", "적정주가", "투자의견"):
                    i = txt.find(kw)
                    if i >= 0:
                        log(f"      '{kw}' 주변: {txt[max(0, i - 30):i + 70]}")
            except Exception as e:
                log(f"    [ERR endUrl] {type(e).__name__}")

    log("  --- front-api 타격 ---")
    base = "https://m.stock.naver.com/front-api"
    hdr = {**H, "Accept": "application/json", "Referer": page}
    cands = sorted(p2 for p2 in paths if p2.startswith("/"))[:12]
    cands += ["/research/company?page=1&pageSize=20",
              "/research/companyList?page=1&pageSize=20",
              "/investment/research/company?page=1&pageSize=20"]
    for c in cands:
        try:
            rr = requests.get(base + c, headers=hdr, timeout=12)
            ct = rr.headers.get("content-type", "")[:22]
            log(f"    [{rr.status_code}] {ct:22} {c}")
            if rr.status_code == 200 and "json" in ct:
                log(f"      → {rr.text[:300]}")
        except Exception as e:
            log(f"    [ERR] {type(e).__name__} {c}")


def probe_kind() -> None:
    log("\n══ 2. KIND 조회공시 ══")
    url = "https://kind.krx.co.kr/disclosure/todaydisclosure.do"
    from src.sources import kind_inquiry as K
    log(f"  payload keys = {list(K.PAYLOAD)}")
    s = requests.Session()
    s.headers.update({**H, "Referer": "https://kind.krx.co.kr/"})
    try:
        s.get("https://kind.krx.co.kr/disclosure/todaydisclosure.do", timeout=20)
        r = s.post(url, data=K.PAYLOAD, timeout=25)
        log(f"  HTTP {r.status_code} / {len(r.content):,}bytes "
            f"/ ct={r.headers.get('content-type', '')[:40]}")
        soup = BeautifulSoup(r.text, "html.parser")
        log(f"  table={len(soup.find_all('table'))} tr={len(soup.find_all('tr'))}")
        th = [t.get_text(strip=True) for t in soup.find_all("th")]
        log(f"  헤더: {[x for x in th if x][:12]}")
        shown = 0
        for tr in soup.find_all("tr"):
            c = [x.get_text(" ", strip=True) for x in tr.find_all("td")]
            if len(c) >= 3:
                log(f"    행: {c[:8]}")
                shown += 1
                if shown >= 3:
                    break
        if not soup.find_all("tr"):
            log(f"  본문 앞부분: {soup.get_text(' ', strip=True)[:300]}")
        # 조회공시 키워드가 응답에 있는지
        for kw in ("조회공시", "答辯", "답변", "현저한"):
            if kw in r.text:
                log(f"  키워드 '{kw}' 발견")
    except Exception as e:
        log(f"  [ERR] {type(e).__name__} {e}")


def main() -> None:
    probe_research()
    probe_research_api()
    probe_kind()
    with open("data/src2_probe.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    print("\n[probe] data/src2_probe.txt 저장")


if __name__ == "__main__":
    main()
