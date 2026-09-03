"""한국투자증권 커뮤니티 - AI 게시글 생성/배포 파이프라인.

수집 → 종목매핑 → 중복제거
     → [Gemini] 검색 그라운딩으로 사실 보강
     → [Claude + Gemini 병렬] 게시글 작성
     → 정규식 자동검수 → [교차 LLM 심사] → 상위 N건 선별
     → 텔레그램 배포 → 상태저장
"""
import json
import os
import sys

import config
from src import state, tickers, generator, telegram_bot, enrich, judge
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

    items = []
    for it in raw:
        it = tickers.resolve(it)
        it["board"] = tickers.board_of(it)
        if state.is_new(s, it["id"]):
            items.append(it)

    # 슬롯 쿼터 컷
    picked, cnt = [], {k: 0 for k in config.SLOT_QUOTA}
    for it in items:
        k = it["kind"] if it["kind"] in config.SLOT_QUOTA else "research"
        if cnt[k] < int(config.SLOT_QUOTA[k] * config.OVERGEN_RATE):
            cnt[k] += 1
            picked.append(it)
    print(f"[main] 생성 대상 {len(picked)}건 {cnt}")

    if dry:
        print(json.dumps(picked[:3], ensure_ascii=False, indent=1))
        return

    # 1단계: 사실 보강 (Gemini + 검색 그라운딩)
    if config.ENABLE_ENRICH:
        picked = enrich.enrich_all(picked)

    # 2단계: 병렬 작성 (Claude + Gemini)
    posts = generator.generate(picked, s["recent_tone"])

    # 3단계: 교차 심사 후 상위 N건 선별
    if config.ENABLE_JUDGE:
        posts = judge.judge_all(posts)
        posts, dropped = judge.select(posts, config.MIN_JUDGE_SCORE, config.TARGET_POSTS)
        print(f"[main] 심사 통과 {len(posts)}건 / 탈락 {len(dropped)}건")
        for d in dropped[:5]:
            print(f"   탈락 {d['id']} ({d.get('provider')}) - {d.get('drop_reason','')}")
    else:
        posts = posts[:config.TARGET_POSTS]

    os.makedirs(os.path.dirname(config.OUTPUT_PATH), exist_ok=True)
    with open(config.OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=1)

    sent = telegram_bot.send_all(posts)
    telegram_bot.send_summary(posts, sent)
    print(f"[main] 전송 {sent}건")

    state.mark(s, posts)
    state.save(s)


if __name__ == "__main__":
    main()
