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


def _direction_errors(body: str, facts: str) -> list[str]:
    """등락 방향 오용 검사.

    실측: +23.74% 상승 건에 "이 정도 낙폭이면" 이라고 써놓고 심사 19점을 받았다.
    facts 의 등락률 부호로 방향을 확정하고, 반대 방향 어휘가 나오면 리젝한다.
    """
    m = re.search(r"등락률[:\s]*([-+]?\d+(?:\.\d+)?)\s*%", facts or "")
    if not m:
        return []
    up = float(m.group(1)) > 0
    wrong = _UP.search(body) if up else _DOWN.search(body)
    return [f"방향오용({wrong.group()})"] if wrong else []


_HEDGE = re.compile(r"것 같|로 보입니다|인 듯|듯합니다|듯요|보이네요|것으로 보")
_ENDING = re.compile(r"([가-힣]{1,4})[.!?]")


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


def _number_overuse(body: str) -> list[str]:
    """숫자 나열. 제공된 수치를 전부 소비하면 표지 나열이 된다.
    실측: Voice 를 바꿔도 4건 모두 같은 6개 숫자를 같은 순서로 썼다."""
    nums = {n.replace(",", "") for n in NUM_RE.findall(body) if len(n.replace(",", "")) >= 2}
    return [f"수치과다({len(nums)}개)"] if len(nums) > 4 else []


def check(body: str, facts: str, fmt: str = None, angle: str = None,
          length: str = None) -> list[str]:
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
        from src.personas import LENGTHS
        spec = LENGTHS.get(length)
        if spec:
            lo, hi = spec["min"], spec["max"]
    if n < lo:
        errs.append(f"너무짧음({n}자/{length or '-'})")
    if n > hi:
        errs.append(f"너무김({n}자/{length or '-'})")

    # 환각 수치 탐지: 본문 숫자가 원본 facts 에 없으면 리젝
    # (연도/퍼센트 등 흔한 값은 화이트리스트)
    allow = _numbers(facts) | {"1", "2", "3", "4", "5", "10", "100"}
    hallu = [x for x in _numbers(body) if x not in allow and len(x) >= 3]
    if hallu:
        errs.append(f"미확인수치{hallu[:3]}")

    errs += _direction_errors(body, facts)
    errs += _hedge_errors(body)
    errs += _number_overuse(body)
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
        from src.personas import FORMATS
        if FORMATS.get(fmt, {}).get("no_question") and body.rstrip().endswith("?"):
            errs.append(f"질문마무리금지({fmt})")

    return errs
