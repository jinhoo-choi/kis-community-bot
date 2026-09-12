"""통합 페르소나 접근자와 생성 프롬프트 조립.

실제 정의는 personas_v2.py가 소유하고, 이 모듈은 generator·filters가 공통으로 쓰는
길이·질문·주장 상한과 Persona × Angle 프롬프트를 연결한다.
"""
from src import angles, claims, rules
from src import personas_v2 as v2

# 통합형 페르소나 공용 사용자 프롬프트. 시스템 프롬프트는 personas_v2가 갖는다.
USER_PROMPT = """다음 자료를 바탕으로 커뮤니티 게시글 본문을 작성하세요.

[유형] {kind}
[종목] {stock}
[제목] {title}
[사실관계]
{facts}
"""


# filters/generator 가 v2 내부 구조를 몰라도 되도록 여기서 흡수한다.

def style_ids() -> dict:
    """슬롯별 페르소나 가중치 테이블."""
    return v2.SLOT_W


def no_question(style: str) -> bool:
    return v2.PERSONAS[style]["no_question"] if style in v2.PERSONAS else False


def len_bounds(style_or_len: str) -> tuple[int, int]:
    """(min, max) 글자 수. 페르소나가 길이를 갖는다."""
    p = v2.PERSONAS.get(style_or_len)
    return (p["min"], p["max"]) if p else (50, 300)


def claim_cap(style: str) -> int:
    """주장 상한."""
    return v2.claim_cap(style) if style in v2.PERSONAS else 4


def num_cap(style_or_len: str) -> int:
    """허용 숫자 개수. claim_cap 에서 파생시킨다.

    별도 상수로 두었더니 서로 어긋났다 — check_list 는 주장 5개를 허용하면서
    숫자는 3개로 막고 있었다. 주장 하나가 숫자를 둘 데려오기도 한다
    ('20일 평균의 3.2배' = 주장 1개, 숫자 2개).
    실측: 수치과다 6건 중 5건이 이 불일치에서 나왔다.
    """
    if style_or_len in v2.PERSONAS:
        return v2.claim_cap(style_or_len) + 1
    return 4


def build_messages_v2(item: dict, persona: str, angle: str = "") -> tuple[str, str]:
    p = v2.PERSONAS[persona]
    system = (v2.SYSTEM_PROMPT
              .replace("{persona_name}", p["name"])
              .replace("{persona_desc}", p["desc"])
              .replace("{sentences}", p["sentences"])
              # 프롬프트는 "250자 이내"만 말하고 하한이 없었다. 필터는
              # 페르소나별 하한(35~110자)으로 자르는데 지시는 정반대였다.
              # 실측 #77: 너무짧음 리젝이 raw 106자 -> clean 106자,
              # 즉 후처리 문제가 아니라 모델이 실제로 짧게 쓴 것이었다.
              .replace("{min}", str(p["min"]))
              .replace("{max}", str(p["max"]))
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
