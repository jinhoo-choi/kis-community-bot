"""Cost guard boundary tests. Fake SDK only; no model/network or Telegram calls."""
import concurrent.futures as cf
import copy
import json
import os
import sys
import tempfile
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from src import decide, template_reserve, generator, filters, judge
from src.llm import base, budget
from src.llm.base import GenResult
from src.llm.claude import ClaudeProvider, _message_result, _job_hash

H = "claude-haiku-4-5-20251001"
S = "claude-sonnet-5"


def run(name, condition):
    print(("  OK  " if condition else "  FAIL") + "  " + name)
    assert condition, name


def denied(fn):
    try:
        fn()
    except budget.BudgetDenied:
        return True
    return False


class Messages:
    def __init__(self, count=5000, fail=None, missing=False):
        self.count, self.fail, self.missing = count, fail, missing
        self.calls = self.counts = 0
        self.kw = []

    def count_tokens(self, **kw):
        self.counts += 1
        return NS(input_tokens=self.count)

    def create(self, **kw):
        self.calls += 1
        self.kw.append(kw)
        if self.fail:
            raise self.fail
        usage = None if self.missing else NS(
            input_tokens=1000, output_tokens=100, cache_read_input_tokens=4000,
            cache_creation_input_tokens=0, service_tier="standard")
        return NS(content=[NS(type="text", text="본문")], usage=usage,
                  id=f"mock-{self.calls}", model=kw["model"])


def provider(messages, model=H):
    p = ClaudeProvider.__new__(ClaudeProvider)
    p._client, p.model, p.name, p.use_batch = NS(messages=messages), model, "claude", False
    return p


def main():
    base.reset_usage()
    budget.start(.29)
    token = budget.reserve(H, 5000, 350)
    run("cold cache + full output reserved before dispatch",
        budget.summary()["outstanding_reserved_usd"] == .008625)
    budget.settle(token, GenResult("x", "claude", H, input_tokens=5000,
                                  cache_read_tokens=4000, output_tokens=100))
    run("settlement uses actual usage and releases unused allowance",
        budget.summary()["estimated_settled_usd"] == .0019
        and budget.summary()["outstanding_reserved_usd"] == 0)

    budget.start(.029)
    def task(_):
        try:
            return budget.reserve(H, 10000, 500)
        except budget.BudgetDenied:
            return None
    with cf.ThreadPoolExecutor(max_workers=20) as pool:
        reservations = list(pool.map(task, range(20)))
    run("parallel admission cannot race past cap",
        sum(x is not None for x in reservations) == 1
        and budget.summary()["projected_total_usd"] < .029)

    budget.start(.01)
    token = budget.reserve(H, 4000, 100)
    budget.fail(token)
    run("uncertain call retains full reserve and stops further work",
        budget.summary()["outstanding_reserved_usd"] == .006
        and denied(lambda: budget.reserve(H, 1, 1)))
    budget.start(.01)
    token = budget.reserve(H, 4000, 100)
    budget.fail(token, known_unbilled=True)
    run("local/rejected attempt releases allowance, next retry reserves afresh",
        budget.summary()["projected_total_usd"] == 0
        and budget.reserve(H, 4000, 100) is not None)

    budget.start(.29)
    run("unknown model fails closed", denied(lambda: budget.reserve("unknown", 5, 5)))
    budget.start(.29)
    token = budget.reserve(H, 1000, 100)
    budget.settle(token, GenResult("x", "claude", H, input_tokens=0))
    run("missing usage is not free", budget.stopped()
        and budget.summary()["outstanding_reserved_usd"] > 0)
    budget.start(.29)
    token = budget.reserve(H, 1000, 100)
    budget.settle(token, GenResult("x", "claude", H, input_tokens=20000, output_tokens=100))
    run("underestimated response is visible and blocks later calls",
        budget.summary()["overruns"] == 1 and budget.stopped())
    budget.start(.29)
    base.PENDING_BATCHES["old"] = 3
    run("unresolved legacy batches cannot be ignored", denied(lambda: budget.reserve(H, 10, 10)))
    base.PENDING_BATCHES.clear()

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "budget.json")
        budget.start(.29, path, "run1")
        token = budget.reserve(H, 5000, 350)
        persisted = json.load(open(path))
        budget.start(.29, path, "run1")
        run("restart retains pre-dispatch reservation and fails closed",
            persisted["reserved"][token] == 8625 and budget.stopped()
            and budget.summary()["outstanding_reserved_usd"] == .008625)
        budget.start(.29, path, "run2")
        run("previous-run uncertainty blocks new spend", budget.stopped())
        budget.start(.29, path, "run3")
        run("uncertainty persists through multiple later runs", budget.stopped()
            and budget.summary()["outstanding_reserved_usd"] == .008625)

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "malformed.json")
        budget.start(.29, path, "malformed")
        bad = json.load(open(path))
        bad["spent"] = float("nan")
        json.dump(bad, open(path, "w"))
        run("NaN checkpoint cannot bypass projected-budget comparison",
            denied(lambda: budget.start(.29, path, "malformed")))
        bad["spent"] = True
        json.dump(bad, open(path, "w"))
        run("boolean microdollars are rejected as corrupt checkpoint state",
            denied(lambda: budget.start(.29, path, "malformed")))

    budget.start(.29)
    msg = Messages()
    p = provider(msg)
    result = p.generate("system", "user", max_tokens=350)
    run("provider counts and reserves at actual SDK boundary",
        result.ok and msg.calls == 1 and msg.counts == 1
        and budget.summary()["estimated_settled_usd"] == .0019)
    budget.start(.001)
    msg = Messages()
    result = provider(msg).generate("system", "user", max_tokens=350)
    base.reset_usage()
    base.record_usage(result, "write")
    run("budget denied request makes zero paid calls and ledger attempts",
        not result.ok and result.cost_status == "not_sent" and msg.calls == 0
        and base.usage_summary()["calls"] == 0 and base.usage_summary()["api_attempts"] == 0)
    budget.start(.29)
    msg = Messages(fail=TimeoutError("lost response"))
    result = provider(msg).generate("system", "user", max_tokens=350)
    run("provider timeout is retained, not retried or priced at zero",
        not result.ok and result.cost_status == "unconfirmed" and msg.calls == 1
        and budget.stopped() and budget.summary()["outstanding_reserved_usd"] > 0)
    budget.start(.29)
    msg = Messages(missing=True)
    result = provider(msg).generate("system", "user")
    run("provider missing usage retains reservation",
        result.cost_status == "unconfirmed" and budget.stopped())
    budget.start(.29)
    msg = Messages()
    result = provider(msg).search("system", "user")
    run("unbounded server search never reaches paid SDK", msg.calls == 0 and not result.ok)
    budget.start(.29)
    msg = Messages()
    provider(msg)._prewarm([([{"type": "text", "text": "long",
                             "cache_control": {"type": "ephemeral"}}], "u")])
    run("prewarm is covered by the same guard", msg.calls == 1
        and budget.summary()["estimated_settled_usd"] > 0)

    budget.start(.29)
    msg = Messages(fail=TypeError("response decoder failed after dispatch"))
    result = provider(msg).generate("system", "user")
    run("post-dispatch TypeError remains an uncertain billed attempt",
        result.cost_status == "unconfirmed" and budget.stopped()
        and budget.summary()["outstanding_reserved_usd"] > 0)

    budget.start(.29)
    msg = Messages(fail=TypeError("response temperature decoding failed"))
    result = provider(msg).generate("system", "user")
    run("runtime temperature parse failure cannot be mislabeled not-sent",
        msg.calls == 1 and result.attempts == 1 and result.cost_status == "unconfirmed")

    budget.start(.29)
    message = Messages().create(model=H)
    message.usage.output_tokens = None
    result = _message_result(message, "claude", H)
    token = budget.reserve(H, 5000, 350)
    budget.settle(token, result)
    run("null billed usage cannot release reserve", result.cost_status == "unconfirmed"
        and budget.stopped() and budget.summary()["outstanding_reserved_usd"] > 0)
    message.usage.output_tokens = 100
    message.usage.cache_read_input_tokens = -1
    run("negative cache usage is not silently clamped to a free charge",
        _message_result(message, "claude", H).cost_status == "unconfirmed")

    budget.start(.29)
    prior = GenResult("prior", "claude", H, input_tokens=100000, output_tokens=10000,
                      request_id="old-recovered", billing_mode="standard")
    budget.account_recovered(prior)
    budget.account_recovered(prior)
    run("recovered charges count exactly once before new admission",
        budget.summary()["estimated_settled_usd"] == .15
        and denied(lambda: budget.reserve(H, 110000, 100)))

    with tempfile.TemporaryDirectory() as d:
        old_path = config.BATCH_STATE_PATH
        config.BATCH_STATE_PATH = os.path.join(d, "batch.json")
        try:
            sysmsg, usermsg = "system", "user"
            jobhash = _job_hash(H, sysmsg, usermsg, 1.0, 700)
            json.dump({"old-batch": {"model": H, "status": "in_progress",
                                     "jobs": {"j0": jobhash}, "collected": []}},
                      open(config.BATCH_STATE_PATH, "w"))
            budget.start(.29)
            base.PENDING_BATCHES.clear()
            msg = Messages()
            msg.batches = NS(retrieve=lambda bid: NS(processing_status="in_progress"))
            results = provider(msg).generate_many([(sysmsg, usermsg)])
            run("matching unresolved legacy batch cannot fall through to fresh sync calls",
                msg.calls == 0 and budget.stopped()
                and base.PENDING_BATCHES.get("old-batch") == 1 and not results[0].ok)
            base.PENDING_BATCHES.clear()
            open(config.BATCH_STATE_PATH, "w").write("{broken")
            budget.start(.29)
            msg = Messages()
            run("corrupt legacy checkpoint cannot mean zero liability",
                denied(lambda: provider(msg).generate_many([("s", "u")]))
                and msg.calls == 0 and budget.stopped())
        finally:
            config.BATCH_STATE_PATH = old_path
            base.PENDING_BATCHES.clear()

    old_attempt = os.environ.get("GITHUB_RUN_ATTEMPT")
    os.environ["GITHUB_RUN_ATTEMPT"] = "2"
    try:
        budget.start(.29)
        run("fresh-runner workflow reruns require billing reconciliation", budget.stopped())
    finally:
        if old_attempt is None:
            os.environ.pop("GITHUB_RUN_ATTEMPT", None)
        else:
            os.environ["GITHUB_RUN_ATTEMPT"] = old_attempt

    # Soft scores may fall; facts/compliance/fatal remain independent hard gates.
    budget.start(.29)
    samples = []
    for i in range(4):
        samples.append({"id": str(i), "kind": "flow", "stock_code": str(i),
                        "tone": "", "body": f"안전한 본문 {i}", "provider": "claude",
                        "score": {"factual": 4, "compliant": 4, "fit": 1,
                                  "total": 9.3, "fatal": []}})
    samples[1]["score"]["factual"] = 3
    samples[2]["score"]["compliant"] = 3
    samples[3]["score"]["fatal"] = ["unsupported"]
    sent, held = decide.decide_distribution(samples)
    run("soft-only acceptance never bypasses hard safety", [x["id"] for x in sent] == ["0"]
        and len(held) == 3)
    incomplete_json = json.dumps(dict(factual=5, useful=3, natural=3,
                                     compliant=5, gain=3, fit=3))
    run("raw judge JSON missing fatal is not normalized into a safety pass",
        judge._parse(incomplete_json) is None)
    incomplete = dict(samples[0], score={})
    run("missing hard safety scores fail closed even with zero soft-score floor",
        not decide.decide_distribution([incomplete])[0])
    flow = json.load(open("data/market_cache.json"))["items"]
    reserve = template_reserve.build(copy.deepcopy(flow), 65)
    sent, _ = decide.decide_distribution(reserve, allow_template_guarantee=False)
    run("budget exhaustion cannot silently exceed existing template cap",
        len(sent) <= template_reserve.normal_template_limit(config.TARGET_POSTS))
    fixtures = json.load(open("tests/fixtures/budget_trim_oct7.json"))
    recovered = 0
    for case in fixtures:
        post = dict(case, fmt=case["tone"], length=case["tone"])
        errors = filters.check(post["body"], post["facts"], post["fmt"], post["angle"],
                               post["length"], kind=post["kind"], stock_code=post["stock_code"])
        changed = generator.trim_excess_sentence(post, errors)
        assert changed == case["expected_trimmed"], case["id"]
        assert post["body"] == case["expected_body"], case["id"]
        if changed:
            recovered += 1
            assert not filters.check(post["body"], post["facts"], post["fmt"], post["angle"],
                                     post["length"], kind=post["kind"], stock_code=post["stock_code"])
            assert post["body"].split()[0] == case["body"].split()[0]
    run("36 stored rejects: 14 shortened drafts recover, 22 stay rejected, all filters intact",
        len(fixtures) == 36 and recovered == 14)
    budget.start(None)
    print("33/33 budget tests passed")


if __name__ == "__main__":
    main()
