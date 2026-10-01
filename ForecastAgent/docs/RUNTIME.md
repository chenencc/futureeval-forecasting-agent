# Acquisition runtime contracts

The acquisition model is configured independently from the task protocol. Codex is an operator and code
maintenance interface, not a runtime dependency. This runtime borrows execution
patterns from coding agents without importing a coding SDK or TradingAgents.

## 1. Tool contracts

Every model call is checked against the tools actually exposed for that turn.
The gateway validates types, required fields in optimized profiles, enum values,
item/character limits and numeric ranges before execution. It checks existing
need IDs, discovered/saved URLs, document indices and empty reading ranges.
Errors include a code, field, correction instruction and bounded allowed values
inside `tool_result_v1.data.contract_error`.

Search queries must share a meaningful task/need/entity token. This conservative
lexical check rejects internal capability names and unrelated queries before a
provider reservation. It is not semantic relevance, reliability or fact checking.
Older profiles retain optional metadata compatibility. Direct batch tools keep
independent per-item failures; child calls still validate before HTTP.

Channel and skill enums use independent schema objects. Shared string definitions
cannot accidentally impose a channel enum on a query, quote or entity field.

New collection tasks require one Exa provider attempt before normal finish.
Preflight rejections do not satisfy it; provider failures do consume the attempt.
The obligation never overrides forced closure or physical resource caps. Its
completion is reported independently from useful discovery and capture adequacy.
Existing optional policies and frozen allowances remain unchanged. See
[Exa discovery](EXA.md) for unavailable credentials and interrupted reservations.

## 2. Context management

`collection_context_v2` projects program state, source/document metadata, excerpt
references and up to two complete recent tool groups. The serialized message
projection is bounded to 28,000 Unicode characters, not tokens. This excludes
the separately transmitted tool schemas. Oversized groups are omitted whole;
tool calls and replies are never separated. State truncation is explicit.

Bodies, provider payloads and the full transcript stay in the task snapshot.
Only loaded, frozen acquisition skills are injected on every turn. Unloaded
skills remain catalog entries. If immutable instructions alone exceed the
ceiling, the dispatch fails before a model HTTP request, preserving the ledger.
Historical audit-only body text and raw payloads are omitted from the model view.

The runtime uses one collection instruction contract, with no mandatory recent
search checkbox or competing three/five-search instructions. Existing task
ceilings remain frozen: v3 has three Tavily basic attempts; legacy v2 ledgers
retain their original allowances. No migration creates search or model credit.

## 3. Progress detection

Each turn records new accepted source keys, readable parsed versions, excerpt
associations, located passage IDs, newly delivered reading ranges and market
snapshot IDs. Discovery and material progress are reported separately. Planning
and first skill loading can advance preparation, without counting as material.

Blocked/empty bodies, repeated catalogs, identical excerpts and wholly overlapping
read ranges do not advance acquisition. Errors from one tool cannot be hidden by
a successful catalog call in the same batch. Three consecutive stalled turns or
error turns enter bounded closure. The program can export explicit gaps if model
closure fails. Progress is an acquisition measure, never a truth or quality score.

## 4. Sessions, recovery and termination

`bundle.json`, `intelligence.json` and the inspection interface expose `sessions`,
`session_state` and `progress`. Each session records budget/attempt counters before
and after dispatch, per-turn deltas, its stop reason and whether it is resumable.

| State | Meaning |
| --- | --- |
| `running` | A dispatch is executing. |
| `interrupted` | A prior dispatch stopped before finalization. |
| `completed` | Export finished and mechanical acquisition checks reported no gaps. |
| `completed_with_gaps` | Export finished with missing material or capture warnings. |
| `budget_exhausted` | Dispatch/time/lifetime limit ended execution. |
| `retryable_failure` | A transport/context failure preserved an incomplete package. |

Acquisition adequacy remains separate in `result.acquisition_complete`. A valid
archive or dated dataset does not require an extra recent search to permit
completion. A missing recent search is a discovery note, not an unconditional
failure. Current historical bodies remain audit-only, and `historical_clean`
remains false.

Dispatch exhaustion is resumable only while lifetime model allowance remains.
Completed exports return without another model call. Lifetime exhaustion exports
preserved material without another model request and cannot automatically resume.
Existing `incomplete`/`status`/runtime-stage fields remain compatible with batch
and monitoring callers. Their own retry/backoff caps still apply.

Pure local replies are atomically cached in `tool_outputs/`, keyed to the task,
arguments, source versions/provenance and relevant state. A checksum is verified
before replay. Replayed text does not create progress. Source changes invalidate
the key. Mutating tools and paid/network operations are not replayed through this
cache. Unknown interrupted reservations remain consumed; identical acquisition
attempts are blocked rather than resubmitted. Recovery never resets budgets.

## Validation

Offline tests cover wrong parameters with zero HTTP spend, protocol-safe context
bounds, loaded skills surviving compaction, historical body isolation, overlapping
reads, cached reply/version integrity, mixed success/error stalls, interrupted
paid reservations, resumable outages and lifetime exhaustion. These tests do not
establish improved live evidence quality or reduced model token consumption.
Use a later explicitly scoped pilot with unchanged question/cutoff/budgets to
measure those outcomes from actual transport records.

The maintained audit supports `python -m ForecastAgent.review_campaign ARTIFACT
--run RUN_ID --fresh` for independent acquisitions. It compares whole experiment
totals with the initial v3 and v2 baselines rather than subtracting preserved
prefix costs. Input identity, transport hashes, search caps, mandatory Exa and
session dispatch caps are reported separately from evidence quality.

See [model-independent acquisition](MODEL_INDEPENDENT_ACQUISITION.md) for the
shared operating clock, batch reading frontier, parameter preflight and separate
12-decision / 4-failure / 16-HTTP dispatch limits. Existing lifetime ledgers remain
unchanged.
