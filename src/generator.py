"""2단계 — 게시글 생성. Claude / Gemini 병렬 작성.

같은 톤이라도 모델이 다르면 문장 리듬·어휘 선택이 갈린다.
50건을 한 모델로 뽑으면 문체 지문이 남아 커뮤니티에서 금방 티가 나므로
프로바이더를 섞는 것 자체가 품질 방어책이다.
"""
import random
import re

from src import filters
from src.llm import router
from src.decide import temperature_for
from src import angles
from src import facts as facts_mod
from src import personas as P


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
    # 줄바꿈이 공백으로 합쳐지면 줄머리 패턴이 안 먹는다. 위치 무관으로 지운다
    # (실측: "NHN이 8.5% 올랐습니다. (4문장 충족)" 이 ')' 로 끝나 통과했다).
    r"\s*\(\s*\d+\s*문장[^)]*\)\s*",
    r"\s*\((충족|만족|규칙\s*준수)[^)]*\)\s*",
    # 페르소나 계약 문구가 본문 앞에 새어 나왔다 (실측 #72:
    # '배경을 밝힘. "직전 거래일 장중 고가와 저가의 차이가...').
    r"^[^\n]{0,40}(밝힘|붙임|끝냄|씀)\.\s*[\"“]?",
    r"^\s*(말투|글 ?구조|규칙|출력)\s*[:：][^\n]*\n?",
]


# 리젝하기보다 고쳐 쓰는 편이 나은 표현들.
# 의미가 그대로 보존되고 기사체만 빠진다.
def _josa_was(word: str, polite: str = "습니다") -> str:
    """앞말 받침에 따라 '이었/였' 을 고른다. 안 그러면 '942억원였습니다' 가 된다."""
    if not word:
        return "였" + polite
    ch = word[-1]
    if "가" <= ch <= "힣":
        has_batchim = (ord(ch) - 0xAC00) % 28 != 0
    else:
        has_batchim = ch.isdigit() or ch.isalpha()
    return ("이었" if has_batchim else "였") + polite


def _fix_cliche(b: str) -> str:
    """'~를 기록했습니다' 같은 금융기사 상투어를 자연스럽게 치환한다.

    리젝하면 재생성해도 반복된다(실측: 11건 리젝 중 5건). 의미가 보존되는
    표현은 고쳐 쓰는 편이 물량 손실 없이 기사체만 빼는 방법이다.
    """
    def _rep(m, polite):
        # 앞말을 반드시 살려서 붙인다. 안 그러면 "942억원을 기록했습니다" 가
        # "이었습니다" 로 통째로 사라진다.
        return m.group(1) + _josa_was(m.group(1), polite)

    # 동작 명사(상승/하락/증가)는 '이었습니다' 보다 '했습니다' 가 자연스럽다
    b = re.sub(r"(상승|하락|증가|감소|반등|급등|급락)[을를]\s*기록했습니다",
               r"\1했습니다", b)
    b = re.sub(r"(상승|하락|증가|감소|반등|급등|급락)[을를]\s*기록했어요",
               r"\1했어요", b)
    b = re.sub(r"([가-힣A-Za-z0-9,\.%]+)\s*[을를]\s*기록했습니다",
               lambda m: _rep(m, "습니다"), b)
    b = re.sub(r"([가-힣A-Za-z0-9,\.%]+)\s*[을를]\s*기록했어요",
               lambda m: _rep(m, "어요"), b)
    b = re.sub(r"([가-힣A-Za-z0-9,\.%]+?)(?:으)?로\s*집계됐습니다",
               lambda m: _rep(m, "습니다"), b)
    b = re.sub(r"([가-힣A-Za-z0-9,\.%]+)\s*[을를]\s*기록하며", r"\1이었고", b)
    return b


# 한글·영숫자·기본 문장부호 외의 문자는 생성 오류다 (실측: "규모라면 ꤼ 의미")
_JUNK = re.compile(
    r"[^\uAC00-\uD7A3\u3131-\u318E0-9A-Za-z\s"
    r".,!?%~·:;()\[\]{}'\"/\-+＋−–—…‘’“”]"
)


def clean(body: str) -> str:
    b = body.strip()
    b = _JUNK.sub("", b)
    b = re.sub(r"\s{2,}", " ", b)
    for pat in _STRIP_PATTERNS:
        b = re.sub(pat, "", b, flags=re.M)
    b = _fix_cliche(b)
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


def hist_v2(recent: dict, item: dict) -> list:
    return recent.get(item.get("stock_code") or "_theme", [])


def pick_style(item: dict, recent: dict, used_now: set,
               allow_uncertainty: bool = False) -> tuple[str, str, str, str]:
    """(voice, angle, format) 선택.

    외부 검토 반영으로 축을 셋으로 나눴다. 그리고 회피 기준을 조합 ID 가 아니라
    '의미적 반복'으로 바꿨다 — 사람은 조합 ID 반복보다
    "또 숫자로 시작해서 질문으로 끝나네"를 훨씬 빨리 알아챈다.
    그래서 voice/angle/format 을 각각 따로 억제한다.
    """
    kind = item["kind"]
    if True:
        # 페르소나 하나가 말투·구조·길이를 모두 갖는다. 축은 Persona × Angle.
        pw = dict(P.style_ids().get(kind, {}))
        # 글자수 기준을 버리고 Fact Slot(독립 사실 개수·종류)으로 판단한다.
        # 글자수는 정보량이 아니다 — "A사가 B사를 흡수합병" 은 짧지만 사실 1개,
        # 시세 3종은 길이가 비슷해도 독립 사실 3개다 (외부 검토 지적).
        n_fact = facts_mod.count(item)
        both = facts_mod.has_both_sides(item)
        sl = facts_mod.slots(item)
        need = {
            "quick_memo": 1, "brief_report": 1,
            "fact_note": 2, "data_focus": 2, "open_talk": 2,
            "term_guide": 2, "careful_note": 2,
            "check_list": 2, "timeline_note": 2, "two_view": 2,
        }
        for pid in list(pw):
            if n_fact < need.get(pid, 2):
                pw[pid] = 0
        # quick_memo 는 표본 9건에서 전멸했다 (sent 0 / held 5 / reject 4,
        # 사유가 전부 '정보량 부족'). 2~3문장으로는 fit 을 구조적으로 못 넘는다.
        # 다만 공시는 사실 하나로도 글이 된다("A사가 B사를 흡수합병"). 거기만 남긴다.
        if kind != "disclosure":
            pw["quick_memo"] = 0

        # 구조적 전제 조건
        if not both:
            pw["two_view"] = 0                     # 양방향 근거가 있어야 성립
        if "term_word" not in sl:
            pw["term_guide"] = 0                   # 풀어줄 용어가 있어야 성립
        # 5거래일 누적은 '기간'이지 별개 시점이 아니다. 정렬 가능한 시점 2개가 필요하다.
        import re as _re
        n_anchor = len(_re.findall(r"\d{4}[-.]\d{1,2}[-.]?\d{0,2}|\d{1,2}월 \d{1,2}일",
                                   item.get("facts", "")))
        if n_anchor < 2:
            pw["timeline_note"] = 0
        if not (sl & {"vs_avg", "five_day", "intraday", "flow_inv", "short"}):
            pw["data_focus"] = 0                   # 비교값이 있어야 성립
        if not any(pw.values()):
            # 최후 폴백도 공시가 아니면 quick_memo 를 쓰지 않는다.
            pw = {"quick_memo": 1} if kind == "disclosure" else {"brief_report": 1}

        hist = hist_v2(recent, item)
        used_p = {h.split(":")[0] for h in hist} | {u[0] for u in used_now}
        used_a2 = {h.split(":")[1] for h in hist if ":" in h} | {u[1] for u in used_now}
        persona = _weighted(pw, used_p)
        cand2 = angles.available(item)
        if not allow_uncertainty:
            cand2 = [a for a in cand2 if a != "uncertainty"] or cand2
        # 호환 그래프: 성립하지 않는 조합은 처음부터 만들지 않는다
        compat = [a for a in cand2 if P.v2.compatible(persona, a)]
        cand2 = compat or cand2
        ang2 = _weighted({a: 3 for a in cand2}, used_a2) if cand2 else ""
        used_now.add((persona, ang2, persona, persona))
        return persona, ang2, persona, persona


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
        jobs = [P.build_messages_v2(it, tn, ag)
                for _, it, tn, fm, ag, ln in grp]
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
            errs = filters.check(
                p["body"], p["facts"], p.get("fmt"), p.get("angle"), p.get("length"),
                p.get("stock_name") if p.get("theme_assigned") else None)
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
            p["retry_hint"] = _hint(p.get("reject_errs", []))
            alt = next((n for n in names if n != p["provider"]), p["provider"])
            made = _run(alt, [p], [p["tone"]], [p.get("fmt", "fact_read")],
                        [p.get("angle", "")], [p.get("length", "medium")])
            if made and not filters.check(
                    made[0]["body"], p["facts"], p.get("fmt"), p.get("angle"),
                    p.get("length"),
                    p.get("stock_name") if p.get("theme_assigned") else None):
                posts.append(made[0])

    print(f"[gen] 정규식 통과 {len(posts)}건 / 시도 {len(items)}건")
    return posts


_HINTS = {
    "수치과다": "숫자를 너무 많이 썼습니다. 숫자는 2~3개만 쓰고, "
              "숫자가 하나도 없는 문장을 반드시 넣으세요.",
    "미확인표현": "'확인되지 않았다' 류 표현을 썼습니다. 그 문장을 통째로 빼고, "
                "확인된 사실만으로 글을 완성하세요.",
    "방향오용": "등락 방향 어휘를 반대로 썼습니다. 입력의 등락률 부호를 다시 확인하세요.",
    "너무김": "너무 깁니다. 문장 수를 줄이고 곁가지 내용을 버리세요.",
    "너무짧음": "너무 짧습니다. 사실을 하나 더 넣되 숫자를 늘리지는 마세요.",
    "질문마무리금지": "질문으로 끝냈습니다. 마지막 사실에서 그냥 끊으세요.",
    "stock_ending": "'지켜봐야 한다' 류 상투적 마무리를 썼습니다. 그 문장을 빼세요.",
    "완충표현": "'~것 같습니다', '~로 보입니다'를 너무 많이 썼습니다. 한 번까지만 쓰세요.",
    "어미반복": "같은 종결어미가 반복됩니다. 어미를 섞으세요.",
    "literary_style": "'~했다', '~이다' 같은 기사체를 썼습니다. 존댓말 구어체로 쓰세요.",
    "미확인수치": "입력에 없는 숫자를 만들었습니다. 입력에 적힌 숫자만 그대로 쓰세요.",
}


def _hint(errs: list[str]) -> str:
    out = []
    for e in errs:
        for k, msg in _HINTS.items():
            if k in e and msg not in out:
                out.append(msg)
    return "\n".join(f"- {m}" for m in out) or "- 규칙을 다시 확인하고 작성하세요."


def collect_fallbacks() -> list[str]:
    out = []
    for p in router.writers().values():
        out += (p.fallbacks or [])
    return out
