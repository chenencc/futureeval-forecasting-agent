# Historical collection campaign

The blind fixture contains 135 unique Metaculus questions selected from 2026
ForecastBench question sets. It is not a claim that all questions were created
or resolved in 2026. Outcome labels remain separate and are never loaded.
Original source: `forecastingresearch/forecastbench-datasets`, commit
`5e184cb7ccf4d577787c8201e08875e2fe070a43`.

## Operation

```sh
python -m ForecastAgent.historical_batch init --root snapshots/historical-2026-135
python -m ForecastAgent.historical_batch status --root snapshots/historical-2026-135
python -m ForecastAgent.historical_batch run --root snapshots/historical-2026-135 --limit 5
python -m ForecastAgent.historical_batch archive --root snapshots/historical-2026-135 --output snapshots/historical-2026-135.zip
```

Initialization and status are offline. Run requires the existing provider keys.
The manual Actions workflow restores the same campaign before running at most
five questions. It has no schedule. Completed tasks are cached; incomplete tasks
resume with their original budgets. Errors are isolated per question. Five
execution reservations per question and exponential retry cooldowns prevent
unbounded reruns. Missing task state after an execution reservation blocks
further acquisition, preventing accidental search-budget resets.

Each question has three lifetime Tavily basic attempts, including failed or
interrupted reservations. The model transport additionally has 72 lifetime HTTP
attempts. Each resumed run has 24 logical turns and a 900-second dispatch
deadline; bounded tools already in progress can finish after that deadline.
Model retry delays cannot cross the deadline. Ultra remains the fixed model.

`batch.json` and `blind_inputs.json` freeze ordering, cutoff, criteria and input
hash. Each task preserves `bundle.json`, `intelligence.json`, raw sources,
conversation, tool results, reserved/completed step timestamps, execution
versions and `model_calls/`. Transport records include the request, raw response
body, decoded response, usage when returned, retry index and elapsed time.
Authorization headers are excluded and configured keys are redacted. A reserved
attempt with no completion has an unknown outcome and still consumes budget.
Existing old ledgers may lack transport telemetry; it is not reconstructed.

## Historical limitations

The temporal report separates pre-cutoff local snapshots, current captures of
old material, unknown publication dates and quarantined material. Publication
metadata is a source claim, not proof of a past body version. Original question
rules may contain later edits and have not been audited. Model outcome knowledge
is not controlled. Therefore `historical_clean` remains false. This campaign
produces exploratory cutoff-aware intelligence packages, not a clean backtest.

## Preservation

The ZIP contains all task files and a per-file SHA-256 manifest. Model response
files are included, even though intelligence.json references them by relative
path. Download and preserve the portable archive outside GitHub for long-term
storage: Actions artifacts have 90-day retention. The workflow refuses to reset
budgets when previous executed campaign state is unavailable. No source bodies
or model transport records are committed to the public repository.

Preparation does not start acquisition. First inspect a five-question pilot,
then another five, before running the remaining campaign batches.
