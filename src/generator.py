"""2단계 — 게시글 생성. Claude / Gemini 병렬 작성.

같은 톤이라도 모델이 다르면 문장 리듬·어휘 선택이 갈린다.
50건을 한 모델로 뽑으면 문체 지문이 남아 커뮤니티에서 금방 티가 나므로
프로바이더를 섞는 것 자체가 품질 방어책이다.
"""
import random
import re

import config
from src import filters
from src.llm import router
from src.decide import temperature_for
from src.personas import SLOT_TONES, build_messages


# 리젝된 생성물 보관 (품질 검토용). main 이 filter_log 에 함께 기록한다.
REJECTED: list[dict] = []

# 모델이 본문 앞뒤에 붙이는 군더더기. 리젝하기 전에 정리해 준다.
# (실측: Gemini 가 "안녕하세요 AI 작성 봇입니다" 로 시작하거나
#  자기검토 체크리스트를 본문으로 출력하는 사례가 있었다)
_STRIP_PATTERNS = [
    r"^\s*안녕하세요[,.]?\s*(저는\s*)?AI[^\n]*\n",
    r"^\s*(본문|게시글|출력)\s*[:：][^\n]*\n",
    r"^\s*```[a-z]*\s*|\s*```\s*$",
    r"\n\s*[*\-]\s+[^\n]*(Yes|No)\.[^\n]*$",
]


def clean(body: str) -> str:
    b = body.strip()
    for pat in _STRIP_PATTERNS:
        b = re.sub(pat, "", b, flags=re.M)
    # 따옴표로 통째로 감싼 출력
    if len(b) > 2 and b[0] in "\"'" and b[-1] == b[0]:
        b = b[1:-1]
    return b.strip()


def pick_tone(item: dict, recent: dict) -> str:
    """슬롯별 허용 톤 중, 최근 같은 종목에 쓴 톤은 제외."""
    pool = SLOT_TONES.get(item["kind"], ["calm"])
    used = set(recent.get(item.get("stock_code") or "_theme", []))
    return random.choice([t for t in pool if t not in used] or pool)


def _run(provider_name: str, items: list[dict], tones: list[str]) -> list[dict]:
    if not items:
        return []
    p = router.writers()[provider_name]
    # temperature 가 다른 항목은 배치를 나눈다 (배치는 파라미터가 요청별로 고정되므로)
    groups = {}
    for i, (it, tn) in enumerate(zip(items, tones)):
        groups.setdefault(temperature_for(it), []).append((i, it, tn))

    results = [None] * len(items)
    for temp, grp in groups.items():
        jobs = [build_messages(it, tn) for _, it, tn in grp]
        for (idx, _, _), r in zip(grp, p.generate_many(jobs, temperature=temp)):
            results[idx] = r

    out = []
    for it, tn, r in zip(items, tones, results):
        if not r.ok or not r.text:
            # 원인을 삼키면 '0건 생성'만 보이고 왜인지 알 수 없다
            print(f"[gen] ⚠ {provider_name} 실패 {it['id']}: "
                  f"{r.error or '빈 응답'}")
            continue
        body = clean(r.text)
        if not body:
            print(f"[gen] ⚠ {provider_name} 후처리 후 빈 본문 {it['id']}")
            continue
        out.append({**it, "tone": tn, "body": body,
                    "provider": r.provider, "model": r.model})
    return out


def generate(items: list[dict], recent: dict) -> list[dict]:
    tones = {id(it): pick_tone(it, recent) for it in items}
    buckets = router.split_by_ratio(items)

    posts, retry = [], []
    for name, chunk in buckets.items():
        made = _run(name, chunk, [tones[id(x)] for x in chunk])
        print(f"[gen] {name}: {len(made)}/{len(chunk)}건 생성")
        for p in made:
            errs = filters.check(p["body"], p["facts"])
            if errs:
                # 본문을 함께 남겨야 '이 리젝이 타당했는지' 사후 검토가 된다
                print(f"[gen] 정규식 리젝 {p['id']} {errs}")
                print(f"       └ {p['body'][:200]!r}")
                p["reject_errs"] = errs
                REJECTED.append(dict(p))
                retry.append(p)
            else:
                posts.append(p)

    # 리젝분은 '다른 프로바이더'로 1회 재생성 (같은 모델은 같은 실수를 반복한다)
    if retry:
        names = list(router.writers().keys())
        for p in retry:
            alt = next((n for n in names if n != p["provider"]), p["provider"])
            made = _run(alt, [p], [p["tone"]])
            if made and not filters.check(made[0]["body"], p["facts"]):
                posts.append(made[0])

    print(f"[gen] 정규식 통과 {len(posts)}건 / 시도 {len(items)}건")
    return posts


def collect_fallbacks() -> list[str]:
    out = []
    for p in router.writers().values():
        out += (p.fallbacks or [])
    return out
