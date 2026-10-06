# ForecastAgent release 1.0.4

Release 1.0.4 is based on the immutable `v1.0.3` tag. Acquisition and independent
supplement remain unchanged. The evidence delivery adapter preserves every
previously visible original coordinate and adds complete paragraphs and table
contexts before invoking the frozen 1.0.1 Mercury registry and scoring rules.

The first and conditional second requests retain their 22,000 and 28,000 byte
caps. Mercury still makes at most two HTTP attempts per analysis. Packing or
coordinate validation failure returns the validated previous selector input;
it does not spend another model attempt. Failed second projection preserves the
first decision when a safe extension is unavailable. Source citations resolve
through the compact source ledger.

```powershell
python -m ForecastAgent.releases.v1_0_4 --root snapshots/official --snapshots snapshots/incoming
python -m unittest ForecastAgent.tests.test_release_1_0_4 -v
```

The default command verifies inventory without acquisition or submission.
Authorized production adds `--submit`. Production uses the exact tag and source
manifest; its infrastructure utilities are maintained separately on `main`.

## Release 1.0.4 evidence and limitations

Three paired binary cohorts contain 25 questions. Final Brier decreased from
0.09201352 to 0.08551265, while both arms classified 22/25 correctly. The 20
nonbinary pairs completed all first reads; final multiple-choice and numeric
losses increased, while discrete and research-date losses decreased. These are
retrospective single-replicate comparisons, not an official tournament score or
a clean historical forecasting benchmark. Resolved-question web material may
contain outcomes. Efficiency and original-context delivery motivate promotion;
universal forecasting improvement is not established.

Production packing exactly reproduces 42 official first request states and 25
available conditional second states from these saved experiments, with zero
provider calls. Three date experiments used research grids and are excluded
from production compatibility claims. Production always requires authoritative
API range metadata and never fabricates a date grid.

The 22 release contract tests include a 100-question mixed-format queue with
simulated HTTP, recovery, unknown POST reconciliation, payload and citation
validation, packing fallback and a real child-process timeout. They test
engineering behavior, not live forecasting accuracy.

## Cutover and rollback

Before changing the worker tag, confirm that no worker is active and the latest
exact campaign has no old-version nonterminal task. Drain any such task with
its original release and budgets first. Accepted/skipped records and submission
receipts remain byte-preserved. The adapter refuses incompatible inflight
identities rather than restarting their calls. No quota reset accompanies an
upgrade or rollback.

At a clean task boundary, rollback changes the production checkout, tag check
and module entrypoint to `v1.0.3`. Finish any active 1.0.4 task with 1.0.4 before
rolling back. A verification-only dispatch restores the exact checkpoint,
checks fresh monitor inventory and archives the preserved state without model
calls or forecast submissions.

## Inherited acquisition and delivery contracts

Composition: **new V3 material acquisition -> deterministic independent
supplement -> unchanged release 1.0.1 Mercury analysis**.

Codex operates this repository; it is not a runtime dependency. The old analysis
core is verified against the 47-file frozen baseline before execution. A new
candidate namespace is required; this release never rewrites old experiment
journals or resets their reservations.

## Runtime

```powershell
python -m ForecastAgent.releases.v1_0_2 --root snapshots/official-1.0.2 --snapshots snapshots/tournament
```

The default is an inventory-only dry run. Authorized production execution adds
`--submit`. The supervisor processes at most five questions per dispatch, one
isolated child at a time, with a 1,500-second task deadline and 6,000-second batch
deadline. It kills the child's browser/reader process tree on timeout. Subsequent
dispatches drain the persistent queue, subject to the original stage budgets.
An external scheduler must continue dispatching the worker; the Python command
does not install a scheduler or change the production workflow.

Group questions are split by question ID and inherit exact shared rules.
Conditional Yes/No branches retain the premise and target rules explicitly.
Invalid branches are separately recorded, while other posts continue.

## Acquisition and analysis contracts

- Ultra is preferred; the existing audited two-service-failure policy switches
  to Super for the remainder of the dispatch. Provider account quota is shared.
- Per-question original acquisition limits remain three Tavily basic searches,
  one Exa search, eight initial HTTP fetches, and one basic Extract batch with
  at most five URLs. Independent supplement retains its separate two HTTP,
  two browser-render, and eight local-reparse limits.
- Collector completion is not evidence adequacy. Eligible locally closed tasks
  can export readable raw material with recorded gaps, preserving every original
  journal and result byte. Completed, checksum-verified 5xx service-error receipts
  may also export available readable material with an explicit interruption gap.
  Unknown in-flight receipts, account quota and credential errors do not use that
  bridge. The analysis package is checksum-bound to the collector adapter.
- Mercury independently reads saved original material. A second request remains
  conditional on diagnostics and enough unseen text. A failed reread retains
  the valid first score. Mercury service failure uses the existing authorized
  reasoning-only fallback. Invalid identities do not trigger fallback.
  A malformed/transport response is recovered only when its exact failed journal
  matches the frozen request; capped replay makes no extra Mercury HTTP attempt.
- Binary probabilities are clipped to 0.02–0.98. Multiple-choice probabilities
  use a bounded simplex so labels and sum are preserved. Range/date/discrete
  forecasts follow the authoritative API grid and CDF constraints. Closed CDF
  endpoints remain 0 and 1; they are not binary probabilities.
- A 0.02 floor is mathematically infeasible for more than 50 categories. Missing
  range metadata and unsupported types are explicit integrity blocks, not
  invented forecasts. The release cannot promise a valid score when every
  provider is unavailable or the platform deadline has passed.

## Reliability and recovery

The full input inventory is persisted before any account/model network call.
Each question is accepted, skipped with a reason, waiting for a bounded retry,
or explicitly blocked. `coverage-inventory.json`, `campaign.json`, per-question
`failure.json`, and `supervisor-report.json` expose coverage and attention IDs.
Three watchdog failures reach an attention state. Existing acquisition/model
caps are never reopened by the watchdog. All state must be restored from the
exact previous artifact before continuing on another runner.

Candidates are bound to the saved analysis-input SHA-256. Delivery uses the
unchanged atomic forecast/private-comment adapter and readback reconciliation.
An unknown POST outcome is reconciled rather than blindly reposted. Already
forecasted questions skip collection and analysis. A corrupted saved candidate
is blocked before another inference or POST.

## Validation

```powershell
python -m unittest ForecastAgent.tests.test_release_1_0_2 -v
python -m ForecastAgent.releases.stress --root snapshots/release-1.0.2-offline --count 100
```

The offline load test runs real analysis, reservations, distribution generation,
atomic delivery adapters and readback against simulated model/platform HTTP
boundaries. It is not 100 live model calls or 100 real platform submissions.
Fault tests include actual child/descendant timeout, recovery, provider outage,
reread outage, corrupt state, grouped/conditional IDs, and lost POST responses.

`retrieval_trial.yaml` with `candidate_policy=release_1_0_2` additionally performs
a bounded real-provider pilot: four native historical binary/category/numeric/
discrete cases use fresh full collection and supplement; a synthetic saved
date case tests real-provider CDF compatibility because archived date examples
lack authoritative API scaling. Every live case preserves its original journals
and validates cached replay. The workflow has no Metaculus token and no forecast
delivery step. Current web material on resolved questions can contain outcomes;
these tests measure engineering reliability, not historical forecasting skill.
