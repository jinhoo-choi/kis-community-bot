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


def main():
    ok = []
    print("── 1. 비용 원장 ──")
    ok += ledger_tests()
    print(f"\n{sum(ok)}/{len(ok)} passed")
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
