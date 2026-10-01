# Collection dispatch repair and Exa supplement

The five-question v3 pilot at run `36796437198` completed only BTC. Four tasks
reached the 24-turn limit after rereading isolated bodies or rejecting closure
without a recent search. It consumed 116 physical Ultra attempts, 1,622,549
reported tokens and seven Tavily basic attempts. These costs remain in the ledger.

## Control changes

- A historical audit-only body returns an explicit blocked error, empty content
  and no continuation offset. Metadata listings identify unreadable documents.
- Batch reading with no eligible passage or successful read reports no progress.
  Exact repeated local reads and archive requests are blocked across dispatches.
- Links extracted from a current audit-only body remain stored for audit, but
  are excluded from the model's discovery catalog until its parent is eligible.
- Three consecutive failed/nonprogressing turns request a gap export. Collection
  dispatches have twelve turns and twelve physical Ultra HTTP attempts, including
  retries. The existing 72-attempt lifetime ceiling remains unchanged.
- Missing recent discovery is recorded as a gap instead of preventing export.
  Critical needs with no associated usable source are also automatic gaps.
  `acquisition_complete=false` distinguishes an exported package from adequate
  collection. Workflow completion alone does not establish useful evidence.
- A deterministic program export closes a dispatch that used its limit without
  a model export. Ordinary model transport failures remain resumable. A resumed
  incomplete task clears dispatch-local closing latches, preserving physical
  attempt ledgers and request hashes.
- `plan_channels` exposes allowed catalog IDs as an enum and describes every
  required field. IDs are channel capabilities, not names of provider tools.
- Model views use program state and two recent complete turns with a 12,000
  character target for the recent section. Raw conversations remain on disk.
  This is a context projection, not a claimed tokenizer or total token ceiling.

## Explicit Exa allowance

Existing ledgers never silently gain Exa quota when a key is configured.
The operator action `historical_batch enable-exa` requires an authorization ID
and reason. It adds at most one Exa attempt to each unfinished task, writes a
budget amendment and snapshots pre-resume counters. Repeating the same action
does not add another attempt. Completed BTC is reused without another model,
search or Exa call. Tavily's three-attempt lifetime limits do not change.

The manual five-question workflow has an `enable_exa` input, false by default.
The authorized 2026-10-01 repair uses this supplement and restores the prior
campaign artifact before continuing. Credentials stay in Actions Secrets.

## Consumption comparison

Reports must show all three quantities: the old v2 experiment, the interrupted
v3 prefix, and the new resume increments plus cumulative totals. Resume cost is
not a fresh-run performance estimate: it starts with prior material, a partly
spent HTTP budget and one already completed task. Neither lower incremental
cost nor successful export proves that the 135-question batch is ready.
Current-vintage datasets and missing historical snapshots retain their temporal
limitations. No forecast, outcome verdict, trading or scoring is added.

After local artifact synchronization, `python -m ForecastAgent.review_campaign
ARTIFACT_ID --run RUN_ID` verifies raw model transport hashes and original
question/search prefixes, then saves an English structured report in the local
`reports` directory. It separates known token usage from attempts without usage
and compares usable bodies and exact saved excerpt coordinates. Metadata-only
discovery and current-vintage rows retain their acquisition limitations.

## Dynamic capability exposure and completion of the Exa trial

The first repair dispatch (`36799858331`) exported all packages with gaps, but
did not use the granted Exa allowance. Its 30 new Ultra HTTP attempts reported
491,666 tokens. Repeated full tool schemas contributed 27,564 characters per
request; reduced recent conversation alone did not resolve that overhead.

Collection now exposes only the forced tool during planning and closing.
Ordinary turns omit exhausted searches, unavailable historical/current tools
and readers whose required material is absent. Raw HTTP records preserve the
exact tools sent on each request, permitting measured comparisons.

`historical_batch resume-exa` reopens only insufficient gap exports with the
matching prior authorization and an unspent one-attempt Exa allowance. It keeps
the previous export in `result_history` and records `supplement_requests`.
It does not grant another search or reset any budget. An already pending
supplement is unchanged by a repeated request. Ultra chooses the missing-source
query; the runtime requires that one authorized discovery attempt. Closing
retains priority over supplementation, including after errors or exhausted
dispatch limits. Failed/reserved provider attempts remain spent.

Use `review_campaign --prior-artifact ... --prior-run ... --output ...` to
measure this second dispatch against the first repair separately from total
costs since the interrupted v3 pilot.

Run `36801202353` exposed a parameter-grounding failure: sixteen new Ultra
attempts reported 127,736 tokens, but generated channel IDs as `need_ids`, so
validation rejected all Exa calls before HTTP reservation. It added no search
or body. Full request/response evidence is retained. Dynamic schemas now bind
existing need IDs as an enum, and validation feedback supplies those exact IDs.
The authorized supplement uses a focused Ultra context containing the frozen
question, evidence needs, accepted URLs and remaining budget, without unrelated
channel catalogs or failed prior turns. This is one subtask in the same runtime,
not a new provider, task budget or forecast stage.

Run `36801811402` successfully executed the four Exa allowances, with a
reported provider estimate of USD 0.028. It added 38 Ultra HTTP attempts and
392,361 reported tokens, plus one Tavily attempt. However, raw queries were
`exa_search` or `dated_observations`, so the returned metadata was unrelated
to the frozen events. Executed search counts are not evidence of useful
discovery. The shared Tavily/Exa option validation now rejects exact internal
channel IDs and tool names before HTTP reservation. No further five-question
dispatch is triggered, and no spent Exa allowance is renewed.

The latest state preserves all 200 physical Ultra attempts and 2,634,312
known tokens from the original v3 campaign and three repair dispatches. It
has eight Tavily attempts, four Exa attempts, seventeen saved bodies, four
v3-eligible bodies and four excerpts. Eligibility includes exploratory
current-vintage observations and is not proof of a clean backtest. The
135-question expansion remains unvalidated. Query grounding and efficient
use of readable archives require further runtime work.
