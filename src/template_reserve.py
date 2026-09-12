"""API 장애에도 확정 시세만으로 준비하는 flow 문장틀 reserve.

문장틀은 LLM 심사를 우회하는 일반 예외가 아니다. 이 모듈이 원본 facts와
template_id로 본문을 다시 만들고, 그 결과가 정확히 일치하는 경우에만
결정형 필터를 통과할 수 있다. 호출자가 넣은 ``template_validated`` 같은
boolean은 신뢰하지 않는다.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from src import claims, facts, filters


MODEL = "template_v1"
RESERVE_EXTRA = 15
NORMAL_SHARE = 0.30
APPROVED_TONES = ("data_focus", "fact_note", "brief_report")
APPROVED_RELATIONS = ("vol_ratio", "ret5", "close_pos")


@dataclass(frozen=True)
class _Template:
    tone: str
    change_variant: int
    close_variant: int
    relation_variant: int
    relation_order: tuple[str, str, str]
    relation_first: bool = False


# 65건 reserve에서 동일 문장틀을 세 번 이하로 쓰려면 ceil(65/3)=22종이
# 필요하다. 각 항목은 문장 순서·표현·관계값 선택 순서가 다른 독립 계약이다.
TEMPLATES: tuple[_Template, ...] = (
    _Template("data_focus", 0, 0, 0, ("vol_ratio", "ret5", "close_pos")),
    _Template("fact_note", 1, 1, 1, ("ret5", "close_pos", "vol_ratio"), True),
    _Template("brief_report", 2, 2, 2, ("close_pos", "vol_ratio", "ret5")),
    _Template("data_focus", 3, 3, 3, ("vol_ratio", "close_pos", "ret5"), True),
    _Template("fact_note", 4, 4, 4, ("ret5", "vol_ratio", "close_pos")),
    _Template("brief_report", 5, 5, 5, ("close_pos", "ret5", "vol_ratio"), True),
    _Template("data_focus", 6, 6, 6, ("vol_ratio", "ret5", "close_pos")),
    _Template("fact_note", 7, 7, 7, ("ret5", "close_pos", "vol_ratio"), True),
    _Template("brief_report", 0, 2, 3, ("close_pos", "vol_ratio", "ret5")),
    _Template("data_focus", 1, 3, 4, ("vol_ratio", "close_pos", "ret5"), True),
    _Template("fact_note", 2, 4, 5, ("ret5", "vol_ratio", "close_pos")),
    _Template("brief_report", 3, 5, 6, ("close_pos", "ret5", "vol_ratio"), True),
    _Template("data_focus", 4, 6, 7, ("vol_ratio", "ret5", "close_pos")),
    _Template("fact_note", 5, 7, 0, ("ret5", "close_pos", "vol_ratio"), True),
    _Template("brief_report", 6, 0, 1, ("close_pos", "vol_ratio", "ret5")),
    _Template("data_focus", 7, 1, 2, ("vol_ratio", "close_pos", "ret5"), True),
    _Template("fact_note", 0, 4, 6, ("ret5", "vol_ratio", "close_pos")),
    _Template("brief_report", 1, 5, 7, ("close_pos", "ret5", "vol_ratio"), True),
    _Template("data_focus", 2, 6, 0, ("vol_ratio", "ret5", "close_pos")),
    _Template("fact_note", 3, 7, 1, ("ret5", "close_pos", "vol_ratio"), True),
    _Template("brief_report", 4, 0, 2, ("close_pos", "vol_ratio", "ret5")),
    _Template("data_focus", 5, 1, 3, ("vol_ratio", "close_pos", "ret5"), True),
)


_CHANGE = (
    "{subject} 직전 거래일보다 {pct}% {direction}해 거래를 마쳤습니다.",
    "{name} 주가는 직전 거래일 종가보다 {pct}% {direction}했습니다.",
    "{subject} 직전 거래일보다 {pct}% {direction}하며 장을 마쳤네요.",
    "{subject} 직전 거래일보다 {pct}% {direction}해 거래를 마감했습니다.",
    "{subject} 직전 거래일보다 {pct}% {direction}한 채 거래를 마쳤어요.",
    "{subject} 직전 거래일 종가 대비 {pct}% {direction}했습니다.",
    "{name} 주가는 직전 거래일보다 {pct}% {direction}했습니다.",
    "{subject} 직전 거래일 종가보다 {pct}% {direction}하며 마감했네요.",
)

_CLOSE = (
    "이날 마감가는 {close}원이었는데요.",
    "마감 가격은 {close}원이었습니다.",
    "마감 가격은 {close}원이었어요.",
    "장 마감 가격은 {close}원이었어요.",
    "이날 마감가는 {close}원이었습니다.",
    "장은 {close}원에 끝났네요.",
    "마감 가격은 {close}원이었습니다.",
    "이날 종가는 {close}원이었네요.",
)


def _relation_sentence(kind: str, value: str, direction: str,
                       direction_noun: str, variant: int) -> str:
    choices = {
        "vol_ratio": (
            "거래량은 20일 평균의 {v}배였습니다.",
            "거래량은 20일 평균의 {v}배 수준이었네요.",
            "20일 평균과 비교한 거래량은 {v}배 수준이었어요.",
            "거래량은 20일 평균 대비 {v}배를 기록했네요.",
            "20일 평균 대비 거래량은 {v}배였네요.",
            "거래량은 20일 평균의 {v}배 수준이었습니다.",
            "20일 평균과 비교하면 거래량은 {v}배였네요.",
            "거래량은 20일 평균 대비 {v}배 수준이었어요.",
        ),
        "ret5": (
            "5거래일 누적으로는 {v}% {d}했습니다.",
            "5거래일 누적으로는 {v}% {d}했네요.",
            "5거래일 동안 누적 {v}% {d}했어요.",
            "5거래일 누적 등락률은 {v}% {dn}입니다.",
            "5거래일 기준으로 누적 {v}% {d}했네요.",
            "5거래일 전체로 보면 {v}% {d}했습니다.",
            "5거래일 누적으로 {v}% {d}한 것으로 집계됐네요.",
            "5거래일 누적으로 보면 {v}% {d}했어요.",
        ),
        "close_pos": (
            "종가는 장중 고가 대비 {v}% 낮은 수준이었습니다.",
            "종가는 장중 고가 대비 {v}% 낮았네요.",
            "종가는 장중 고가 대비 {v}% 낮은 수준이었어요.",
            "마감가는 장중 고가 대비 {v}% 낮았네요.",
            "종가는 장중 고가 대비 {v}% 낮은 수준이었네요.",
            "종가는 장중 고가 대비 {v}% 낮은 수준이었습니다.",
            "종가는 장중 고가 대비 {v}% 낮게 마쳤네요.",
            "마감 가격은 장중 고가 대비 {v}% 낮았어요.",
        ),
    }
    return choices[kind][variant % len(choices[kind])].format(
        v=value, d=direction, dn=direction_noun)


def _parse(item: dict, spec: _Template) -> dict | None:
    if item.get("kind") != "flow" or not item.get("stock_code") or not item.get("stock_name"):
        return None
    raw = item.get("facts", "")
    chg = re.search(r"(?m)^등락률:\s*([-+]?\d+(?:\.\d+)?)%\s*$", raw)
    close = re.search(r"(?m)^종가:\s*([\d,]+)원\s*$", raw)
    if not chg or not close:
        return None
    pct = float(chg.group(1))
    if pct == 0:
        return None

    available = {}
    m = re.search(r"(?m)^· 거래량:\s*20일 평균의\s*([\d.]+)배\s*$", raw)
    if m:
        available["vol_ratio"] = m.group(1)
    m = re.search(r"(?m)^· 5거래일 누적 등락률:\s*([-+]?\d+(?:\.\d+)?)%\s*$", raw)
    if m:
        available["ret5"] = m.group(1)
    m = re.search(r"(?m)^· 마감 위치:\s*장중 고가 대비\s*([\d.]+)% 낮은 수준\s*$", raw)
    if m and float(m.group(1)) > 0:
        available["close_pos"] = m.group(1)
    relation = next((name for name in spec.relation_order if name in available), None)
    if not relation:
        return None

    rel_pct = float(available[relation]) if relation == "ret5" else pct
    name = item["stock_name"]
    last = ord(name[-1]) if name else 0
    has_batchim = 0xAC00 <= last <= 0xD7A3 and (last - 0xAC00) % 28 != 0
    return {
        "name": name,
        "subject": name + ("은" if has_batchim else "는"),
        "pct": chg.group(1).lstrip("+-"),
        "close": close.group(1),
        "direction": "상승" if pct > 0 else "하락",
        "direction_noun": "상승" if pct > 0 else "하락",
        "relation": relation,
        "relation_value": (available[relation].lstrip("+-") if relation == "ret5"
                           else available[relation]),
        "relation_direction": "상승" if rel_pct > 0 else "하락",
        "relation_direction_noun": "상승" if rel_pct > 0 else "하락",
    }


def template_id(index: int) -> str:
    return f"flow_template_{index + 1:02d}"


def render(item: dict, tid: str) -> dict | None:
    """원본 flow 항목을 지정 문장틀로 렌더링한다."""
    try:
        index = int(tid.rsplit("_", 1)[1]) - 1
        spec = TEMPLATES[index]
    except (ValueError, IndexError):
        return None
    values = _parse(item, spec)
    if not values:
        return None

    change = _CHANGE[spec.change_variant].format(**values)
    close = _CLOSE[spec.close_variant].format(**values)
    relation = _relation_sentence(
        values["relation"], values["relation_value"],
        values["relation_direction"], values["relation_direction_noun"],
        spec.relation_variant,
    )
    body = " ".join((change, relation, close) if spec.relation_first
                    else (change, close, relation))
    return {
        **item,
        "tone": spec.tone,
        "fmt": spec.tone,
        # LLM의 claim 선택용 Angle과 달리 문장틀은 아래 검증기가 정확한 세
        # 주장만 허용한다. 통계에는 별도 template_relation을 남긴다.
        "angle": "",
        "length": spec.tone,
        "body": body,
        "provider": "template",
        "model": MODEL,
        "template_id": tid,
        "template_relation": values["relation"],
    }


def validation_errors(post: dict) -> list[str]:
    """본문을 원본부터 재구성해 문장틀 우회를 차단한다."""
    errs = []
    tid = post.get("template_id", "")
    expected = render(post, tid)
    if not expected:
        return ["template_contract:원본또는ID불일치"]
    for key in ("body", "tone", "fmt", "length", "template_relation"):
        if post.get(key) != expected.get(key):
            errs.append(f"template_contract:{key}불일치")
    if post.get("provider") != "template" or post.get("model") != MODEL:
        errs.append("template_contract:생성경로불일치")

    body = post.get("body", "").strip()
    sentence_count = len(re.findall(r"[.!?](?:\s|$)", body))
    if not 3 <= sentence_count <= 4:
        errs.append(f"template_contract:문장수{sentence_count}")
    if not 70 <= len(body) <= 200:
        errs.append(f"template_contract:길이{len(body)}")
    if post.get("tone") not in APPROVED_TONES:
        errs.append("template_contract:페르소나")
    if post.get("template_relation") not in APPROVED_RELATIONS:
        errs.append("template_contract:관계값")

    # Angle 기반 LLM claim 선택은 쓰지 않는다. 대신 정확한 renderer 일치와
    # 최대 3개 claim, 근거 없는 숫자 없음을 직접 확인한다.
    item = {**post, "angle": ""}
    errs.extend(f"template_filter:{e}" for e in filters.check(
        body, post.get("facts", ""), post.get("fmt"), None,
        post.get("length")))
    errs.extend(f"template_grounding:{e}" for e in
                claims.grounding_errors(body, item, 3))
    if not facts.uses_derived(body, post.get("facts", "")):
        errs.append("template_contract:결합사실미사용")
    return list(dict.fromkeys(errs))


def is_valid(post: dict) -> bool:
    return not validation_errors(post)


def build(items: list[dict], target: int) -> list[dict]:
    """검증된 reserve를 최대 target건 만든다.

    동일 template_id 3건, 동일 말미 20자 2건을 builder와 decide 양쪽에서
    각각 확인한다. 한 항목에는 문장틀 하나만 배정한다.
    """
    out = []
    per_template: Counter[str] = Counter()
    endings: Counter[str] = Counter()
    seen_items = set()
    for item_index, item in enumerate(items):
        if len(out) >= target:
            break
        source_id = item.get("id")
        if not source_id or source_id in seen_items:
            continue
        for offset in range(len(TEMPLATES)):
            index = (item_index + offset) % len(TEMPLATES)
            tid = template_id(index)
            if per_template[tid] >= 3:
                continue
            post = render(item, tid)
            if not post or validation_errors(post):
                continue
            ending = post["body"][-20:]
            if ending and endings[ending] >= 2:
                continue
            out.append(post)
            per_template[tid] += 1
            endings[ending] += 1
            seen_items.add(source_id)
            break
    return out


def normal_template_limit(target: int) -> int:
    return min(15, max(1, round(target * NORMAL_SHARE)))


def normal_llm_target(target: int) -> int:
    return max(0, target - normal_template_limit(target))


def guarantee_tone_limit(target: int) -> int:
    return max(1, math.ceil(target / len(APPROVED_TONES)))
