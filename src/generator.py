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
from src import angles
from src.personas import VOICE_W, FORMAT_W, LENGTH_W, build_messages


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
    # Gemini 가 자기검토 결과를 본문에 섞는 사례 (실측)
    r"^[^\n]*\b\d+\s*(sentences?|문장)\?[^\n]*\n?",
    r"^[^\n]*(충족|만족)\.\s*$",
    r"^\s*\(?\d+문장[^)\n]*\)?\s*(->|→)[^\n]*\n?",
    r"^\s*(말투|글 ?구조|규칙|출력)\s*[:：][^\n]*\n?",
]


def clean(body: str) -> str:
    b = body.strip()
    for pat in _STRIP_PATTERNS:
        b = re.sub(pat, "", b, flags=re.M)
    # 따옴표로 통째로 감싼 출력
    if len(b) > 2 and b[0] in "\"'" and b[-1] == b[0]:
        b = b[1:-1]
    return b.strip()


# 이미 쓴 축의 억제 계수. 1.0 으로 낮추는 정도로는 편중이 남았다
# (5건 중 short_note 3건, context 3건). 금지하지는 않되 확률을 크게 줄인다.
PENALTY = 0.3


def _weighted(weights: dict, penalize: set) -> str:
    """가중 랜덤. 최근/이번 실행에 쓴 값은 확률을 크게 낮춘다(금지가 아니라 억제)."""
    items = [(k, w * PENALTY if k in penalize else float(w))
             for k, w in weights.items() if w > 0]
    total = sum(w for _, w in items)
    r = random.uniform(0, total)
    acc = 0
    for k, w in items:
        acc += w
        if r <= acc:
            return k
    return items[-1][0]


# uncertainty 앵글은 전체의 일부만 허용한다. 안 그러면 '정보가 없다'는 글이 늘어난다.
UNCERTAINTY_QUOTA = 0.10


def pick_style(item: dict, recent: dict, used_now: set,
               allow_uncertainty: bool = False) -> tuple[str, str, str, str]:
    """(voice, angle, format) 선택.

    외부 검토 반영으로 축을 셋으로 나눴다. 그리고 회피 기준을 조합 ID 가 아니라
    '의미적 반복'으로 바꿨다 — 사람은 조합 ID 반복보다
    "또 숫자로 시작해서 질문으로 끝나네"를 훨씬 빨리 알아챈다.
    그래서 voice/angle/format 을 각각 따로 억제한다.
    """
    kind = item["kind"]
    vw = VOICE_W.get(kind, {"calm": 3, "dry": 2, "explainer": 2, "light": 1})
    fw = FORMAT_W.get(kind, {"fact_read": 3, "question": 2, "check_points": 2})
    lw = LENGTH_W.get(kind, {"short": 2, "medium": 3, "long": 2})

    hist = recent.get(item.get("stock_code") or "_theme", [])
    used_v = {h.split(":")[0] for h in hist} | {u[0] for u in used_now}
    used_a = {h.split(":")[1] for h in hist if h.count(":") >= 2} | {u[1] for u in used_now}
    used_f = {h.split(":")[-1] for h in hist} | {u[2] for u in used_now}

    used_l = {h.split(":")[3] for h in hist if h.count(":") >= 3} | {u[3] for u in used_now}

    voice = _weighted(vw, used_v)
    fmt = _weighted(fw, used_f)
    length = _weighted(lw, used_l)

    cand = angles.available(item)
    if not allow_uncertainty:
        cand = [a for a in cand if a != "uncertainty"] or cand
    angle = _weighted({a: 3 for a in cand}, used_a) if cand else ""

    used_now.add((voice, angle, fmt, length))
    return voice, angle, fmt, length


def pick_tone(item: dict, recent: dict) -> str:      # 하위 호환
    return pick_style(item, recent, set())[0]


def _run(provider_name: str, items: list[dict], tones: list[str],
         fmts: list[str] = None, angs: list[str] = None,
         lens: list[str] = None) -> list[dict]:
    if not items:
        return []
    fmts = fmts or ["fact_read"] * len(items)
    angs = angs or [""] * len(items)
    lens = lens or ["medium"] * len(items)
    p = router.writers()[provider_name]
    # temperature 가 다른 항목은 배치를 나눈다 (배치는 파라미터가 요청별로 고정되므로)
    groups = {}
    for i, (it, tn, fm, ag, ln) in enumerate(zip(items, tones, fmts, angs, lens)):
        groups.setdefault(temperature_for(it), []).append((i, it, tn, fm, ag, ln))

    results = [None] * len(items)
    for temp, grp in groups.items():
        jobs = [build_messages(it, tn, fm, ag, ln) for _, it, tn, fm, ag, ln in grp]
        for g, r in zip(grp, p.generate_many(jobs, temperature=temp)):
            results[g[0]] = r

    out = []
    for it, tn, fm, ag, ln, r in zip(items, tones, fmts, angs, lens, results):
        if not r.ok or not r.text:
            # 원인을 삼키면 '0건 생성'만 보이고 왜인지 알 수 없다
            print(f"[gen] ⚠ {provider_name} 실패 {it['id']}: "
                  f"{r.error or '빈 응답'}")
            continue
        body = clean(r.text)
        if not body:
            print(f"[gen] ⚠ {provider_name} 후처리 후 빈 본문 {it['id']}")
            continue
        out.append({**it, "tone": tn, "fmt": fm, "angle": ag, "length": ln,
                    "body": body, "provider": r.provider, "model": r.model})
    return out


def generate(items: list[dict], recent: dict) -> list[dict]:
    used_now: set = set()
    n_unc = max(1, int(len(items) * UNCERTAINTY_QUOTA))
    styles = {}
    for i, it in enumerate(items):
        styles[id(it)] = pick_style(it, recent, used_now, allow_uncertainty=(i < n_unc))
    buckets = router.split_by_ratio(items)

    posts, retry = [], []
    for name, chunk in buckets.items():
        made = _run(name, chunk,
                    [styles[id(x)][0] for x in chunk],
                    [styles[id(x)][2] for x in chunk],
                    [styles[id(x)][1] for x in chunk],
                    [styles[id(x)][3] for x in chunk])
        print(f"[gen] {name}: {len(made)}/{len(chunk)}건 생성")
        for p in made:
            errs = filters.check(p["body"], p["facts"], p.get("fmt"), p.get("angle"))
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
            made = _run(alt, [p], [p["tone"]], [p.get("fmt", "fact_read")],
                        [p.get("angle", "")], [p.get("length", "medium")])
            if made and not filters.check(made[0]["body"], p["facts"],
                                          p.get("fmt"), p.get("angle")):
                posts.append(made[0])

    print(f"[gen] 정규식 통과 {len(posts)}건 / 시도 {len(items)}건")
    return posts


def collect_fallbacks() -> list[str]:
    out = []
    for p in router.writers().values():
        out += (p.fallbacks or [])
    return out
