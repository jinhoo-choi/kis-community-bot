"""통합 페르소나 접근자와 생성 프롬프트 조립.

실제 정의는 personas_v2.py가 소유하고, 이 모듈은 generator·filters가 공통으로 쓰는
길이·질문·주장 상한과 Persona × Angle 프롬프트를 연결한다.
"""
import re

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


# 강조 기호는 '써도 된다' 고 허용만 하면 모델이 기본값(안 씀)을 유지한다.
# 실측 #135: 허용 후 5건 중 느낌표 0건. 그래서 항목 단위로 배정했는데(#41),
# 무작위 배정이라 강조할 게 없는 글에도 붙었다.
# 실측 #137: '장중 고저 차이가 저가 대비 6.1%였습니다!' — 강조할 만한 사실이 아니다.
# 이제 느낌표는 사실이 두드러질 때만 배정하고, 어느 사실에 붙일지 지정한다.
# 기준(캐시 특징주 199건 보정): 거래량 5배↑ / 등락 크기 4배↑ / 등락률 25%↑
# → 16%. 당사 커뮤니티 상위글 느낌표 사용률 19% 에 근접한다.
_SALIENT = (
    (r"거래량: 20일 평균의 ([\d.]+)배", 5.0, "거래량"),
    (r"등락 크기: 최근 20거래일 평균 등락폭의 ([\d.]+)배", 4.0, "등락 크기"),
    (r"(?m)^등락률: -?([\d.]+)%", 25.0, "등락률"),
    (r"최근 매출액 대비: ([\d.]+)%", 10.0, "매출액 대비 계약 규모"),
)


def salient_fact(item: dict) -> str:
    """강조할 만한 사실의 이름. 없으면 빈 문자열."""
    f = item.get("facts", "")
    for pat, th, label in _SALIENT:
        m = re.search(pat, f)
        if m and float(m.group(1)) >= th:
            return label
    return ""


def accent_for(item: dict) -> str:
    """강조 배정. 느낌표는 두드러진 사실이 있을 때만, ㅎㅎ 는 무작위 4%.

    ㅎㅎ 는 주가가 내린 글에는 배정하지 않는다(필터도 막는다 — 조롱으로 읽힌다).
    같은 항목은 재시도해도 같은 배정을 받는다.
    """
    import zlib
    if salient_fact(item):
        return "bang"
    h = zlib.crc32(str(item.get("id", "")).encode()) % 100
    pct = re.search(r"(?m)^등락률[:\s]*(-?[\d.]+)\s*%", item.get("facts", ""))
    down = bool(pct and pct.group(1).startswith("-"))
    if h < 4 and not down:
        return "hehe"
    return ""


# '~했습니다' 는 나쁜 어미가 아니다. 당사 커뮤니티에서 쓰면 좋아요 1.22~1.46배다.
# 문제는 봇 글 52% 에 들어가 모든 글이 같은 끝맺음을 갖는 것(상위글 1~2.4%).
# 어미 고정 지시를 빼도(#40) 사실 서술의 기본 과거형이라 줄지 않았다(#137: 52%).
# 커뮤니티에서 흔하고(26%) 반응도 좋은(1.06~1.11배) 명사형 종결을 일부 글에
# 배정해 끝맺음을 섞는다. 없애는 게 아니라 섞는 것이다.
NOUN_ENDING_SHARE = 30


def noun_ending_for(item: dict) -> bool:
    import zlib
    return zlib.crc32(("end:" + str(item.get("id", ""))).encode()) % 100 < NOUN_ENDING_SHARE


def build_messages_v2(item: dict, persona: str, angle: str = "") -> tuple[list, str]:
    """system 을 [고정 블록(캐시), 가변 블록] 으로 돌려준다.

    고정 블록은 모든 호출에 동일하므로 캐시 경계를 여기에 둔다. 매 호출 달라지는
    페르소나·앵글·주장은 경계 뒤에 붙인다 — 경계 앞에 두면 접두부 해시가 매번
    달라져 적중이 영영 없다.
    Haiku 4.5 는 4,096토큰 이상이어야 캐시된다(고정부 약 4,300토큰).
    미달이면 오류 없이 캐시만 되지 않는다.
    """
    p = v2.PERSONAS[persona]
    static = v2.STATIC_PROMPT.replace("{rule_block}", rules.writer_block())
    system = (v2.DYNAMIC_PROMPT
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
                       claims.block(item, v2.claim_cap(persona), angle)))
    accent = accent_for(item)
    if accent == "bang":
        system += ("\n[이번 글의 강조] '" + salient_fact(item) +
                   "' 을 말하는 문장 끝에만 느낌표를 한 번 붙입니다.")
    elif accent == "hehe":
        system += "\n[이번 글의 강조] 문장 끝 한 곳에 'ㅎㅎ' 를 한 번 붙여 가볍게 씁니다."
    if noun_ending_for(item):
        system += ("\n[이번 글의 끝맺음] 문장 하나는 '거래량은 20일 평균의 25배.' 처럼 "
                   "명사로 끝냅니다. 모든 문장을 '~했습니다' 로 끝내지 않습니다.")
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
    return [{"type": "text", "text": static,
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": system}], user
