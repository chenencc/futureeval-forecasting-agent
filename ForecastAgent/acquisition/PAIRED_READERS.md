# Paired source-reader pilot

This opt-in harness tests one acquisition boundary at a time. It does not call a
model, search provider, analysis module, or forecast submission API.

## Design

1. Select at most five questions not used to repair the candidate reader.
2. Freeze exact questions, official rules, original collection hashes, observed
   search leads, source URLs and matching checks before network execution.
3. Fetch at most two common public sources per question. Compare release and
   candidate parsing of the **identical response bytes**.
4. On frozen HTML sources, run both browsers with the same installed browser,
   two render reservations per question, 25 routed requests per render and a
   20-second configured deadline. Alternate which arm runs first by question.
5. Retain raw responses, rendered DOM, capture times, failure records and hashes.
   Export independent packages with lineage to the unchanged original parent.
6. Review entity, measurement, period, source role and usable rows. Regex checks
   locate possible evidence; they do not certify a fact. Review the original
   spans before opening the arm mapping. Report omissions and regressions.

The live browser arms receive sequential site responses, so their DOM is not
identical. This is a bounded source-reader pilot, not an end-to-end forecasting
A/B experiment. Original agent discovery is shared, not rerun independently.

## Frozen allowances

| Capability | Allowance |
| --- | ---: |
| Shared HTTP source operations per question | 2 |
| Browser operations per arm per question | 2 |
| Routed browser requests per render | 25 |
| Configured render deadline | 20 seconds |
| Local HTML reparses per arm per question | 8 |
| New OpenRouter, Tavily, Exa calls | 0 |
| Automatic retries | 0 |

Previous search ledgers are immutable and remain charged to their original
tasks. A trial output root has its own new free source-read reservations. A
failed or reserved operation is never automatically rerun. Resuming verifies
parent, code and capture hashes. Browser routed-request counts do not prove the
number of all wire transactions or redirect hops.

## Run

Install `ForecastAgent/requirements-crawl4ai.txt` and Playwright Chromium first.

```sh
python -m ForecastAgent.acquisition.paired_sources \
  --manifest frozen-manifest.json --output snapshots/paired-readers
python -m ForecastAgent.acquisition.paired_sources \
  --manifest frozen-manifest.json --output snapshots/paired-readers --execute
```

Preflight is the default. On Windows, `--browser-channel chrome` selects the same
installed headless Chrome in both **experimental** arms; it does not edit the
release browser or production worker. The manifest binds that choice.

## October 8 cohort

MiniBench question IDs: `46074`, `46079`, `46091`, `46096`, `46113`. They cover ECDC
West Nile surveillance, TEPCO robotic-arm operation, NOAA tide observations,
UODO breach announcements and Ukrainian parliamentary votes. None was used in
the preceding Crawl4AI prototype repair. They are existing archived questions,
not five newly published questions.

The shared initial agent discovery is archived release 1.0.4 material. The paired
source-reader baseline is unchanged release **1.0.5** code. This isolates reader
behavior; it is not a full new release 1.0.5 agent run. Later official-rule
supplements are separately identified in the frozen input. Analysis stays at
release 1.0.5 and is not executed here.

The target event windows are after trial collection. Missing future values do
not establish an extraction failure or a NO outcome. Browser access cannot
grant protected content. PDF sources remain on the specialized existing reader.

### Output

- `identity.json`: exact inputs, model-free scope, browser and code identity.
- `state.json`: reservation journal, failures, capture hashes and transport audit.
- `captures/`: raw HTML/PDF or rendered DOM and separately derived documents.
- `packages/<question>/<arm>.json`: independent, unscored evidence overlays.
- `report.json`: per-source metrics and located evidence.
- `blinded-review.json`: comparison rows without explicit arm names.
- `blinding-map.json`: interpretation key, opened after material inspection.

Snippet shape may reveal the reader; reviewer masking is not fully blind.
Promotion requires inspected target evidence, bounded consumption and no
material regression. Five questions cannot establish forecast accuracy or
general reliability across all sites.

## Observed pilot results

The frozen October 8 trial completed all ten source selections and nine browser
pairs. It used ten shared HTTP operations, eighteen browser reservations, eight
local reparses and **zero** new model, Tavily or Exa calls. Original five parent
bundles and previous search allowances remained unchanged. All 33 stored
response/DOM capture files passed checksum verification.

| Observation | Release reader | Crawl4AI reader |
| --- | ---: | ---: |
| Fresh readable selected sources / 10 | 8 | 9 |
| Captured browser results / 9 | 8 | 9 |
| Explicit deadline-partial DOM captures | 0 | 2 |
| Allowed routed browser requests | 171 | 201 |
| Median browser audit elapsed seconds | 5.98 | 7.16 |

Release browser successes are not proof of complete target data. Candidate
browser captures include two explicitly partial pages. Readability does not
establish the correct entity, time or metric.

The candidate recovered the TEPCO news index and retained additional NOAA
station context and parliamentary bill titles. It did not recover the embedded
ECDC country-count dashboard, NOAA six-minute observations or a FELG-specific
announcement from this source pool. Navigation/footer years produced misleading
regex matches, and an April robotic-arm arrival notice is not an October
operating-status report. The target dates are future at collection time.

The original trial remains immutable. A separate **offline** export retains a
complete HTTP body when a render ends in partial DOM. The partial versions stay
in the original capture journal, with independent hashes and timestamps. Use:

```sh
python -m ForecastAgent.acquisition.paired_sources \
  --manifest frozen-manifest.json --output snapshots/original-trial \
  --export-preserved snapshots/preserved-export
```

This command performs no network requests and binds the original trial identity
and state hashes. New live trials use the same complete-before-partial selection
rule. Original pre-correction live ledgers refuse execution after a code change;
they are not reset or silently rerun.

The promotion decision remains **optional backend only**. The next independent
capabilities to test are observed iframe/data-response discovery and explicit
article/table/navigation/footer roles. Source artifacts and the English
per-question audit are archived outside the public repository.
