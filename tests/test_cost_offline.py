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


def main():
    ok = []
    print("── 1. 비용 원장 ──")
    ok += ledger_tests()
    print("── 2. 보강 자격 ──")
    ok += enrich_tests()
    print(f"\n{sum(ok)}/{len(ok)} passed")
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
