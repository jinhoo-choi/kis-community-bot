"""Delivery-first cost profile. All provider/delivery behavior is mocked offline."""
import contextlib
import copy
import io
import json
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from src import decide, filters, generator, judge, template_reserve, state
from src.llm import base
from src.llm.claude import ClaudeProvider, _message_result
from tests.test_pipeline_offline import FLOW, POSTS, _Writer, run_main

CHECKS = 0


def run(name, condition):
    global CHECKS
    CHECKS += 1
    print(("  OK  " if condition else "  FAIL") + "  " + name)
    assert condition, name


def post(i, *, total=10, factual=4, compliant=4, fatal=None, kind="flow"):
    return {"id": str(i), "kind": kind, "stock_code": str(i), "tone": "",
            "body": f"확인한 사실 {i}", "provider": "claude",
            "score": dict(factual=factual, compliant=compliant, fit=1, total=total,
                          fatal=[] if fatal is None else fatal)}


def main():
    config.COST_PRIORITY_MODE = True
    sent, _ = decide.decide_distribution([post("low")])
    run("total 10 and fit 1 can pass", len(sent) == 1)
    sent, _ = decide.decide_distribution([post("under", total=9.3)])
    run("flow still has an explicit soft floor of 10", not sent)
    sent, _ = decide.decide_distribution([post("research", total=9.3, kind="research")])
    run("existing research relaxation is not accidentally tightened", len(sent) == 1)
    sent, held = decide.decide_distribution([
        post("f", factual=3), post("c", compliant=3), post("fatal", fatal=["unsupported"])])
    run("fact/compliance/fatal failures remain blocked", not sent and len(held) == 3)
    sent, _ = decide.decide_distribution([dict(post("missing"), score={})])
    run("missing hard scores are not a pass", not sent)
    incomplete = json.dumps(dict(factual=5, useful=3, natural=3, compliant=5, gain=3, fit=3))
    run("missing raw fatal field is an incomplete review", judge._parse(incomplete) is None)

    fixtures = json.load(open("tests/fixtures/yield_trim_oct7.json"))
    recovered = 0
    for case in fixtures:
        p = dict(case, fmt=case["tone"], length=case["tone"])
        errors = filters.check(p["body"], p["facts"], p["fmt"], p["angle"], p["length"],
                               kind=p["kind"], stock_code=p["stock_code"])
        changed = generator.trim_excess_sentence(p, errors)
        assert changed == case["expected_trimmed"], case["id"]
        assert p["body"] == case["expected_body"], case["id"]
        if changed:
            recovered += 1
            assert not filters.check(p["body"], p["facts"], p["fmt"], p["angle"], p["length"],
                                     kind=p["kind"], stock_code=p["stock_code"])
            assert p["body"].split()[0] == case["body"].split()[0]
    run("36 stored rejects recover 14 through all filters, 22 remain rejected",
        len(fixtures) == 36 and recovered == 14)

    # Test the actual provider cache-prime order without contacting any SDK/server.
    provider = ClaudeProvider.__new__(ClaudeProvider)
    calls = []
    provider.generate = lambda s, u, *a: calls.append(u) or NS(text=u)
    cached = [{"type": "text", "text": "constant", "cache_control": {"type": "ephemeral"}}]
    results = provider._sync_many([(cached, str(i)) for i in range(5)], [1.0] * 5, 700)
    run("cacheable writer starts with a useful response, no extra warmup",
        calls[0] == "0" and len(calls) == 5 and [r.text for r in results] == list(map(str, range(5))))

    saved_rejected = list(generator.REJECTED)
    saved_run, saved_writers = generator._run, generator.router.writers
    retried = []
    generator.REJECTED[:] = [dict(id=str(i), provider="claude", tone="brief_report",
                                  fmt="brief_report", angle="compare", length="brief_report",
                                  reject_errs=[], _rewrite_attempted=False) for i in range(3)]
    generator.reset_quality_tracking()
    generator.router.writers = lambda: {"claude": NS(available=lambda: True)}
    def fake_rewrite(name, items, *args, **kw):
        retried.extend(p["id"] for p in items)
        return []
    generator._run = fake_rewrite
    try:
        generator.retry_rejected(limit=1)
        run("one missing post does not submit the entire rejected pool",
            retried == ["0"] and sum(not p["_rewrite_attempted"] for p in generator.REJECTED) == 2)
        generator.retry_rejected(limit=1)
        run("staged retries preserve one attempt per source", retried == ["0", "1"])
    finally:
        generator._run, generator.router.writers = saved_run, saved_writers
        generator.REJECTED[:] = saved_rejected

    generator.reset_quality_tracking()
    first_bad = generator._record_writer_quality("claude", 5, 0)
    then_good = generator._record_writer_quality("claude", 15, 10)
    run("a small bad stage does not prematurely disable the only writer",
        not first_bad and not then_good and "claude" not in generator._DEGRADED_WRITERS)
    generator.reset_quality_tracking()

    reserve = template_reserve.build(copy.deepcopy(FLOW), 65)
    sent, _ = decide.decide_distribution(reserve, allow_template_guarantee=False)
    run("cost profile keeps the normal five-template cap",
        len(sent) <= template_reserve.normal_template_limit(50))
    cooled = frozenset(template_reserve.template_id(i) for i in range(len(template_reserve.TEMPLATES)))
    sent, _ = decide.decide_distribution(reserve, allow_template_guarantee=False, cooled_templates=cooled)
    run("cost profile does not bypass cooldown", not sent)

    # Mock prices intentionally exceed $0.30 before 50 posts. Delivery must continue.
    scores = dict(factual=4, useful=2, natural=3, compliant=4, gain=1, fit=1, fatal=[], reason="soft")
    # Reuse actual saved outputs as already-filtered stage results. This tests
    # orchestration, not future model quality or the synthetic writer's yield.
    by_id = {p["id"]: p for p in POSTS}
    def filtered_stage(items, recent):
        out = []
        for item in items:
            p = copy.deepcopy(by_id[item["id"]])
            p["provider"] = "claude"
            p.pop("template_id", None)
            p.pop("score", None)
            base.record_usage(base.GenResult(p["body"], "claude", "claude-haiku-4-5-20251001",
                                             input_tokens=5000, output_tokens=100), "write")
            out.append(p)
        return out
    original_generate = generator.generate
    generator.generate = filtered_stage
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            sent, warns, err, row, searches = run_main(POSTS, _Writer(), scores,
                                                      cost_priority=True)
    finally:
        generator.generate = original_generate
    usage = row.get("llm_usage", {})
    run("50 delivered even after advisory cost target is exceeded",
        len(sent) == 50 and err is None and usage.get("estimated_token_cost_usd", 0) > .30
        and usage.get("cost_target_met") is False and usage.get("cost_target_mode") == "advisory")
    run("small initial stages and no new paid enrichment",
        row["generation_stages"][0] <= 10 and searches == 0)
    run("normal profile preserves hard quality and at most five templates",
        sum(p.get("provider") == "template" for p in sent) <= 5
        and all(not p.get("score") or (p["score"]["factual"] >= 4
                and p["score"]["compliant"] >= 4 and not p["score"]["fatal"]) for p in sent))
    saved_cooldown, original_generate = state.cooled_templates, generator.generate
    state.cooled_templates = lambda *args: cooled
    generator.generate = filtered_stage
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            no_templates, _, missing_err, _, _ = run_main(POSTS, _Writer(), scores,
                                                          cost_priority=True)
    finally:
        state.cooled_templates, generator.generate = saved_cooldown, original_generate
    run("all templates cooled: continue past 45 LLM until the real selector reaches 50",
        len(no_templates) == 50 and missing_err is None
        and all(p["provider"] == "claude" for p in no_templates))

    base.reset_usage()
    base.record_usage(base.GenResult("", "claude", "claude-haiku-4-5-20251001",
                                    ok=False, cost_status="unconfirmed"), "write")
    run("unknown cost cannot be reported as meeting target", base.usage_summary()["cost_target_met"] is None)
    for usage in (None, NS(input_tokens=100, output_tokens=None),
                  NS(input_tokens=100, output_tokens=10, service_tier="priority"),
                  NS(input_tokens=100, output_tokens=10, cache_read_input_tokens=-1)):
        message = NS(content=[NS(type="text", text="normal body")], usage=usage,
                     model="claude-haiku-4-5-20251001", id="unknown-usage")
        normalized = _message_result(message, "claude", message.model)
        base.reset_usage()
        base.record_usage(normalized, "write")
        assert normalized.ok and normalized.cost_status == "unconfirmed"
        assert base.usage_summary()["cost_target_met"] is None
    run("SDK missing/null/unsupported billing remains unknown without blocking content", True)
    print(f"{CHECKS}/{CHECKS} yield tests passed")


if __name__ == "__main__":
    main()
