"""금지 규칙 단일 소스(Single Source of Truth).

리스크봇 운영에서 filter_prompt.txt 를 이중 관리하다 한쪽만 수정해
규칙이 무력화된 사례가 있었다. 같은 실수를 구조적으로 막기 위해
'작성 프롬프트 / 심사 프롬프트 / 정규식 필터' 세 곳이 모두 이 파일에서 파생된다.

규칙을 추가할 때 여기만 고치면 3곳에 동시 반영된다.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Rule:
    id: str
    writer: str          # 작성 프롬프트에 들어갈 금지 문구
    judge: str           # 심사 프롬프트에 들어갈 판정 기준
    regex: str = ""      # 정규식 자동 탐지 (없으면 모델 심사에만 의존)
    fatal: bool = True   # 위반 시 즉시 탈락 여부


RULES: list[Rule] = [
    Rule(
        id="first_person_trade",
        writer="1인칭 투자 경험 서술 (샀다 / 팔았다 / 보유 중 / 물렸다 / 익절 / 손절)",
        judge="'제가 샀다' 등 1인칭 거래 경험 서술 — AI는 거래 주체가 아니므로 허위 진술",
        regex=r"(저는|나는|제가)[^.\n]{0,20}(샀|팔았|보유|물렸|익절|손절|수익)",
    ),
    Rule(
        id="trade_advice",
        writer="매수·매도 권유 표현 (담아라, 들어가라, 비중확대, 손절하세요 등)",
        judge="매수 또는 매도를 권유하는 표현",
        regex=r"(매수|매도)\s*(추천|권장|하세요|하시죠)|담으|들어가야|비중\s*확대|손절하",
    ),
    Rule(
        id="price_target",
        writer='목표주가 단정 ("○○원 간다", "○○원까지")',
        judge="목표주가를 단정하는 표현",
        regex=r"목표가.{0,6}(원|까지)|\d[\d,]*원\s*(간다|갑니다|돌파는\s*확정)",
    ),
    Rule(
        id="hallucinated_number",
        writer="입력 사실관계에 없는 숫자·날짜·기업명 생성. 수치를 합산하거나 계산하지 말 것",
        judge="제공된 사실관계에 없는 구체적 수치나 날짜를 지어냄",
        regex="",          # 숫자 집합 diff 로 별도 검사 (filters._number_check)
    ),
    Rule(
        id="certainty",
        writer='단정적 예측 ("반드시 오른다", "무조건 상승")',
        judge="근거 없이 상승·하락을 단정",
        regex=r"(반드시|무조건|확실히)\s*(오른|상승|하락|간다)",
        fatal=False,
    ),
    Rule(
        id="disparage",
        writer="특정 종목·기업·투자자에 대한 조롱이나 비방",
        judge="특정 종목이나 투자자를 조롱·비방",
        regex="",
    ),
    Rule(
        id="format",
        writer="마크다운, 소제목, 불릿, 이모지, 해시태그",
        judge="마크다운·이모지 등 커뮤니티에 어울리지 않는 서식",
        regex=r"^\s*[#*\-]\s|\*\*|[\U0001F300-\U0001FAFF\u2600-\u27BF]",
        fatal=False,
    ),
]


def writer_block() -> str:
    return "\n".join(f"- {r.writer}" for r in RULES)


def judge_block() -> str:
    fatal = [r for r in RULES if r.fatal]
    return "\n".join(f"- {r.judge}" for r in fatal)


def regex_rules() -> list[tuple[str, str]]:
    return [(r.id, r.regex) for r in RULES if r.regex]


# 사실관계가 빈약할 때 프롬프트에 주입되는 경고.
# 리스크봇의 _body_failed 플래그와 같은 역할 — 정보가 없으면 '없다고 쓰게' 만든다.
THIN_FACTS_WARNING = """
[주의] 이 항목은 배경 정보 확보에 실패했습니다.
제목에 드러난 사실 외에는 아는 것이 없는 상태로 작성하세요.
- 회사의 사업 내용, 실적, 업황을 추측해서 쓰지 마세요.
- 공시/리포트의 배경이나 의도를 추정하지 마세요.
- 짧아도 됩니다. 확인된 것만 쓰고, 나머지는 질문으로 넘기세요.
"""
