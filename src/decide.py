"""배포 판정. main() 안에 인라인으로 두지 않고 순수 함수로 분리한다.

리스크봇에서 decide_send_scope() 를 분리한 것과 같은 이유:
테스트가 실제 프로덕션 코드를 호출해야 의미가 있다.
main() 안에 있으면 테스트는 로직을 '복사'해서 검증하게 되고, 그건 아무것도 검증하지 못한다.
"""
from collections import Counter

import config


def decide_distribution(
    posts: list[dict],
    target: int = None,
    per_stock: int = None,
    per_kind_cap: dict = None,
    min_score: int = None,
) -> tuple[list[dict], list[dict]]:
    """(배포, 보류) 반환.

    순서가 중요하다. 상한 적용 전에 정렬해야 '좋은 글이 상한에 걸려 잘리는' 일이 없다.
      1) 치명 위반 제거
      2) 최소 점수 미달 제거
      3) 점수 내림차순 정렬
      4) 종목별 / 유형별 상한 적용
      5) target 컷
    """
    target = target if target is not None else config.TARGET_POSTS
    per_stock = per_stock if per_stock is not None else config.MAX_PER_STOCK
    # 소량 실행에서 슬롯 쿼터(1건)를 그대로 쓰면 멀쩡한 글을 버린다.
    # 그렇다고 상한을 완전히 풀면 한 유형이 전부 차지한다
    # (실측: 5건 요청에 특징주만 3건 — 구조가 똑같은 글이 연속으로 나감).
    # 유형당 max(2, 목표/3) 로 느슨한 상한만 둔다.
    if per_kind_cap is None and config.TARGET_POSTS < 30:
        cap = max(2, target // 3)
        per_kind_cap = {k: cap for k in config.SLOT_QUOTA} | {"theme": cap}
    # SLOT_QUOTA(생성 대상 배분)를 배포 상한으로 쓰면 안 된다. flow 가 120 이라
    # 목표 50건을 혼자 채운다 (실측: 배포 50건 중 flow 35건).
    per_kind_cap = per_kind_cap if per_kind_cap is not None else config.DIST_CAP
    min_score = min_score if min_score is not None else config.MIN_JUDGE_SCORE

    held = []
    pool = []

    for p in posts:
        s = p.get("score")
        if s is None:                       # 심사 불가 → 정규식 통과분으로 채택
            pool.append(p)
        elif s.get("fatal"):
            p["hold_reason"] = "fatal:" + ",".join(s["fatal"])[:60]
            held.append(p)
        elif s.get("fit") is not None and s["fit"] < config.MIN_FIT:
            p["hold_reason"] = f"커뮤니티적합성 {s['fit']}/5 {s.get('reason','')}"
            held.append(p)
        elif s.get("total", 0) < min_score:
            p["hold_reason"] = f"저점수 {s['total']}/20 {s.get('reason','')}"
            held.append(p)
        else:
            pool.append(p)

    pool.sort(key=lambda x: (x.get("score") or {}).get("total", 0), reverse=True)

    sent, per_s, per_k = [], Counter(), Counter()
    for p in pool:
        if len(sent) >= target:
            p["hold_reason"] = "정원초과"
            held.append(p)
            continue

        code = p.get("stock_code") or "_theme"
        if code != "_theme" and per_s[code] >= per_stock:
            p["hold_reason"] = f"종목상한({per_stock})"
            held.append(p)
            continue

        kind = p.get("kind", "")
        if kind in per_kind_cap and per_k[kind] >= per_kind_cap[kind]:
            p["hold_reason"] = f"유형상한({kind})"
            held.append(p)
            continue

        per_s[code] += 1
        per_k[kind] += 1
        sent.append(p)

    return sent, held


def temperature_for(item: dict) -> float:
    """슬롯별 temperature 차등.

    리스크봇에서 '대응방안 생성 시 temperature=0.0 이 아니면 수치 hallucination 발생'을
    실측했다. 다만 이 봇은 문체 다양성이 품질의 핵심이라 일괄 0.0 은 쓸 수 없다.
    → 수치가 본문에 직접 등장하는 슬롯만 낮추고, 서술 위주 슬롯은 높게 유지한다.
    """
    return config.TEMPERATURE_BY_KIND.get(item.get("kind", ""), config.TEMPERATURE)
