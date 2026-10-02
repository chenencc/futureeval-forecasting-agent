# Competition shadow runtime

This runtime is a separately versioned adapter around the immutable release
1.0 analysis protocol. It has no submission client, submission switch or
forecast-writing endpoint. A shadow candidate is a diagnostic artifact.
The existing v8 prompts still describe retrospective analysis; this adapter
does not claim they are a production competition protocol.

## Ownership and state

The existing monitor discovers questions. The existing collection worker owns
retrieval journals and the lifetime three-Tavily/one-Exa limits. This runtime
does not call search providers or create another acquisition task. It adopts
completed live collector bundles after checking exact question rules, readable
body hashes and pre-deadline capture timestamps.

`queue.json` records every observed question, including group children and
unsupported types. Immutable raw snapshot versions are saved separately.
Closed and missed questions remain visible. Earliest scoring/close deadline
determines processing order. A missing deadline or rule change is an explicit
integrity block. Absence from a partial archive page never closes a task.

Each question owns an immutable bridge, bounded supplemental journal, original
v8 analysis journals and a shadow candidate. Crashes reuse these directories;
updated collector bundles cannot overwrite a frozen analysis input. Missing
queue state with existing task files is an error. Rule changes do not clear
provider budgets. The workflow also refuses to bypass a missing prior artifact
if the previous worker reached its processing step.

## Operation

Ingest a saved monitor directory and inspect the queue without API calls:

```text
python -m ForecastAgent.competition --root snapshots/competition-shadow --snapshot snapshots/incoming/TIMESTAMP
```

Add `--collector-root snapshots/collector` for a dry scheduling report.
`--execute` permits bounded supplementation and diagnostic analysis, requiring
`METACULUS_TOKEN` for fresh read-only status/rule checks and
`OPENROUTER_API_KEY` for model calls. `--network-supplement` additionally permits
the existing bounded free HTTP/browser recovery tools. Execution is limited
to five available tasks per dispatch and reserves at least thirty minutes
before starting a stage. Status/rules are rechecked before supplementation,
before analysis and after analysis. A call already in flight cannot be
canceled by this adapter; an expired result is preserved without a candidate.

The manual GitHub workflow `FutureEval competition shadow runtime` adopts a
successful main-branch collector run and resolves its original monitor run
from the collector report. Its default is dry operation. Every dispatch
restores and uploads the durable shadow artifact, including failed attempts.
It never receives Tavily or Exa secrets.

## Candidate policy

Both available model probabilities produce their arithmetic mean. A missing
Mercury result can produce an explicitly labeled single reasoning route.
Unavailable analysis never produces the bookkeeping default of 0.5. Quality
flags and provisional recovery status accompany the candidate. These fields
are diagnostics, not an approval gate or a Metaculus receipt.

## Remaining launch gates

- Dedicated prospective analysis context and event-window handling.
- Typed numeric, discrete and multiple-choice forecasts; currently recorded
  as unsupported rather than silently skipped.
- Durable submission outbox, timeout readback, separate forecast/comment
  receipts and official testing-area validation.
- Automatic event wiring, independent watchdog and local archival integration
  for the new artifact. Manual dispatch and a 90-day artifact alone are not
  durable production scheduling or long-term storage.
- Confirmation of the required participation form and bot permissions.

Neither successful collection nor `shadow_ready` means a forecast was accepted.
