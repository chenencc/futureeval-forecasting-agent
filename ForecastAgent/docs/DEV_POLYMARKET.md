# Polymarket discovery development

This module is isolated on `dev_polymarket`. Production retrieval, analysis,
forecast submission and release tags remain independently maintained.

## Run a bounded acquired-question trial

```sh
python -m ForecastAgent.polymarket_discovery --source /path/to/tasks --output snapshots/polymarket-trial --limit 20
```

The input is a directory of immutable `ID/bundle.json` files. Selection is spread
across the sorted available ID list. Each question has at most two public Gamma
queries and one result page per query. This trial uses no model, Tavily or Exa.
Original bundles and their provider ledgers are not changed.

Responses, including failed requests, are cached. Resume with the same directory
to reuse them; input identity changes are rejected. `manifest.json` freezes source
hashes and caps, and `report.json` retains question rules and candidate contracts.

## Interpreting results

Candidates use the existing conservative lexical matcher. A candidate is not a
verified equivalent contract. Review entity, event stage, deadline and timezone,
threshold, geographic scope, and resolution source. Market end dates do not
establish the event deadline. Closed markets remain visible for discovery but
have no live price in this report. All candidates remain ineligible for edge.

This is a current discovery test over historical questions, not a point-in-time
backtest. Missing candidates can reflect query design, first-page limits, absent
contracts or API errors. No-match and API-error counts are reported separately.

API reference: https://github.com/Polymarket/py-sdk/blob/main/src/polymarket/clients/public.py

## No-Stream-inspired trial (2026-10-03)

Run: https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37090846344
Implementation commit: `6a6b809`.

The same twenty questions reused cached response projections, added one public
Gamma query each, formed a deduplicated pool of up to sixty child contracts, and
made one free Super ranking request per question. All twenty calls succeeded.
Four questions retained nine reference contracts: four other-cut rows and five
driver/consequence rows. No row was graded same-quantity/same-date. These are model
classifications, not independently verified equivalence labels or accuracy.

Actual additional usage: twenty Gamma requests and twenty model HTTP attempts;
469,091 reported prompt tokens and 1,111 completion tokens. No Tavily, Exa,
forecast submissions or production changes.

Known limits to address before expanding:

- Nineteen pools reached the sixty-child ceiling. Large event families can crowd
  out later API results. Preserve event families and report omitted rows before
  treating the selected slate as exhaustive.
- Contract direction must be explicit. A YES on removal is not a YES on
  participation, even when both concern the same tournament.
- Ranking reads bounded rules text and must retain original complete responses
  for subsequent clause review.
- Query expansion and pooling changed together; this is an exploratory pipeline
  comparison. A frozen-pool comparison is needed to isolate the ranker's effect.
