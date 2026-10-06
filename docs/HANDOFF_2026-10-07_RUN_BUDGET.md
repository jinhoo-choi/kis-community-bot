# 2026-10-07: API cost guard and lower soft-quality profile

## Status and scope

Based on `0bfde5284c8575ee3fe24c0f440da603de30a3ab`. Requested outcome: below
$0.30 per run even with lower soft quality, while retaining 50 delivered posts.
This change is prepared for review, not merged or validated by a paid run.

**A hard pre-request projection limit is implemented, but 50 posts under that
limit is not established.** The current five-template normal cap and inclusive
four-day cooldown remain. Budget pressure cannot activate the emergency template
bypass. Insufficient output is a failed/short run, never a cost-success claim.

Budget scope is this bot's Anthropic API usage, not coding-assistant subscription
or development token costs. This is a usage estimate, not an invoice guarantee.

## Observed baseline

Natural run [37537764333](https://github.com/jinhoo-choi/kis-community-bot/actions/runs/37537764333),
2026-10-07 07:09:53 KST, delivered 50: 45 LLM and five templates.

| Role | Calls | Unrounded USD estimate |
|---|---:|---:|
| Enrichment/search | 6 | 0.1653300 |
| Haiku writing | 118 | 0.3060759 |
| Sonnet 5 judging | 71 | 0.1564255 |
| Total | 195 | 0.6278314 |

All six newly searched candidates contributed zero delivered posts. Excluding
search alone leaves $0.4625014. Two enriched drafts become eligible under softer
scores, so replay excludes their dependent outputs rather than pretending their
facts were free.

Of 71 scored outputs, 64 satisfy factual >=4, compliant >=4 and fatal empty;
62 do not depend on newly paid enrichment. With reconstructed reserve and all
existing stock/tone/source restrictions, the earliest tested 45-LLM +5-template
prefix costs $0.3559519 (89 writes, 53 judges). Conditional single-cold-write
priming lowers that to $0.32894415, still above $0.30.

Historical larger-blend sensitivities (not activated or a future guarantee):
37 LLM +13 templates at $0.2833948, or 40+10 at $0.28770095 assuming the priming
saving. All 65 reserve bodies reproduced exactly from cached source facts.
Those mixes retained research/policy/disclosure, unique source/template IDs,
stock repeat <=1 and tone count <=15 in three reserve orderings.

The 22-template bank is not enough for 10–15 templates every weekday with the
existing cooldown: a D use is excluded through D+4 and returns on D+5. Five
consecutive trading days at 15/day would require at least 75 eligible structures.
Larger shares and cooldown changes require an explicit decision; this PR does
not silently enable either.

## Changes and invariants

| Change | Invariant |
|---|---|
| `src/llm/budget.py`: atomic microdollar reserve before every synchronous paid call | settled cost + outstanding reservations + next full request <= $0.29 |
| Count input using the free model-specific count endpoint, pad by max(10%,256 tokens), reserve all input at cold-cache write rate and all allowed output | no assumed cache hit, batch discount or successful retry |
| Durable pre-submit checkpoint; same-run restarts retain spend; unknown outcomes retain reserve across future runs | no blind zero-cost treatment; reconciliation required |
| Unknown models/count failure/missing usage/pending old batch stop paid work | no cheaper unapproved model fallback |
| Disable automatic SDK retries; rejected/local failures release, uncertain outcomes stop | every subsequent billable attempt needs fresh reservation |
| Bounded mode uses synchronous requests only; old batch recovery stays visible | no asynchronous unconfirmed liability ignored |
| New paid searches disabled; valid enrichment cache still applied | source-based gates unchanged |
| Small generation stages (10), at most two simultaneous writes, useful first response primes cache | no extra prewarm call; same prompt bytes/models |
| Writing output ceiling 350, judge ceiling unchanged at 300 | truncated/invalid content still fails ordinary filters/judgment |
| Soft score floor 0, fit floor 1 in budget profile | factual/compliant >=4 and fatal blocking remain; models unchanged |
| One whole excess sentence can be removed from count/length-only rejected drafts | first sentence stays; all filters re-run; ordinary Sonnet review required |
| Normal template cap enforced in bounded final selection | 50 target, source dedup, stock/tone caps, cooldown preserved |

`config.py` adds RUN_API_BUDGET_USD (default 0.29), checkpoint path, budget stage
and output limits. Values <=0 or >0.29 fail closed. `None` is only an explicit
in-process offline harness switch; an environment value cannot disable the guard.
The daily workflow pins 0.29 and preserves/uploads the checkpoint with existing
state, without changing secrets, permission scopes, schedule or live destination.
Hosted workflow reruns (`GITHUB_RUN_ATTEMPT>1`) fail closed until cost reconciliation:
a killed runner may lose its local checkpoint before upload/commit. A same-workspace
restart retains its durable reservations; a fresh runner is not assumed to do so.
Recovered prior-batch charges enter the budget before new admission. Unknown
service tiers or malformed usage/checkpoints cannot release capacity.

Output ceilings alone have no demonstrated average savings: historical writing
was 70–214 tokens, judging 66–99. Priming's potential $0.02700775 saving assumes
matching prefixes and valid TTL; it has not been measured on a new run.
Conservative full-request reservations can stop earlier than the optimistic
historical cost prefixes above.

## Offline checks

The rolling-data baseline was already broken at the old LIG fixture lookup in
`test_cost_offline.py`; this PR includes the same two-case frozen fixture repair
as independent PR #67, but does not include its comparison-basis filter change.

- Existing full suite: unit 474/474; template 14/14; cost 43/43; mocked pipeline
  9/9; E2E 20/20; all audit failures 0, two pre-existing pipeline warnings
- New budget suite: 33/33, plus independent review/reproduction of all identified
  failure cases. It covers parallel admission, full-output/cold-cache reserve,
  settlement, unknown replies, retries, unknown model, prior batch, durable
  restarts, SDK boundary, prewarm, hard safety and template cap
- Frozen October 7 trim fixture: 36 rejected flow drafts, 14 recovered through
  every unchanged deterministic filter, 22 still rejected. **The 14 have no new
  Sonnet scores and are not counted as proven deliveries.**
- Installed SDK 1.11.0 signatures checked offline: `max_retries` constructor and
  `messages.count_tokens` are supported. Production count endpoint not invoked
- No paid generation, manual workflow dispatch, Telegram send or merge

The official [token-counting documentation](https://platform.claude.com/docs/en/build-with-claude/token-counting)
says counting is free but approximate. [Prompt-caching prices](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
support the cold 5-minute write reserve and 1-hour multiplier. Because vendor
billing can differ from estimates, a reservation overrun is surfaced and blocks
later work; invoice verification remains separate.

## First natural run to verify after approval and merge

1. API guard settled + outstanding projection, denied requests, overruns and stop
   reason; every uncertainty must remain visible
2. Actual delivered 50, not merely generated 50; template count and cooldown
3. All sent LLM factual/compliant >=4, no fatal; all template reconstructions valid
4. New searches zero; cached enrichment and non-flow supply retained
5. Trim count, Sonnet pass rate and the retained original/shortened bodies
6. Cache creation/read counts and actual writer/judge calls; no assumed savings
7. Compare provider invoice separately; the ledger is still an estimate

Rollback should revert the budget/profile change as a unit; do not retain an
incomplete provider guard without its checkpoint and usage accounting.
