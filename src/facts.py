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


def count(item: dict) -> int:
    """서술 가능한 독립 사실 개수 (방향 파생 슬롯은 제외)."""
    return len(slots(item) - {"change_up", "change_down", "five_day_up", "five_day_down"})


# ── 코드가 하는 정량 평가 ────────────────────────────────────
# 모델에게 판단시키지 않는다. 서술적 사실만 라벨로 붙인다.

def evaluate(r: dict) -> list[str]:
    """시세 딕셔너리에서 관찰 라벨을 만든다. 원인·전망은 넣지 않는다."""
    out = []

    x = r.get("vol_x")
    if x:
        if x >= 5:
            out.append(f"거래량 평가: 20일 평균의 {x:.1f}배로 매우 이례적인 수준")
        elif x >= 2.5:
            out.append(f"거래량 평가: 20일 평균의 {x:.1f}배로 평소보다 크게 많음")
        elif x >= 1.5:
            out.append(f"거래량 평가: 20일 평균의 {x:.1f}배로 평소보다 많음")
        elif x < 0.7:
            out.append(f"거래량 평가: 20일 평균의 {x:.1f}배로 한산한 편")

    hi, lo = r.get("high"), r.get("low")
    if hi and lo and lo > 0:
        rng = (hi - lo) / lo * 100
        if rng >= 15:
            out.append(f"장중 변동 평가: 고저 차이가 저가 대비 {rng:.1f}%로 매우 큼")
        elif rng >= 8:
            out.append(f"장중 변동 평가: 고저 차이가 저가 대비 {rng:.1f}%로 큰 편")

    fo = r.get("open")
    if fo and r.get("close") and hi:
        # 고가 대비 종가가 얼마나 밀렸는지 (되돌림 여부, 사실 서술)
        back = (hi - r["close"]) / hi * 100
        if back >= 7:
            out.append(f"마감 위치 평가: 장중 고가 대비 {back:.1f}% 낮은 수준에서 마감")
        elif back <= 1:
            out.append("마감 위치 평가: 장중 고가 부근에서 마감")

    d5 = r.get("ret5")
    if d5 is not None and abs(d5) >= 20:
        out.append(f"최근 흐름 평가: 5거래일 누적 {d5:+.1f}%로 단기 변동이 컸음")

    fr = r.get("frgn_net")
    if fr:
        side = "순매수" if fr > 0 else "순매도"
        out.append(f"외국인 {side}: {abs(fr)/1e8:,.0f}억원")
    ins = r.get("inst_net")
    if ins:
        side = "순매수" if ins > 0 else "순매도"
        out.append(f"기관 {side}: {abs(ins)/1e8:,.0f}억원")

    sr = r.get("short_ratio")
    if sr is not None:
        lvl = "높은 편" if sr >= 10 else ("보통" if sr >= 3 else "낮은 편")
        out.append(f"공매도 비중: 거래대금 대비 {sr:.1f}% ({lvl})")

    return out
