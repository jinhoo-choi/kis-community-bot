"""생성물 자동 검수. 하나라도 걸리면 리젝 → 1회 재생성 → 재실패 시 드랍."""
import re

from src import rules

# 정규식 규칙은 src/rules.py 에서 파생된다 (이중 관리 금지)
RULES = rules.regex_rules()

NUM_RE = re.compile(r"\d[\d,]*\.?\d*")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUM_RE.findall(text)}


_UP = re.compile(r"낙폭|급락|하락(?!률)|떨어졌|빠졌|내렸")
_DOWN = re.compile(r"급등|상승(?!률)|올랐|뛰었|치솟")


# 반대 방향 어휘가 정당한 문맥. 일간 등락률과 부호가 다를 수 있는 지표들이다.
# 상승·하락 양쪽에 똑같이 적용해야 한다 — 상승일 예외만 두었더니
# 하락일에 '5거래일 누적으로는 올랐다'가 리젝됐다 (실측 5건, 그 회차 리젝 1위).
_CONTEXT_OK = re.compile(r"5거래일|누적|고가 (대비|에서)|저가 (대비|에서)"
                         r"|장중|마감 위치|되돌|반납|낙폭 만회")


def _direction_errors(body: str, facts: str) -> list[str]:
    """등락 방향 오용 검사.

    실측: +23.74% 상승 건에 "이 정도 낙폭이면" 이라고 써놓고 심사 19점을 받았다.
    facts 의 등락률 부호로 방향을 확정하고, 반대 방향 어휘가 나오면 리젝한다.

    단 문장 단위로 본다. 글 전체를 한 방향으로 묶으면
    "어제는 12.44% 올랐지만 5거래일 누적으로는 3.41% 내렸다"(둘 다 사실)가
    리젝된다 — 실측 오탐 2건. 하락 어휘가 다른 지표를 가리키는 문맥은 통과시킨다.
    """
    m = re.search(r"등락률[:\s]*([-+]?\d+(?:\.\d+)?)\s*%", facts or "")
    if not m:
        return []
    up = float(m.group(1)) > 0
    pat = _UP if up else _DOWN
    for sent in re.split(r"(?<=[.!?])\s+|\n", body):
        w = pat.search(sent)
        if w and not _CONTEXT_OK.search(sent):
            return [f"방향오용({w.group()})"]
    return []


_HEDGE = re.compile(r"것 같|로 보입니다|인 듯|듯합니다|듯요|보이네요|것으로 보")
# 종결어미만 잡는다. 이전 정규식은 마지막 1~4글자를 통째로 떠서
# '올랐네요' 와 '늘었네요' 가 서로 다른 값으로 세어졌다(실측 FAIL).
_ENDING = re.compile(
    r"(습니다|네요|어요|예요|에요|인데요|더군요|거든요|겠죠|하죠|합니다|입니다)\s*[.!?]")


def _hedge_errors(body: str) -> list[str]:
    """완충 표현 남발. 개별로는 자연스러운데 반복되면 기계 티가 난다."""
    n = len(_HEDGE.findall(body))
    return [f"완충표현{n}회"] if n >= 3 else []


def _ending_variety(body: str) -> list[str]:
    """문장 어미 단조로움. 같은 어미가 4번 이상이면 리듬이 죽는다."""
    ends = _ENDING.findall(body)
    if len(ends) < 4:
        return []
    from collections import Counter
    top, n = Counter(ends).most_common(1)[0]
    return [f"어미반복({top}×{n})"] if n >= 4 else []


# 길이별 허용 수치 개수. 짧은 글에 숫자 5개면 표지 나열이지만
# 긴 글에서는 자연스러울 수 있다.
_NUM_CAP = {"short": 3, "medium": 4, "long": 5}


def _number_overuse(body: str, length: str = None, slot_n: int = 0) -> list[str]:
    """숫자 나열 제한. 제공된 수치를 전부 소비하면 표지 나열이 된다."""
    nums = {n.replace(",", "") for n in NUM_RE.findall(body) if len(n.replace(",", "")) >= 2}
    from src import personas as _P
    cap = _P.num_cap(length) if length else 4
    # 정량 데이터를 늘리면 모델이 더 많이 쓴다. 페르소나 상한만 고정하면
    # 데이터 확대와 충돌한다(실측: data_focus 가 7개 사용).
    # 입력 사실이 많으면 상한을 한 단계 올려 준다.
    if slot_n and slot_n >= 6:
        cap += 1
    return [f"수치과다({len(nums)}개/{length or '-'})"] if len(nums) > cap else []


def _slot_n(facts: str) -> int:
    try:
        from src import facts as _f
        return _f.count({"facts": facts})
    except Exception:
        return 0


def check(body: str, facts: str, fmt: str = None, angle: str = None,
          length: str = None, theme_stock: str = None) -> list[str]:
    """위반 사유 리스트 반환. 빈 리스트면 통과."""
    errs = []

    for name, pat in RULES:
        if re.search(pat, body, re.MULTILINE):
            errs.append(name)

    # 길이 기준은 Length 축에 연동한다.
    # Length 를 도입하면서 프롬프트 지시(3문장 120자 / 6~7문장 250자)와
    # 고정 상하한(50~300)이 어긋나 멀쩡한 글 4건이 리젝됐다(실측).
    n = len(body.strip())
    lo, hi = 50, 300
    if length:
        from src import personas as _P
        lo, hi = _P.len_bounds(length)
    if n < lo:
        errs.append(f"너무짧음({n}자<{lo}/{length or '-'})")
    if n > hi:
        errs.append(f"너무김({n}자>{hi}/{length or '-'})")
    # 토큰 상한에 걸려 문장 중간에서 끊긴 글이 심사까지 올라갔다
    # (실측: gemini 44자, "…나선다고 밝혔"). 종결부호로 끝나지 않으면 미완성이다.
    if body.strip() and body.strip()[-1] not in ".!?\"')]”’":
        errs.append("미완성(종결부호 없음)")

    # 첫 문장이 수치로 시작하면 주어 없이 툭 던지는 글이 된다
    # (실측: 50건 중 22건. "9.43% 올랐습니다."로 시작해 종목명이 끝까지 안 나옴).
    # 파손도 여기서 걸린다 — "6.4배습니다.", "2.5배.", "663,000원."
    first = re.split(r"(?<=[.!?])\s", body.strip(), 1)[0] if body.strip() else ""
    if re.match(r"^[\d(]", first):
        errs.append("수치선두")
    if first and len(first) < 12:
        errs.append(f"선두파손({first[:12]})")
    # 같은 수치가 첫 문장과 둘째 문장에 그대로 반복되는 사례
    # (실측: "4.4% 장중 저가 대비 고가가 4.4%였습니다.")
    _n1 = re.findall(r"\d[\d,]*\.?\d*", first)
    if _n1 and len(set(_n1)) < len(_n1):
        errs.append("선두수치중복")

    # 환각 수치 탐지: 본문 숫자가 원본 facts 에 없으면 리젝
    # (연도/퍼센트 등 흔한 값은 화이트리스트)
    allow = _numbers(facts) | {"1", "2", "3", "4", "5", "10", "100"}
    hallu = [x for x in _numbers(body) if x not in allow and len(x) >= 3]
    if hallu:
        errs.append(f"미확인수치{hallu[:3]}")

    errs += _direction_errors(body, facts)
    # 테마글은 게시 위치로만 종목방을 쓴다. 본문에 종목명이 들어가면
    # '이 정책이 이 종목에 호재'라는 암시가 되어 투자권유로 오인될 수 있다.
    if theme_stock and theme_stock in body:
        errs.append(f"테마글종목언급({theme_stock})")

    errs += _hedge_errors(body)
    # 숫자 개수 대신 '인용한 주장 수' 로 판정한다 (claims.grounding_errors).
    # 개수 세기는 claim 과 어긋나 "1 대 1.8702948" 이 2개로 계산됐다.
    from src import claims as _cl, personas as _P2
    _cap = _P2.claim_cap(length) if length else 4
    _g = _cl.grounding_errors(body, {"facts": facts, "angle": angle}, _cap)
    if _g:
        errs += _g
    else:
        errs += _number_overuse(body, length, _slot_n(facts))
    errs += _ending_variety(body)

    # 미확인 표현은 uncertainty 앵글에서만 허용한다.
    # "정보가 없다"는 안전한 문장이라 모델이 습관적으로 쓰고, 그게 50건 중 20건에
    # 한 번씩 나오면 전체가 똑같아 보인다 (외부 검토 지적).
    # 정보의 부재를 생략하는 것은 거짓을 쓰는 것과 다르다 — 원인을 암시하지 않았다면
    # 원인을 모른다고 밝힐 필요도 없다.
    if angle is not None:
        from src import angles as _ang
        if _ang.forbids_missing(angle):
            m = _ang.MISSING_RE.search(body)
            if m:
                errs.append(f"미확인표현({m.group()[:12]})")

    # 구조가 질문 마무리를 요구하지 않는데 물음표로 끝나면 리젝.
    # 실측: 프롬프트에서 질문 강제를 뺐는데도 4건 전부 물음표로 끝났다.
    if fmt:
        from src import personas as _P
        if _P.no_question(fmt) and body.rstrip().endswith("?"):
            errs.append(f"질문마무리금지({fmt})")

    return errs
