"""한국투자증권 커뮤니티 - AI 게시글 생성/배포 파이프라인.

수집 → 종목매핑 → 중복제거 → Claude 생성 → 자동검수 → 텔레그램 배포 → 상태저장
"""
import json
import os
import sys

from config import SLOT_QUOTA, TARGET_POSTS, OVERGEN_RATE, OUTPUT_PATH
from src import state, tickers, generator, telegram_bot
from src.sources import dart, research, market, policy


def collect() -> list[dict]:
    over = OVERGEN_RATE
    items = []
    items += dart.fetch(int(SLOT_QUOTA["disclosure"] * over))
    items += research.fetch(int(SLOT_QUOTA["research"] * over))
    items += market.fetch(int(SLOT_QUOTA["flow"] * over))
    pol = policy.fetch(int(SLOT_QUOTA["policy"] * over))
    items += pol
    items += policy.make_polls(items, SLOT_QUOTA["poll"])
    return items


def main():
    dry = "--dry-run" in sys.argv

    s = state.prune(state.load())

    raw = collect()
    print(f"[main] 수집 총 {len(raw)}건")

    # 종목 매핑 + 게시판 라우팅
    items = []
    for it in raw:
        it = tickers.resolve(it)
        it["board"] = tickers.board_of(it)
        if state.is_new(s, it["id"]):
            items.append(it)
    print(f"[main] 신규 {len(items)}건")

    # 슬롯 쿼터에 맞춰 컷
    picked, cnt = [], {k: 0 for k in SLOT_QUOTA}
    for it in items:
        k = it["kind"] if it["kind"] in SLOT_QUOTA else "research"
        cap = int(SLOT_QUOTA[k] * OVERGEN_RATE)
        if cnt[k] < cap:
            cnt[k] += 1
            picked.append(it)
    print(f"[main] 생성 대상 {len(picked)}건 {cnt}")

    if dry:
        print(json.dumps(picked[:3], ensure_ascii=False, indent=1))
        return

    posts = generator.generate(picked, s["recent_tone"])[:TARGET_POSTS]

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=1)

    sent = telegram_bot.send_all(posts)
    telegram_bot.send_summary(posts, sent)
    print(f"[main] 전송 {sent}건")

    state.mark(s, posts)
    state.save(s)


if __name__ == "__main__":
    main()
