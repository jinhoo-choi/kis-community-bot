#!/usr/bin/env python3
"""게이트 차단 공시 유형에 쓸 DART 정형 API 엔드포인트를 실호출로 확인한다.

#113 dry-run 실측: 공시 게이트 차단 23건 전부가 tier5:글감부족이었고,
원인은 과차단이 아니라 dart_detail.ENDPOINTS 에 없는 유형이라 facts 가
공시명·회사·제출인뿐이었기 때문이다.

엔드포인트명과 필드 키를 기억으로 적으면 조용히 빈 값이 붙는다. 후보만 코드에 두고
**응답에서 실제 키를 읽어 출력**한다. DART OpenAPI 는 무료이고 LLM 호출이 없으므로
이 프로브 자체의 비용은 0이다.
"""
import json
import sys
from datetime import datetime as dt, timedelta as td
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import DART_API_KEY, USER_AGENT
from src.sources.dart_detail import corp_codes

BASE = "https://opendart.fss.or.kr/api/{}.json"
LIST = "https://opendart.fss.or.kr/api/list.json"
OUT = ROOT / "data" / "dart_ep_probe.txt"

# (차단 유형 제목 키워드, 엔드포인트 후보들)
# 후보가 틀리면 status 로 드러난다. 맞는 것만 골라 ENDPOINTS 에 넣는다.
CANDIDATES = [
    ("영업양수",       ["bsnInhDecsn", "bsnTrfDecsn"]),
    ("타법인주식",     ["otcprStkInvscrInhDecsn", "otcprStkInvscrTrfDecsn"]),
    ("유형자산취득",   ["astInhtrfEtcPtbkOpt", "tgastInhDecsn"]),
    ("감자",           ["crDecsn"]),
    ("주식병합",       ["stkMgDecsn", "crDecsn"]),
    ("교환사채",       ["exbdIsDecsn"]),
    ("자기주식취득",   ["tsstkAqDecsn"]),
    ("신탁계약",       ["tsstkAqTrctrCnsDecsn", "tsstkAqTrctrCcDecsn"]),
]


def _get(url, params):
    r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT},
                     timeout=30)
    try:
        return r.json()
    except Exception:
        return {"status": "?", "message": r.text[:120]}


def main():
    if not DART_API_KEY:
        print("DART_API_KEY 없음"); return
    end = dt.now()
    bgn = (end - td(days=10)).strftime("%Y%m%d")
    lines = [f"# DART 엔드포인트 프로브 {end:%Y-%m-%d %H:%M}"]
    codes = corp_codes()

    # 대조군: 이미 운영에서 동작하는 엔드포인트. 이게 비면 호출 방식 자체가 문제다.
    ctrl = _get(LIST, {"crtfc_key": DART_API_KEY, "bgn_de": bgn,
                       "end_de": end.strftime("%Y%m%d"), "corp_cls": "Y",
                       "page_count": 100, "last_reprt_at": "N"})
    crow = next((x for x in ctrl.get("list", [])
                 if "전환사채권발행결정" in x.get("report_nm", "")
                 and "정정" not in x.get("report_nm", "")), None)
    lines.append("\n## [대조군] 전환사채 cvbdIsDecsn")
    if crow:
        cc = crow.get("corp_code") or codes.get(crow.get("stock_code", ""), "")
        d = _get(BASE.format("cvbdIsDecsn"), {
            "crtfc_key": DART_API_KEY, "corp_code": cc,
            "bgn_de": bgn, "end_de": end.strftime("%Y%m%d")})
        lines.append(f"   {crow.get('corp_name')} corp_code={cc or '(없음)'} "
                     f"status={d.get('status')} rows={len(d.get('list') or [])}")
        if d.get("list"):
            lines.append("   keys: " + ", ".join(sorted(d["list"][0].keys())))
    else:
        lines.append("   최근 10일 표본 없음")

    for kw, eps in CANDIDATES:
        lst = _get(LIST, {"crtfc_key": DART_API_KEY, "bgn_de": bgn,
                          "end_de": end.strftime("%Y%m%d"), "corp_cls": "Y",
                          "page_count": 100, "last_reprt_at": "N"})
        rows = [x for x in lst.get("list", []) if kw in x.get("report_nm", "")]
        lines.append(f"\n## {kw} — 최근 10일 {len(rows)}건")
        if not rows:
            continue
        row = rows[0]
        corp = row.get("corp_code") or codes.get(row.get("stock_code", ""), "")
        lines.append(f"   표본: {row['report_nm'][:50]} / {row.get('corp_name')}")
        # 013(데이터 없음)이 '그 유형이 없어서'인지 'corp_code 가 비어서'인지 갈라야 한다
        lines.append(f"   corp_code={corp or '(없음)'} "
                     f"stock={row.get('stock_code') or '-'} rcept={row.get('rcept_no')}")
        for ep in eps:
            d = _get(BASE.format(ep), {
                "crtfc_key": DART_API_KEY, "corp_code": corp,
                "bgn_de": bgn, "end_de": end.strftime("%Y%m%d")})
            st, msg = d.get("status"), (d.get("message") or "")[:40]
            got = d.get("list") or []
            lines.append(f"   - {ep:28s} status={st} {msg} rows={len(got)}")
            if got:
                lines.append("     keys: " + ", ".join(sorted(got[0].keys())))
                lines.append("     sample: " + json.dumps(
                    got[0], ensure_ascii=False)[:400])

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[probe] {OUT}")


if __name__ == "__main__":
    main()
