# ForecastAgent Research Console

The web branch starts at release `v1.0.1`, commit `c9bfab44a670f4307ebc2330866479c82aec71ef`.
This read-only UI presents the release pipeline: original acquisition, independent supplement,
Mercury original-evidence conditional rereading, and validated delivery receipts.
It does not change the forecasting runtime or trigger provider calls.

## Run locally

```sh
cd ForecastAgent/web
npm ci
npm run dev
npm run build
```

The checked-in `public/data/console.json` contains six bounded real historical trial
projections (binary, numeric, multiple choice, discrete and date). Their original
runtime commits have not been verified as the release tag. This distinction is visible
in the UI. They are archived examples, not live competition status, and are not a
leakage-free backtest. No forecast submission receipt is available in these examples.

## Update from saved artifacts

Create a local specification with task folder paths and verified source run IDs:

```json
{"tasks":[{"path":"PATH_TO_SAVED_TASK","run_id":"VERIFIED_RUN_ID"}]}
```

From the repository root:

```sh
python -m ForecastAgent.web_export.export --spec LOCAL_SPEC.json --output ForecastAgent/web/public/data/console.json
python -m unittest ForecastAgent.tests.test_web_export
```

Each folder requires `analysis-input.json`; optional `analysis/result.json`,
`mercury/result.json` and supplement journals provide further stages. The exporter
reads a strict field allowlist. It excludes headers, credentials, raw prompts and
responses, local paths and unrelated state. Source previews are capped at 1,800
characters and retain a saved body hash. Text is rendered as text, never executed HTML.
The exported JSON must be reviewed before publication: the source material itself
can contain private information even after credential redaction.

## Presentation contract

- Missing stages and token usage are `not_recorded` / `null`, never invented zeroes.
- A captured page is not semantic verification or event truth.
- Analysis output is not a submission receipt.
- Original publication, observation and capture timestamps remain distinct.
- Binary probabilities, categorical distributions and numeric CDFs are displayed
  from saved output without rerunning or recalibrating the forecast.
- Search/filter, question deep links, stage navigation, expandable source previews,
  archive exports and original run links work without a backend or browser secrets.

## Remaining work

This first version is a static snapshot. Live artifact ingestion, release-worker
adapters, authenticated private full-body reading, experiment pairing, receipt
read-back, Polymarket equivalence records and deployment are not yet implemented.
Do not describe the export timestamp as the time of a forecast or a live worker update.
