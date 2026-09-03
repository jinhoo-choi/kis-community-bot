"""Claude API 로 게시글 본문 생성.

- 기본은 Message Batches API (비실시간 대량 작업이라 비용 효율이 좋다).
- USE_BATCH=0 이면 동기 호출로 폴백.
문서: https://docs.claude.com/en/docs/build-with-claude/batch-processing
"""
import random
import time

from anthropic import Anthropic

from config import ANTHROPIC_API_KEY, MODEL, USE_BATCH
from src import filters
from src.personas import SLOT_TONES, build_messages

client = Anthropic(api_key=ANTHROPIC_API_KEY)


def pick_tone(item: dict, recent: dict) -> str:
    """슬롯별 허용 톤 중, 최근 3일간 같은 종목에 쓴 톤은 제외."""
    pool = SLOT_TONES.get(item["kind"], ["calm"])
    used = set(recent.get(item.get("stock_code") or "_theme", []))
    cand = [t for t in pool if t not in used] or pool
    return random.choice(cand)


def _req(item, tone, idx):
    system, user = build_messages(item, tone)
    return {
        "custom_id": f"p{idx}",
        "params": {
            "model": MODEL,
            "max_tokens": 700,
            "temperature": 1.0,          # 문체 다양성 확보
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
    }


def _sync(items, tones):
    out = {}
    for i, (item, tone) in enumerate(zip(items, tones)):
        system, user = build_messages(item, tone)
        r = client.messages.create(
            model=MODEL, max_tokens=700, temperature=1.0,
            system=system, messages=[{"role": "user", "content": user}],
        )
        out[f"p{i}"] = "".join(b.text for b in r.content if b.type == "text")
        time.sleep(0.3)
    return out


def _batch(items, tones, poll_sec=20, timeout_sec=1800):
    reqs = [_req(it, tn, i) for i, (it, tn) in enumerate(zip(items, tones))]
    batch = client.messages.batches.create(requests=reqs)
    print(f"[gen] batch {batch.id} 제출 ({len(reqs)}건)")

    waited = 0
    while waited < timeout_sec:
        b = client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        time.sleep(poll_sec)
        waited += poll_sec
    else:
        raise TimeoutError("batch timeout")

    out = {}
    for res in client.messages.batches.results(batch.id):
        if res.result.type == "succeeded":
            msg = res.result.message
            out[res.custom_id] = "".join(b.text for b in msg.content if b.type == "text")
    return out


def generate(items: list[dict], recent: dict) -> list[dict]:
    tones = [pick_tone(it, recent) for it in items]
    texts = (_batch(items, tones) if USE_BATCH else _sync(items, tones))

    posts, retry_idx = [], []
    for i, (item, tone) in enumerate(zip(items, tones)):
        body = (texts.get(f"p{i}") or "").strip()
        if not body:
            continue
        errs = filters.check(body, item["facts"])
        if errs:
            print(f"[gen] 리젝 {item['id']} {errs}")
            retry_idx.append(i)
            continue
        posts.append({**item, "tone": tone, "body": body})

    # 리젝분 1회 재생성 (동기, 소량)
    if retry_idx:
        r_items = [items[i] for i in retry_idx]
        r_tones = [tones[i] for i in retry_idx]
        r_texts = _sync(r_items, r_tones)
        for j, (item, tone) in enumerate(zip(r_items, r_tones)):
            body = (r_texts.get(f"p{j}") or "").strip()
            if body and not filters.check(body, item["facts"]):
                posts.append({**item, "tone": tone, "body": body})

    print(f"[gen] 최종 {len(posts)}건 통과 / {len(items)}건 시도")
    return posts
