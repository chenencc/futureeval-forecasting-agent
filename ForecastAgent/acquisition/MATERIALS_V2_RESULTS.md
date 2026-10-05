# Fixed-frontier V2 paired results

GitHub Actions run [37297914625](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37297914625)
completed all five jobs on commit `2dd3e12`. Both arms use fixed Super, no fallback,
the same 33 captured originals and 15 preregistered source-specific checks. Every
arm has the same 11-HTTP/12-decision ceiling. No search, fetch, supplement,
analysis model or submission was called. Five archives were imported into the
local audited store. The production release remains unchanged.

## Observed results

| Measure | Fresh release-reading baseline | Repaired V2 |
| --- | ---: | ---: |
| Fixed banked material checks | 2/15 | 6/15 |
| Actual physical model requests | 49 | 44 |
| Known reported tokens | 472,492 | 427,880 |
| Unknown usage attempts | 0 | 0 |
| Integrity failures | 0 | 0 |
| Terminal dispatches with gaps | 1 | 2 |
| Budget-censored dispatches | 4 | 3 |
| Tasks marked acquisition complete | 0 | 0 |
| Unchanged first-analysis visible checks | 8/15 | 8/15 |

V2 uses 10.2% fewer physical requests and 9.4% fewer known tokens than its fresh
baseline. All five pairs are auditable at the common ceiling, but only one pair
has both dispatches terminal. This is a bounded retention comparison, not a claim
that every acquisition task completed. All actual provider attempts received a
response; there was no provider service failure in this run.

The prior Super run 37288399817 scored 6/15 for the baseline and 0/15 for V1.
The unchanged baseline varying from 6 to 2 exposes substantial small-sample/model
variability. V2 improves explicit mechanics and retained originals in these
repaired cases, but stable generalization and forecast quality remain unproven.

| Question | Fresh baseline | V2 | Trace review |
| --- | ---: | ---: | --- |
| 36871 stablecoins | 2/3 | 3/3 | Both values and dated/currency/column context banked. Two valid adequacy declarations stored. Assessment error plus later catalog turn cause stalled program closure; overlapping reads duplicate 2,000 characters. |
| 43494 datacenters | 0/3 | 1/3 | New York official permit-moratorium body banked. All planning succeeds; no copied-quote rejection loop. Target date/threshold and Pennsylvania detail checks remain absent, with no adequacy assessment. |
| 43501 hack | 0/3 | 1/3 | Substantive government incident body retained and generic hacker context rejected. Opening timestamp is absent, but the plan invents May 7 as its boundary. Log chronology/vendor response remain absent; an assessment format error prevents storage. |
| 43991 court | 0/3 | 0/3 | Alternate Le Figaro material includes 45 months, 30 suspended and already served, with distinct custodial components. The frozen rubric requires different exact source keys, so these do not count. Seven passage-review requests exhaust most of the nine-request dispatch; political reactions are also banked and two passages remain pending. |
| 44801 gas | 0/3 | 1/3 | AAA indexes include August 27 $4.09 and September 3 $4.14, plus March observations. Dedicated target detail/table remains unbanked. Later September/October entries are mixed into the bank. Broad weekly/monthly history does not establish an exhaustive daily maximum against $5.016. |

Fixed anchor coverage is not accuracy or verified adequacy. In particular the
court's zero source-specific score does not mean zero useful original content.
The original question fields and opening-time absence remain archived, making
the invented hack boundary observable rather than hidden.

## Verified repairs and remaining defects

- Each V2 task freezes its plan in one action without planning errors. Original
  field handles remove the manual field/quote binding failure.
- Fifteen actual successful provider requests contain a pinned review focus.
  Every span matches its original source version and coordinates; each request
  stays within two focus spans and 6,000 exact text characters.
- Source banking works, but overlapping reads and broadly associated background
  still waste context. Fixed first-analysis visibility remains unchanged.
- A review gate which serially disposes every surfaced candidate can monopolize
  the decision budget. It needs bounded useful batching/deferral, not a larger
  review queue or automatic relevance acceptance.
- The status interface rejects `missing_material: "None"` and `"No further
  material needed."` for adequate declarations. The contract needs a clear
  no-gap representation before another inference is spent on repair.
- Adequacy bookkeeping adds no material progress. Successful bookkeeping then
  neutral navigation can trip the stall detector instead of a clean finish.
  Material gaps and execution/protocol closure must remain distinct.
- Whole-field rule handles do not validate the agent's interpretation. Missing
  opening time cannot become an inferred event boundary.

Do not promote V2 or expand a recollection campaign from these results. A next
candidate should address the generic review fan-out, status/closure interface,
and unknown-rule-field handling, replay these exact traces offline, and then use
a small untouched pilot. Neither rubric nor retrospective bodies may be changed
to inflate this five-case result. All current bodies remain potentially subject
to future-information leakage.

Full English structured audit:
`E:/metaculus_data/reports/intelligent-frontier-paired-37297914625.json`.
The experimental workflow remains manually disabled; no additional dispatch or
quota restart was performed during review.
