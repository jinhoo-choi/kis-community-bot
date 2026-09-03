"""생성물 자동 검수. 하나라도 걸리면 리젝 → 1회 재생성 → 재실패 시 드랍."""
import re

RULES = [
    # (규칙명, 정규식)
    ("매매권유",   r"(매수|매도)\s*(추천|권장|하세요|하시죠)|담으|들어가야|비중\s*확대|손절하"),
    ("목표가단정", r"목표가.{0,6}(원|까지)|\d[\d,]*원\s*(간다|갑니다|돌파는\s*확정)"),
    ("1인칭경험",  r"(저는|나는|제가)[^.\n]{0,20}(샀|팔았|보유|물렸|익절|손절|수익)"),
    ("단정예측",   r"(반드시|무조건|확실히)\s*(오른|상승|하락|간다)"),
    ("마크다운",   r"^\s*[#*\-]\s|\*\*"),
    ("이모지",     r"[\U0001F300-\U0001FAFF\u2600-\u27BF]"),
]

NUM_RE = re.compile(r"\d[\d,]*\.?\d*")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUM_RE.findall(text)}


def check(body: str, facts: str) -> list[str]:
    """위반 사유 리스트 반환. 빈 리스트면 통과."""
    errs = []

    for name, pat in RULES:
        if re.search(pat, body, re.MULTILINE):
            errs.append(name)

    n = len(body.strip())
    if n < 100:
        errs.append(f"너무짧음({n}자)")
    if n > 400:
        errs.append(f"너무김({n}자)")

    # 환각 수치 탐지: 본문 숫자가 원본 facts 에 없으면 리젝
    # (연도/퍼센트 등 흔한 값은 화이트리스트)
    allow = _numbers(facts) | {"1", "2", "3", "4", "5", "10", "100"}
    hallu = [x for x in _numbers(body) if x not in allow and len(x) >= 3]
    if hallu:
        errs.append(f"미확인수치{hallu[:3]}")

    return errs
