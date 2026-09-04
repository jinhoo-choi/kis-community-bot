"""5개 톤 정의 + 톤 라우팅.

전제: 게시 시 'AI 작성' 뱃지가 부착된다.
따라서 위장은 하지 않되, 문체만 달리한다.
1인칭 투자경험(샀다/물렸다/보유중)은 모든 톤에서 금지 — AI는 거래 주체가 아니므로
뱃지가 있어도 허위 진술이 된다.
"""
from src import angles, rules

# ── Voice (말투) 4종 ───────────────────────────────────────────
# 외부 검토 반영: 기존 10종에 말투/관점/구조가 뒤섞여 있었다.
# 말투만 남기고 관점은 angles.py, 구조는 FORMATS 로 분리했다.
VOICES = {
    "dry": {
        "name": "드라이",
        "desc": "형용사를 거의 쓰지 않는다. 사실과 숫자를 담담하게 놓는다. "
                "감탄·과장·수사 없음.",
    },
    "calm": {
        "name": "차분",
        "desc": "완충 표현을 쓴다. '~로 보입니다', '~일 가능성'. "
                "단정하지 않고 여지를 남긴다.",
    },
    "light": {
        "name": "가벼움",
        "desc": "짧은 문장을 연달아 쓴다. 리듬이 있다. 비유는 딱 하나까지. "
                "가벼운 자조는 되지만 특정 종목이나 투자자를 비하하지 않는다.",
    },
    "explainer": {
        "name": "설명",
        "desc": "초보 눈높이로 풀어 쓴다. 전문용어가 나오면 바로 괄호로 설명한다. "
                "'쉽게 말하면' 같은 연결어를 쓴다.",
    },
}

# 하위 호환 (기존 코드/테스트가 TONES 를 참조한다)
TONES = VOICES

# ── Format (구조) 7종 ─────────────────────────────────────────
# 길이·문장수는 여기에 귀속시킨다.
# 기존에는 Global 규칙이 '최소 5문장'을 요구하는데 short_note 는 '3~4문장'을
# 요구해서 동시에 만족 불가능한 조합이 있었다 (외부 검토 지적, 코드에서 확인됨).
FORMATS = {
    "fact_read": {
        "no_question": True, "name": "사실→해석", "sentences": "5~7문장",
        "desc": "확인된 사실을 먼저 쭉 적고, 마지막 한두 문장에서만 해석을 붙인다. "
                "질문으로 끝내지 말고 담담하게 마무리한다.",
    },
    "question": {
        "no_question": False, "name": "질문마무리", "sentences": "4~6문장",
        "desc": "사실을 적고 마지막 줄을 구체적인 질문으로 끝낸다. "
                "'어떻게 보시나요' 같은 막연한 질문은 쓰지 않는다.",
    },
    "two_sides": {
        "no_question": True, "name": "양면", "sentences": "5~7문장",
        "desc": "그렇게 볼 여지와 조심할 부분을 각각 두 문장씩 적는다. "
                "결론을 내지 않고 양쪽을 나란히 둔 채 끝낸다.",
    },
    "check_points": {
        "no_question": False, "name": "확인포인트", "sentences": "4~6문장",
        "desc": "앞으로 무엇을 더 봐야 하는지 구체적으로 두세 가지 짚는다. "
                "불릿이 아니라 문장으로 이어 쓴다.",
    },
    "timeline": {
        "no_question": True, "name": "시간순", "sentences": "4~6문장",
        "desc": "언제 무엇이 있었는지 순서대로 적는다. 사실관계에 있는 날짜만 쓴다. "
                "마지막 시점으로 마무리한다.",
    },
    "short_note": {
        "no_question": True, "name": "짧은메모", "sentences": "3~4문장",
        "desc": "아주 짧게 끝낸다. 배경 설명 없이 사실과 한 줄 감상만. "
                "질문으로 끝내지 않는다.",
    },
    "plain_summary": {
        "no_question": True, "name": "쉬운요약", "sentences": "4~6문장",
        "desc": "어려운 내용을 쉬운 말로 바꿔 설명한다. 용어가 나오면 바로 풀어 쓴다. "
                "요약으로 마무리한다.",
    },
}

# ── 조합 가중치 ────────────────────────────────────────────────
# 0=금지 1=어색 2=보통 3=추천.
# 허용/금지 이분법은 poll 처럼 조합이 6가지밖에 안 남는 문제를 만든다 (외부 검토 지적).
# 가중 랜덤으로 뽑되, 어울리지 않는 짝의 확률만 낮춘다.
VOICE_W = {
    "disclosure": {"dry": 3, "calm": 3, "explainer": 2, "light": 1},
    "research":   {"dry": 3, "calm": 3, "explainer": 2, "light": 1},
    "flow":       {"light": 3, "dry": 3, "calm": 2, "explainer": 2},
    "policy":     {"explainer": 3, "calm": 3, "dry": 2, "light": 2},
    "poll":       {"explainer": 3, "light": 3, "calm": 2, "dry": 1},
    "theme":      {"explainer": 3, "calm": 3, "light": 2, "dry": 2},
}

FORMAT_W = {
    "disclosure": {"fact_read": 3, "short_note": 2, "check_points": 3,
                   "timeline": 2, "plain_summary": 2, "two_sides": 1, "question": 1},
    "research":   {"fact_read": 3, "two_sides": 3, "check_points": 2,
                   "question": 2, "short_note": 2, "plain_summary": 1, "timeline": 1},
    "flow":       {"short_note": 2, "fact_read": 3, "question": 3,
                   "check_points": 2, "two_sides": 1, "plain_summary": 1, "timeline": 1},
    "policy":     {"plain_summary": 3, "two_sides": 3, "check_points": 2,
                   "question": 2, "fact_read": 2, "timeline": 2, "short_note": 1},
    "poll":       {"question": 3, "two_sides": 3, "check_points": 2,
                   "plain_summary": 1, "fact_read": 1, "short_note": 1, "timeline": 1},
    "theme":      {"plain_summary": 3, "two_sides": 2, "question": 2,
                   "fact_read": 2, "check_points": 2, "short_note": 1, "timeline": 1},
}

SYSTEM_PROMPT = """당신은 한국투자증권 앱 커뮤니티에 게시될 글을 쓰는 AI 작성 봇입니다.
게시글에는 'AI 작성' 뱃지가 표기되므로 신분을 숨기지 않습니다.

[말투]
{voice_desc}

[이번 글에서 강조할 것]
{angle_desc}

[글 구조]
{format_desc}
- 분량: {sentences}, 총 250자 이내

[절대 금지]
{rule_block}

[문체 — 가장 중요]
- 반드시 존댓말 구어체로 씁니다. "~습니다 / ~네요 / ~인데요 / ~같은데요"
- 신문 기사체 종결("~했다", "~이다", "~한다")은 절대 쓰지 않습니다. 커뮤니티 말투가 아닙니다.
- 독자를 "당신"으로 부르지 않습니다. 부르지 말고 그냥 질문만 던지세요.

[타 증권사 다루기]
- 타사 리포트는 "어디에서 무슨 제목으로 나왔다"까지만 사실로 전달합니다.
- 그 내용을 평가·의심·조롱하지 않습니다. "하나의 해석일 뿐", "그림의 떡" 같은 표현 금지.
- 외부 기관에 문의·확인하라는 안내를 하지 않습니다.

[규칙]
- 문장 길이를 불규칙하게. 최소 한 문장은 5어절 이하로 짧게 끊을 것
- 마무리 방식은 위 [글 구조]가 정한다. 구조가 질문을 요구하지 않으면 질문으로 끝내지 말 것
- 입력에 명시된 사실만 사용한다.
- 미확인 정보는 원칙적으로 언급하지 않는다. 판단에 꼭 필요할 때만 한 번 짚는다.
  "상세 수치는 확인되지 않았습니다" 류의 문장으로 분량을 채우지 말 것

[출력]
본문 텍스트만 출력. 제목, 설명, JSON, 따옴표 없이 본문만."""

USER_PROMPT = """다음 자료를 바탕으로 커뮤니티 게시글 본문을 작성하세요.

[유형] {kind}
[종목] {stock}
[제목] {title}
[사실관계]
{facts}
"""


def build_messages(item: dict, tone: str, fmt: str = "fact_read",
                   angle: str = "") -> tuple[str, str]:
    """(system, user) 프롬프트 쌍 반환.

    금지 규칙은 src/rules.py 단일 소스에서 주입된다.
    심사 프롬프트(judge.py)도 같은 소스를 쓰므로 한쪽만 수정되어
    규칙이 무력화되는 일이 구조적으로 발생하지 않는다.
    """
    f = FORMATS[fmt]
    system = SYSTEM_PROMPT.format(
        voice_desc=VOICES[tone]["desc"],
        angle_desc=angles.desc(angle),
        format_desc=f["desc"],
        sentences=f["sentences"],
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
