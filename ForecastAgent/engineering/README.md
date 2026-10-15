# ForecastAgent engineering P0

Version: `forecast-engineering-p0-v1`. This package provides four engineering
capabilities without replacing native task, budget, evidence or delivery ledgers.
All command-line entry points are local and make zero model/search/platform calls.

## Authority and scope

| Record | Authority |
|---|---|
| Native campaign/worker state | Tasks, cumulative budgets and execution recovery |
| Original content-addressed objects | Complete captured bodies and source clocks |
| Market and competition SQLite | Saved query projections with native provenance |
| Engineering SQLite | Traces, lifecycle observations and isolated experiments |
| Confirmed bound platform receipt | Actual prediction acceptance |

Engineering recovery plans are advice only. They do not submit, trade, reset
budgets, reopen tasks, replace an original body or run an autonomous retry loop.
Existing terminal records remain terminal. Raw captures and later supplements
remain separate vintages. The website's redacted excerpts are not research inputs.

## 1. Executable contracts

`schemas.json` is a JSON Schema2020-12 document. `contracts.validate` also checks
body hashes, question identities, source uniqueness, cutoff clocks, complete
categorical option sets and monotonic numeric/discrete/date CDFs. Schema changes
require an explicit version and adapter, rather than silently changing old data.

- Unknown usage is null. An unknown capture/publication clock remains null.
- `strict_as_of` rejects unknown or late question clocks and late/unknown source
  capture clocks; `capture_time_unbounded` is explicitly not a historical backtest.
- Numeric CDF endpoints may be0and1. Binary clipping is not applied to CDF knots
  or categorical vectors. Production probability policies remain unchanged.
- Empty-body packets must carry a gap. They can be audited but cannot invoke a
  prediction runner. Invalid model output remains saved in the experiment store.

## 2. Correlated traces

`Trace` records dispatch/task identifiers, parent/child spans, actual monotonic
duration, typed failure and known/unknown usage. Reservations are committed before
callbacks. A hard process kill leaves a visible running span; no duration or usage
is invented. Metadata rejects credential-bearing fields and recognizable tokens;
prompts and full source bodies are not trace metadata.

`status` gives stage P50/P95, completed/failed/incomplete counts and unknown-usage
counts. A span is a measured function invocation, not a confirmed submission.
Old task states imported from databases do not invent historical trace durations.

Market monitor/worker entry points support optional `FORECAST_ENGINEERING_ROOT`.
The worker wraps actual native task, collection/map and forecast functions; the
monitor measures its actual discovery invocation. Engine activation preserves the
selected engineering package while native modules retain their pinned sources.
Leaving the variable unset retains the original execution path. Deployment and
publication of these new traces require the corresponding selected code/controller;
local tests are not a claim of production activation.

Both platforms can also call `native.observed_call` around their existing entry
point. Competition database observation is implemented; its immutable production
release entry point has not been replaced or promoted by this package.

## 3. Frozen experiments and replay

An experiment fixes explicit domain/question/batch IDs, original body hashes,
rules/options/scales, cutoff policy, exact prompt/model/config for each route and
engineering implementation hash. Experiments are independent of native tables.

`snapshots.resolve` recovers complete native JSON, verifies archive bytes and the
original bundle hash, then exports a strict allowlist. Resolutions, old predictions,
market prices, transcripts, graphs and later unselected material are excluded.
The packet is a sanitized acquisition snapshot, not a claim to reproduce the old
model's exact selected input. Missing original objects never fall back to Web text.

The experiment name is immutable. To change a prompt, cohort, configuration or
implementation, create a new experiment. Attempt claims are atomic and unique per
case/route/mode. Repeating completed, failed or interrupted work never calls a
runner again. Interrupted attempts stay visible for explicit review. Audit mode
does not consume a future prediction-run slot.

The CLI performs input validation only. Actual model trials use the Python
`experiments.run(..., runner=...)` adapter, whose caller must retain native provider
reservations, spending authorization and exact model/config execution. This package
does not create a second provider client or grant any new allowance.

Labels have a separate table and source hash. Event resolution and future market
price are distinct types. Event Brier is calculated only for resolved labels;
ambiguous labels are withheld. Paired Brier uses only cases completed by every
route. No score is invented for unstarted/failed/unresolved cases. This numerical
evaluation does not by itself certify temporal validity or forecasting skill.

## 4. Lifecycle and recovery

Stages: discovery, acquisition, supplement, mapping, analysis, delivery and sync.
States: queued, running, retry_wait, completed, completed_with_gaps, blocked,
submission_unknown and accepted. Transitions are serialized in SQLite. Recovery
of one stage does not reopen a completed earlier stage.

| Condition | Advice |
|---|---|
| Completed, completed with gaps, accepted or blocked | Preserve terminal state |
| Submission response unknown | Authenticated native readback only |
| Running with interrupted owner | Inspect owner and exact native checkpoint |
| Retry deadline still future | Wait |
| Credentials, integrity, request/schema problem | Repair before native retry |
| Budget wait with recorded future reset | Wait; recheck native allowance later |
| Budget exhausted without a reset deadline | Requires explicit review |

Native adapters retain observed states even when a retry deadline is already
past. They never fabricate a new deadline or treat an HTTP502as account quota
exhaustion. Competition acceptance requires a payload-bound saved accepted receipt;
an accepted-looking task without that proof is submission_unknown in this view.

## Commands

Install `requirements.txt` into the selected Python environment, then run from the
checkout (or use the installed immutable runtime path recorded in runtime.json):

```powershell
python -m ForecastAgent.engineering --root E:/metaculus_data/engineering doctor
python -m ForecastAgent.engineering --root E:/metaculus_data/engineering inventory --domain competition --database E:/metaculus_data/competitions/store/competition.sqlite3
python -m ForecastAgent.engineering --root E:/metaculus_data/engineering freeze --spec frozen-spec.json
python -m ForecastAgent.engineering --root E:/metaculus_data/engineering replay --experiment EXACT_EXPERIMENT_ID
python -m ForecastAgent.engineering --root E:/metaculus_data/engineering observe --domain market --database E:/metaculus_data/prediction_markets/store/market.sqlite3
python -m ForecastAgent.engineering --root E:/metaculus_data/engineering evaluate --experiment EXACT_EXPERIMENT_ID
python -m ForecastAgent.engineering --root E:/metaculus_data/engineering status
```

`frozen-spec.json` contains `name`, explicit `cases` and `routes`. Each case has
`domain`, `database`, `batch_id`, `task_key`, optional `as_of_utc`. Every route has
`id`, `model`, exact `prompt`, `config`. `inventory` is a discovery aid; it never
selects the latest batch inside a running experiment.

## Storage and operation

`engineering.sqlite3` has version1and eight application tables: objects, traces,
spans, lifecycle, experiments, cases, runs and labels, with foreign keys,
unique attempt claims and transactional initialization. Unknown future DB versions
fail closed. Source database connections are query-only and closed explicitly;
SQLite may create WAL coordination files without modifying native business rows.

Full evidence and prompts in experiment objects are private research data. The
summary/status JSON contains counts and identifiers, not full bodies or credentials.
Back up the engineering database along with the frozen manifests; original source
databases and referenced objects remain under their existing backup policy.

### Verification

```powershell
python -m unittest discover -s ForecastAgent/engineering/tests -t . -v
```

Tests cover native body/hash preservation, hidden-answer traps, all five question
types, concurrent claims, killed processes, retry deadlines, terminal preservation,
payload-bound receipts, output schemas and separated price/outcome evaluation.
