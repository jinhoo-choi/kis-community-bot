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
    Rule(
        id="literary_style",
        writer='신문 기사체 종결("~했다", "~이다", "~한다", "~필요하다"). 커뮤니티 말투가 아니다',
        judge="'~했다/~이다/~한다' 같은 신문 기사체 종결 (커뮤니티 글은 존댓말 구어체)",
        regex=r"(했다|이다|한다|된다|있다|없다|아니다|필요하다|보인다)\s*[.。]",
        fatal=False,
    ),
    Rule(
        id="second_person",
        writer='독자를 "당신"으로 지칭 (한국 커뮤니티에서 시비조로 읽힌다)',
        judge="독자를 '당신'으로 지칭",
        regex=r"당신",
    ),
    Rule(
        id="other_broker_disparage",
        writer="타 증권사·애널리스트의 리포트나 의견을 조롱·폄하·의심하는 표현. "
               "리포트 내용은 사실로만 전달하고 평가하지 말 것",
        judge="타 증권사 리포트나 애널리스트 의견을 조롱·폄하하거나 신뢰성을 깎아내림",
        regex=r"(그림의 떡|믿을 게 못|의문이다|하나의 해석일 뿐|말장난|뻔한 소리)",
    ),
    Rule(
        id="external_referral",
        writer="타 증권사·외부 기관에 문의하거나 방문하라는 안내. "
               "자사 커뮤니티에서 외부로 보내는 문장은 쓰지 않는다",
        judge="타 증권사 등 외부로 문의·방문을 안내",
        regex=r"(증권|투자증권|자산운용)에\s*(직접\s*)?(문의|연락|확인)",
    ),
    Rule(
        id="meta_output",
        writer="자기소개나 작성 과정 노출 (\"안녕하세요 AI 작성 봇입니다\", 체크리스트, 자기검토)",
        judge="자기소개나 작성 과정·체크리스트가 본문에 노출됨",
        regex=r"(AI\s*(작성|생성)\s*(봇|도우미)|안녕하세요[,.]?\s*(저는|AI)|"
              r"Yes\.|체크리스트|^\s*\*\s)",
    ),
    Rule(
        id="direction_mismatch",
        writer='등락 방향을 잘못 쓰는 것. 상승에 "낙폭", 하락에 "급등" 같은 표현',
        judge="등락 방향이 사실관계와 반대 (상승인데 '낙폭', 하락인데 '급등' 등)",
        regex="",          # 문맥 판단이 필요해 정규식 대신 별도 검사(filters._direction_check)
    ),
    # ── AI 티를 만드는 습관들 ───────────────────────────────
    # 실제 산출물에서 반복 관찰된 패턴이다. 개별 문장은 자연스러운데
    # 50건이 전부 같은 마무리·같은 완충 표현을 쓰면 기계가 쓴 티가 난다.
    Rule(
        id="stock_ending",
        writer="상투적 마무리 문구. "
               '"지켜봐야 할 것 같습니다", "확인이 필요해 보입니다", "관심이 쏠리는 모습입니다", '
               '"주목됩니다", "귀추가 주목", "눈여겨볼 만합니다", "참고하시면 좋겠습니다". '
               "글을 억지로 마무리하려 하지 말고 마지막 사실에서 그냥 끊으세요",
        judge="상투적 마무리 문구로 글을 닫음 (지켜봐야/확인이 필요/주목됩니다 류)",
        regex=r"(지켜봐야|확인이 필요|확인해\s*볼 필요|관심이 (쏠리|모이)|주목(됩니다|받)|"
              r"귀추|눈여겨볼|참고하시|살펴볼 필요|체크해\s*볼)",
    ),
    Rule(
        id="hedge_overuse",
        writer='완충 표현 남발. "~것 같습니다", "~로 보입니다", "~인 듯합니다"는 '
               "글 전체에서 최대 한 번만 쓰세요. 나머지는 단정 대신 사실로 적으세요",
        judge="'~것 같습니다', '~로 보입니다' 류 완충 표현을 세 번 이상 사용",
        regex="",      # 횟수 기반이라 filters._hedge_check 로 별도 검사
        fatal=False,
    ),
    Rule(
        id="uniform_ending",
        writer="모든 문장을 같은 어미로 끝내지 마세요. "
               "'~네요'만 반복하거나 '~습니다'만 반복하면 기계가 쓴 티가 납니다",
        judge="문장 어미가 단조롭게 반복됨",
        regex="",      # filters._ending_variety 로 별도 검사
        fatal=False,
    ),
    Rule(
        id="textbook",
        writer="일반론 설명(\"유상증자는 일반적으로 ~입니다\" 같은 사전식 정의). "
               "이미 아는 사람들이 보는 곳이다",
        judge="사전식 일반론 설명으로 분량을 채움",
        regex=r"(은|는)\s*일반적으로\s",
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
