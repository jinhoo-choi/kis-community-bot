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


def _needs_relation(body: str, facts_text: str = "") -> list[str]:
    """코드가 계산한 결합 사실을 실제로 썼는지 본다.

    외부 검토 지적: '관계 표현 키워드가 있는가' 로 검사하면 모델이
    "5거래일 누적 흐름 가운데 거래량은 3.2배였습니다" 처럼 단어만 끼워넣어
    통과한다(Goodhart). 키워드가 아니라 **특정 사실의 사용 여부**를 봐야 한다.

    결합 사실은 개별 raw 수치만으로는 드러나지 않는 비교·비중·위치다
    (평균 대비 배수, 5거래일 누적, 고가 대비 마감 위치, 수급 순위).
    같은 수치뿐 아니라 관계 문맥까지 본문에 등장해야 통과시킨다.
    """
    from src import facts as _facts
    derived = _facts.derived_values(facts_text)
    if not derived:
        return []                      # 결합 사실이 없는 소재는 요구하지 않는다
    return [] if _facts.uses_derived(body, facts_text) else ["결합사실미사용"]


def _ending_variety(body: str) -> list[str]:
    """문장 어미 단조로움. 같은 어미가 4번 이상이면 리듬이 죽는다.

    추가로 '~습니다' 일변도를 막는다. 네이버 종토방 실측에서
    반응 좋은 글의 종결어미 중 '~습니다' 는 1.7% 뿐이었다.
    우리 글은 사실상 100% 라 'AI 티' 피드백의 주원인으로 보인다.
    존댓말은 유지하되 구어체 어미를 섞게 한다.
    """
    ends = _ENDING.findall(body)
    if len(ends) < 3:
        return []
    from collections import Counter
    errs = []
    c = Counter(ends)
    top, n = c.most_common(1)[0]
    if n >= 4:
        errs.append(f"어미반복({top}×{n})")
    formal = sum(v for k, v in c.items()
                 if k in ("습니다", "합니다", "입니다", "됩니다"))
    if len(ends) >= 3 and formal == len(ends):
        errs.append(f"어미단조(격식체만 {formal}문장)")
    return errs


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
          length: str = None, theme_stock: str = None,
          require_question: bool = False) -> list[str]:
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
    # 커뮤니티 말투는 말줄임표와 물결로도 끝난다. 이걸 빼두면 멀쩡한 글이
    # 미완성으로 잘린다 (실측: "삼성전자가 3% 올랐습니다…" 가 리젝됐다).
    if body.strip() and body.strip()[-1] not in ".!?\"')]”’…~":
        errs.append("미완성(종결부호 없음)")

    # 리포트 수치는 '누가 제시했는지' 가 붙어야 인용이 된다.
    # 주어가 없으면 봇의 단정으로 읽힌다 (실측 #72: fatal 목표주가 단정).
    if re.search(r"적정가격|목표주가", body) and "리포트" in (facts or ""):
        firms = re.findall(r"([가-힣A-Za-z]{2,10}(?:증권|투자증권|자산운용|IR협의회))",
                           facts or "")
        if not any(f in body for f in firms):
            errs.append("출처없는목표주가")

    # 첫 문장이 수치로 시작하면 주어 없이 툭 던지는 글이 된다
    # (실측: 50건 중 22건. "9.43% 올랐습니다."로 시작해 종목명이 끝까지 안 나옴).
    # 파손도 여기서 걸린다 — "6.4배습니다.", "2.5배.", "663,000원."
    first = re.split(r"(?<=[.!?])\s", body.strip(), 1)[0] if body.strip() else ""
    if re.match(r"^[\d(]", first):
        errs.append("수치선두")
    if first and len(first) < 12:
        errs.append(f"선두파손({first[:12]})")
    # 종목명 뒤에 수치만 던진 문장도 길이는 충족하지만 문장으로 성립하지 않는다.
    # 실측: "다이나믹솔루션 160억원.", "에넥스 2,000,000주입니다."
    if re.fullmatch(r"[가-힣A-Za-z0-9&·]+\s+[\d,.]+\s*(?:원|억원|주|%)"
                    r"(?:입니다|이네요|네요)?[.!?]", first):
        errs.append("선두파손(명사+수치)")
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

    # 기준일을 정확히 알고 있는데 상대 날짜로 바꾸면 주말·휴장일에 거짓이 된다.
    if re.search(r"기준일:\s*\d{4}-\d{2}-\d{2}", facts or ""):
        rel = re.search(r"오늘|금일|어제|전일|전날|지난\s*거래일|지난거래일", body)
        if rel:
            errs.append(f"상대날짜({rel.group()})")

    # 종가를 출발가처럼 연결한 문장은 사실 두 개를 문법적으로 잘못 합친 것이다.
    # 실측: '9,380원에서 6.59% 올랐습니다' (9,380원은 출발가가 아니라 종가).
    if re.search(r"\d[\d,]*원에서\s*[-+]?\d+(?:\.\d+)?%\s*"
                 r"(?:올랐|내렸|상승|하락)", body):
        errs.append("종가표현오류")
    if re.search(r"[가-힣A-Za-z0-9]+[이가]\s*저가 대비[^.!?\n]{0,30}"
                 r"(?:변동폭|고저차)(?:이었|였습니다)", body):
        errs.append("문장성분오류")
    if re.search(r"저가\s*대비\s*\d+(?:\.\d+)?%\s*(?:범위|폭)(?:에서)?\s*움직", body):
        errs.append("장중범위표현오류")
    if re.search(r"저가\s*대비\s*\d+(?:\.\d+)?%\s*(?:높아|낮아).{0,15}변동성", body):
        errs.append("장중범위표현오류")
    if re.search(r"(?:외국인|기관)\s*순매매\s*수급\s*순위", body):
        errs.append("수급표현오류")

    # 두 날짜를 보고 모델이 직접 '약 6개월 만'을 계산한 실발송 문장. 기간이
    # facts 에 명시되지 않았다면 날짜 계산 역시 새 주장이다.
    period = re.search(r"(?:약\s*)?\d+\s*(?:개월|년)\s*만(?:에)?", body)
    if period and period.group() not in (facts or ""):
        errs.append("기간계산근거없음")
    if re.search(r"는데요\s*\.$", body.strip()):
        errs.append("미완성(연결어미 마무리)")

    # 용어 정의는 입력에 정의문을 제공했을 때만 허용한다. 모델 상식으로 만든 정의는
    # 맞더라도 이 글의 근거가 아니다.
    if fmt == "term_guide" and "용어 설명:" not in (facts or ""):
        errs.append("용어근거없음")

    if re.search(r"\d+\.\d{5,}\s*:\s*\d+\.\d{5,}", body):
        errs.append("비율표기미정리")

    # '결정' 공시를 이미 완료된 사건으로 바꾸지 않는다.
    if re.search(r"(?:증자|사채|주식).{0,20}결정", facts or "") and re.search(
            r"(?:주|사채)를\s*(?:발행|모집)(?:했습니다|했어요|하였습니다)", body):
        errs.append("결정공시완료형")

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
    errs += _needs_relation(body, facts)

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
    if require_question and not body.rstrip().endswith("?"):
        errs.append("질문마무리필수(poll)")

    return errs
