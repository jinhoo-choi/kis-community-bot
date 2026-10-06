# 2026-10-07: delivery-first cost reduction

## Current decision

The latest instruction makes **50 delivered posts the priority**. Raise the pass
rate by lowering soft quality requirements, then generate fewer drafts.

This revision supersedes PR #68's initial hard-budget approach. The $0.29 guard,
its checkpoint/restart blocks, output truncation ceiling and budget-based stops
are removed. The **$0.30 API cost target is advisory only**: crossing it never
stops generation, review or delivery. Unknown usage is reported as unknown,
not as a free call or a met cost target.

The normal maximum remains five templates per 50, with the existing inclusive
four-day cooldown. This change does not substitute a higher template share or
another model. Existing fact/provenance gates, dedup, stock/tone restrictions,
factual/compliance >=4 and fatal blocking remain.

Prepared as a draft PR, not merged. No paid rerun, manual workflow or deployment.
API costs below refer to the bot, not development-assistant tokens/subscriptions.

## Measured evidence and offline comparison

Natural run [37537764333](https://github.com/jinhoo-choi/kis-community-bot/actions/runs/37537764333),
2026-10-07 07:09:53 KST: **50 delivered, 118 writes, 71 reviews, six searches,
$0.6278314 estimated API cost**. Writing cost $0.3060759; review $0.1564255;
search/enrichment $0.1653300. None of the newly searched candidates was delivered.

For the already-reviewed outputs:

| Acceptance rule | Eligible /71 | Independent of newly paid enrichment |
|---|---:|---:|
| Actual non-relaxed total >=14, fit >=2 | 52 | 52 |
| New non-relaxed total >=10, fit >=1 | 64 | 62 |
| No soft score floor, fit >=1 | 64 | 62 |

The lowered thresholds recover **12 soft-held outputs: eight flow, four
disclosure**. Ten are independent of paid enrichment: eight flow, two disclosure.
The same seven hard factual/fatal failures remain blocked. Lowering below ten
adds no further accepted output in this sample. Research/policy/theme retain
their existing more-relaxed thresholds rather than being accidentally tightened.

With paid-enrichment-dependent candidates excluded, ten-write stages and
five-review chunks select 50 as **46 LLM +4 templates**, using **90 writes +54
reviews**, for **$0.360254**. That is 28 fewer writes and 17 fewer reviews than
actual operation, approximately 24% fewer each. This is historical-output replay,
not a new model run or a prediction that the same outputs will recur.

Isolating the threshold change under the same new scheduling/exclusions:
**110 ->90 writes, 67 ->54 reviews, $0.4330082 ->$0.360254**.

Single-cold-write priming could save $0.02700775 if the prefix/TTL assumptions
hold, bringing the staged replay to **$0.33324625**. Even the most optimistic
individually checked prefix is $0.3559519, or $0.32894415 with conditional priming.
Therefore **sub-$0.30 is not yet established** from the already-scored data.

Fourteen additional drafts pass unchanged deterministic filters after removing
one complete excess sentence. They still require ordinary Sonnet review. Their
possible contribution is excluded from every cost/pass-rate projection above.
A new natural run is needed to measure their review yield and actual total cost.

## Changes and invariants

| Change | Invariant |
|---|---|
| Non-relaxed total14 ->10, fit2 ->1; existing lower type-specific floors retained | factual/compliance >=4, complete hard-score payload and fatal blocking |
| Initial generation10, adaptive subsequent5–20, review chunks5 | continue until actual combined selection reaches50; no monetary stop |
| Stop check uses actual template cooldown and source overlap | do not stop at45 LLM if fewer than five allowed templates can fill the gap |
| Fresh candidates first, then one rewrite per rejected source in small missing-count-bounded chunks | no batch rewrite of dozens when only one post is missing |
| First useful response primes an explicitly cacheable writer prefix | same Haiku/Sonnet models and prompts, no extra warmup call |
| New paid enrichment omitted; valid cache still applied | source/fact gates and non-flow candidate supply preserved |
| Count/length-only rejects may lose one complete later sentence | lead preserved, all filters rerun, ordinary Sonnet review required |
| Writer degradation uses cumulative >=20 samples in the small-stage profile | one unlucky five-item stage cannot disable the only writer |
| API target fields are informational | unknown usage -> unknown target status; no delivery gating |
| Five-template cap and cooldown remain | no emergency cap bypass to hide low LLM yield |

There is no claimed guarantee against source shortage, provider failure or
Telegram outage. Those still produce explicit operational failures; unsafe or
fabricated content is never used to force50. The cost target does not create a
new reason to underfill.

## Verification

The existing rolling-data cost test already failed on an old LIG lookup. The
first PR commit applies the same two-case frozen fixture repair as PR #67,
without including its comparison-basis filter change. PR #66's batch SDK work
is not included.

- Baseline profile: 474 unit,14 template,43 cost,9 mocked pipeline,20 E2E tests
- New delivery-first profile tests: soft thresholds, unchanged hard safety,
  retained research relaxation, no template/cooldown bypass, useful cache primer,
  cumulative writer tracking, limited one-per-source retries
- Orchestration fixture reuses already-filtered saved outputs with mocked
  provider/delivery calls: **50 delivered even though modeled cost exceeds $0.30**
- A fixture with every template cooled continues beyond45 LLM to50 LLM
- SDK missing/null/unsupported usage keeps safe content usable while reporting
  cost target status unknown
- 36 fixed rejected flow drafts:14 filter-recovered,22 still rejected; zero new
  quality scores
- All audits: failure0, two pre-existing pipeline warnings

The baseline suite explicitly uses COST_PRIORITY_MODE=0 to retain its historical
contracts; the new suite explicitly exercises the production delivery-first
profile. These are separate, clearly labeled checks.

## Next natural run, after approval and merge

1. Delivered count50 and Telegram acknowledgments; new profile active
2. Soft-rescue counts; factual/compliance >=4 and no fatal in sent LLM posts
3. Writing/review calls and generation stages; all source IDs unique
4. Trimmed drafts' actual Sonnet pass rate, with original and shortened bodies
5. Actual template count<=5, cooldown maintained, non-flow distribution
6. Cache writes/reads and total API estimate against the advisory $0.30 target
7. Provider invoice separately; execution latency and delivery deadline are not
   established by token-ledger replay

The official [prompt-caching pricing](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
supports the conditional priming arithmetic. Actual cache hits and invoice totals
remain unverified until operation.
