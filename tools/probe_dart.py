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
            r = requests.get(LIST, headers=H, timeout=20, params={
                "crtfc_key": KEY, "bgn_de": b, "end_de": e, "page_count": "100"})
            rows = r.json().get("list") or []
            hit = next((x for x in rows if "공급계약" in x.get("report_nm", "")), None)
            if hit:
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
                    txt = raw.decode("euc-kr", "ignore")
                    log(f"  본문 {len(txt):,}자. 계약금액 주변 발췌:")
                    i = txt.find("계약금액")
                    log("  " + (txt[max(0, i-100):i+400].replace("\n", " ")
                                if i >= 0 else "'계약금액' 문자열 없음"))
            else:
                log("  최근 30일 공급계약 공시 없음")
        except Exception as ex:
            log(f"  원문 확인 실패: {type(ex).__name__} {ex}")

    with open("data/dart_probe.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    print("\n[probe] data/dart_probe.txt 저장")


if __name__ == "__main__":
    main()
