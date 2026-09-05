"""5개 톤 정의 + 톤 라우팅.

전제: 게시 시 'AI 작성' 뱃지가 부착된다.
따라서 위장은 하지 않되, 문체만 달리한다.
1인칭 투자경험(샀다/물렸다/보유중)은 모든 톤에서 금지 — AI는 거래 주체가 아니므로
뱃지가 있어도 허위 진술이 된다.
"""
import config
from src import angles, claims, rules
from src import personas_v2 as v2

# ── Voice (말투) 4종 ───────────────────────────────────────────
# 외부 검토 반영: 기존 10종에 말투/관점/구조가 뒤섞여 있었다.
# 말투만 남기고 관점은 angles.py, 구조는 FORMATS 로 분리했다.
# Voice 도 Angle 과 같은 방식으로 '계약'을 준다.
# 라벨만 주면 문체가 안 바뀐다 — Angle 에서 이미 확인한 실수를 Voice 에서 반복했다.
# (실측: Angle/Format/Length 를 고정하고 Voice 만 바꿔 뽑았더니 4건이 거의 같았다)
VOICES = {
    "dry": {
        "name": "드라이",
        "desc": "종결어미는 '~습니다' 로만 씁니다. '~네요', '~어요', '~는데요' 금지.\n"
                "형용사와 부사를 쓰지 않습니다. 수식 없이 사실만 놓습니다.\n"
                "해석 문장을 넣지 않습니다. 숫자와 사실로만 끝냅니다.",
    },
    "calm": {
        "name": "차분",
        "desc": "종결어미는 '~습니다' 와 '~인데요' 를 섞어 씁니다.\n"
                "반드시 한 문장은 '폭'이나 '흐름'에 대한 사실을 담습니다.\n"
                "예: 장중 고저 차이가 컸다 / 거래가 특정 구간에 몰렸다 / "
                "며칠째 같은 방향이 이어졌다.\n"
                "이때 쓰는 값은 입력에 적힌 것만 씁니다. 직접 계산하지 않습니다.\n"
                "이때도 등락 방향 어휘는 입력의 부호를 그대로 따릅니다. "
                "상승 건에 '낙폭', 하락 건에 '급등' 같은 반대 어휘를 쓰지 않습니다.\n"
                "전망이나 반대 시나리오를 넣지 않습니다.",
    },
    "light": {
        "name": "가벼움",
        "desc": "짧은 문장을 씁니다. 한 문장은 반드시 5어절 이하로 끊습니다.\n"
                "종결어미는 '~네요', '~어요' 위주로 씁니다.\n"
                "숫자를 나열하지 않습니다. 가장 눈에 띄는 하나만 쓰고 나머지는 버립니다.\n"
                "다만 쓰기로 한 숫자는 입력에 적힌 그대로 옮깁니다. "
                "만원·억 단위로 바꾸거나 반올림하거나 줄여 쓰지 않습니다.",
    },
    "explainer": {
        "name": "설명",
        "desc": "숫자나 용어 하나를 골라 그것이 무슨 뜻인지 한 문장으로 풀어 줍니다.\n"
                "예: '20일 평균의 5.8배'가 무슨 상황을 뜻하는지.\n"
                "단, 한 번만 풀어 줍니다. 여러 개를 설명하면 사전이 됩니다.\n"
                "종결어미는 '~습니다', '~예요' 를 섞습니다.",
    },
}

# 하위 호환 (기존 코드/테스트가 TONES 를 참조한다)
TONES = VOICES

# ── Format (구조) 6종 ─────────────────────────────────────────
# short_note 를 제거했다. 그건 구조가 아니라 '길이'다 (외부 검토 지적).
# 길이를 Format 에 섞어두니 reaction/context/amount 어떤 앵글이 뽑혀도
# "3~4문장 안에서 모델이 가장 익숙한 구조"로 수렴했다.
FORMATS = {
    "fact_read": {
        "no_question": True, "name": "사실→해석",
        "desc": "확인된 사실을 먼저 적고, 마지막 한 문장에서만 해석을 붙인다. "
                "질문으로 끝내지 말고 담담하게 마무리한다.",
    },
    "question": {
        "no_question": False, "name": "질문마무리",
        "desc": "사실을 적고 마지막 줄을 구체적인 질문으로 끝낸다. "
                "'어떻게 보시나요' 같은 막연한 질문은 쓰지 않는다. "
                "입력에 있는 항목을 지목해 묻는다.",
    },
    "two_sides": {
        "no_question": True, "name": "양면",
        "desc": "그렇게 볼 여지와 조심할 부분을 나란히 적는다. "
                "결론을 내지 않고 양쪽을 둔 채 끝낸다.",
    },
    "check_points": {
        "no_question": False, "name": "확인포인트",
        "desc": "앞으로 어떤 정형 숫자를 보면 되는지 구체적으로 짚는다. "
                "불릿이 아니라 문장으로 이어 쓴다. 막연한 '지켜봐야 한다'는 쓰지 않는다.",
    },
    "timeline": {
        "no_question": True, "name": "시간순",
        "desc": "언제 무엇이 있었는지 순서대로 적는다. 사실관계에 있는 날짜만 쓴다. "
                "마지막 시점으로 마무리한다.",
    },
    "lead_number": {
        "no_question": True, "name": "숫자리드",
        "desc": "숫자 하나를 첫 문장에 단독으로 던지고 시작한다. "
                "예: '20% 상승.' / '6,500원.' 그다음 문장에서 그 숫자의 정체를 밝힌다. "
                "회사명이나 배경 설명으로 시작하지 않는다.",
    },
}

# ── Length (길이) 3종 ─────────────────────────────────────────
# 구조와 분리한다. 같은 구조라도 길이가 다르면 다른 글이 되고,
# 길이가 같아도 구조가 다르면 다른 글이 되어야 한다.
# 하한을 반드시 명시한다. "120자 이내"만 주면 모델이 34자로 쓰고 필터 하한에 걸린다(실측).
# 상한은 필터 상한보다 낮게 둬서 경계에서 대량 리젝이 나지 않게 한다.
LENGTHS = {
    "short":  {"name": "짧게", "spec": "3문장. 70자 이상 120자 이하로 쓸 것",
               "min": 40, "max": 160},
    "medium": {"name": "보통", "spec": "4~5문장. 110자 이상 190자 이하로 쓸 것",
               "min": 80, "max": 260},
    "long":   {"name": "길게", "spec": "6~7문장. 180자 이상 270자 이하로 쓸 것",
               "min": 140, "max": 330},
}

# ── 조합 가중치 ────────────────────────────────────────────────
# 0=금지 1=어색 2=보통 3=추천. 가중 랜덤으로 뽑고 이미 쓴 값은 0.3배로 억제한다.
VOICE_W = {
    "disclosure": {"dry": 3, "calm": 3, "explainer": 3, "light": 1},
    "research":   {"dry": 3, "calm": 3, "explainer": 2, "light": 2},
    "flow":       {"light": 3, "dry": 3, "calm": 2, "explainer": 1},
    "policy":     {"explainer": 3, "calm": 3, "dry": 2, "light": 2},
    "poll":       {"explainer": 3, "light": 3, "calm": 2, "dry": 1},
    "theme":      {"explainer": 3, "calm": 3, "light": 2, "dry": 2},
}

FORMAT_W = {
    "disclosure": {"fact_read": 3, "check_points": 3, "lead_number": 3,
                   "timeline": 2, "two_sides": 1, "question": 2},
    "research":   {"fact_read": 3, "lead_number": 3, "two_sides": 2,
                   "check_points": 2, "question": 2, "timeline": 1},
    "flow":       {"lead_number": 3, "fact_read": 3, "question": 2,
                   "check_points": 2, "two_sides": 1, "timeline": 1},
    "policy":     {"two_sides": 3, "check_points": 3, "fact_read": 2,
                   "question": 2, "timeline": 2, "lead_number": 1},
    "poll":       {"question": 3, "two_sides": 3, "check_points": 2,
                   "fact_read": 1, "lead_number": 1, "timeline": 1},
    "theme":      {"two_sides": 3, "fact_read": 2, "question": 2,
                   "check_points": 2, "lead_number": 2, "timeline": 1},
}

LENGTH_W = {
    "disclosure": {"short": 2, "medium": 3, "long": 2},
    "research":   {"short": 3, "medium": 3, "long": 1},
    "flow":       {"short": 3, "medium": 3, "long": 1},
    "policy":     {"short": 2, "medium": 3, "long": 2},
    "poll":       {"short": 2, "medium": 3, "long": 1},
    "theme":      {"short": 2, "medium": 3, "long": 2},
}

SYSTEM_PROMPT = """당신은 한국투자증권 앱 커뮤니티에 게시될 글을 쓰는 AI 작성 봇입니다.
게시글에는 'AI 작성' 뱃지가 표기되므로 신분을 숨기지 않습니다.

[말투]
{voice_desc}

[이 글이 독자에게 알려줄 하나]
{angle_desc}

[글 구조]
{format_desc}

[분량]
{length_spec}
입력 데이터를 전부 쓰려 하지 마세요.
- 제공된 숫자 중 **2~3개만** 사용합니다. 어떤 숫자를 남길지는 위 [강조할 하나]가 정합니다.
- **숫자가 하나도 없는 문장을 반드시 하나 이상 넣으세요.** 분량이 남는다고
  숫자를 더 넣지 마세요. 관찰한 상황을 말로 적으면 됩니다.
  예: "거래가 하루 종일 몰렸습니다" / "장 초반부터 강했습니다" /
      "코스닥 거래대금 상위권에 올랐습니다"
- 숫자를 다 넣은 글은 표지 나열이지 게시글이 아닙니다.

[절대 금지]
{rule_block}

[기계처럼 쓰지 않기 — 실제로 자주 어기는 부분]
- 글을 억지로 마무리하지 마세요. 마지막 사실에서 그냥 끊는 편이 자연스럽습니다.
  "지켜봐야 할 것 같습니다", "확인이 필요해 보입니다", "관심이 쏠리는 모습입니다"는 쓰지 마세요.
- "~것 같습니다", "~로 보입니다"는 글 전체에서 한 번까지만 쓰세요.
- 문장 어미를 섞으세요. "~네요"만 네 번 반복하면 바로 티가 납니다.
- 숫자를 적을 때마다 해석을 붙이지 마세요. 숫자만 놓고 넘어가도 됩니다.
- 쓰기로 한 숫자는 입력에 적힌 그대로 옮기세요. 단위를 바꾸거나 반올림하면 값이 틀어집니다.
- 숫자를 더하거나 빼거나 나누지 마세요. 차이·비율이 필요하면 입력에 이미 계산되어 있습니다.
  입력에 없으면 그 값은 쓰지 않는 것이 맞습니다.
- 등락 방향 어휘는 입력의 부호를 그대로 따르세요.
  등락률이 양수면 상승·상승폭, 음수면 하락·낙폭입니다. 반대로 쓰면 사실 오류입니다.
- 용어 풀이는 글의 목적이 그것일 때만 하세요. 습관적으로 괄호를 열지 마세요.
- "다만", "한편", "그리고"는 글 전체에서 한 번까지.

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
                   angle: str = "", length: str = "medium") -> tuple[str, str]:
    """(system, user) 프롬프트 쌍 반환.

    금지 규칙은 src/rules.py 단일 소스에서 주입된다.
    심사 프롬프트(judge.py)도 같은 소스를 쓰므로 한쪽만 수정되어
    규칙이 무력화되는 일이 구조적으로 발생하지 않는다.
    """
    f = FORMATS[fmt]
    system = SYSTEM_PROMPT.format(
        voice_desc=VOICES[tone]["desc"],
        angle_desc=angles.contract(angle),
        format_desc=f["desc"],
        length_spec=LENGTHS.get(length, LENGTHS["medium"])["spec"],
        rule_block=rules.writer_block(),
    )
    if item.get("thin_facts"):
        system += "\n" + rules.THIN_FACTS_WARNING

    # 재생성이면 직전 실패 사유를 알려준다.
    # 같은 프롬프트로 다시 돌리면 같은 실수를 반복한다(실측: 수치 나열 3회 연속).
    if item.get("retry_hint"):
        system += (
            "\n[직전 시도에서 이런 문제가 있었습니다 — 이번엔 반드시 고치세요]\n"
            + item["retry_hint"]
        )
    user = USER_PROMPT.format(
        kind=item.get("kind", ""),
        stock=item.get("stock_name") or "해당 종목 없음(테마)",
        title=item.get("title", ""),
        # 고르지 않은 수치는 프롬프트에서 지운다. 보이면 쓴다.
        facts=claims.facts_view(item, num_cap(length), angle).strip()[:4000],
    )
    return system, user


# ── 모드 공통 접근자 ──────────────────────────────────────────
# filters/generator 가 v1/v2 를 몰라도 되도록 여기서 흡수한다.

def is_v2() -> bool:
    return config.PERSONA_MODE == "v2"


def style_ids() -> dict:
    """가중치 테이블. v1 은 Format, v2 는 Persona."""
    return v2.SLOT_W if is_v2() else FORMAT_W


def no_question(style: str) -> bool:
    if style in v2.PERSONAS:
        return v2.PERSONAS[style]["no_question"]
    return FORMATS.get(style, {}).get("no_question", False)


def len_bounds(style_or_len: str) -> tuple[int, int]:
    """(min, max) 글자 수. v2 는 페르소나가 길이를 갖는다."""
    if style_or_len in v2.PERSONAS:
        p = v2.PERSONAS[style_or_len]
        return p["min"], p["max"]
    spec = LENGTHS.get(style_or_len)
    return (spec["min"], spec["max"]) if spec else (50, 300)


def claim_cap(style: str) -> int:
    """주장 상한. v1 은 길이축 기준으로 근사한다."""
    if style in v2.PERSONAS:
        return v2.claim_cap(style)
    return {"short": 3, "medium": 4, "long": 5}.get(style, 4)


def num_cap(style_or_len: str) -> int:
    if style_or_len in v2.PERSONAS:
        return v2.PERSONAS[style_or_len]["num_cap"]
    return {"short": 3, "medium": 4, "long": 5}.get(style_or_len, 4)


def build_messages_v2(item: dict, persona: str, angle: str = "") -> tuple[str, str]:
    p = v2.PERSONAS[persona]
    system = (v2.SYSTEM_PROMPT
              .replace("{persona_name}", p["name"])
              .replace("{persona_desc}", p["desc"])
              .replace("{sentences}", p["sentences"])
              .replace("{num_cap}", str(p["num_cap"]))
              .replace("{angle_desc}", angles.contract(angle))
              .replace("{claim_block}",
                       claims.block(item, v2.claim_cap(persona), angle))
              .replace("{rule_block}", rules.writer_block()))
    if item.get("thin_facts"):
        system += "\n" + rules.THIN_FACTS_WARNING
    if item.get("retry_hint"):
        system += ("\n[직전 시도에서 이런 문제가 있었습니다 — 이번엔 반드시 고치세요]\n"
                   + item["retry_hint"])
    user = USER_PROMPT.format(
        kind=item.get("kind", ""),
        stock=item.get("stock_name") or "해당 종목 없음(테마)",
        title=item.get("title", ""),
        # 고르지 않은 수치는 프롬프트에서 지운다. 보이면 쓴다.
        facts=claims.facts_view(item, v2.claim_cap(persona), angle).strip()[:4000],
    )
    return system, user
