"""Positive Claim Grammar — 말할 수 있는 주장만 미리 정한다.

지금까지는 이 방식이었다.
    모델이 자유롭게 쓴다 → 나쁜 표현을 규칙으로 하나씩 잡는다
규칙을 17종까지 늘렸는데도 같은 기능의 다른 표현이 계속 나왔다.
"기대감이 반영된 것으로 보입니다" 같은 우회를 정규식으로 쫓는 건 끝이 없다.

방향을 뒤집는다.
    입력에서 **허용된 주장 목록**을 먼저 만든다 → 그 안에서 문체만 생성한다
목록에 없는 주장은 애초에 만들 수 없으므로 blacklist 를 늘릴 필요가 줄어든다.

원인·수급주체 추정·업황수혜·기대감·전망은 claim type 자체를 두지 않는다.
"""
import re
import zlib as _zlib

# (claim_id, 라벨, facts 에서 뽑는 정규식, 값 포맷)
CLAIM_SPECS = [
    ("change",    "등락률",        r"등락률[:\s]*([-+]?[\d,.]+)\s*%", "{}%"),
    ("close",     "종가",          r"종가[:\s]*([\d,]+)\s*원", "{}원"),
    ("turnover",  "거래대금",      r"거래대금[:\s]*([\d,]+)\s*억원", "{}억원"),
    ("vol_ratio", "거래량 배수",   r"거래량[:\s]*20일 평균의\s*([\d.]+)배", "20일 평균의 {}배"),
    ("range",     "장중 고저차",   r"장중 고저 차이[:\s]*저가 대비\s*([\d.]+)%", "저가 대비 {}%"),
    ("close_pos", "마감 위치",     r"마감 위치[:\s]*장중 고가 대비\s*([\d.]+)%", "장중 고가 대비 {}% 낮음"),
    ("ret5",      "5거래일 누적",  r"5거래일 누적 등락률[:\s]*([-+]?[\d.]+)\s*%", "{}%"),
    ("frgn",      "외국인 순매매", r"외국인 (순매[수도][^\n]*)", "{}"),
    ("inst",      "기관 순매매",   r"기관 (순매[수도][^\n]*)", "{}"),
    ("short",     "공매도 비중",   r"공매도 비중[:\s]*([^\n]+)", "{}"),
    ("event",     "공시 사건",     r"공시명[:\s]*([^\n]+)|리포트 제목[:\s]*([^\n]+)", "{}"),
    ("issue_amt", "발행/계약 금액",
                  r"(?:발행 총액|계약\s*금액|양[수도]\s*금액|조달)[:\s]*([^\n]+)", "{}"),
    ("conv_prc",  "전환·행사가액", r"(?:전환가액|행사가액)[:\s]*([^\n]+)", "{}"),
    ("rate",      "표면이자율",    r"표면이자율[:\s]*([^\n]+)", "{}"),
    ("ratio_mg",  "합병·배정 비율", r"(?:합병 비율|1주당 배정|합병비율)[:\s]*([^\n]+)", "{}"),
    ("purpose",   "자금·사업 목적", r"(?:자금 용도|시설자금|운영자금|채무상환자금|"
                                    r"취득 목적|합병 목적|처분 목적)[:\s]*([^\n]+)", "{}"),
    ("shares",    "발행 주식수",   r"발행 보통주[:\s]*([^\n]+)", "{}"),
    ("maturity",  "만기·예정일",   r"(?:만기|상장 예정일|납입일|예정일)[:\s]*([^\n]+)", "{}"),
    ("target",    "제시 적정가격", r"제시 적정가격[:\s]*([^\n]+)", "{}"),
    ("opinion",   "투자의견",      r"투자의견[:\s]*([^\n]+)", "{}"),
    ("broker",    "발간 증권사",   r"작성[:\s]*([^\n]+)", "{}"),
    ("inquiry",   "조회공시 답변", r"답변 성격[:\s]*([^\n]+)", "{}"),
    ("scale_vs",  "규모 비교",     r"(?:최근 매출액 대비|자산총액 대비|발행주식 대비)"
                                    r"[:\s]*([\d.]+)\s*%", "{}%"),
    ("counterpart", "거래 상대",   r"(?:계약 상대|대상 회사|상대 회사)[:\s]*([^\n]+)", "{}"),
    ("stake",     "지분율",        r"(?:양수 후 지분율|취득 후 지분율)[:\s]*([^\n]+)", "{}"),
    ("contract",  "계약 내용",     r"계약 내용[:\s]*([^\n]+)", "{}"),
    ("region",    "공급 지역",     r"공급 지역[:\s]*([^\n]+)", "{}"),
    ("sector",    "회사 사업내용", r"(?:주력|영위)[^\n]*", "{}"),
    ("policy",    "정책·발표 내용", r"요지[:\s]*([^\n]{10,200})", "{}"),
]

# claim type 자체를 두지 않는 것들. 목록에 없으므로 쓸 수 없다.
FORBIDDEN_TYPES = [
    "등락의 원인이나 배경",
    "수급 주체 추정 (누가 샀는지 팔았는지에 대한 해석)",
    "업황 수혜, 관련주 파급",
    "투자자 기대감·심리",
    "향후 주가나 실적 전망",
    "회사의 의도나 전략에 대한 추측",
    "수치의 크기에 대한 평가 (많다/적다/이례적이다)",
]


def build(item: dict) -> list[dict]:
    """입력에서 허용 주장 목록을 만든다."""
    facts = item.get("facts", "")
    out, seen = [], set()
    for cid, label, pat, fmt in CLAIM_SPECS:
        m = re.search(pat, facts)
        if not m:
            continue
        val = next((g for g in m.groups() if g), None) if m.groups() else m.group(0)
        val = (val or "").strip()
        if not val or cid in seen:
            continue
        seen.add(cid)
        out.append({"id": f"C{len(out)+1}", "type": cid,
                    "label": label, "value": fmt.format(val)})
    return out


# 앵글별 우선 주장. "이 글이 알려줄 하나"에 직결되는 것부터 고른다.
ANGLE_PREF = {
    "reaction":    ["change", "close", "turnover"],
    "compare":     ["vol_ratio", "ret5", "range", "close_pos"],
    "ratio":       ["ratio_mg", "scale_vs", "stake", "conv_prc", "vol_ratio", "rate"],
    "amount":      ["issue_amt", "scale_vs", "turnover", "shares", "target"],
    "terms":       ["conv_prc", "rate", "ratio_mg", "counterpart", "opinion", "maturity"],
    "purpose":     ["purpose", "contract", "counterpart", "issue_amt", "event"],
    "duration":    ["maturity", "ret5", "event"],
    "decode":      ["event", "contract", "term_word", "sector", "region"],
    "inquiry":     ["inquiry", "event", "change"],
    "uncertainty": ["event", "change"],
    "context":     ["sector", "policy", "event"],
}


def select(item: dict, n: int, angle: str = "") -> list[dict]:
    """이번 글에서 쓸 주장을 **코드가 고른다**.

    이전에는 전체 목록을 주고 "이 중 N개만 고르세요"라고 했다. 모델은 목록을
    소진하려 든다 — 근거를 붙일수록 더 쓴다 (실측: 리젝 15건 중 8건이 주장과다).
    고르는 일을 모델에게 맡기지 않는다.

    순수 함수다. 같은 (facts, n, angle) 이면 프롬프트와 검수가 같은 집합을 본다.
    """
    cs = build(item)
    if len(cs) <= n:
        return cs
    order = {c["type"]: i for i, c in enumerate(cs)}
    pref = [t for t in ANGLE_PREF.get(angle, []) if t in order]
    rest = [c["type"] for c in cs if c["type"] not in pref]
    # 앵글 우선분 뒤는 항목마다 다른 지점에서 시작해 글마다 조합이 갈리게 한다.
    # 고정 순서면 같은 유형 50건이 전부 등락률·종가·거래대금이 된다.
    seed = _zlib.crc32(item.get("facts", "").encode())   # hash() 는 프로세스마다 달라진다
    off = (seed % len(rest)) if rest else 0
    picked = pref + rest[off:] + rest[:off]
    keep = picked[:n]
    return [c for c in cs if c["type"] in keep]


def block(item: dict, use_n: int = 3, angle: str = "") -> str:
    """프롬프트에 넣을 주장 블록. 고른 것만 보여준다."""
    cs = select(item, use_n, angle)
    if not cs:
        return ""
    lines = [f"- {c['label']}: {c['value']}" for c in cs]
    return (
        "[이번 글에 쓸 사실 — 아래 것만 씁니다]\n"
        + "\n".join(lines)
        + "\n\n입력에 다른 수치가 있어도 이번 글에는 쓰지 마세요. 고르는 일은 이미 끝났습니다.\n"
        + "각 문장은 위 사실 중 하나에 근거하거나, 숫자 없는 서술이어야 합니다.\n"
        + "특히 아래는 이 글에서 다룰 수 있는 종류의 주장이 아닙니다.\n"
        + "\n".join(f"- {t}" for t in FORBIDDEN_TYPES)
    )


def facts_view(item: dict, n: int, angle: str = "") -> str:
    """프롬프트에 넣을 사실관계. **고르지 않은 수치는 지운다.**

    블록에서 4개만 고른다고 말해도 [사실관계]에 9개가 그대로 있으면 모델은
    그걸 다 쓴다 (실측: 선정형 전환 후에도 주장과다 6건. 본문이 정확히
    입력 순서대로 나열됐다). 지시는 데이터에 진다.

    지우는 것은 '선정되지 않은 claim 에 해당하는 줄'뿐이다.
    종목명·기준일·주의문·enrich 배경 서술은 그대로 남긴다.
    """
    facts = item.get("facts", "")
    keep = {c["type"] for c in select(item, n, angle)}
    drop_pats = [pat for cid, _l, pat, _f in CLAIM_SPECS if cid not in keep]
    out = []
    for line in facts.splitlines():
        if any(re.search(pat, line) for pat in drop_pats) and re.search(r"\d", line):
            continue
        out.append(line)
    return "\n".join(out)


# ── Sentence-level Grounding ────────────────────────────────
# 숫자 개수를 세는 방식은 claim 과 어긋난다. "1 대 1.8702948" 은 주장 1개인데
# 숫자 2개로 계산돼 수치과다로 리젝됐다 (실측 8건).
# 본문의 숫자가 어느 claim 에서 왔는지 역추적해 '사용된 주장 수'를 센다.

_NUM = re.compile(r"\d[\d,]*\.?\d*")


def _nums(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(text or "")
            if len(n.replace(",", "")) >= 2}


def used(body: str, cs: list[dict], extra_allow: set = frozenset()) -> tuple[set[str], set[str]]:
    """(사용된 claim id, 근거 없는 숫자).

    본문 숫자가 어떤 claim 의 값에 포함되면 그 claim 을 인용한 것으로 본다.
    어느 claim 에도 없는 숫자는 근거가 없다.
    """
    body_nums = _nums(body)
    hit, matched = set(), set()
    for c in cs:
        cn = _nums(c["value"])
        inter = body_nums & cn
        if inter:
            hit.add(c["id"])
            matched |= inter
    # 연도·순번 등 흔한 값은 근거 없음으로 보지 않는다
    allow = {"1", "2", "3", "4", "5", "10", "100", "2026", "2027"} | set(extra_allow)
    return hit, {n for n in body_nums - matched if n not in allow}


_CODE_RE = re.compile(r"종목코드[:\s]*(\d{6})|\((\d{6})[,)]")


def _codes(item: dict) -> set[str]:
    """종목코드는 주장이 아니라 식별자다.

    실측: "엔에프씨(265740)" 가 근거없는수치로 리젝됐다. facts 에 종목코드가
    있는데도 CLAIM_SPECS 에 대응 항목이 없어 어느 주장에도 매칭되지 않았다.
    """
    out = {item["stock_code"]} if item.get("stock_code") else set()
    for m in _CODE_RE.finditer(item.get("facts", "")):
        out.add(m.group(1) or m.group(2))
    return out


def grounding_errors(body: str, item: dict, cap: int) -> list[str]:
    """근거 검사. 숫자 개수가 아니라 인용한 주장 수로 판정한다."""
    cs = build(item)
    if not cs:
        return []
    hit, ungrounded = used(body, cs, _codes(item))
    errs = []
    if len(hit) > cap:
        errs.append(f"주장과다({len(hit)}개/{cap})")
    if ungrounded:
        errs.append(f"근거없는수치{sorted(ungrounded)[:3]}")
    return errs
