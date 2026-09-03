"""5개 톤 정의 + 톤 라우팅.

전제: 게시 시 'AI 작성' 뱃지가 부착된다.
따라서 위장은 하지 않되, 문체만 달리한다.
1인칭 투자경험(샀다/물렸다/보유중)은 모든 톤에서 금지 — AI는 거래 주체가 아니므로
뱃지가 있어도 허위 진술이 된다.
"""
from src import rules

TONES = {
    "pro": {
        "name": "전문",
        "desc": (
            "드라이하고 건조한 애널리스트 톤. 숫자와 사실을 앞에 두고 해석을 뒤에 붙인다. "
            "형용사는 최소한으로. 감탄이나 과장 표현을 쓰지 않는다."
        ),
    },
    "calm": {
        "name": "진중",
        "desc": (
            "차분하고 균형 잡힌 톤. 호재와 리스크를 함께 언급하되 리스크를 먼저 짚는다. "
            "단정하지 않고 '~로 보인다', '~일 가능성' 같은 완충 표현을 쓴다."
        ),
    },
    "light": {
        "name": "장난",
        "desc": (
            "가볍고 유쾌한 톤. 짧은 문장을 연달아 쓴다. 비유를 딱 하나만 넣는다. "
            "가벼운 자조 개그는 허용하되 특정 종목이나 투자자를 비하하지 않는다."
        ),
    },
    "buddy": {
        "name": "친근",
        "desc": (
            "초보 투자자 눈높이의 설명 톤. 전문용어가 나오면 괄호로 풀어 쓴다. "
            "'쉽게 말하면' 같은 연결어를 쓰고, 마지막은 반드시 질문으로 끝낸다."
        ),
    },
    "skeptic": {
        "name": "회의",
        "desc": (
            "역발상·회의적인 톤. 호재의 이면과 이미 주가에 반영된 부분을 짚는다. "
            "냉소적이되 조롱하지 않는다. 문장은 짧고 건조하게."
        ),
    },
}

# 슬롯별 허용 톤 (일반인 위장 페르소나였던 newbie/veteran/bear 는 제거됨)
SLOT_TONES = {
    "disclosure": ["pro", "calm"],
    "research":   ["pro", "calm", "skeptic"],
    "flow":       ["light", "calm", "skeptic"],
    "policy":     ["calm", "buddy"],
    "poll":       ["buddy"],
}

SYSTEM_PROMPT = """당신은 한국투자증권 앱 커뮤니티에 게시될 글을 쓰는 AI 작성 봇입니다.
게시글에는 'AI 작성' 뱃지가 표기되므로 신분을 숨기지 않습니다.

[스타일]
{tone_desc}

[절대 금지]
{rule_block}

[규칙]
- 8~10줄, 총 150~300자
- 문장 길이를 불규칙하게. 최소 한 문장은 5어절 이하로 짧게 끊을 것
- 마지막 줄은 독자에게 던지는 열린 질문으로 마무리
- 입력에 명시된 사실만 사용. 불확실하면 "확인되지 않았다"고 적을 것

[출력]
본문 텍스트만 출력. 제목, 설명, JSON, 따옴표 없이 본문만."""

USER_PROMPT = """다음 자료를 바탕으로 커뮤니티 게시글 본문을 작성하세요.

[유형] {kind}
[종목] {stock}
[제목] {title}
[사실관계]
{facts}
"""


def build_messages(item: dict, tone: str) -> tuple[str, str]:
    """(system, user) 프롬프트 쌍 반환.

    금지 규칙은 src/rules.py 단일 소스에서 주입된다.
    심사 프롬프트(judge.py)도 같은 소스를 쓰므로 한쪽만 수정되어
    규칙이 무력화되는 일이 구조적으로 발생하지 않는다.
    """
    system = SYSTEM_PROMPT.format(
        tone_desc=TONES[tone]["desc"],
        rule_block=rules.writer_block(),
    )
    if item.get("thin_facts"):
        system += "\n" + rules.THIN_FACTS_WARNING
    user = USER_PROMPT.format(
        kind=item.get("kind", ""),
        stock=item.get("stock_name") or "해당 종목 없음(테마)",
        title=item.get("title", ""),
        facts=item.get("facts", "").strip()[:4000],
    )
    return system, user
