"""DART 공시 상세 보강.

문제: list.json 은 공시 '제목'만 준다. 그래서 유상증자 글에 증자 규모도, 발행가도,
자금 목적도 없이 "상세 수치는 확인되지 않았다"만 반복하는 글이 나왔다 (실측).

해결: DART 주요사항보고서 정형 API 를 유형별로 호출해 핵심 수치를 채운다.
document.xml(공시원문 ZIP) 을 파싱하는 방법도 있지만, 원문은 서식이 제각각이라
정형 API 가 훨씬 정확하고 가볍다.

corp_code(8자리 DART 고유번호)가 필요하므로 corpCode.xml 을 1회 받아 캐시한다.
"""
import functools
import io
import re
import zipfile
from xml.etree import ElementTree as ET

import requests

from datetime import datetime as _dt, timedelta as _td

from config import DART_API_KEY, USER_AGENT

BASE = "https://opendart.fss.or.kr/api/{}.json"
CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"

# (제목 키워드, 엔드포인트, 표시명, 뽑을 필드)
#   필드는 (API 키, 라벨, 단위) — 단위는 표기용
ENDPOINTS = [
    (r"유상증자", "piicDecsn", "유상증자 결정", [
        ("nstk_ostk_cnt", "발행 보통주", "주"),
        ("fdpp_fclt", "시설자금", "원"),
        ("fdpp_op", "운영자금", "원"),
        ("fdpp_dtrp", "채무상환자금", "원"),
        ("fdpp_ocsa", "타법인증권취득자금", "원"),
        ("nstk_asstd", "신주 발행가", "원"),
        ("ic_mthn", "증자 방식", ""),
        ("ssl_at", "상장 예정일", ""),
    ]),
    (r"무상증자", "fricDecsn", "무상증자 결정", [
        ("nstk_ostk_cnt", "발행 보통주", "주"),
        ("nstk_asstd", "신주 배정 기준", ""),
        ("nstk_ascnt_ps_ostk", "1주당 배정", "주"),
    ]),
    (r"전환사채", "cvbdIsDecsn", "전환사채 발행 결정", [
        ("bd_tm", "회차", ""),
        ("bd_knd", "종류", ""),
        ("bd_fta", "발행 총액", "원"),
        ("cv_prc", "전환가액", "원"),
        ("bd_intr_ex", "표면이자율", "%"),
        ("bd_mtd", "만기일", ""),
    ]),
    (r"신주인수권", "bdwtIsDecsn", "신주인수권부사채 발행 결정", [
        ("bd_fta", "발행 총액", "원"),
        ("ex_prc", "행사가액", "원"),
        ("bd_intr_ex", "표면이자율", "%"),
    ]),
    # 순서 주의: '자기주식취득신탁계약체결결정'은 아래 '자기주식취득' 패턴에도 걸린다.
    (r"자기주식취득신탁계약\s*체결", "tsstkAqTrctrCnsDecsn", "자기주식 취득 신탁계약 체결", [
        ("ctr_prc", "계약 금액", "원"),
        ("ctr_pd_bgd", "계약 시작일", ""),
        ("ctr_pd_edd", "계약 종료일", ""),
        ("ctr_pp", "계약 목적", ""),
        ("ctr_cns_int", "계약 기관", ""),
        ("aq_wtn_div_ostk_rt", "발행주식 대비", "%"),
    ]),
    (r"자기주식취득신탁계약\s*해지", "tsstkAqTrctrCcDecsn", "자기주식 취득 신탁계약 해지", [
        ("ctr_prc_bfcc", "해지 전 계약 금액", "원"),
        ("ctr_pd_bfcc_edd", "해지 전 계약 종료일", ""),
        ("cc_pp", "해지 목적", ""),
        ("cc_prd", "해지 예정일", ""),
        ("aq_wtn_div_ostk_rt", "발행주식 대비", "%"),
    ]),
    (r"타법인주식.*(양수|취득)|출자증권(양수|취득)", "otcprStkInvscrInhDecsn", "타법인 주식 양수 결정", [
        ("iscmp_cmpnm", "대상 회사", ""),
        ("iscmp_mbsn", "대상 회사 사업", ""),
        ("inhdtl_inhprc", "양수 금액", "원"),
        ("inhdtl_tast_vs", "자산총액 대비", "%"),
        ("atinh_eqrt", "양수 후 지분율", "%"),
        ("inh_pp", "양수 목적", ""),
    ]),
    (r"타법인주식.*(양도|처분)|출자증권(양도|처분)", "otcprStkInvscrTrfDecsn", "타법인 주식 양도 결정", [
        ("iscmp_cmpnm", "대상 회사", ""),
        ("trfdtl_trfprc", "양도 금액", "원"),
        ("trfdtl_tast_vs", "자산총액 대비", "%"),
        ("trf_pp", "양도 목적", ""),
    ]),
    (r"유형자산\s*양수", "tgastInhDecsn", "유형자산 양수 결정", [
        ("ast_sen", "자산 종류", ""),
        ("inhdtl_inhprc", "양수 금액", "원"),
        ("inhdtl_tast_vs", "자산총액 대비", "%"),
        ("inh_pp", "양수 목적", ""),
    ]),
    (r"주식교환|주식이전", "stkExtrDecsn", "주식교환·이전 결정", [
        ("extr_sen", "거래 종류", ""),
        ("extr_tgcmp_cmpnm", "상대 회사", ""),
        ("extr_rt", "교환 비율", ""),
        ("aprskh_plnprc", "주식매수청구가", "원"),
        ("extr_pp", "거래 목적", ""),
    ]),
    (r"자기주식\s*취득|자기주식취득", "tsstkAqDecsn", "자기주식 취득 결정", [
        ("aq_pp", "취득 목적", ""),
        ("aq_wtn_div_ostk", "취득 예정 보통주", "주"),
        ("aq_wtn_div_ostk_rt", "발행주식 대비", "%"),
        ("aq_mth", "취득 방법", ""),
    ]),
    (r"자기주식\s*처분|자기주식처분", "tsstkDpDecsn", "자기주식 처분 결정", [
        ("dp_pp", "처분 목적", ""),
        ("dppdd_ostk", "처분 예정 보통주", "주"),
    ]),
    (r"합병", "cmpMgDecsn", "회사 합병 결정", [
        ("mg_mth", "합병 방법", ""),
        ("mg_rt", "합병 비율", ""),
        ("mg_pp", "합병 목적", ""),
    ]),
    (r"분할", "cmpDvDecsn", "회사 분할 결정", [
        ("dv_mth", "분할 방법", ""),
        ("dv_impef", "분할 목적", ""),
    ]),
]

# 정형 API 가 없는 유형. 발행결정 API 를 쏘면 status 013(데이터 없음)이 오고,
# 수치 없이 제목만 남아 심사에서 전건 fatal 이 된다
# (실측: "자기주식취득신탁계약 해지결정", "자기주식처분결과보고서" 2회 연속 재현).
# 제목 매칭보다 먼저 판정한다 — '자기주식처분결과보고서'는 '자기주식처분' 패턴에 걸린다.
# 프로브로 확인: status 101(잘못된 URL)이 오는 유형만 여기 둔다.
# '해지결정'을 넣었던 건 오판이었다 — tsstkAqTrctrCcDecsn 이 실재하고
# NICE 건에 계약금액 100억·해지사유가 들어 있었다. 그게 fatal 2건의 정체다.
NO_DETAIL_API = re.compile(r"결과보고서|종료보고서|정정신고|철회|취소|발행결과|정정명령")

_HEADERS = {"User-Agent": USER_AGENT}


@functools.lru_cache(maxsize=1)
def corp_codes() -> dict[str, str]:
    """{종목코드(6): 고유번호(8)}. ZIP 안의 XML 을 파싱한다."""
    if not DART_API_KEY:
        return {}
    try:
        r = requests.get(CORP_CODE_URL, params={"crtfc_key": DART_API_KEY},
                         headers=_HEADERS, timeout=60)
        r.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        xml = zf.read(zf.namelist()[0])
        root = ET.fromstring(xml)
        table = {}
        for e in root.iter("list"):
            stock = (e.findtext("stock_code") or "").strip()
            corp = (e.findtext("corp_code") or "").strip()
            if len(stock) == 6 and corp:
                table[stock] = corp
        print(f"[dart] corp_code {len(table)}종목 매핑")
        return table
    except Exception as e:
        print(f"[dart] ⚠ corpCode 로드 실패: {e}")
        return {}


def _fmt(val: str, unit: str) -> str:
    # 원문 값에 줄바꿈과 들여쓰기가 섞여 온다 (실측: ast_nm, ctr_cns_int, dl_pym).
    # 한 줄 형식이 깨지므로 공백을 접는다. 긴 서술형은 잘라 쓴다.
    v = re.sub(r"\s+", " ", (val or "")).strip()
    if not v or v == "-":
        return ""
    if len(v) > 90:
        v = v[:90].rstrip() + "…"
    # 숫자면 천 단위 구분, 억/조 단위로 읽기 쉽게
    if unit == "원" and re.fullmatch(r"[\d,]+", v):
        n = int(v.replace(",", ""))
        if n >= 1_0000_0000:
            return f"{n/1_0000_0000:,.0f}억원"
        return f"{n:,}원"
    return f"{v}{unit}" if unit and not v.endswith(unit) else v


def enrich_one(item: dict, day: str) -> bool:
    """공시 항목의 facts 에 정형 수치를 덧붙인다. 보강했으면 True."""
    code = item.get("stock_code")
    corp = corp_codes().get(code or "")
    if not corp:
        return False

    title = item.get("title", "")
    if NO_DETAIL_API.search(title):
        item["no_detail_api"] = True
        return False
    for pat, ep, label, fields in ENDPOINTS:
        if not re.search(pat, title):
            continue
        try:
            # bgn_de=end_de=전일 로 못 박으면 이사회 결의일과 접수일이 다른 건이
            # 전부 0건으로 돌아온다. 주요사항보고서는 결의 후 며칠 뒤 접수되기도 한다.
            # 범위를 일주일로 넓히고 가장 최근 행을 쓴다.
            # 정정공시는 원 결의일이 몇 달 전일 수 있다. 창을 넓게 잡는다.
            back = 180 if re.search(r"정정", title) else 7
            bgn = (_dt.strptime(day, "%Y%m%d") - _td(days=back)).strftime("%Y%m%d")
            r = requests.get(BASE.format(ep), headers=_HEADERS, timeout=20, params={
                "crtfc_key": DART_API_KEY, "corp_code": corp,
                "bgn_de": bgn, "end_de": day,
            })
            d = r.json()
            if d.get("status") != "000" or not d.get("list"):
                return False

            row = d["list"][-1]
            lines = []
            for key, lab, unit in fields:
                v = _fmt(row.get(key, ""), unit)
                if v:
                    lines.append(f"- {lab}: {v}")
            if not lines:
                return False

            item["facts"] = (
                item["facts"].rstrip()
                + f"\n\n[{label} 상세 — DART 정형 데이터]\n"
                + "\n".join(lines)
                + "\n※ 위 수치는 공시 원문 값이다. 그대로 쓰되 계산하거나 합산하지 말 것."
            )
            item["dart_detail"] = ep
            return True
        except Exception as e:
            print(f"[dart] {ep} 실패 {code}: {type(e).__name__}")
            return False
    return False


def enrich_all(items: list[dict], day: str) -> int:
    targets = [i for i in items if i.get("kind") == "disclosure"]
    if not targets or not DART_API_KEY:
        return 0
    n = sum(1 for i in targets if enrich_one(i, day))
    miss = [i.get("title", "")[:34] for i in targets if not i.get("dart_detail")]
    print(f"[dart] 상세 보강 {n}/{len(targets)}건")
    if miss:
        print(f"[dart] 미보강 제목: {miss[:8]}")
    return n
