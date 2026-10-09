# Financial research inventory and consensus imports

This is an additive, offline research layer for Market Pulse. It does not change
acquisition budgets, model prompts, forecast distributions or submission behavior.
No provider credentials are required. The production worker does not invoke it yet.

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
