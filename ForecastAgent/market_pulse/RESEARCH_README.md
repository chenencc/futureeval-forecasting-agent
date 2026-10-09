# Financial research inventory and consensus imports

This is an additive research layer for Market Pulse. P0/P1 are offline. P2 adds
explicit experimental acquisition routing and a bounded agent pilot. Existing
production prompts, forecasts and submission behavior are unchanged. P0/P1 and
offline P2 planning require no credentials; a live P2 pilot requires the existing
local provider configuration. The production worker does not invoke P2 yet.

## P0: material inventory

Audit a saved acquisition package, original bound variables and an archived model
exposure library. Each question has eight checklist slots: latest identified saved
actual, immediately preceding fiscal quarter, same quarter in the previous year,
target-metric guidance, consensus, consensus revisions, operating observations,
and accounting changes. Guidance leaves use their explicit forward-quarter
contract; historical actual slots are marked not applicable.

States are `not_found`, `saved_unparsed`, `parsed_unexposed`, `exposed`, and
`not_applicable`. A keyword hit is only an unparsed lead; it does not prove issuer,
period or metric compatibility. Not found means not established in these saved
inputs, not that the information does not exist. Absence of guidance is not
negative evidence. The latest saved report is not necessarily the latest public
report. A future target-quarter actual release is not required historical input.

Every exposed fact requires row, unit and period references inside the supplied
original exposure library. Hashes, offsets and literal text are checked. Without
an archived library, parsed facts remain unexposed. Exposure is not independent
fact verification. Already bound history facts with unknown accounting basis
remain exposure records, not accepted GAAP consensus matches.

```powershell
python -m ForecastAgent.market_pulse.research inventory `
  --package saved-package.json --variables reviewed-variables.json `
  --state source-state.json --output new-inventory.json
```

Use the original acquisition package and full bound variables, not a compact
model state or supplement descriptor. For saved-report supplements, follow
`parent_package` and use the supplement's full `variables` array.

## P1: source-bound consensus adapter

Import saved HTML tables or explicitly prepared rectangular JSON grids. An
explicit mapping identifies each cell, original row/column labels, metric,
statistic, basis, units, estimate role and period. This keeps interpretation
separate from extraction. There are no issuer-specific selectors or quarter dates
in the adapter. Changed labels are gaps rather than silently accepted values.

Example mapping (coordinates start at zero; header row/column are excluded):

```json
{
  "metadata": {
    "url": "https://example.org/consensus",
    "issuer": "Example Company",
    "captured_at_utc": "2028-10-01T00:00:00Z",
    "published_at_utc": null,
    "upstream_id": null,
    "upstream_identity_evidence": null,
    "publication_time_evidence": null
  },
  "mappings": [{
    "row": 1, "column": 1,
    "expected_row_label": "Revenue mean",
    "expected_column_label": "Q4 FY2028",
    "metric": "revenue", "basis": "not_applicable",
    "source_unit": "USD_billions", "statistic": "mean",
    "role": "consensus_estimate", "period_kind": "fiscal_quarter",
    "period_label": "Q4 FY2028", "fiscal_period": [4, 2028]
  }]
}
```

```powershell
python -m ForecastAgent.market_pulse.research import-consensus `
  --format html --source saved.html --mapping mapping.json `
  --table-index 0 --output new-consensus.json
python -m ForecastAgent.market_pulse.research inventory `
  --package saved-package.json --variables reviewed-variables.json `
  --state source-state.json --consensus new-consensus.json `
  --as-of 2028-10-02T00:00:00Z --output new-inventory.json
```

Supported statistics: mean, median, low, high, standard deviation, analyst count.
GAAP and adjusted EPS never merge. USD, millions and billions normalize to raw
USD; per-share values retain per-share units. The source and grid hashes, raw
HTML where available, exact cell coordinates, literal values and mappings are
retained. For multilevel grids, `statistic_header_row` and
`expected_statistic_label` additionally bind the statistic to an original header.
Duplicate records are removed without overwriting source packages.
Merged/nonrectangular HTML tables are explicitly withheld for manual review;
a reviewed grid import must declare its transformation and source format.
Provider error envelopes are not grids and cannot become estimate records.

`Q3-2026` is not automatically a fiscal quarter. A fiscal mapping requires an
original explicit FY label; calendar and unmapped quarters remain candidates.
Unknown dates never become capture dates. Date-only publication information
should be retained in metadata without inventing an exact UTC publication time.
As-of eligibility requires valid, declared publication/capture times with
publication evidence; after-cutoff, stale or unknown times require review.
Publication evidence and mappings are supplied interpretations, not independently
verified assertions. The compatibility report exposes this distinction.

Upstream identity is recorded separately from the hosting domain. Twenty-four
analyst estimates are not twenty-four independent sources. Shared upstream
records form one declared group; unknown upstream identity remains unknown.
Revision deltas are computed only within one declared issuer/upstream/metric/
basis/period/statistic/unit series, using publication time rather than capture
order. Analyst dispersion is not a calibrated forecast error distribution.

## Validation on 2026-10-09

- 199 Market Pulse tests passed, including 22 new inventory/consensus tests.
- Four saved questions were audited with their archived exposure libraries.
- No model, Tavily or Exa calls were needed for the saved-input audit.
- Native public Tesla capture returned HTTP 403. The blocked body was preserved;
  it was not accepted as consensus content. A separate web-extracted, manually
  transcribed two-row EPS table exercised 16 statistic records and one declared
  upstream group. This is not an automated-fetch qualification or target-quarter
  consensus coverage; its original quarter label remains unmapped.
- Alpha Vantage's public demo returned an information/error response rather than
  estimate data. Endpoint entitlement and a native provider schema remain
  unqualified. No API-specific field names or free access claims are assumed.

Reports are independent sidecars; existing snapshots and submitted forecasts
remain intact. All CLI outputs use exclusive creation to prevent replacement.

## P2: operating-mechanism acquisition

Route research gaps through generic mechanisms, without issuer-specific selectors:

| Question family | Candidate mechanisms |
| --- | --- |
| Revenue | Volume, price/mix, segments, currency, guidance, consensus, revisions |
| GAAP diluted EPS | Margins, expenses, taxes, diluted shares, one-offs, guidance, consensus, revisions |
| Forward guidance | Prior guidance, management language, demand/volume, price/mix, consensus |

The P0 inventory distinguishes saved, parsed and exposed material. A missing slot
is a research lead, not a mandatory requirement for every issuer. Future actuals
and unpublished future guidance are not completion requirements. Historical
guidance can be useful context but does not become target guidance.

`mechanism.plan` creates candidates from saved original bodies/documents and
observed URLs in rules, searches, source leads and actual page links. It rejects
outcome fields, credential-bearing links and explicit sibling-issuer routes.
Original fiscal labels and bound fact references supply provisional source-role
hints. Calendar-to-fiscal mappings are never inferred. Guidance question issuer
identity is bound to the original title; a trailing `(Revenue)` is a metric leaf,
not an issuer name.

Exact passage previews deduplicate derived views within one URL and driver.
Alternate coordinates remain in the audit. This is not semantic deduplication
across different sources. Reads retain original hashes and character offsets.
Each new packet displays up to 9,000 characters with explicit continuation
coordinates; this avoids claiming a full read when the model saw only a prefix.
Original source truncation remains a gap even after all saved text is read.

```powershell
python -m ForecastAgent.market_pulse.research plan-mechanisms `
  --package saved-package.json --variables reviewed-variables.json `
  --state source-state.json --output new-mechanism-plan.json
python -m ForecastAgent.market_pulse.mechanism_trial `
  --package saved-package.json --variables reviewed-variables.json `
  --state source-state.json --output new-p2-stage `
  --model-http 2 --local-reads 4 --free-captures 2 --network
```

Omit `--network` to disallow new page captures. Configure the existing free model
with `FORECAST_MODEL` and process-local credentials; never include key values in
arguments or reports. Super was used for the first bounded pilot.

The standalone agent can choose a saved read, an observed public URL capture, or
stop. It uses forced function output with program-owned action/driver IDs. Search
requests are handoffs only: the standalone pilot dispatches **zero Tavily/Exa
searches**. It does not export forecasts, verify facts, compute probabilities or
send submissions. New captures and reads stay in timestamped independent sidecars.

For a new explicitly opted-in original-collector experiment:

```python
from ForecastAgent.market_pulse.mechanism_acquisition import collect

result = collect(original_request, new_task_directory)
```

This process-local wrapper adds mechanism guidance to the existing collector.
All original tools, reservations, provider fallback, output formats and budget
gates still run. Search attempts remain owned by the **same original task ledger**:
at most **three Tavily basic searches and one Exa search** over its lifetime.
Failed attempts count. Saved-URL free reads do not consume new search attempts.
Existing tasks cannot silently migrate to a changed P2 policy.

The standalone stage separately freezes its small model/read/capture limits,
input hashes, model and implementation identity. A lock prevents duplicate local
workers. Every HTTP/action reservation is persisted before execution; interrupted
unknown attempts remain consumed. Restarts do not replenish budgets. A failed
URL is not automatically repeated, and an HTTP error body is preserved.

### Initial bounded validation on 2026-10-09

- 221 Market Pulse tests passed, including 22 P2 regression tests.
- Three archived cases: Apple EPS, Microsoft revenue and NVIDIA guidance.
- Actual pilot: 5 successful Super HTTP responses, 66,754 reported tokens,
  USD 0 reported cost, zero Tavily/Exa calls and zero new URL captures.
- Apple selected two derived views of one retrospective "above expectations"
  passage. These were not qualified target guidance or independent new evidence.
- Microsoft read saved guidance, then stopped with gaps; the original pilot
  packet exposed only part of the archived reading to the model.
- NVIDIA stopped without reading prior-quarter context. Its future target
  guidance release was not required to complete current acquisition.
- Offline fixes tightened guidance triggers, removed matching derived passage
  views, added exact continuation actions and clarified prior-context roles.
  They were replayed on the same saved inputs with **zero extra provider calls**.
- Original eight source files and all frozen pilot inputs remained unchanged.

This qualifies bounded execution and offline routing safeguards, **not improved
new-source recall or forecast performance**. The revised agent choices and the
opt-in full collector still need a separate bounded live qualification. Consensus
and revision gaps remain. No production change, quota reset or submission occurred.

Structured report and original pilot receipts:
`E:/metaculus_data/tournaments/market-pulse-26q4/research/p2-20261009/report.json`.
