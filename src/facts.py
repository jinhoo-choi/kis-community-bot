"""Fact Slot 추출 + 코드 기반 정량 평가.

외부 검토 1순위 반영: 페르소나 선택 기준을 '입력 글자수'에서 '독립 사실 개수'로 바꾼다.
글자수는 정보량이 아니다.
    "A사가 B사를 흡수합병하기로 결정"            → 사실 1개
    "종가 +12.41%, 거래대금 942억, 20일평균 3.2배" → 독립 사실 3개
길이는 비슷한데 쓸 수 있는 내용은 전혀 다르다.

정량 평가는 코드가 한다. 모델에게 "3.2배가 많은 건가?"를 판단시키면 환각이 난다.
코드가 라벨을 붙여 facts 에 넣어주면 모델은 인용만 하면 된다.
라벨은 서술적 사실에 한정한다 — 원인·전망·투자판단은 넣지 않는다.
"""
import re

# 국내 주식 일일 가격제한폭. 초과분은 버린다.
# 다만 '파싱 오류로 확정'은 틀린 전제다 — 프로브로 네이버 원본을 확인한 결과
# 스카이랩스 135.00% 는 페이지에 실제로 있던 값이었다 (사유 미확인, 신규상장 등
# 가격제한폭 예외로 추정). 파싱 오류든 예외 종목이든 게시 대상이 아니라 버린다.
# LLM 검증으로는 잡히지 않는다 — 입력에 있으면 근거가 있다고 판정하기 때문이다.
PRICE_LIMIT_PCT = 30.0


def sanity_errors(r: dict) -> list[str]:
    """시세 레코드의 물리적 불변식 검사. 위반 시 항목을 버린다."""
    errs = []
    pct = r.get("pct")
    if pct is not None and abs(pct) > PRICE_LIMIT_PCT + 0.5:
        errs.append(f"등락률 {pct}% > 가격제한폭")
    # 목록 페이지 종가와 일별시세 종가가 다르면 둘 중 하나를 잘못 읽은 것이다.
    a, b = r.get("close"), r.get("close_hist")
    if a and b and abs(a - b) / b > 0.01:
        errs.append(f"종가 불일치 {a} vs {b}")
    return errs


# 슬롯 = 독립적으로 한 문장을 만들 수 있는 사실 단위
SLOT_PATTERNS = [
    ("change",    r"등락률[:\s]*[-+]?\d"),
    ("price",     r"종가[:\s]*[\d,]+"),
    ("turnover",  r"거래대금[:\s]*[\d,]+"),
    ("vs_avg",    r"20일 평균[^\n]*배"),
    ("five_day",  r"5거래일[^\n]*%"),
    ("intraday",  r"장중 고가|장중 고저 차이"),
    ("flow_inv",  r"외국인 순매|기관 순매"),
    ("short",     r"공매도"),
    ("amount",    r"발행 총액|조달|적정가격|계약금액"),
    ("terms",     r"전환가액|행사가액|표면이자율|합병 비율|증자 방식|투자의견"),
    ("purpose",   r"시설자금|운영자금|채무상환|취득 목적|자금 용도"),
    ("date",      r"만기|납입일|예정일|상장 예정|시행|협약식|공시 시각"),
    ("term_word", r"전환사채|신주인수권|유상증자|무상증자|자기주식|흡수합병|분할"),
    ("sector",    r"주력|영위|제조업|(반도체|바이오|배터리|조선|방산|금융)\s*(업|기업|산업)"),
    ("inquiry",   r"답변 성격"),
    ("missing",   r"미제공|명시되지 않|공개되지 않"),
]

# 방향성 슬롯 — two_view 는 양방향 근거가 있어야 쓸 수 있다
POSITIVE = {"change_up", "five_day_up", "amount", "purpose", "inquiry"}
NEGATIVE = {"change_down", "five_day_down", "intraday", "missing", "short"}


def slots(item: dict) -> set[str]:
    """이 항목이 제공하는 독립 사실 슬롯."""
    facts = item.get("facts", "")
    core = "\n".join(l for l in facts.splitlines()
                     if l.strip() and not l.strip().startswith("※"))
    out = {sid for sid, pat in SLOT_PATTERNS if re.search(pat, core)}

    m = re.search(r"등락률[:\s]*([-+]?\d+(?:\.\d+)?)", core)
    if m:
        out.add("change_up" if float(m.group(1)) > 0 else "change_down")
    m = re.search(r"5거래일[^\n]*?([-+]?\d+(?:\.\d+)?)\s*%", core)
    if m:
        out.add("five_day_up" if float(m.group(1)) > 0 else "five_day_down")
    return out


def has_both_sides(item: dict) -> bool:
    s = slots(item)
    return bool(s & POSITIVE) and bool(s & NEGATIVE)


# 슬롯을 그대로 세면 왜곡된다. change/price/turnover/intraday/vs_avg 는
# 모두 '같은 날 시세'라는 한 소스에서 파생된 관찰이다 (외부 검토 지적).
# source_family 로 묶어 독립 사실만 센다.
FAMILY = {
    "change": "market_daily", "price": "market_daily", "turnover": "market_daily",
    "intraday": "market_daily",
    "vs_avg": "market_relative", "five_day": "market_relative",
    "flow_inv": "investor_flow", "short": "short_sale",
    "amount": "corp_action", "terms": "corp_action", "purpose": "corp_action",
    "quantity": "corp_action",
    "inquiry": "exchange_inquiry",
    "date": "_meta", "term_word": "_meta", "sector": "_meta", "missing": "_meta",
}


def families(item: dict) -> set[str]:
    """독립 사실 계열. _meta 는 생성 가능성 판단용 보조 속성이라 제외한다."""
    return {FAMILY.get(s, s) for s in slots(item)} - {"_meta", None}


def count(item: dict) -> int:
    """독립 사실 개수.

    같은 계열을 1개로 완전히 합치면 전환사채 공시(발행총액·전환가액·자금용도)가
    1개가 되어 짧은 페르소나만 남는다. 반대로 슬롯을 그대로 세면 시세 파생값이
    부풀려진다. 계열당 최대 2개까지만 인정하는 절충을 쓴다.
    """
    from collections import Counter
    c = Counter(FAMILY.get(s, s) for s in slots(item)
                if s not in ("change_up", "change_down",
                             "five_day_up", "five_day_down"))
    c.pop("_meta", None)
    return sum(min(n, 2) for n in c.values())


# ② two_view 는 슬롯 교집합이 아니라 '상반된 관찰 쌍'이 있어야 성립한다.
#    flow_inv 는 순매수면 한 방향, 순매도면 반대 방향이라 슬롯만으로는 판정할 수 없다.
#    호재/악재로 분류하지 않고 '서로 다른 방향의 관찰값'으로만 취급한다.
def contrast_pairs(item: dict) -> list[tuple[str, str]]:
    core = item.get("facts", "")
    out = []

    up = re.search(r"등락률[:\s]*\+?(\d+(?:\.\d+)?)", core)
    down = re.search(r"등락률[:\s]*-(\d+(?:\.\d+)?)", core)
    back = re.search(r"장중 고가 대비 ([\d.]+)% 낮은", core)
    frgn_sell = re.search(r"외국인 순매도", core)
    frgn_buy = re.search(r"외국인 순매수", core)
    inst_sell = re.search(r"기관 순매도", core)

    if up and back:
        out.append(("종가 상승", f"장중 고가 대비 {back.group(1)}% 낮게 마감"))
    if up and (frgn_sell or inst_sell):
        who = "외국인" if frgn_sell else "기관"
        out.append(("종가 상승", f"{who} 순매도"))
    if down and frgn_buy:
        out.append(("종가 하락", "외국인 순매수"))
    if re.search(r"공매도 비중", core) and up:
        out.append(("종가 상승", "공매도 비중 존재"))
    return out


def has_both_sides(item: dict) -> bool:
    return bool(contrast_pairs(item))


# ── 코드가 하는 정량 평가 ────────────────────────────────────
# 모델에게 판단시키지 않는다. 서술적 사실만 라벨로 붙인다.

def evaluate(r: dict) -> list[str]:
    """게시글 입력에 넣을 관찰값. **판단 라벨을 붙이지 않는다.**

    외부 검토 지적: "3.2배로 평소보다 크게 많음"에서 '크게 많음'은 정보를 늘리지 않고
    투자판단 뉘앙스만 더한다. 독자는 3.2배를 보고 스스로 판단한다.
    판단이 필요한 곳은 Angle 선정 같은 내부 로직이며, 그건 rank() 가 담당한다.
    """
    out = []
    if r.get("vol_x"):
        out.append(f"거래량: 20일 평균의 {r['vol_x']:.1f}배")

    hi, lo, cl = r.get("high"), r.get("low"), r.get("close")
    if hi and lo and lo > 0:
        out.append(f"장중 고저 차이: 저가 대비 {(hi - lo) / lo * 100:.1f}%")
    if hi and cl:
        out.append(f"마감 위치: 장중 고가 대비 {(hi - cl) / hi * 100:.1f}% 낮은 수준")

    # 누적수익률과 변동성은 다른 개념이다. '변동이 컸다'로 서술하지 않는다.
    if r.get("ret5") is not None:
        out.append(f"5거래일 누적 등락률: {r['ret5']:+.2f}%")

    tv = r.get("eok") or r.get("value_eok")
    for key, who in (("frgn_net", "외국인"), ("inst_net", "기관")):
        v = r.get(key)
        if not v:
            continue
        side = "순매수" if v > 0 else "순매도"
        line = f"{who} {side}: {abs(v)/1e8:,.0f}억원"
        if tv:
            line += f" (거래대금 대비 {abs(v)/1e8 / tv * 100:.1f}%)"
        out.append(line)

    sr = r.get("short_ratio")
    if sr is not None:
        line = f"공매도 비중: 거래대금 대비 {sr:.1f}%"
        if r.get("short_avg40"):
            line += f" (40거래일 평균 {r['short_avg40']:.1f}%의 {sr/r['short_avg40']:.1f}배)"
        out.append(line)
    return out


def rank(r: dict) -> dict:
    """내부용 상대 강도. 게시글에 노출하지 않고 Angle 선정에만 쓴다."""
    return {
        "vol_x": r.get("vol_x") or 0,
        "range_pct": ((r["high"] - r["low"]) / r["low"] * 100)
                     if r.get("high") and r.get("low") else 0,
        "abs_chg": abs(r.get("pct") or 0),
    }
