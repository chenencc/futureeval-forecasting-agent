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
