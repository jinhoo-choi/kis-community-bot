"""한국투자증권 커뮤니티 - AI 게시글 생성/배포 파이프라인.

수집 → [하드 게이트] → 종목매핑 → 중복제거
     → [1] Gemini 검색 그라운딩으로 사실 보강 (실패 시 thin_facts 플래그)
     → [2] Claude + Gemini 병렬 작성 (슬롯별 temperature 차등)
     → [3] 정규식 검수 → 교차 LLM 심사
     → [4] 배포 판정 (점수·종목상한·유형상한)
     → 텔레그램 배포 → 상태·통계 저장

설계 원칙
  - 구조적 규칙(gate)은 확률적 AI 판단보다 항상 선행한다.
  - 금지 규칙은 src/rules.py 단일 소스에서 3곳(작성·심사·정규식)에 파생된다.
  - 판정 로직은 src/decide.py 순수 함수로 분리되어 테스트가 프로덕션 코드를 호출한다.
  - 미탐 > 오탐. 애매하면 배포하지 않는다 (리스크 모니터링과 반대 방향).
"""
import json
import os
import sys

import config
from src import (state, tickers, generator, telegram_bot, enrich, judge,
                 gate, decide, stats, dedup, crawl)
from src.sources import dart, research, market, policy


def collect() -> list[dict]:
    q, over = config.SLOT_QUOTA, config.OVERGEN_RATE
    items = []
    items += dart.fetch(int(q["disclosure"] * over))
    items += research.fetch(int(q["research"] * over))
    items += market.fetch(int(q["flow"] * over))
    items += policy.fetch(int(q["policy"] * over))
    items += policy.make_polls(items, q["poll"])
    return items


def main():
    dry = "--dry-run" in sys.argv
    s = state.prune(state.load())

    raw = collect()
    print(f"[main] 수집 총 {len(raw)}건")

    # 하드 게이트 — AI 호출 이전에 구조적으로 배제
    gated, blocked = gate.apply(raw)

    resolved = []
    for it in gated:
        it = tickers.resolve(it)
        it["board"] = tickers.board_of(it)
        resolved.append(it)

    # 다축 dedup — 같은 사건이 DART/리서치/수급으로 중복 유입되는 것을 잡는다
    items, dup_reasons = dedup.filter_new(resolved, s["seen"])

    degraded = crawl.degraded_sources()
    if degraded:
        print(f"[main] ⚠ 수집 이상 소스: {degraded}")

    picked, cnt = [], {k: 0 for k in config.SLOT_QUOTA}
    for it in items:
        k = it["kind"] if it["kind"] in config.SLOT_QUOTA else "research"
        if cnt[k] < int(config.SLOT_QUOTA[k] * config.OVERGEN_RATE):
            cnt[k] += 1
            picked.append(it)
    print(f"[main] 생성 대상 {len(picked)}건 {cnt}")

    if dry:
        print(json.dumps({"blocked": blocked[:10], "sample": picked[:2]},
                         ensure_ascii=False, indent=1))
        return

    if config.ENABLE_ENRICH:
        picked = enrich.enrich_all(picked)
    enriched_n = sum(1 for x in picked if x.get("enriched"))

    posts = generator.generate(picked, s["recent_tone"])

    if config.ENABLE_JUDGE:
        posts = judge.judge_all(posts)

    sent_posts, held = decide.decide_distribution(posts)
    print(f"[main] 배포 {len(sent_posts)}건 / 보류 {len(held)}건")
    for h in held[:5]:
        print(f"   보류 {h['id']} ({h.get('provider')}) - {h.get('hold_reason','')}")

    os.makedirs(os.path.dirname(config.OUTPUT_PATH), exist_ok=True)
    with open(config.OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(sent_posts, f, ensure_ascii=False, indent=1)

    sent = telegram_bot.send_all(sent_posts)
    telegram_bot.send_summary(sent_posts, sent)

    row = stats.record(**stats.summarize(
        raw, blocked, enriched_n, posts, sent_posts, held,
        generator.collect_fallbacks()),
        dedup=dup_reasons, crawl_health=crawl.health())
    print("[main] filter_log " + stats.detail_log(picked, sent_posts, held))
    if degraded:
        telegram_bot.send_warning(f"수집 이상 소스: {', '.join(degraded)}")
    print("[main] stats " + json.dumps(row, ensure_ascii=False))

    for p_ in sent_posts:
        dedup.mark(p_, s["seen"], __import__("datetime").datetime.now(config.KST).strftime("%Y-%m-%d"))
    state.mark(s, sent_posts)
    state.save(s)


if __name__ == "__main__":
    main()
