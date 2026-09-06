"""DART 주요사항보고서 엔드포인트 프로브.

상세 보강이 3/16 에 그쳤다. 미보강 로그를 보면 원인이 둘이다.
  1) 엔드포인트를 등록하지 않은 유형 (타법인주식및출자증권취득결정 등)
  2) 정형 API 자체가 없는 유형 (단일판매ㆍ공급계약체결)

엔드포인트 이름과 필드 키를 기억으로 넣으면 조용히 0건이 되고, 원인 추적에
며칠 걸린다 (실측: bgn_de=end_de 고정 때문에 2/18 이 나온 걸 못 보고 있었다).
실제 응답을 받아 확인한다.

판정:
  - JSON 이고 status 필드가 있으면 → 엔드포인트 존재
      status 000 = 데이터 있음 / 013 = 없음(엔드포인트는 유효)
  - HTML 이거나 파싱 실패면 → 엔드포인트 없음
status 000 인 경우 첫 행의 키를 전부 덤프한다. 필드명도 추측하지 않는다.
"""
import json
import os
from datetime import datetime, timedelta

import requests

KEY = os.environ.get("DART_API_KEY", "")
BASE = "https://opendart.fss.or.kr/api/{}.json"
LIST = "https://opendart.fss.or.kr/api/list.json"
H = {"User-Agent": "Mozilla/5.0"}
OUT = []


def log(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    OUT.append(line)


# 미보강 로그에서 확인된 유형 + 앞으로 쓸 만한 유형
CANDIDATES = [
    "otcprStkInvscrInhDecsn",       # 타법인 주식·출자증권 양수 결정
    "otcprStkInvscrTrfDecsn",       # 타법인 주식·출자증권 양도 결정
    "tsstkAqTrctrCnsDecsn",         # 자기주식 취득 신탁계약 체결 결정
    "tsstkAqTrctrCcDecsn",          # 자기주식 취득 신탁계약 해지 결정
    "exbdIsDecsn",                  # 교환사채 발행 결정
    "cmpDvmgDecsn",                 # 회사 분할합병 결정
    "stkExtrDecsn",                 # 주식교환·이전 결정
    "bsnInhDecsn",                  # 영업 양수 결정
    "bsnTrfDecsn",                  # 영업 양도 결정
    "tgastInhDecsn",                # 유형자산 양수 결정
    "tgastTrfDecsn",                # 유형자산 양도 결정
    "crDecsn",                      # 감자 결정
    # 단일판매·공급계약: 정형 API 가 있는지 자체가 불확실하다. 없음을 확인하는 것도 결과다.
    "sglPrvsCntrCncln",
    "cntrCncln",
    "slcCntrCncln",
]


def probe(ep: str, corp: str, bgn: str, end: str) -> dict:
    try:
        r = requests.get(BASE.format(ep), headers=H, timeout=20, params={
            "crtfc_key": KEY, "corp_code": corp, "bgn_de": bgn, "end_de": end})
    except Exception as e:
        return {"exists": None, "note": f"요청 실패 {type(e).__name__}"}
    ct = r.headers.get("Content-Type", "")
    if "json" not in ct:
        return {"exists": False, "note": f"JSON 아님 HTTP {r.status_code} {ct[:40]}"}
    try:
        d = r.json()
    except Exception:
        return {"exists": False, "note": f"파싱 실패 HTTP {r.status_code}"}
    if "status" not in d:
        return {"exists": False, "note": "status 필드 없음"}
    if d["status"] == "101":        # 잘못된 URL = 엔드포인트 자체가 없다
        return {"exists": False, "note": f"101 {d.get('message','')[:40]}"}
    return {"exists": True, "status": d["status"], "msg": d.get("message", "")[:40],
            "rows": d.get("list") or []}


def main():
    if not KEY:
        log("DART_API_KEY 없음 — 중단")
        return

    # 실제 공시 목록에서 corp_code 를 얻는다. 임의 종목으로 두드리면
    # 엔드포인트가 살아 있어도 전부 013(데이터 없음)이라 구분이 안 된다.
    end = datetime.now() - timedelta(days=1)
    bgn = end - timedelta(days=30)
    b, e = bgn.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    log(f"=== 기간 {b} ~ {e} ===\n")

    log("── 1. 엔드포인트 존재 여부 (아무 corp_code 로 두드려 응답 형태만 본다) ──")
    alive = []
    for ep in CANDIDATES:
        r = probe(ep, "00126380", b, e)      # 삼성전자 corp_code
        if r["exists"]:
            alive.append(ep)
            log(f"  O {ep:28} status={r['status']} {r.get('msg','')}")
        else:
            log(f"  X {ep:28} {r['note']}")

    log(f"\n살아있는 엔드포인트 {len(alive)}/{len(CANDIDATES)}")

    log("\n── 2. 실데이터로 필드 키 확인 ──")
    # 최근 30일 공시 목록에서 회사를 모아 살아있는 엔드포인트에 순회 조회한다.
    corps = []
    try:
        r = requests.get(LIST, headers=H, timeout=20, params={
            "crtfc_key": KEY, "bgn_de": b, "end_de": e,
            "pblntf_ty": "B", "page_count": "100"})       # B = 주요사항보고
        for row in (r.json().get("list") or []):
            if row.get("corp_code"):
                corps.append((row["corp_code"], row.get("report_nm", "")))
    except Exception as ex:
        log(f"  목록 조회 실패: {ex}")

    log(f"  주요사항보고 {len(corps)}건 확보")
    seen = set()
    for ep in alive:
        got = False
        for corp, nm in corps[:60]:
            if (ep, corp) in seen:
                continue
            seen.add((ep, corp))
            r = probe(ep, corp, b, e)
            if r["exists"] and r.get("status") == "000" and r.get("rows"):
                row = r["rows"][-1]
                log(f"\n  [{ep}] 샘플: {nm[:40]}")
                for k, v in row.items():
                    log(f"      {k:26} = {str(v)[:60]}")
                got = True
                break
        if not got:
            log(f"\n  [{ep}] 최근 30일 실데이터 없음 — 엔드포인트는 유효")

    log("\n── 3. 단일판매·공급계약 원문 접근 확인 ──")
    # 정형 API 가 없다면 document.xml(공시원문 ZIP)이 유일한 경로다.
    tgt = next((c for c in corps if "공급계약" in c[1]), None)
    if not tgt:
        try:
            rows = []
            for pg in range(1, 6):        # 100건 1페이지로는 원 공시가 안 잡혔다
                rr = requests.get(LIST, headers=H, timeout=20, params={
                    "crtfc_key": KEY, "bgn_de": b, "end_de": e,
                    "page_count": "100", "page_no": str(pg)})
                got = rr.json().get("list") or []
                rows += got
                if len(got) < 100:
                    break
            log(f"  전체 공시 {len(rows)}건에서 탐색")
            # 정정 건은 본문이 짧아 구조를 못 본다 (실측 2,217바이트). 원 공시를 고른다.
            a = next((x for x in rows if "공급계약" in x.get("report_nm", "")
                      and "정정" not in x.get("report_nm", "")), None)
            b = next((x for x in rows if "공급계약" in x.get("report_nm", "")
                      and "정정" in x.get("report_nm", "")), None)
            picks = [x for x in (a, b) if x]
            for hit in picks:
                log(f"  대상: {hit['corp_name']} {hit['report_nm'][:40]} "
                    f"rcept_no={hit['rcept_no']}")
                d = requests.get("https://opendart.fss.or.kr/api/document.xml",
                                 headers=H, timeout=30,
                                 params={"crtfc_key": KEY, "rcept_no": hit["rcept_no"]})
                log(f"  document.xml → HTTP {d.status_code} "
                    f"{d.headers.get('Content-Type','')[:30]} {len(d.content):,}바이트")
                if d.content[:2] == b"PK":
                    import io
                    import zipfile
                    zf = zipfile.ZipFile(io.BytesIO(d.content))
                    log(f"  ZIP 내용: {zf.namelist()}")
                    raw = zf.read(zf.namelist()[0])
                    # meta 는 euc-kr 이라고 하지만 실제 바이트는 UTF-8 이다.
                    # euc-kr 로 읽으면 제목이 '⑥쇳留ㅳ怨듦怨쎌껜寃'로 깨진다 (실측).
                    for enc in ("utf-8", "euc-kr", "cp949"):
                        try:
                            txt = raw.decode(enc)
                            log(f"  디코딩: {enc}")
                            break
                        except UnicodeDecodeError:
                            continue
                    else:
                        txt = raw.decode("utf-8", "ignore")
                    log(f"  본문 {len(txt):,}자")
                    # HTML table 구조다. 라벨/값 쌍을 그대로 뽑아 필드명을 확인한다.
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(txt, "html.parser")
                    log("  --- 표 셀 (라벨 | 값) ---")
                    for tr in soup.find_all("tr")[:40]:
                        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
                        cells = [c for c in cells if c]
                        if cells:
                            log("   " + " | ".join(c[:60] for c in cells))
            if not picks:
                log("  최근 30일 공급계약 공시 없음")
        except Exception as ex:
            log(f"  원문 확인 실패: {type(ex).__name__} {ex}")

    probe_market_pages()

    with open("data/dart_probe.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    print("\n[probe] data/dart_probe.txt 저장")


if __name__ == "__main__":
    main()


def probe_market_pages():
    """네이버 상승률/하락률 페이지 컬럼 구조 확인.

    sise_quant(거래대금 상위)와 컬럼이 다르다. 인덱스를 추측하면
    KIND 때처럼 조용히 틀린 값을 읽는다 (td[2]를 td[1]로 잡아 2743종목 중 3개만 파싱).
    헤더 텍스트를 그대로 덤프해 이름 기반 매핑을 짤 근거를 만든다.
    """
    from bs4 import BeautifulSoup
    H2 = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
    pages = [
        ("거래대금 KOSPI", "https://finance.naver.com/sise/sise_quant.naver?sosok=0"),
        ("상승률 KOSPI", "https://finance.naver.com/sise/sise_rise.naver?sosok=0"),
        ("하락률 KOSPI", "https://finance.naver.com/sise/sise_fall.naver?sosok=0"),
        ("상승률 KOSDAQ", "https://finance.naver.com/sise/sise_rise.naver?sosok=1"),
    ]
    log("\n── 4. 네이버 시세 페이지 컬럼 구조 ──")
    for label, url in pages:
        try:
            r = requests.get(url, headers=H2, timeout=20)
            r.encoding = "euc-kr"
            soup = BeautifulSoup(r.text, "html.parser")
            th = [t.get_text(strip=True) for t in soup.find_all("th")]
            log(f"\n  [{label}] HTTP {r.status_code}")
            log(f"    헤더: {[x for x in th if x][:15]}")
            n = 0
            for tr in soup.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) < 8:
                    continue
                cells = [c.get_text(strip=True) for c in tds]
                if not any(cells):
                    continue
                log(f"    행: {cells[:12]}")
                n += 1
                if n >= 2:
                    break
        except Exception as ex:
            log(f"  [{label}] 실패 {type(ex).__name__} {ex}")
