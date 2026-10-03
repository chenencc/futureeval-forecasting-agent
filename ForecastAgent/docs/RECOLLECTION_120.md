# Fresh full acquisition of the existing 120 questions

This branch experiment keeps the old Mercury analysis and the immutable official
competition release unchanged. It runs acquisition only: the existing agent,
programmatic body inspection and duplicate handling, independent supplementation,
and the saved intelligence handoff. There is no scoring or submission.

The frozen input combines the original 100 binary questions and the existing
20 nonbinary core questions. Outcome labels are excluded. Nonbinary format
metadata remains in the fixture and acquisition background. This is current
information collection, not a historically clean forecasting backtest.

Dispatch the existing `analysis_p0_forty.yaml` workflow on `dev_formal` with
`experiment=recollect120` and `pilot_only=false`. The separate job processes at
most five original tasks per dispatch, then supplements terminal raw tasks.
It uploads `recollection-120-v1-state` before queuing its next dispatch. Completed
original tasks and finished handoffs are skipped. Partial reservations survive
restoration. The latest completed recollection artifact is mandatory after any
consumed dispatch; missing state refuses a reset. The exact code commit and
input hash remain frozen across the chain.

This is a fresh user-authorized experiment with separate allowances. Earlier
campaigns and their ledgers remain intact. Within this experiment each task has
at most three Tavily basic searches, one Exa search, eight initial free fetches,
one basic Extract rescue batch and three acquisition executions. The campaign
model allowance is 1,920 HTTP attempts shared by 120 tasks. Independent repair
retains the defaults documented in `CRAWL_README.md`. Ultra is preferred, with
the existing service-failure fallback to Super. Account quota errors do not
trigger fallback. Retry deadlines and provider-review stops are preserved.

Outputs include original inputs, raw per-task bundles and journals,
`handoffs/<id>/repair` captures and ledgers, immutable analysis-input packages,
`full-collection-status.json` and `continuation.json`. A readable capture is not
proof of relevance or truth. Unresolved gaps remain explicit.

Use `ForecastAgent.local_sync` to import completed artifacts into
`E:/metaculus_data`. The existing local sync mechanism accepts this artifact
without a new file format. Remote cleanup must retain its newest recovery state
until the queue finishes or the complete local ZIP is verified.
