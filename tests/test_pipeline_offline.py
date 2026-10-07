"""main() 전 구간 오프라인 실행. 수집·모델·검색·텔레그램·상태 저장을 전부 가짜로 막는다.

입력은 저장된 자료만 쓴다.
  - flow  : data/market_cache.json (2026-10-02 기준 195건)
  - 비-flow: data/posts_latest.json 의 10-06 실발송 research 7·policy 6 (facts 원문)
  - research 6건(10-06 검색분): 상세 없음 템플릿으로 재구성(원문 아님)
가짜 작성자는 같은 id 의 실발송 본문(비-flow) 또는 문장틀 본문(flow)을 돌려준다.
10-06 전체 입력·응답 재현이 아니다. 경로·계약 검증용이다.
"""
import copy
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import main as M  # noqa: E402
from src import (generator, judge, state, stats, telegram_bot, tickers, trading,  # noqa: E402
                 crawl, enrich, template_reserve)
from src.llm import router, base  # noqa: E402
from src.llm.base import GenResult  # noqa: E402
from tests.test_cost_offline import SIX, _research_thin, _FakeSearch  # noqa: E402

HAIKU = "claude-haiku-4-5-20251001"


def run(name, cond, detail=""):
    print(("  OK  " if cond else "  FAIL") + f"  {name}" + (f"  ({detail})" if detail else ""))
    return cond


FLOW = json.load(open("data/market_cache.json", encoding="utf-8"))["items"]
POSTS = json.load(open("data/posts_latest.json", encoding="utf-8"))
NONFLOW = [{k: p[k] for k in ("id", "kind", "stock_code", "stock_name", "title", "facts", "src")}
           for p in POSTS if p["kind"] != "flow"]
BODY = {p["id"]: p["body"] for p in POSTS}
BODY.update({t["id"]: t["body"] for t in template_reserve.build(copy.deepcopy(FLOW), 400)})


class _Writer:
    def __init__(self, fail=False):
        self.fail, self.calls, self.fallbacks = fail, 0, None

    def available(self):
        return True

    def generate_many(self, jobs, temperature=1.0, **kw):
        out = []
        for _s, u in jobs:
            self.calls += 1
            iid = next((i for i in BODY if i.split("-")[-1] in u and f"({i.split('-')[-1]})" in u),
                       None)
            # 비-flow 는 제목으로 찾는다(종목코드가 같은 리포트가 여럿일 수 있다)
            iid = next((p["id"] for p in NONFLOW if p["title"] in u), iid)
            if self.fail or not iid:
                out.append(GenResult("", "claude", HAIKU, ok=False, error="mock fail"))
            else:
                out.append(GenResult(BODY[iid], "claude", HAIKU, request_id=f"w{self.calls}",
                                     input_tokens=3000, output_tokens=80))
        return out


_ORIG_PICK = generator.pick_style
_STYLE = {p["id"]: (p["tone"], p["angle"]) for p in POSTS}


def _pick_style(item, recent, used_now, allow_uncertainty=False):
    if item.get("id") in _STYLE:
        t, a = _STYLE[item["id"]]
        used_now.add((t, a, t, t))
        return t, a, t, t
    return _ORIG_PICK(item, recent, used_now, allow_uncertainty)


class _Judge:
    def __init__(self, scores):
        self.scores, self.calls = scores, 0

    def available(self):
        return True

    def generate(self, system, user, **kw):
        self.calls += 1
        return GenResult(json.dumps(self.scores, ensure_ascii=False), "claude",
                         "claude-sonnet-5", request_id=f"j{self.calls}",
                         input_tokens=2000, output_tokens=60)


def run_main(raw, writer, judge_scores, cost_priority=False):
    """main() 을 한 번 돌린다. (발송분, 경고, 예외, stats 행, 검색 호출 수)"""
    tmp = tempfile.mkdtemp()
    sent_box, warns, fake_search = [], [], _FakeSearch()
    jd = _Judge(judge_scores)
    patches = [
        (M, "collect", lambda: copy.deepcopy(raw)),
        (trading, "is_holiday", lambda *a: False),
        (state, "STATE_PATH", os.path.join(tmp, "state.json")),
        (stats, "PATH", os.path.join(tmp, "run_stats.jsonl")),
        (config, "OUTPUT_PATH", os.path.join(tmp, "posts.json")),
        (config, "IGNORE_SEEN", True),
        (config, "COST_PRIORITY_MODE", cost_priority),
        (config, "BATCH_STATE_PATH", os.path.join(tmp, "batch.json")),
        (enrich, "CACHE_PATH", os.path.join(tmp, "enrich_cache.json")),
        (enrich, "enricher", lambda: fake_search),
        (tickers, "listed", lambda: json.load(open("data/listed_cache.json",
                                                   encoding="utf-8"))["map"]),
        (crawl, "degraded_sources", lambda: []),
        (crawl, "health", lambda: {}),
        (router, "writers", lambda: {"claude": writer}),
        (judge, "judges", lambda: {"claude": jd, "claude_backup": jd}),
        (judge, "cross_judge_for", lambda w: "claude_backup"),
        (telegram_bot, "target_ready", lambda: (True, "")),
        (telegram_bot, "send_brief", lambda *a, **k: None),
        (telegram_bot, "send_all", lambda ps: sent_box.extend(ps) or list(ps)),
        (telegram_bot, "send_summary", lambda *a, **k: None),
        (telegram_bot, "send_warning", lambda t: warns.append(t)),
        (M, "dump_events", lambda p: p),
        # 실발송 본문이 있는 항목은 그 글을 쓴 페르소나·Angle 을 그대로 쓴다(가짜 작성자가
        # 프롬프트를 따르지 않으므로). 나머지는 실제 pick_style 이 고른다.
        (generator, "pick_style", _pick_style),
        (stats, "detail_log", lambda *a, **k: "(skip)"),
    ]
    saved = [(o, n, getattr(o, n)) for o, n, _ in patches]
    for o, n, v in patches:
        setattr(o, n, v)
    generator.REJECTED.clear()
    generator.STYLE_HELD.clear()
    generator._DEGRADED_WRITERS.clear()
    err = None
    argv = sys.argv
    sys.argv = ["main.py"]
    try:
        M.main()
    except BaseException as e:      # 목표 미달은 RuntimeError, 대상 없음은 SystemExit
        err = e
    finally:
        sys.argv = argv
        for o, n, v in saved:
            setattr(o, n, v)
    rows = [json.loads(x) for x in open(os.path.join(tmp, "run_stats.jsonl"), encoding="utf-8")]
    return sent_box, warns, err, rows[-1] if rows else {}, fake_search.calls


GOOD = {"factual": 5, "useful": 4, "natural": 4, "compliant": 5, "gain": 4, "fit": 4,
        "fatal": [], "reason": "mock"}


def main():
    ok = []
    print("── 5. 파이프라인 오프라인 ──")
    raw = FLOW + NONFLOW + [_research_thin(*x) for x in SIX]
    w = _Writer()
    sent, warns, err, row, searches = run_main(raw, w, GOOD)
    ids = [p["id"] for p in sent]
    kinds = {k: sum(p.get("kind") == k for p in sent) for k in ("flow", "research", "policy")}
    per_stock = max(sum(p.get("stock_code") == c for p in sent)
                    for c in {p.get("stock_code") for p in sent}) if sent else 0
    ok.append(run("정상 fixture: 발송 50건·고유 50·중복 0·종목상한 준수",
                  len(sent) == 50 and len(set(ids)) == 50 and err is None
                  and per_stock <= config.MAX_PER_STOCK,
                  f"{kinds}, 템플릿 {sum(p.get('provider') == 'template' for p in sent)}"))
    ok.append(run("정상 fixture: research·policy 공급 유지(특징주만으로 채우지 않음)",
                  kinds["research"] > 0 and kinds["policy"] > 0))
    ok.append(run("정상 fixture: research 6건 검색 0회, 사유 기록",
                  searches == 0 and row.get("enrich_skipped", {}).get(
                      "research:보강으로충족불가", 0) >= 6, str(row.get("enrich_skipped"))))
    u = row.get("llm_usage", {})
    ok.append(run("정상 fixture: 원장 신규 필드 기록",
                  "by_billing_mode" in u and u.get("billing_check") == "unverified"
                  and "style_held" in row and "enrich_outcome" in row))
    unsafe = [p["id"] for p in sent if (p.get("score") or {}).get("fatal")
              or (p.get("score") and (p["score"]["factual"] < 4 or p["score"]["compliant"] < 4))]
    ok.append(run("정상 fixture: 안전하지 않은 통과 0", not unsafe))

    # 장애 1: 작성 전면 실패 + 공급 부족(flow 20건) → 명시적 미달
    sent, warns, err, row, _ = run_main(FLOW[:20], _Writer(fail=True), GOOD)
    ok.append(run("장애: 작성 실패·공급 부족 → 미달 경고·예외, 성공 처리 안 함",
                  len(sent) < 50 and isinstance(err, RuntimeError)
                  and "미달" in str(err) and any("미달" in x for x in warns),
                  f"발송 {len(sent)}건, {err}"))
    ok.append(run("장애: 발송분은 검증 통과 문장틀뿐",
                  all(p.get("provider") == "template" for p in sent)))

    # 장애 2: 심사가 전부 fatal → LLM 글 발송 0 (문장틀 보장 모드만)
    bad = dict(GOOD, fatal=["입력에 없는 주장"])
    sent, warns, err, row, _ = run_main(FLOW + NONFLOW, _Writer(), bad)
    ok.append(run("장애: 심사 fatal 전건 → LLM 글 통과 0",
                  not any(p.get("provider") != "template" for p in sent),
                  f"발송 {len(sent)}건(문장틀)"))
    # 장애 3: 사실성 3점 → 통과 0
    low = dict(GOOD, factual=3)
    sent, warns, err, row, _ = run_main(FLOW + NONFLOW, _Writer(), low)
    ok.append(run("장애: 사실성 3점 전건 → LLM 글 통과 0",
                  not any(p.get("provider") != "template" for p in sent)))

    print(f"\n{sum(ok)}/{len(ok)} passed")
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
