"""실행 통계 기록 (run_stats.jsonl).

리스크봇에서 폴백률·처리량을 기록해 튜닝 근거로 삼은 패턴을 이식.
현재 이 봇은 "어제보다 나아졌는지"를 판단할 근거가 전혀 없다.

한 줄 = 한 실행. 누적되면 다음을 볼 수 있다.
  - 프로바이더별 평균 심사점수 → WRITER_RATIO 조정 근거
  - 게이트 차단 사유 분포   → 수집 쿼리 개선 근거
  - enrich 성공률          → 보강 단계 유지 여부 판단
  - 모델 폴백 발생          → 모델 은퇴 조기 감지
"""
import json
import os
from collections import Counter
from datetime import datetime

from config import KST

PATH = "data/run_stats.jsonl"


def record(**kw) -> dict:
    row = {"ts": datetime.now(KST).isoformat(timespec="seconds"), **kw}
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def _reject_reasons() -> dict:
    """정규식 필터에서 떨어진 건들의 사유 분포. 유형별로도 나눈다."""
    from src.generator import REJECTED
    c = Counter()
    for p in REJECTED:
        for e in p.get("reject_errs", []):
            c[f"{p.get('kind', '?')}:{e.split('(')[0]}"] += 1
    return dict(c.most_common(25))


def _axis_avg(posts: list[dict]) -> dict:
    """심사 항목별 평균. 총점만 보면 어느 축이 병목인지 모른다."""
    # judge 는 항목 점수를 score 최상위에 평평하게 넣는다 (중첩 아님)
    axes = ("factual", "useful", "natural", "compliant", "gain", "fit")
    acc, n = {}, {}
    for p in posts:
        sc = p.get("score") or {}
        for k in axes:
            v = sc.get(k)
            if isinstance(v, (int, float)):
                acc[k] = acc.get(k, 0) + v
                n[k] = n.get(k, 0) + 1
    return {k: round(acc[k] / n[k], 2) for k in acc}


def _fail_samples(held: list[dict]) -> dict:
    """사유별 실제 문장 꼬리 1건. 정규식을 고치려면 걸린 문장이 필요하다."""
    from src.generator import REJECTED
    out = {}
    for p in held:
        for f in ((p.get("score") or {}).get("fatal") or []):
            k = "fatal:" + f.split("(")[0].strip()[:20]
            out.setdefault(k, p.get("body", "").strip()[-40:])
    for p in REJECTED:
        for e in p.get("reject_errs", []):
            k = e.split("(")[0]
            out.setdefault(k, p.get("body", "").strip()[-40:])
    return dict(list(out.items())[:18])


def summarize(collected, blocked, enriched, generated, sent, held, fallbacks) -> dict:
    def avg_score(ps):
        v = [(p.get("score") or {}).get("total") for p in ps]
        v = [x for x in v if x]
        return round(sum(v) / len(v), 2) if v else None

    by_provider = {}
    for name in {p.get("provider") for p in generated if p.get("provider")}:
        grp = [p for p in generated if p.get("provider") == name]
        by_provider[name] = {
            "generated": len(grp),
            "sent": sum(1 for p in sent if p.get("provider") == name),
            "avg_score": avg_score(grp),
        }

    import config as _c
    return {
        "collected": len(collected),
        "gate_blocked": len(blocked),
        "gate_reasons": dict(Counter(w.split(":")[0] for _, w in blocked)),
        # 통과율을 역산하려면 tier 합계가 아니라 사유별 분포가 필요하다
        "gate_detail": dict(Counter(w for _, w in blocked).most_common(25)),
        "enrich_ok": enriched,
        # filter_log 를 아티팩트로 돌린 뒤 리젝 사유를 볼 수 없게 됐다.
        # 튜닝에 필요한 건 사유 분포이므로 집계만이라도 여기 남긴다.
        "reject_reasons": _reject_reasons(),
        # fatal 은 치명적 위반 코드다. 진짜 위반인지 오탐인지 가르려면
        # 건수가 아니라 코드별 분포가 필요하다 (실측 #69: fatal 42건, 원인 미상).
        "fatal_codes": dict(Counter(
            f"{p.get('kind')}:{f}" for p in held
            for f in ((p.get("score") or {}).get("fatal") or [])).most_common(20)),
        # 어느 항목이 점수를 깎는지. fit 이 범인이면 소재 문제, natural 이면 문체 문제다.
        "score_axis_avg": _axis_avg(generated),
        # 사유 이름만으로는 정규식을 못 고친다. 어떤 문장이 걸렸는지 꼬리만 본다.
        # 본문 전체는 아티팩트에 두고 여기엔 30자만 남긴다.
        "fail_samples": _fail_samples(held),
        "hold_kinds": dict(Counter(f"{p.get('kind')}:"
                                   f"{(p.get('hold_reason') or '?').split('(')[0].split(' ')[0]}"
                                   for p in held).most_common(20)),
        # 유료 청구는 보강 성공 건수가 아니라 호출 건수에 붙는다
        "enrich_calls": __import__("src.enrich", fromlist=["CALLS"]).CALLS[0],
        "generated": len(generated),
        "sent": len(sent),
        "held": len(held),
        "hold_reasons": dict(Counter((p.get("hold_reason") or "?").split(":")[0].split("(")[0]
                                     for p in held)),
        "avg_score": avg_score(sent),
        "by_provider": by_provider,
        "model_fallbacks": fallbacks,
    }


def detail_log(items: list[dict], sent: list[dict], held: list[dict]) -> str:
    """건별 통과/탈락 사유 로그 (인사이트봇 filter_log 패턴).
    집계만으로는 '왜 이 글이 안 나갔는지'를 못 본다. 튜닝은 건별 사유에서 나온다."""
    import json as _j
    from datetime import datetime as _d
    path = f"data/filter_log_{_d.now(KST).strftime('%Y%m%d_%H%M')}.json"
    from src.generator import REJECTED
    sent_ids = {p["id"] for p in sent}
    rows = []
    for p in REJECTED:
        rows.append({"id": p.get("id"), "result": "rejected",
                     "reason": ",".join(p.get("reject_errs", [])),
                     "provider": p.get("provider"), "tone": p.get("tone"),
                     "angle": p.get("angle"),
                     "len": len(p.get("body", "")), "body": p.get("body", "")})
    for p in sent + held:
        rows.append({
            "id": p["id"], "kind": p.get("kind"), "stock": p.get("stock_name"),
            "title": p.get("title", "")[:60], "provider": p.get("provider"),
            "tone": p.get("tone"), "angle": p.get("angle"),
            "score": (p.get("score") or {}).get("total"),
            "score_parts": {k: (p.get("score") or {}).get(k) for k in
                            ("factual", "useful", "natural", "compliant", "gain", "fit")},
            "result": "sent" if p["id"] in sent_ids else "held",
            "reason": p.get("hold_reason", ""),
            "attr_reject": p.get("attr_reject"),
            "thin_facts": p.get("thin_facts", False),
            "len": len(p.get("body", "")),
            "body": p.get("body", ""),
        })
    os.makedirs("data", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        _j.dump(rows, f, ensure_ascii=False, indent=1)
    return path
