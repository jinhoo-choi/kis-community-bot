"""비용 절감 변경의 오프라인 검증. 실제 모델·검색·텔레그램 호출은 하지 않는다.

모든 공급자 호출은 가짜 클라이언트로 대체한다. 네트워크 키가 없어도 돈다.
"""
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.llm import base  # noqa: E402
from src.llm.base import GenResult  # noqa: E402
from src.llm.claude import ClaudeProvider, _message_result  # noqa: E402

HAIKU = "claude-haiku-4-5-20251001"


def run(name, cond, detail=""):
    print(("  OK  " if cond else "  FAIL") + f"  {name}" + (f"  ({detail})" if detail else ""))
    return cond


def _msg(mid, text="본문", inp=0, out=0, cr=0, cw=0, web=0, model=HAIKU):
    blocks = [NS(type="text", text=text)] if text else []
    return NS(id=mid, model=model, content=blocks,
              usage=NS(input_tokens=inp, output_tokens=out,
                       cache_read_input_tokens=cr, cache_creation_input_tokens=cw,
                       server_tool_use=NS(web_search_requests=web),
                       service_tier="standard"))


def ledger_tests() -> list[bool]:
    ok = []
    base.reset_usage()
    # 표준: 입력 3,000(무캐시) + 출력 100 → (3000*1 + 100*5)/1e6
    base.record_usage(_message_result(_msg("m1", inp=3000, out=100), "claude", HAIKU),
                      "write", job_id="flow-1")
    # 배치: 같은 사용량, 단가 50%
    base.record_usage(_message_result(_msg("m2", inp=3000, out=100), "claude", HAIKU,
                                      billing_mode="batch"), "write", job_id="flow-2")
    # 캐시: 무캐시 1,000 + 읽기 2,000 + 쓰기 1,000 (input_tokens 는 무캐시분만 온다)
    base.record_usage(_message_result(_msg("m3", inp=1000, cr=2000, cw=1000, out=0),
                                      "claude", HAIKU), "write")
    # 검색: 토큰 + 1회 $0.01
    base.record_usage(_message_result(_msg("m4", inp=1000, out=0, web=1), "claude", HAIKU),
                      "enrich")
    s = base.usage_summary(delivered=2)
    exp = {
        "write_std": (3000 + 500) / 1e6,
        "write_batch": (3000 + 500) / 1e6 / 2,
        "cache": (1000 * 1.0 + 2000 * 0.10 + 1000 * 1.25) / 1e6,
        "search": 1000 / 1e6 + 0.01,
    }
    want = round(sum(exp.values()), 6)
    ok.append(run("표준/배치/캐시/검색 비용 분리 계산",
                  s["estimated_token_cost_usd"] == want
                  and s["by_billing_mode"]["batch"]["calls"] == 1
                  and s["by_billing_mode"]["standard"]["calls"] == 3
                  and s["uncached_input_tokens"] == 3000 + 3000 + 1000 + 1000
                  and s["cache_read_tokens"] == 2000 and s["cache_write_tokens"] == 1000
                  and s["grounding_queries"] == 1,
                  f"{s['estimated_token_cost_usd']} vs {want}"))

    # 같은 응답 재조회: 한 번만 합산
    before = s["estimated_token_cost_usd"]
    again = base.record_usage(_message_result(_msg("m1", inp=3000, out=100), "claude", HAIKU),
                              "write")
    batch_dup = [base.record_usage(GenResult("t", "claude", HAIKU, batch_id="b1",
                                             custom_id="j0_x", output_tokens=10,
                                             billing_mode="batch"), "write")
                 for _ in range(2)]
    s = base.usage_summary()
    ok.append(run("같은 message/batch 결과 재기록 시 1회만 합산",
                  again is False and batch_dup == [True, False]
                  and s["calls"] == 5
                  and s["estimated_token_cost_usd"] == round(before + 10 * 5 / 1e6 / 2, 6)))

    # 처리 여부 불명확: 0원 합산 금지, 별도 집계
    base.record_usage(GenResult("", "claude", HAIKU, ok=False, error="timeout",
                                cost_status="unconfirmed"), "write")
    s = base.usage_summary()
    ok.append(run("처리 불명확 요청은 '비용 미확정'으로 분리",
                  s["unconfirmed_cost_calls"] == 1 and s["cost_complete"] is False
                  and s["billed_cost_usd"] is None and s["billing_check"] == "unverified"))

    # 기존 필드 호환
    legacy = {"pricing_as_of", "cost_scope", "calls", "input_tokens", "output_tokens",
              "thinking_tokens", "cache_read_tokens", "cache_write_tokens",
              "grounding_queries", "failed_calls", "estimated_token_cost_usd",
              "unknown_cost_calls", "api_attempts", "cost_per_delivered_usd",
              "by_route", "by_attempt_type"}
    ok.append(run("기존 llm_usage 필드 유지(추가만)", legacy <= set(s)))
    ev = base._USAGE_EVENTS[0]
    ok.append(run("원장에 run/job/request id 연결",
                  ev["run_id"] and ev["job_id"] == "flow-1" and ev["request_id"] == "m1"))

    # 캐시 예열: 본문 없는 정상 응답을 prewarm 역할로 기록
    base.reset_usage()
    calls = []

    class _Msgs:
        def create(self, **kw):
            calls.append(kw)
            # 10-06 run_log: 예열 1회차 write 4,697 / 2회차 read 4,697, 고정부 8토큰
            n = len(calls)
            return _msg(f"pw{n}", text="", inp=8,
                        cw=4697 if n == 1 else 0, cr=4697 if n == 2 else 0)

    cp = ClaudeProvider.__new__(ClaudeProvider)
    cp.model, cp.name, cp._client = HAIKU, "claude", NS(messages=_Msgs())
    jobs = [([{"type": "text", "text": "고정부", "cache_control": {"type": "ephemeral"}}], "u")]
    cp._prewarm(jobs)
    cp._prewarm(jobs)
    s = base.usage_summary()
    pw = s["by_route"].get(f"claude|{HAIKU}|paid|prewarm", {})
    want = round((8 + 4697 * 1.25 + 8 + 4697 * 0.10) / 1e6, 6)
    ok.append(run("캐시 예열 2회 비용이 prewarm 역할로 원장 포함",
                  pw.get("calls") == 2 and pw.get("failed_calls") == 0
                  and pw.get("estimated_token_cost_usd") == want,
                  f"{pw.get('estimated_token_cost_usd')} (10-06 누락분 추정 $0.00636)"))

    class _Boom:
        def create(self, **kw):
            raise type("APITimeoutError", (Exception,), {})("timeout")
    cp._client = NS(messages=_Boom())
    cp._prewarm(jobs)
    s = base.usage_summary()
    ok.append(run("예열 타임아웃은 실패+비용 미확정(0원 아님)",
                  s["unconfirmed_cost_calls"] == 1))
    base.reset_usage()
    return ok


# 10-06 에 검색한 research 6건. 원본 facts 는 저장돼 있지 않아 fetch_naver_api 의
# '상세 없음' 템플릿과 enrich_cache 의 종목명으로 재구성했다(원문 재현 아님).
SIX = [("naver-api-96427", "삼양식품", "003230"), ("naver-api-96425", "NAVER", "035420"),
       ("naver-api-96422", "대한항공", "003490"), ("naver-api-96420", "LG전자", "066570"),
       ("naver-api-96419", "더블유게임즈", "192080"), ("naver-api-96415", "뷰웍스", "100120")]


def _research_thin(i, name, code):
    return {"id": i, "kind": "research", "stock_code": code, "stock_name": name,
            "title": f"{name} 리포트",
            "facts": (f"종목: {name} ({code})\n리포트 제목: {name} 리포트\n"
                      "발간: 테스트증권 / 2026-10-02\n"
                      "※ 제시 수치는 증권사 의견이며 단정하지 말 것.\n"
                      "※ 목표주가·투자의견 미제공. 추정하지 말 것."), "src": "u"}


def _policy_thin(i="pol-x", title="정부, 반도체 소부장 세제지원 확대 발표"):
    return {"id": i, "kind": "policy", "stock_code": None, "stock_name": None,
            "title": title,
            "facts": f"출처: 테스트\n제목: {title}\n요지: {title}\n"
                     "※ 수혜 종목을 특정하거나 추천하지 말 것.", "src": "u"}


class _FakeSearch:
    def __init__(self):
        self.calls = 0

    def available(self):
        return True

    def search(self, *a, **k):
        self.calls += 1
        return GenResult("- 반도체 소부장 지원은 2026-10-01 발표됐다", "claude", HAIKU,
                         sources=[{"url": "https://example.org/a", "title": "t"}],
                         request_id=f"s{self.calls}", input_tokens=1000,
                         grounding_queries=1)


def enrich_tests() -> list[bool]:
    import json
    import tempfile
    import main as M
    from src import enrich, gate, tickers
    # 상장사 목록은 네트워크 대신 저장된 캐시만 쓴다
    _lc = json.load(open("data/listed_cache.json", encoding="utf-8"))["map"]
    tickers.listed = lambda: _lc
    ok = []
    base.reset_usage()
    cnt = {"research": 21, "policy": 12, "flow": 195, "disclosure": 0}
    six = [_research_thin(*x) for x in SIX]
    _, blocked = gate.apply([dict(x) for x in six])
    ok.append(run("재구성 research 6건은 기존 게이트에서 tier5:글감부족",
                  [w for _, w in blocked] == ["tier5:글감부족"] * 6))
    reasons = [M._enrich_skip_reason(x, {}, cnt) for x in six]
    ok.append(run("research 6건은 검색 전 '보강으로충족불가'로 제외",
                  reasons == ["research:보강으로충족불가"] * 6))
    # 게이트 기준은 그대로: 회사 배경을 붙여도 research 는 막히고, 요지가 있으면 통과
    bg = dict(six[0], facts=six[0]["facts"] + "\n\n[검색으로 확인된 배경]\n- 주력 사업은 라면")
    gist = dict(six[0], facts=six[0]["facts"] + "\n리포트 요지: 3분기 영업이익 1,200억원 전망")
    ok.append(run("research 게이트 완화 없음(배경≠요지, 요지는 통과)",
                  not gate.has_substance(bg) and gate.has_substance(gist)))

    fake = _FakeSearch()
    orig_enricher, orig_path = enrich.enricher, enrich.CACHE_PATH
    tmp = tempfile.mkdtemp()
    enrich.enricher = lambda: fake
    enrich.CACHE_PATH = os.path.join(tmp, "enrich_cache.json")
    try:
        pool = [x for x in six + [_policy_thin()]
                if not M._enrich_skip_reason(x, {}, cnt)]
        enrich.enrich_all(pool, workers=5)
        ok.append(run("rescue 풀: research 검색 0회, 정책 1회만 호출",
                      fake.calls == 1 and [x["id"] for x in pool] == ["pol-x"]))
        ok.append(run("정상 정책 보강은 게이트 통과(기존 경로 유지)",
                      gate.has_substance(pool[0]) and pool[0].get("enriched")))
        # 캐시 재사용: 같은 id 를 다시 보강하면 호출 0
        again = [_policy_thin()]
        enrich.enrich_all(again, workers=5)
        cached = json.load(open(enrich.CACHE_PATH, encoding="utf-8"))
        ok.append(run("보강 캐시 재사용(재호출 0)",
                      fake.calls == 1 and again[0].get("enriched")
                      and cached["pol-x"]["status"] == "ok"))
    finally:
        enrich.enricher, enrich.CACHE_PATH = orig_enricher, orig_path
    # 중복·게시판 부적합·슬롯 충족은 검색 전에 제외
    seen = {"ID::pol-dup": "2026-10-05"}
    ok.append(run("중복 항목은 검색 전 제외",
                  M._enrich_skip_reason(_policy_thin("pol-dup"), seen, cnt) == "중복"))
    ok.append(run("제목 기준 게시판 부적합(채용)은 검색 전 제외",
                  M._enrich_skip_reason(_policy_thin("pol-job", "금감원 청년 채용 박람회 개최"),
                                        {}, cnt) == "게시판부적합"))
    full = dict(cnt, policy=999)
    ok.append(run("이미 기대량이 슬롯을 채운 유형은 검색 전 제외",
                  M._enrich_skip_reason(_policy_thin(), {}, full) == "슬롯충족"))

    # 실데이터 대조: 10-06 원장의 보강 비용 = 이번 변경으로 회피되는 호출
    row = [json.loads(x) for x in open("data/run_stats.jsonl", encoding="utf-8")
           if x.startswith('{"ts": "2026-10-06')][-1]
    er = row["llm_usage"]["by_route"]["claude|claude-haiku-4-5-20251001|paid|enrich"]
    ec = json.load(open("data/enrich_cache.json", encoding="utf-8"))
    ok.append(run("10-06 대조: 검색 6건 전부 research·캐시 ok, 회피 가능 $0.180627",
                  er["calls"] == 6 and er["estimated_token_cost_usd"] == 0.180627
                  and all(ec.get(i, {}).get("status") == "ok" for i, _, _ in SIX)
                  and row["enrich_delivered"] == 0))
    base.reset_usage()
    return ok


def preflight_tests() -> list[bool]:
    import json
    import random
    import main as M
    from src import generator as G, facts, filters, personas as P
    ok = []
    flow = json.load(open("data/market_cache.json", encoding="utf-8"))["items"]
    facts.annotate_terms(flow)
    posts = json.load(open("data/posts_latest.json", encoding="utf-8"))
    random.seed(7)
    picks = [(it, G.pick_style(dict(it), {}, set())) for it in flow + posts]
    held = [it["id"] for it, st in picks if not st[0]]
    bad = [it["id"] for it, (pid, ang, _, _) in picks
           if pid and ang and not P.v2.compatible(pid, ang)]
    nonflow = [st[0] for it, st in picks if it["kind"] != "flow"]
    ok.append(run("실데이터 245건(flow 캐시+10-06 발송분): 보류 0, 계약 밖 조합 0",
                  not held and not bad and all(nonflow),
                  f"비-flow 페르소나 {sorted(set(nonflow))}"))

    # 요건을 만족하는 조합이 없는 정책 요지(주장 1개, duration 앵글만 가능)
    t = "금감원 '청년금융특강' 신청 접수"
    thin = {"id": "pol-t", "kind": "policy", "title": t,
            "facts": f"출처: 테스트\n보도 시각: 2026-10-06 06:00 KST\n제목: {t}\n"
                     "요지: 금융감독원은 19일까지 신청을 받는다고 밝혔다.\n※ 단정하지 말 것."}
    # 종전 fallback(ceb95c0): 사실 요건을 보지 않고 호환 Angle 이 있는 기본 후보를 다시 열어
    # 최단 페르소나를 썼다. CI 는 얕은 checkout 이라 git 이력 대신 그 규칙을 그대로 계산한다.
    from src import angles, claims
    cand = angles.available(thin)
    base_w = P.style_ids()["policy"]
    old_pool = [pid for pid, w in base_w.items()
                if w > 0 and any(P.v2.compatible(pid, a) for a in cand)]
    old_pid = min(old_pool, key=lambda pid: P.len_bounds(pid)[0]) if old_pool else ""
    it = dict(thin)
    new = G.pick_style(it, {}, set())
    ok.append(run("부적합 조합: 종전 fallback 은 생성, 변경 후 호출 없이 보류",
                  old_pid and new[0] == "" and it.get("_style_hold", "").startswith("조합없음"),
                  f"종전={old_pid} 주장{len(claims.build(thin))}개·사실슬롯{facts.count(thin)}"))
    G.STYLE_HELD.clear()

    class _NoCall:
        def available(self):
            return True

        def generate_many(self, jobs, **kw):
            raise AssertionError("보류 후보에 대해 LLM 이 호출됨")
    orig = G.router.writers
    G.router.writers = lambda: {"claude": _NoCall()}
    try:
        made = G.generate([dict(thin)], {})
    finally:
        G.router.writers = orig
    ok.append(run("보류 후보는 작성 호출 0, 사유 기록",
                  made == [] and G.STYLE_HELD and G.STYLE_HELD[0]["id"] == "pol-t"))
    G.STYLE_HELD.clear()

    # 회귀: flow-2026-10-02-079550 '시가 대비 마감: 4.8% 높은 수준' 의 기준 누락
    # posts_latest 는 정기 실행마다 교체된다. 실측 회귀는 고정 원문으로 재현한다.
    p = json.load(open("tests/fixtures/basis_regressions.json", encoding="utf-8"))[
        "missing_close_basis"]
    errs = filters.check(p["body"], p["facts"], p.get("fmt"), p.get("angle"),
                         p.get("length"), None, False, p["kind"], p["stock_code"])
    fixed = p["body"].replace("종가는 4.8% 높은", "종가는 시가 대비 4.8% 높은")
    ok.append(run("회귀 079550: 비교기준 누락은 리젝, 기준을 살리면 통과",
                  any(e.startswith("비교기준누락") for e in errs)
                  and not filters._basis_errors(fixed, p["facts"]),
                  str(errs)))
    f = p["facts"]
    ok.append(run("귀속(기관)·비중 기준(거래대금)·날짜·배수 기준 보존 검사",
                  filters._basis_errors("169억원 순매수가 들어왔습니다.", f)
                  and not filters._basis_errors("기관이 169억원 순매수했습니다.", f)
                  and filters._basis_errors("기관 순매수는 23.6% 비중이었어요.", f)
                  and not filters._basis_errors("기관 순매수는 거래대금 대비 23.6%였어요.", f)
                  and filters._basis_errors("10월 3일 종가는 759,000원입니다.", f)
                  and not filters._basis_errors("10월 2일 종가는 759,000원입니다.", f)
                  and filters._basis_errors("등락 크기는 2.3배였습니다.", f)
                  and not filters._basis_errors("평균 등락폭의 2.3배였습니다.", f)))
    # 실발송 50건·문장틀 66건 오탐
    from src import template_reserve as T
    res = T.build(flow, 200)
    fp = [x["id"] for x in posts if filters._basis_errors(x["body"], x["facts"])]
    fpt = [x["id"] for x in res if filters._basis_errors(x["body"], x["facts"])]
    expected = [p["id"]] if any(x["id"] == p["id"] for x in posts) else []
    ok.append(run("기준 검사 오탐: 현재 발송분의 알려진 079550 외 0건, 문장틀 0건",
                  fp == expected and not fpt, f"{len(res)}개 문장틀"))

    # 마지막 소량 부족분: 10-06 3단계(부족 1, 누적 44/97)
    ok.append(run("소량 부족분 묶음 10→6 (하한=추정치×2)",
                  M._next_stage_size(131, 1, 97, 44) == 6
                  and M._next_stage_size(168, 45, 0, 0) == 60
                  and M._next_stage_size(131, 17, 60, 28) == 37))
    return ok


class _FakeBatches:
    """Message Batches API 가짜. retrieve 상태와 결과를 시나리오대로 돌려준다."""

    def __init__(self, statuses, plan, results_fail=0, cancel_fail=False):
        self.statuses, self.plan = list(statuses), plan
        self.results_fail, self.cancel_fail = results_fail, cancel_fail
        self.created, self.reqs = [], {}
        self.cancels = self.results_calls = 0

    def create(self, requests):
        bid = f"msgbatch_fake{len(self.created) + 1}"
        self.created.append(bid)
        self.reqs[bid] = [r["custom_id"] for r in requests]
        return NS(id=bid)

    def retrieve(self, bid):
        st = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return NS(processing_status=st)

    def cancel(self, bid):
        self.cancels += 1
        if self.cancel_fail:
            raise RuntimeError("cancel failed")

    def results(self, bid):
        self.results_calls += 1
        cids = self.reqs[bid]
        fail = self.results_fail > 0
        self.results_fail -= 1
        for k, cid in enumerate(cids):
            if fail and k == 5:
                raise ConnectionError("stream cut")
            kind, err = self.plan(k)
            if kind == "succeeded":
                yield NS(custom_id=cid, result=NS(type=kind, message=_msg(
                    f"m-{bid}-{cid}", inp=1000, out=50)))
            else:
                yield NS(custom_id=cid, result=NS(type=kind, error=NS(error=NS(type=err))))


def _provider(fb):
    cp = ClaudeProvider.__new__(ClaudeProvider)
    cp.model, cp.name, cp._no_temp, cp.use_batch, cp.fallbacks = HAIKU, "claude", False, True, None
    cp.sync_calls = 0

    def _prewarm_create(**kw):
        return _msg(f"pw-{id(kw)}", text="", inp=8, cw=4697)
    cp._client = NS(messages=NS(batches=fb, create=_prewarm_create))
    cp._client.with_options = lambda **kw: cp._client

    def _gen(system, user, temperature=1.0, max_tokens=700):
        cp.sync_calls += 1
        return GenResult("동기 본문", "claude", HAIKU, request_id=f"sync-{user}",
                         input_tokens=1000, output_tokens=50)
    cp.generate = _gen
    return cp


def _jobs(n=20, tag="a"):
    sysblk = [{"type": "text", "text": "고정부", "cache_control": {"type": "ephemeral"}}]
    return [(sysblk, f"{tag}-{i}") for i in range(n)]


def batch_tests() -> list[bool]:
    import json
    import tempfile
    import config
    ok = []
    ok_all = lambda rs: all(r.ok for r in rs)
    orig = (config.BATCH_STATE_PATH, config.BATCH_CANCEL_WAIT_SEC)
    config.BATCH_STATE_PATH = os.path.join(tempfile.mkdtemp(), "batch_jobs.json")
    config.BATCH_CANCEL_WAIT_SEC = 0
    state = lambda: json.load(open(config.BATCH_STATE_PATH, encoding="utf-8"))
    try:
        # A. 정상 완료
        base.reset_usage()
        fb = _FakeBatches(["in_progress", "ended"], lambda k: ("succeeded", ""))
        cp = _provider(fb)
        rs = cp.generate_many(_jobs(), poll_sec=0, timeout_sec=5)
        for r in rs:
            base.record_usage(r, "write")
        u = base.usage_summary()
        ok.append(run("배치 정상 완료: 20건 회수, 동기 0, 취소 0, 배치 단가 기록",
                      ok_all(rs) and cp.sync_calls == 0 and fb.cancels == 0
                      and u["by_billing_mode"]["batch"]["calls"] == 20
                      and list(state().values())[0]["status"] == "collected"))

        # B. 시간 초과 → 취소 요청 → 종료(성공 8, 취소 12): 성공분 회수, 취소분만 재시도
        base.reset_usage()
        fb = _FakeBatches(["in_progress", "ended"],
                          lambda k: ("succeeded", "") if k < 8 else ("canceled", ""))
        cp = _provider(fb)
        rs = cp.generate_many(_jobs(tag="b"), poll_sec=0, timeout_sec=0)
        for r in rs:
            base.record_usage(r, "write")
        u = base.usage_summary()
        ok.append(run("취소 중 일부 성공: 성공 8 회수, 미처리 12만 동기, 이후 동기 전환",
                      ok_all(rs) and fb.cancels == 1 and cp.sync_calls == 12
                      and u["by_billing_mode"]["batch"]["calls"] == 8
                      and u["by_attempt_type"]["batch_retry"]["calls"] == 12
                      and cp.use_batch is False))

        # C. 취소 실패 + 종료 불명: 무한 대기·전량 재호출·0원 처리 금지
        base.reset_usage()
        fb = _FakeBatches(["in_progress"], lambda k: ("succeeded", ""), cancel_fail=True)
        cp = _provider(fb)
        rs = cp.generate_many(_jobs(tag="c"), poll_sec=0, timeout_sec=0)
        for r in rs:
            base.record_usage(r, "write")
        u = base.usage_summary()
        ok.append(run("취소 실패·종료 불명: 동기 0, 20건 비용 미확정, 비용 완료 아님",
                      not any(r.ok for r in rs) and cp.sync_calls == 0
                      and u["unconfirmed_cost_calls"] == 20 and u["cost_complete"] is False))

        # C'. 다음 실행: 그 배치가 끝나 있으면 결과 비용을 한 번만 원장에(작업은 다름)
        base.reset_usage()
        fb.statuses = ["ended"]
        cp2 = _provider(fb)
        cp2.generate_many(_jobs(16, tag="next"), poll_sec=0, timeout_sec=5)
        u1 = base.usage_summary()
        cp3 = _provider(fb)
        cp3.generate_many(_jobs(16, tag="next2"), poll_sec=0, timeout_sec=5)
        u2 = base.usage_summary()
        orphan = lambda u: u["by_attempt_type"].get("orphan_batch", {}).get("calls", 0)
        ok.append(run("재시작 복구: 이전 배치 성공분 비용 20건 1회만 합산",
                      orphan(u1) == 20 and orphan(u2) == 20))

        # D. 결과 다운로드 중단 1회 → 결과 조회만 재시도, 재제출·동기 0
        base.reset_usage()
        os.remove(config.BATCH_STATE_PATH)
        fb = _FakeBatches(["ended"], lambda k: ("succeeded", ""), results_fail=1)
        cp = _provider(fb)
        rs = cp.generate_many(_jobs(tag="d"), poll_sec=0, timeout_sec=5)
        ok.append(run("결과 조회 중단: 조회만 재시도(2회), 제출 1회, 동기 0",
                      ok_all(rs) and fb.results_calls == 2 and len(fb.created) == 1
                      and cp.sync_calls == 0))

        # E. 결과 조회가 계속 실패 → 미확정으로 남김, 재시작 시 같은 배치를 찾아 회수
        base.reset_usage()
        os.remove(config.BATCH_STATE_PATH)
        fb = _FakeBatches(["ended"], lambda k: ("succeeded", ""), results_fail=3)
        cp = _provider(fb)
        rs = cp.generate_many(_jobs(tag="e"), poll_sec=0, timeout_sec=5)
        first = (sum(r.ok for r in rs) == 5 and cp.sync_calls == 0
                 and sum(r.cost_status == "unconfirmed" for r in rs) == 15
                 and list(state().values())[0]["status"] == "ended_uncollected")
        base.reset_usage()                       # 재시작 = 새 프로세스
        cp2 = _provider(fb)
        rs2 = cp2.generate_many(_jobs(tag="e"), poll_sec=0, timeout_sec=5)
        for r in rs2:
            base.record_usage(r, "write")
        u = base.usage_summary()
        ok.append(run("결과 일부 미수신 → 받은 5건 사용·15건 미확정, 재시작 시 같은 배치 "
                      "재사용(재제출 0)·이미 기록한 5건 비용 재합산 0",
                      first and ok_all(rs2) and len(fb.created) == 1
                      and cp2.sync_calls == 0
                      and u["by_attempt_type"]["batch_reuse"]["estimated_token_cost_usd"] == 0
                      and u["by_attempt_type"]["initial"]["calls"] == 15))

        # F. 처리 중 재시작: 같은 작업이면 기존 배치를 기다린다(중복 제출 금지)
        base.reset_usage()
        os.remove(config.BATCH_STATE_PATH)
        fb = _FakeBatches(["in_progress"], lambda k: ("succeeded", ""), cancel_fail=True)
        cp = _provider(fb)
        cp.generate_many(_jobs(tag="f"), poll_sec=0, timeout_sec=0)
        fb.statuses = ["in_progress", "ended"]
        cp2 = _provider(fb)
        rs2 = cp2.generate_many(_jobs(tag="f"), poll_sec=0, timeout_sec=5)
        ok.append(run("처리 중 재시작: 기존 배치 조회·대기, 제출 1회 유지",
                      ok_all(rs2) and len(fb.created) == 1 and cp2.sync_calls == 0))

        # G. errored: 잘못된 요청은 재시도 안 함, 서버 오류만 재시도
        base.reset_usage()
        os.remove(config.BATCH_STATE_PATH)
        fb = _FakeBatches(["ended"], lambda k: (
            ("errored", "invalid_request_error") if k == 0 else
            ("errored", "api_error") if k == 1 else ("succeeded", "")))
        cp = _provider(fb)
        rs = cp.generate_many(_jobs(tag="g"), poll_sec=0, timeout_sec=5)
        ok.append(run("errored: invalid_request 재시도 0, api_error 1건만 동기",
                      not rs[0].ok and rs[1].ok and cp.sync_calls == 1))

        # H. 동기 모드에서도 과거 미완료 배치를 원장에 표시한다.
        base.reset_usage()
        os.remove(config.BATCH_STATE_PATH)
        fb = _FakeBatches(["in_progress"], lambda k: ("succeeded", ""), cancel_fail=True)
        _provider(fb).generate_many(_jobs(tag="h"), poll_sec=0, timeout_sec=0)
        base.reset_usage()
        cp = _provider(fb)
        cp.use_batch = False
        rs = cp.generate_many(_jobs(3, tag="new-h"))
        u = base.usage_summary()
        ok.append(run("동기 모드: 잔여 20건 미확정 표시, 신규 3건만 동기",
                      ok_all(rs) and cp.sync_calls == 3 and len(fb.created) == 1
                      and u["pending_batch_requests"] == 20 and not u["cost_complete"]))

        # 기존 작업 일부 + 신규 작업. 미완료 기존 작업은 재호출하지 않는다.
        base.reset_usage()
        cp = _provider(fb)
        cp.use_batch = False
        rs = cp.generate_many(_jobs(3, tag="h") + _jobs(2, tag="new-h2"))
        for r in rs:
            base.record_usage(r, "write")
        u = base.usage_summary()
        ok.append(run("동기 혼합 작업: 미완료 기존 3건 보류, 신규 2건만 생성",
                      not any(r.ok for r in rs[:3]) and ok_all(rs[3:])
                      and cp.sync_calls == 2 and len(fb.created) == 1
                      and u["unconfirmed_cost_calls"] == 3
                      and u["pending_batch_requests"] == 20))

        # I. 끝난 잔여 배치는 동기 모드에서 비용만 1회 회수한다.
        base.reset_usage()
        fb.statuses = ["ended"]
        cp = _provider(fb)
        cp.use_batch = False
        cp.generate_many(_jobs(3, tag="new-i"))
        u1 = base.usage_summary()
        cp.generate_many(_jobs(3, tag="new-i2"))
        u2 = base.usage_summary()
        ok.append(run("동기 복구: 이전 성공 20건 비용 1회만 기록, 신규 배치·예열 0",
                      orphan(u1) == 20 and orphan(u2) == 20
                      and u1["pending_batch_requests"] == 0 and u1["cost_complete"]
                      and cp.sync_calls == 6 and len(fb.created) == 1))

        # J. 배치 설정이 켜져 있어도 소량 작업의 복구를 건너뛰지 않는다.
        base.reset_usage()
        os.remove(config.BATCH_STATE_PATH)
        fb = _FakeBatches(["in_progress"], lambda k: ("succeeded", ""), cancel_fail=True)
        _provider(fb).generate_many(_jobs(tag="j"), poll_sec=0, timeout_sec=0)
        base.reset_usage()
        fb.statuses = ["ended"]
        cp = _provider(fb)
        cp.generate_many(_jobs(3, tag="new-j"))
        u = base.usage_summary()
        ok.append(run("15건 미만 복구: 이전 성공 20건 비용 회수, 신규 3건만 동기",
                      orphan(u) == 20 and cp.sync_calls == 3 and len(fb.created) == 1))

        # K. 일부 결과 비용을 이미 기록한 배치 + 새 작업의 재시작 복구.
        base.reset_usage()
        os.remove(config.BATCH_STATE_PATH)
        fb = _FakeBatches(["ended"], lambda k: ("succeeded", ""), results_fail=3)
        cp = _provider(fb)
        cp.generate_many(_jobs(tag="k"), poll_sec=0, timeout_sec=0)
        base.reset_usage()
        cp = _provider(fb)
        cp.use_batch = False
        rs = cp.generate_many(_jobs(3, tag="k") + _jobs(2, tag="new-k"))
        for r in rs:
            base.record_usage(r, "write")
        u = base.usage_summary()
        ok.append(run("동기 일부 재사용: 이미 기록한 3건 재생성·비용 재합산 0",
                      ok_all(rs) and cp.sync_calls == 2 and len(fb.created) == 1
                      and u["by_attempt_type"]["batch_reuse"]["calls"] == 3
                      and u["by_attempt_type"]["batch_reuse"]["estimated_token_cost_usd"] == 0))

        # L. 서버 접수 뒤 제출 응답 유실: 준비 기록·미확정 비용을 보존한다.
        class _AcceptedThenLost(_FakeBatches):
            error_name = "APITimeoutError"

            def create(self, requests):
                super().create(requests)
                raise type(self.error_name, (Exception,), {})("accepted; response lost")

        for error_name in ("APITimeoutError", "APIConnectionError"):
            base.reset_usage()
            os.remove(config.BATCH_STATE_PATH)
            fb = _AcceptedThenLost(["in_progress"], lambda k: ("succeeded", ""))
            fb.error_name = error_name
            cp = _provider(fb)
            rs = cp.generate_many(_jobs(tag="l"), poll_sec=0, timeout_sec=0)
            for r in rs:
                base.record_usage(r, "write")
            u = base.usage_summary()
            ok.append(run(f"제출 {error_name}: 동기 0·준비 기록 보존·20건 미확정",
                          not any(r.ok for r in rs) and cp.sync_calls == 0
                          and len(fb.created) == 1 and len(state()) == 1
                          and u["unconfirmed_cost_calls"] == 20
                          and u["pending_batch_requests"] == 20 and not u["cost_complete"]))

            # 재시작 후 배치·동기 설정 모두 같은 요청을 재제출하지 않는다.
            blocked = True
            for batch_mode in (False, True):
                base.reset_usage()
                cp2 = _provider(fb)
                cp2.use_batch = batch_mode
                rs2 = cp2.generate_many(_jobs(tag="l"), poll_sec=0, timeout_sec=0)
                blocked &= (not any(r.ok for r in rs2) and cp2.sync_calls == 0
                            and len(fb.created) == 1
                            and base.usage_summary()["pending_batch_requests"] == 20)
            ok.append(run(f"제출 {error_name} 재시작: 배치·동기 모두 재제출 0", blocked))

        # M. 미접수가 명확한 제출 오류는 동기 폴백을 유지한다.
        class _Rejected(_FakeBatches):
            def create(self, requests):
                raise ValueError("invalid request")

        base.reset_usage()
        os.remove(config.BATCH_STATE_PATH)
        fb = _Rejected(["ended"], lambda k: ("succeeded", ""))
        cp = _provider(fb)
        rs = cp.generate_many(_jobs(tag="m"))
        ok.append(run("명시적 제출 거절: 기존 동기 20건 폴백 유지",
                      ok_all(rs) and cp.sync_calls == 20 and not fb.created
                      and not os.path.exists(config.BATCH_STATE_PATH)
                      and base.usage_summary()["pending_batch_requests"] == 0))

        # N. 한 실행에서 미완료로 조회한 배치가 끝나면 동일 작업도 회수한다.
        base.reset_usage()
        fb = _FakeBatches(["in_progress"], lambda k: ("succeeded", ""), cancel_fail=True)
        _provider(fb).generate_many(_jobs(tag="n"), poll_sec=0, timeout_sec=0)
        base.reset_usage()
        cp = _provider(fb)
        cp.use_batch = False
        cp.generate_many(_jobs(1, tag="new-n"))
        pending = base.usage_summary()["pending_batch_requests"]
        fb.statuses = ["ended"]
        rs = cp.generate_many(_jobs(tag="n"), poll_sec=0, timeout_sec=0)
        for r in rs:
            base.record_usage(r, "write")
        ok.append(run("동일 실행 재조회: 잔여 배치 완료 회수·pending 해제·재제출 0",
                      pending == 20 and ok_all(rs) and cp.sync_calls == 1
                      and len(fb.created) == 1
                      and base.usage_summary()["pending_batch_requests"] == 0))

        # O. 실제 SDK도 제출 응답 유실을 내부 재시도하지 않아야 한다.
        import anthropic
        from unittest.mock import patch
        for error_name in ("APITimeoutError", "APIConnectionError"):
            base.reset_usage()
            os.remove(config.BATCH_STATE_PATH)
            with patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                         if not k.lower().endswith("_proxy")}, clear=True):
                client = anthropic.Anthropic(api_key="offline-dummy")
            cp = _provider(fb)
            cp._client = client
            cp._prewarm = lambda jobs: None
            requests = []

            def accepted_then_lost(request, **kw):
                requests.append(request)
                raise getattr(anthropic, error_name)(request=request)

            try:
                with patch.object(client._client, "send", side_effect=accepted_then_lost), \
                        patch.object(type(client), "_sleep_for_retry", return_value=None):
                    rs = cp.generate_many(_jobs(tag=error_name), poll_sec=0, timeout_sec=0)
                    blocked = True
                    for batch_mode in (False, True):
                        base.reset_usage()
                        cp.use_batch = batch_mode
                        again = cp.generate_many(_jobs(tag=error_name), poll_sec=0,
                                                 timeout_sec=0)
                        blocked &= not any(r.ok for r in again)
                ok.append(run(f"실제 SDK {error_name}: 제출 1회·재시작 재제출 0",
                              len(requests) == 1 and cp.sync_calls == 0 and blocked
                              and not any(r.ok for r in rs)
                              and client.max_retries == 2
                              and all(r["status"] == "submission_unknown"
                                      for r in state().values())
                              and base.usage_summary()["pending_batch_requests"] == 20,
                              f"POST {len(requests)}회"))
            finally:
                client.close()
    finally:
        config.BATCH_STATE_PATH, config.BATCH_CANCEL_WAIT_SEC = orig
        base.reset_usage()
    return ok


def main():
    ok = []
    print("── 1. 비용 원장 ──")
    ok += ledger_tests()
    print("── 2. 보강 자격 ──")
    ok += enrich_tests()
    print("── 3. 생성 전 검사 ──")
    ok += preflight_tests()
    print("── 4. 배치 ──")
    ok += batch_tests()
    print(f"\n{sum(ok)}/{len(ok)} passed")
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
