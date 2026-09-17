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
import re as _re_mod
import datetime as _dt
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
    # 아래 넷은 결합 사실에만 있는 시간축·장중 위치 값이다.
    # spec 이 없으면 본문이 인용해도 근거없는수치로 리젝된다.
    ("open_pos",  "시가 대비 마감", r"시가 대비 마감[:\s]*([\d.]+\s*%\s*[가-힣]+ 수준)", "{}"),
    ("gap",       "시가 출발",     r"시가 출발[:\s]*(전일 종가 대비[^\n]+)", "{}"),
    ("extreme",   "종가 위치",     r"종가 위치[:\s]*(최근[^\n]+)", "{}"),
    ("streak",    "연속 흐름",     r"연속 흐름[:\s]*(\d+거래일 연속 [가-힣]+)", "{}"),
    ("ma20",      "이동평균 대비", r"20일 이동평균 대비[:\s]*([\d.]+\s*%\s*[가-힣]+)", "{}"),
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
    # 콜론을 필수로 둔다. facts 생성부는 3경로 모두 '제시 적정가격: X' / '투자의견: X'
    # 형태다. 느슨하게 두면 '※ 목표주가·투자의견 미제공' 주석과 '용어 설명:
    # 투자의견은 …' 줄까지 값으로 잡아, 값이 없는 리포트에 가짜 주장이 생긴다.
    ("target",    "제시 적정가격", r"제시 적정가격:\s*([^\n]+)", "{}"),
    ("opinion",   "투자의견",      r"투자의견:\s*([^\n]+)", "{}"),
    # 추가 요청 0회로 만든 결합 사실. rows(45일 OHLCV)는 이미 받아오고 있었다.
    ("move_x",    "등락 크기",     r"등락 크기[:\s]*([^\n]+)", "{}"),
    ("drawdown",  "고점 대비",     r"고점 대비[:\s]*([^\n]+)", "{}"),
    ("gapfill",   "갭 되돌림",     r"갭 되돌림[:\s]*([^\n]+)", "{}"),
    # 리포트 요지. 제목·목표가만으로는 '제목만 반복' 이 돼 fit 이 1~2점에 머문다
    # (#124 리서치 10건 전건). 요지를 주장으로 등록해 본문의 근거가 되게 한다.
    ("gist",      "리포트 요지",   r"리포트 요지[:\s]*([^\n]+)", "{}"),
    # 한경은 '작성: X증권 홍길동', 네이버는 '발간: X증권 / 2026.09.11' 이다.
    # '작성:' 만 보면 네이버 리포트에는 broker 주장이 아예 생기지 않는다.
    # '발간일:' 을 먼저 잡으면 broker 가 날짜가 된다(한경은 발간일·작성 두 줄을
    # 모두 쓰고 발간일이 앞에 온다). 실측: broker='일: 2026.09.10'.
    ("broker",    "발간 증권사",   r"(?:작성|발간)(?!일)[:\s]*([^\n/]+)", "{}"),
    ("inquiry",   "조회공시 답변", r"답변 성격[:\s]*([^\n]+)", "{}"),
    ("scale_vs",  "규모 비교",     r"(?:최근 매출액 대비|자산총액 대비|발행주식 대비)"
                                    r"[:\s]*([\d.]+)\s*%", "{}%"),
    ("counterpart", "거래 상대",   r"(?:계약 상대|대상 회사|상대 회사)[:\s]*([^\n]+)", "{}"),
    ("stake",     "지분율",        r"(?:양수 후 지분율|취득 후 지분율)[:\s]*([^\n]+)", "{}"),
    ("contract",  "계약 내용",     r"계약 내용[:\s]*([^\n]+)", "{}"),
    ("region",    "공급 지역",     r"공급 지역[:\s]*([^\n]+)", "{}"),
    ("sector",    "회사 사업내용", r"(?:주력|영위)[^\n]*", "{}"),
    ("term_def",  "용어 설명",     r"용어 설명[:\s]*([^\n]+)", "{}"),
    ("policy",    "정책·발표 내용", r"(?m)^요지[:\s]*([^\n]{10,200})", "{}"),
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


def _claimable(facts: str) -> str:
    """주장 추출 대상 텍스트. ※ 주의문 줄은 뺀다.

    ※ 줄은 모델에게 주는 경고지 인용할 사실이 아니다. 그런데 spec 이 그 안의
    단어를 잡아 경고문 자체가 주장 값이 된다.
    실측 #123: '※ 계약금액이 매출에 언제 얼마나 반영될지는 공시에 없다' 에서
    issue_amt = '이 매출에 언제 얼마나 반영될지는 공시에 없다. 추정하지 말 것.'
    이 만들어졌다. issue_amt 는 공시 앵커라 이 값이 프롬프트에 강제로 들어갔다.
    PR #19 의 리포트 가짜 주장과 같은 계열이고, 그때는 콜론 필수화로 개별
    대응했지만 여기서 한 번에 막는다.

    facts_view 는 원본 facts 를 그대로 훑으므로 ※ 줄은 프롬프트에 남는다.
    경고문이 사라지는 것이 아니라, 경고문이 '쓸 사실' 로 승격되지 않을 뿐이다.
    """
    return "\n".join(l for l in (facts or "").splitlines()
                      if not l.lstrip().startswith("※"))


def build(item: dict) -> list[dict]:
    """입력에서 허용 주장 목록을 만든다."""
    facts = _claimable(item.get("facts", ""))
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
    "reaction":    ["change", "gist", "move_x", "close", "turnover", "gap"],
    "compare":     ["vol_ratio", "gist", "drawdown", "ret5", "range", "close_pos", "ma20", "extreme"],
    "ratio":       ["ratio_mg", "move_x", "gist", "scale_vs", "stake", "conv_prc", "vol_ratio", "rate", "ma20"],
    "amount":      ["issue_amt", "gist", "gapfill", "scale_vs", "turnover", "shares", "target"],
    "terms":       ["conv_prc", "gist", "rate", "ratio_mg", "counterpart", "opinion", "target", "maturity"],
    "purpose":     ["purpose", "gist", "contract", "counterpart", "issue_amt", "event"],
    "duration":    ["maturity", "gist", "drawdown", "ret5", "event", "streak", "extreme"],
    "decode":      ["term_def", "gist", "event", "contract", "sector", "region"],
    "inquiry":     ["inquiry", "event", "change"],
    "uncertainty": ["event", "change"],
    "context":     ["sector", "gist", "policy", "event"],
}


# 유형별로 '이게 빠지면 글이 성립하지 않는' 주장. 앵글 우선순위보다 앞선다.
# 실측(#110): 공시 보류 사유에 '발행총액 200억 누락, 정보량 부족',
# '용도자금 오인 표현, 정보량 부족' 이 반복됐다. purpose 계열 앵글에서
# issue_amt 가 우선순위 4번째라 n=2~3 에 잘려 '얼마'가 빠진 채 나갔다.
# 실측(2026-09-14 #113): research 는 filter_passed 29건 전건 보류였다. 보류 사유가
# '제목 재진술 수준, 정보 부족'(fit 1~2) 16건 + 사실성 9건으로, 쓸 수 있는 유일한
# 리포트 고유 사실인 target/opinion 이 terms 앵글 우선순위 5~6번째라 n=2~3 에
# 잘리고 event(리포트 제목)만 남았다. 공시의 issue_amt 와 같은 실패다.
# 순서가 곧 우선순위다. keep = picked[:n] 이라 앞쪽이 낮은 cap 에서도 살아남는다.
# 요지를 맨 앞에 둔다 — 정보량이 가장 크고, 목표가로 시작하면 모든 리포트 글이
# '적정가격 X원' 한 형태로 수렴한다(#124 실측). 투자의견은 'Buy' 한 단어라 맨 뒤.
# 요지가 없는 경로(한경)에서는 자동으로 target·opinion 이 앞으로 당겨진다.
ANCHOR_TYPES = {"disclosure": ["issue_amt"],
                "research": ["gist", "target", "opinion"]}


# 결합 사실 계열. 하나도 선정되지 않으면 본문이 종가·등락률만 말하게 되고
# filters 의 '결합사실미사용' 에 걸린다. 실측(5건 실행): 글감을 늘린 직후
# 이 리젝이 3건 새로 생겼다 — 후보가 늘자 회전 구간에서 결합 사실이 통째로
# 밀려났기 때문이다.
DERIVED_TYPES = ("vol_ratio", "range", "close_pos", "ret5",
                 "open_pos", "gap", "extreme", "streak", "ma20")


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
    anchor = [t for t in ANCHOR_TYPES.get(item.get("kind", ""), []) if t in order]
    # 결합 사실이 있는데 하나도 안 고르면 글이 종가·등락률 나열로 끝난다.
    # 앵글 우선분 중 결합 사실이 있으면 그것을, 없으면 첫 결합 사실을 앵커로 둔다.
    if not any(t in DERIVED_TYPES for t in anchor):
        pref_d = [t for t in ANGLE_PREF.get(angle, [])
                  if t in order and t in DERIVED_TYPES]
        cand = pref_d or [c["type"] for c in cs if c["type"] in DERIVED_TYPES]
        if cand:
            anchor = anchor + [cand[0]]
    pref = anchor + [t for t in ANGLE_PREF.get(angle, [])
                     if t in order and t not in anchor]
    rest = [c["type"] for c in cs if c["type"] not in pref]
    # 앵글 우선분 뒤는 항목마다 다른 지점에서 시작해 글마다 조합이 갈리게 한다.
    # 고정 순서면 같은 유형 50건이 전부 등락률·종가·거래대금이 된다.
    seed = _zlib.crc32(item.get("facts", "").encode())   # hash() 는 프로세스마다 달라진다
    off = (seed % len(rest)) if rest else 0
    picked = pref + rest[off:] + rest[:off]
    keep = picked[:n]
    # 목표가·투자의견을 쓰는 순간 출처 표기가 의무가 된다(filters 출처없는목표주가).
    # 앵커가 broker 를 밀어내면 모델은 '한 증권사로부터' 라고 쓰고 그대로 리젝된다
    # (실측 #122: research:출처없는목표주가 3건, #121 에는 없던 리젝이다).
    # 출처는 주장이 아니라 귀속이므로 n 에 포함시키지 않는다 — 종목코드·날짜와 같다.
    if (item.get("kind") == "research" and "broker" in order
            and ("target" in keep or "opinion" in keep)):
        keep = keep + ["broker"]
    # 고른 순서대로 돌려준다. CLAIM_SPECS 순서로 돌려주면 블록 맨 위가 늘
    # change·close(스펙 앞쪽)라, 앵글이 무엇이든 모델이 등락률부터 쓴다.
    # 실측 #123: claim 조합은 43건에 28가지인데 첫 문장은 전건이 '<종목> + 등락률'
    # 한 형태였다. 여기서 순서를 주면 앵글 수만큼 진입이 갈린다.
    by_type = {c["type"]: c for c in cs}
    return [by_type[t] for t in keep if t in by_type]


def block(item: dict, use_n: int = 3, angle: str = "") -> str:
    """프롬프트에 넣을 주장 블록. 고른 것만 보여준다."""
    cs = select(item, use_n, angle)
    if not cs:
        return ""
    lines = [f"- {c['label']}: {c['value']}" for c in cs]
    # 첫 줄이 이 글의 진입점이다. 지정하지 않으면 모델이 늘 등락률로 시작한다.
    lead = f"\n첫 문장은 '{cs[0]['label']}' 로 시작합니다. 나머지는 그다음에 씁니다.\n"
    return (
        "[이번 글에 쓸 사실 — 아래 것만 씁니다]\n"
        + "\n".join(lines)
        + lead
        + "\n\n입력에 다른 사실이 있어도 이번 글에는 쓰지 마세요. 고르는 일은 이미 끝났습니다.\n"
        + "숫자가 없는 문장도 위 사실에서 직접 확인되는 내용이어야 합니다. "
          "평가·정의·배경을 새로 보태지 마세요.\n"
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
        # ※ 주의문은 어떤 경우에도 지우지 않는다. 미선정 spec 에 우연히 걸려
        # 경고문이 통째로 사라지면 모델이 추정 금지 지시를 받지 못한다.
        if not line.lstrip().startswith("※") and any(
                re.search(pat, line) for pat in drop_pats):
            continue
        out.append(line)
    return "\n".join(out)


# ── Sentence-level Grounding ────────────────────────────────
# 숫자 개수를 세는 방식은 claim 과 어긋난다. "1 대 1.8702948" 은 주장 1개인데
# 숫자 2개로 계산돼 수치과다로 리젝됐다 (실측 8건).
# 본문의 숫자가 어느 claim 에서 왔는지 역추적해 '사용된 주장 수'를 센다.

_NUM = re.compile(r"\d[\d,]*\.?\d*")


# 보도자료·RSS 는 '1천490억원' 처럼 자릿수를 한글로 끊어 쓴다. 그대로 파싱하면
# 490 만 남아 facts 의 1490 과 매칭되지 않고 근거없는수치로 리젝된다
# (실측 #122: policy 근거없는수치 ['490'], ['50','700','7400'], ['74.1'] 3건).
_KO_DIGIT = _re_mod.compile(r"(?<!\d)(\d{1,3})천(\d{1,3})(?!\d)")


def _normalize(text: str) -> str:
    return _KO_DIGIT.sub(lambda m: m.group(1) + m.group(2).zfill(3), text or "")


def _nums(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(_normalize(text))
            if len(n.replace(",", "")) >= 2}


def used(body: str, cs: list[dict], extra_allow: set = frozenset(),
         prefer: set = frozenset()) -> tuple[set[str], set[str]]:
    """(사용된 claim id, 근거 없는 숫자).

    본문 숫자가 어떤 claim 의 값에 포함되면 그 claim 을 인용한 것으로 본다.
    어느 claim 에도 없는 숫자는 근거가 없다.

    같은 숫자를 여러 claim 이 가질 수 있다 (실측: 197건 중 4건. 장중 고저차와
    시가 대비 마감이 둘 다 29.6%). 그대로 세면 주장 하나를 둘로 세어
    주장과다·선정외주장 오탐이 난다. prefer(선정된 claim id) 에 귀속 가능한
    숫자는 그쪽으로만 센다.
    """
    body_nums = _nums(body)
    hit, matched = set(), set()
    claimed = set()
    for c in cs:
        if c["id"] in prefer and body_nums & _nums(c["value"]):
            claimed |= body_nums & _nums(c["value"])
    for c in cs:
        cn = _nums(c["value"])
        inter = body_nums & cn
        if inter and c["id"] not in prefer and inter <= claimed:
            continue                      # 선정된 claim 이 이미 설명하는 숫자다
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


# 날짜 표기 자체를 찾는다. 라벨 목록으로는 못 잡는다.
# 숫자 경계를 요구한다. 경계가 없으면 더 긴 숫자의 일부를 날짜로 오인한다.
_DATE_RE = re.compile(
    r"(?<!\d)(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})(?!\d)")


def _metadata_numbers(item: dict) -> set[str]:
    """날짜·시점은 주장 수에 넣지 않되 본문에서 그대로 인용할 수 있게 한다.

    실측(#110): 라벨 화이트리스트가 '리포트 발간일', '계약 종료일' 처럼 조금만
    다른 표기를 놓쳐, 본문의 '9월 11일'·'2027년 10월 17일' 이 근거없는수치로
    리젝됐다. 리서치 생성 10건과 공시 1건이 이 한 가지로 버려졌다.
    라벨이 무엇이든 facts 안의 날짜 구성요소는 시점 표기로 본다.
    """
    out = set()
    for m in _DATE_RE.finditer(item.get("facts", "")):
        y, mo, d = m.groups()
        try:
            # 형식만 보면 '관리번호: 2026-99-77' 의 99·77 까지 시점으로 풀린다.
            _dt.date(int(y), int(mo), int(d))
        except ValueError:
            continue
        for g in (y, mo, d):
            out.add(g)
            out.add(g.lstrip("0") or g)
    for line in item.get("facts", "").splitlines():
        if re.match(r"^(?:기준일|공시일|발행일|발간일|작성일|기사일|신청일|승인일|"
                    r"보도 시각|게시 시각|공시 시각|발행 시각|시점)\s*:", line):
            out |= _nums(line)
    # 기간 단위는 값이 아니라 지표 이름의 일부다. '20일 이동평균 대비: 34.3%' 에서
    # 주장의 값은 34.3 이고 20 은 지표명이다. facts.derived_values 도 같은 이유로
    # 기간 단위를 값에서 제외한다. 여기에만 빠져 있어 본문이 지표명을 그대로
    # 인용하면 근거없는수치로 리젝됐다 (실측 #121: flow:근거없는수치['20'] 12건,
    # 캐시 197건 재현 시 24건).
    # 검색 보강이 붙인 '[검색으로 확인된 배경]' 줄도 코드가 넣은 사실이다.
    # 이 블록은 어떤 CLAIM_SPEC 에도 매핑되지 않아 프롬프트에 그대로 남는데,
    # 인용하면 근거없는수치로 리젝된다(감사 I1, 실측 #125 공시 2건).
    facts_txt = item.get("facts", "")
    if "[검색으로 확인된 배경]" in facts_txt:
        out |= _nums(facts_txt.split("[검색으로 확인된 배경]", 1)[1])
    for m in re.finditer(r"(?<!\d)(\d+)\s*(?:거래일|일|개월|년)(?!\d)",
                         item.get("facts", "")):
        out.add(m.group(1))
        out.add(m.group(1).lstrip("0") or m.group(1))
    return out


def grounding_errors(body: str, item: dict, cap: int) -> list[str]:
    """근거 검사. 숫자 개수가 아니라 인용한 주장 수로 판정한다."""
    all_cs = build(item)
    if not all_cs:
        return []
    selected = (select(item, cap, item.get("angle", ""))
                if item.get("angle") else all_cs)
    selected_ids = {c["id"] for c in selected}
    hit, ungrounded = used(body, all_cs, _codes(item) | _metadata_numbers(item),
                           prefer=selected_ids)
    errs = []
    if len(hit) > cap:
        errs.append(f"주장과다({len(hit)}개/{cap})")
    elif hit - selected_ids:
        errs.append(f"선정외주장({len(hit - selected_ids)}개)")
    if ungrounded:
        errs.append(f"근거없는수치{sorted(ungrounded)[:3]}")
    return errs
