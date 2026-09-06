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
