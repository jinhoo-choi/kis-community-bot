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


def check(body: str, facts: str, fmt: str = None, angle: str = None) -> list[str]:
    """위반 사유 리스트 반환. 빈 리스트면 통과."""
    errs = []

    for name, pat in RULES:
        if re.search(pat, body, re.MULTILINE):
            errs.append(name)

    # 커뮤니티 글은 짧은 게 자연스럽다. 길면 오히려 AI 티가 난다.
    n = len(body.strip())
    if n < 50:
        errs.append(f"너무짧음({n}자)")
    if n > 300:
        errs.append(f"너무김({n}자)")

    # 환각 수치 탐지: 본문 숫자가 원본 facts 에 없으면 리젝
    # (연도/퍼센트 등 흔한 값은 화이트리스트)
    allow = _numbers(facts) | {"1", "2", "3", "4", "5", "10", "100"}
    hallu = [x for x in _numbers(body) if x not in allow and len(x) >= 3]
    if hallu:
        errs.append(f"미확인수치{hallu[:3]}")

    errs += _direction_errors(body, facts)

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
