"""결정형 flow reserve의 수량·품질·우회 방지 계약."""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import stats
from src import template_reserve as tr
from src.decide import decide_distribution


def _item(i: int) -> dict:
    code = f"{i + 100000:06d}"
    name = "검증기업" + chr(0xAC00 + i)
    pct = 3.0 + (i % 20) + (i % 7) / 10
    close = 10_000 + i * 110
    vol = 1.5 + (i % 45) / 10
    gap = 31.0 + (i % 30) / 10
    ret5 = 41.0 + (i % 40) / 10
    return {
        "id": f"flow-2026-09-11-{code}",
        "kind": "flow",
        "stock_code": code,
        "stock_name": name,
        "title": f"{name} 기준일 시세",
        "facts": (
            f"기준일: 2026-09-11\n"
            f"종목: {name} ({code})\n"
            f"종가: {close:,}원\n"
            f"등락률: {pct:.2f}%\n"
            "[결합 사실 — 개별 수치만으로는 안 보이는 것]\n"
            f"· 거래량: 20일 평균의 {vol:.1f}배\n"
            f"· 마감 위치: 장중 고가 대비 {gap:.1f}% 낮은 수준\n"
            f"· 5거래일 누적 등락률: +{ret5:.2f}%\n"
            "※ 결합 사실은 코드가 계산한 값이다. 최소 하나는 본문에 그대로 써야 한다."
        ),
        "src": f"https://finance.naver.com/item/main.naver?code={code}",
        "board": "stock",
    }


def _ok(name: str, condition: bool, detail: str = "") -> bool:
    print(("  OK  " if condition else "  FAIL") + f"  {name}"
          + (f"  ({detail})" if detail else ""))
    return condition


def main() -> None:
    checks = []
    items = [_item(i) for i in range(90)]
    reserve = tr.build(items, 65)
    template_counts = Counter(p["template_id"] for p in reserve)
    endings = Counter(p["body"][-20:] for p in reserve)

    checks.append(_ok("문장틀 22종 계약", len(tr.TEMPLATES) >= 22))
    checks.append(_ok("50+15 reserve 준비", len(reserve) == 65, str(len(reserve))))
    checks.append(_ok("reserve 전건 원문 재검증", all(tr.is_valid(p) for p in reserve)))
    checks.append(_ok("동일 문장틀 하루 3건 이하",
                      max(template_counts.values()) <= 3, str(template_counts)))
    checks.append(_ok("22종이 실제 reserve에 사용됨", len(template_counts) >= 22,
                      str(len(template_counts))))
    checks.append(_ok("동일 말미 20자 2건 이하", max(endings.values()) <= 2))

    sent, held = decide_distribution(reserve, target=50,
                                     per_kind_cap={"flow": 20},
                                     hard_kind_cap={"flow": 30})
    tone_counts = Counter(p["tone"] for p in sent)
    checks.append(_ok("API 전면 장애 dry-run 50건", len(sent) == 50, str(len(sent))))
    checks.append(_ok("보장 모드 문장틀 문체별 17건 이하",
                      max(tone_counts.values()) <= 17, str(tone_counts)))

    forged = dict(reserve[0])
    forged["template_validated"] = True
    forged["body"] += " 임의 해석을 덧붙였습니다."
    forged_sent, forged_held = decide_distribution([forged], target=1)
    checks.append(_ok("boolean 플래그로 본문 변조 우회 불가",
                      not forged_sent and forged_held
                      and forged_held[0]["hold_reason"].startswith("문장틀검증실패")))

    # 정상 모드: 검증된 LLM 글이 70%(50건 중 35건) 있으면 그 글을 먼저
    # 배치하고 문장틀은 정확히 부족한 15건까지만 사용한다.
    llm = []
    for i, item in enumerate(items[:35]):
        llm.append({
            **item,
            "body": f"{item['stock_name']}의 검증된 LLM 본문이며 서로 다른 말미 {i:04d}입니다.",
            "provider": "claude",
            "model": "test",
            "tone": f"llm_tone_{i % 3}",
            "score": {"total": 18, "factual": 5, "compliant": 5,
                      "fit": 4, "fatal": []},
        })
    mixed, _ = decide_distribution(llm + reserve, target=50,
                                   per_kind_cap={"flow": 20},
                                   hard_kind_cap={"flow": 30})
    routes = Counter(p["provider"] for p in mixed)
    checks.append(_ok("정상 모드 LLM 35 + 문장틀 15",
                      len(mixed) == 50 and routes["claude"] == 35
                      and routes["template"] == 15, str(routes)))
    checks.append(_ok("같은 원본 LLM·문장틀 중복 배포 차단",
                      len({p["id"] for p in mixed}) == len(mixed)))

    row = stats.summarize(
        items, [], 0, [], sent, [], [],
        generation_candidates=items,
        generation_attempted=[],
        generation_stages=[],
        delivery_attempted=sent,
        template_reserve=reserve,
    )
    checks.append(_ok(
        "문장틀 준비·보충·페르소나 통계 계측",
        row["template_reserve"] == 65
        and row["template_fallback_count"] == 50
        and row["by_provider"]["template"]["sent"] == 50
        and sum(x["template_prepared"] for x in row["by_persona"].values()) == 65,
    ))

    passed = sum(checks)
    print(f"\n{passed}/{len(checks)} passed")
    raise SystemExit(0 if all(checks) else 1)


if __name__ == "__main__":
    main()
