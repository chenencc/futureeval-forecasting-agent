# Frozen-frontier paired acquisition results

Date: 2026-10-05. Branch: `dev_acquisition_v2`. Production remains unchanged.

## Executed experiments

1. Ultra-only [run 37287329839](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37287329839)
   on `2454155`: all ten arms interrupted by upstream overload. Forty-two HTTP
   attempts, 88,572 known tokens, 32 attempts without reported usage. There is no
   valid acquisition-quality comparison from this run.
2. Separately frozen Super-only [run 37288399817](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37288399817)
   on `c2ef7e4`: the same question/body/lead pool, alternating arm order and frozen
   rubric. Eleven physical HTTP attempts per arm, no fallback or second dispatch.
   All ten snapshots were archived, with no service failures or unknown usage.

The two runs used 136 actual model requests in total, below the original 160
campaign ceiling and the separately frozen 152 combined ceiling. Both used zero
new search, source-fetch, Extract, supplement, analysis or submission requests.
All ten original GitHub artifact archives were imported into `E:/metaculus_data`.

## Material selection at the common Super ceiling

These are exact lexical checks in valid banked original excerpts, not forecast
accuracy, exhaustive relevance, or complete acquisition success. Both arms
started with all 33 original captured responses. The baseline is the release's
existing paragraph-reading strategy, not production's raw-only recall path.

| Question | Baseline checks | Candidate checks | Baseline HTTP | Candidate HTTP |
| --- | --- | --- | --- | --- |
| 36871: stablecoin capitalization | 1/3 | 0/3 | 10 | 8 |
| 43494: data-center restrictions | 2/3 | 0/3 | 10 | 11 |
| 43501: Instructure cybersecurity | 0/3 | 0/3 | 7 | 10 |
| 43991: French candidacy eligibility | 1/3 | 0/3 | 10 | 10 |
| 44801: national gasoline price | 2/3 | 0/3 | 10 | 8 |
| Total | 6/15 | 0/15 | 47 | 47 |

Known tokens: baseline 425,972; candidate 475,168 (+11.55%). Unknown Super usage
attempts: zero. Exact-coordinate/source-version violations and new source-network
attempts: zero. There were no complete terminal pairs: baseline reached its cap
in four cases and stalled in one; candidate reached its cap in three and stalled
in two. Bounded saved-material yield is comparable, but this is not a successful
end-to-end completion-rate evaluation. No caps were restarted.

The candidate banked three original excerpts (777 characters) for the hack case,
but none matched the frozen primary/government/date checks. Zero check matches
does not mean no relevant text exists. The baseline also selected off-period
gasoline context and incompletely retained stablecoin table context; it is not
an adequate final collector merely because it outperformed this candidate.

## Trace-based findings

- **Rule-binding friction:** data-center planning put the 50-MW definition under
  `fine_print`, which is empty; it actually belongs to `resolution_criteria`.
  After the first rejection, a hyperlink quoting mismatch was fixed, but the
  wrong field was not. Ten planning attempts failed. The generic validation
  response did not identify the invalid field/span. The loop prioritizes an
  unfrozen plan before the forced-close latch, repeatedly requesting the same
  failed operation. This is an interface/control defect, not just low model IQ.
- **Read-to-bank failure:** stablecoin document 4 contains the exact June 30
  USDT/USDC market-cap rows and complete headers. Both values were present in
  candidate model requests 4 and 6, but no excerpt was banked. Neutral document
  listings displaced the body from later projected contexts; a repeated read did
  not preserve useful material. This is not a missing-body capture failure.
- **Weak selected fragments:** the hack candidate saved a 100-character generic
  threat-group fragment, 627 characters of old-breach context and a 50-character
  citation footer. They were source-valid but did not preserve the frozen
  government confirmation/date/original vendor-response materials.
- **Premature or censored closure:** gas planning spent several calls on wrong
  argument types and quote binding, then stalled without reading/banking target
  material. The court candidate similarly used budget on planning/navigation
  without retaining the relevant sentence components. Every reason remains in
  the archived task state; no retry is hidden as a successful task.
- None of the five candidate dispatches invoked `inspect_materials` or completed
  an `assess_materials` declaration. A frontier was present in automatic context,
  but adding tools and instructions did not produce the intended feedback loop.

## Fixed analysis visibility

Without calling Mercury, the unchanged release projection exposed 8/15 frozen
checks in both arms. All whole bodies were identical and the release selector
rebuilds its original spans from them. Additional banking alone does not change
that selector's visibility. This experiment therefore establishes no downstream
forecast improvement.

## Decision

Do not promote this intelligent-material strategy or recollect a larger set.
Keep the release unchanged. The next candidate should simplify rule references
using program-generated field/span handles and actionable errors, enforce bounded
planning repair/closure, and preserve read material until it is banked or explicitly
discarded. Validate those mechanisms against these same immutable responses
offline before another provider experiment. Changing analysis, model, discovery
pool or spend at the same time would lose attribution.

All 452 local offline tests passed. That result establishes engineering checks;
it did not establish the autonomous policy's useful selection behavior.

Full English audits:
`E:/metaculus_data/reports/intelligent-frontier-paired-37287329839.json` and
`E:/metaculus_data/reports/intelligent-frontier-paired-37288399817.json`.
The manually invoked experimental workflow was restored to `disabled_manually`.
