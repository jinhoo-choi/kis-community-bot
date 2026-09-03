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

    return {
        "collected": len(collected),
        "gate_blocked": len(blocked),
        "gate_reasons": dict(Counter(w.split(":")[0] for _, w in blocked)),
        "enrich_ok": enriched,
        "generated": len(generated),
        "sent": len(sent),
        "held": len(held),
        "hold_reasons": dict(Counter((p.get("hold_reason") or "?").split(":")[0].split("(")[0]
                                     for p in held)),
        "avg_score": avg_score(sent),
        "by_provider": by_provider,
        "model_fallbacks": fallbacks,
    }
