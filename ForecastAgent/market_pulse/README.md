# Market Pulse Challenge 26Q4

An isolated tournament integration based on `v1.0.5-crawl4ai.1`
(`ba94cea523528b98b8fa67ebb0b9dd0ff3d626bc`). Development branch:
`codex/market-pulse-26q4`.

## Implemented scope

- Authenticated, read-only official tournament discovery and complete visible-feed pagination.
- Group/subquestion normalization using the existing release interface.
- Immutable raw API snapshots, inherited-rule provenance, input hashes, and file checksums.
- Outcome/community-free acquisition inputs and numeric/discrete payload format checks.
- Explicit financial acquisition profiles with unknown issuer IDs and fiscal periods retained as unknown.
- Quote-bound manual audit findings for the initial NVIDIA guidance and AMD revenue conflicts.

The command cannot submit, trigger workflows, call paid providers, reset budgets,
or modify FutureEval state. `automatic_candidate` is an eligibility/preflight
label; it is not a claim that every rule has received semantic verification.
The format probe is synthetic test data, never a model forecast or a submitted distribution.

```powershell
python -m ForecastAgent.market_pulse snapshot --root D:\metaculus\snapshots\market-pulse-26q4\NEW_TIMESTAMP
python -m unittest ForecastAgent.market_pulse.tests.test_inventory
```

`METACULUS_TOKEN` must already be loaded into the process from local secure storage.
Destinations must be new directories. Responses and partial failure state are preserved.

## Verified initial inventory

Authenticated audit on **2026-10-08 10:45 UTC**:

| Visible group | Leaf questions | Type |
| --- | ---: | --- |
| First reported GAAP diluted EPS after September 2026 | 8 | Discrete |
| First reported quarterly revenue after September 2026 | 8 | Numeric |
| NVIDIA forward guidance | 3 | Discrete |
| Total | 19 | 11 discrete + 8 numeric |

All 19 leaves are open, the bot has forecasting permission, and all 19 pass
distribution-format preflight. Four leaves require manual rule review; the
remaining 15 are initial automatic candidates, not predictions.

Project metadata lists 67 leaves, but 48 are not exposed in the current visible
feed. Their status is unknown; do not call them upcoming or forecastable.
The tournament description advertises 65 questions, which also differs from
the project metadata. Refresh the official feed as questions are published.

## Rules and scoring

- Project ID: `33131`; slug: `market-pulse-26q4`.
- Official competition page permits bot participation and prizes, with one entry
  per participant, either bot or human.
- Prize pool: USD 7,500. Tournament forecasting end: December 31, 2026.
- Dedicated tournament text specifies **spot scoring**, not time-average coverage.
- Initial question metadata sets spot scoring before the actual close. Until
  reconciled, use the earliest explicit spot/close timestamp as the conservative
  delivery deadline. Retain every original timestamp; do not rewrite it.
- Earliest current conservative deadline: Tesla EPS/revenue, October 19 at 12:00 UTC.
- NVIDIA guidance has contradictory fiscal quarters/release dates in its title,
  resolution criteria, and fine print. All three guidance leaves need rule review.
- AMD revenue closes October 29, while its rules mention an expected November 3
  release. Preserve the platform close and flag the discrepancy.

The manual audit is explicitly scoped to observed text. New questions and edited
rules require a new audit; the two detectors are not a universal financial parser.

## Acquisition design: reuse the engine, specialize the evidence contract

Keep the release collector, independent supplement, HTML/PDF/table readers,
Crawl4AI repair, provider failover, reservations, and Mercury analysis unchanged
for the initial baseline. Use the existing production Super model for acquisition.
Financial specialization should first improve evidence selection and metadata.

### P0: earnings contract and original issuer releases

For each leaf record the verified issuer, ticker/CIK when available, fiscal period,
report publication window, metric, currency, scale, and whether the target is an
actual or forward guidance. Never infer a ticker/CIK from a title alone.

Prefer the issuer investor-relations earnings release and tables, then its SEC
8-K exhibit / 10-Q / 10-K. Reuse the existing `sec_issuers` and SEC submissions
tools. Add report/exhibit discovery and dated Company Facts normalization as
separate adapters, not as replacements for the HTML/PDF readers.

Keep GAAP diluted EPS distinct from basic and adjusted EPS. Preserve loss signs,
stock-split basis, quarterly versus cumulative values, and first-release versus
restated values. Revenue question bounds use raw USD even where a source displays
millions; guidance leaves may use billions or percentages. Unit conversion requires
an explicit source unit and an auditable calculation.

SEC Company Facts is a free, keyless API. It is useful for historical context;
retain taxonomy, concept, unit, period start/end, fiscal metadata, accession, form,
and filing date. It is not proof of the first earnings-release value, nor does it
provide analyst consensus. Reject silent annual/YTD-to-quarter substitutions.

### P0: source roles and shared evidence

Store official actuals, management guidance, and analyst estimates separately.
For estimates preserve the publication time, contributor count and statistic
when available, target quarter, GAAP/non-GAAP basis, and stale/missing status.
An earnings-calendar date is a scheduling lead, not a confirmed issuer announcement.
Yahoo historical share prices do not substitute for earnings or revenue data.

Build one immutable issuer/report evidence package for sibling EPS/revenue leaves.
The cache key must include verified issuer identity, report period, source URL or
accession, content hash, and capture timestamp. Allocate network cost to the
acquisition receipt that performed it. Consumers reference the same evidence;
they do not claim a second capture or reset their lifetime quotas.

### P1: bounded search and financial fallback

The initial Tavily basic lifetime maximum remains **three**; Exa remains **one**.
Suggested roles: (1) exact official issuer/report, (2) independently dated estimates
and prior guidance, (3) the highest-value unresolved metric/period/source gap.
Do not require every question to consume all three Tavily searches.

Use free HTTP/table/PDF readers first, bounded browser repair next, and the existing
basic Extract rescue for important unreadable pages. Preserve shell pages, failures,
missing consensus, and provider exhaustion as gaps. Add any larger budget through
an explicit new policy/version; never silently refresh a saved task's quota.

## Participation worker still to implement

Do not change the FutureEval worker slug and assume that creates a second tournament.
Its accepted-once terminal state is inappropriate for controlled spot-score revisions.

The separate worker must use state namespace `market-pulse-26q4`, preserve per-leaf
rule/input hashes, receipt-confirmed submissions, and pending/retry statuses. Reuse
release collection and analysis functions; keep tournament scheduling/delivery outside
the frozen analysis core. Re-check rules, close times and own forecast receipts before
spending or submitting. Initial delivery plus a bounded evidence-driven pre-deadline
revision should have explicit cumulative budgets and idempotency keys. A previous
accepted receipt must not mean either an unconditional re-run or a permanent ban on updates.

Next measured trial: Tesla EPS, Tesla revenue, and one different issuer revenue leaf.
Compare shared issuer coverage, exact fiscal period/units, readable official tables,
gaps and actual provider consumption. Run analysis only after the acquisition package
has passed those checks. Deploy a dedicated worker after receipt/revision and deadline
tests; the initial branch has not enabled production submissions.

## Sources

- [Official tournament](https://www.metaculus.com/tournament/market-pulse-26q4/)
- [Official project API](https://www.metaculus.com/api/projects/tournaments/market-pulse-26q4/)
- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)

## Initial three-question acquisition pilot

Completed locally on **2026-10-08 at 11:55 UTC** with the unchanged v105
acquisition/supplement chain and Super. Acquisition IDs: Tesla EPS `46190`,
Tesla revenue `46197`, and Apple revenue `46191`. No analysis or submission ran.

| Question | Program-readable captures | Reviewed useful background | Finding |
| --- | ---: | ---: | --- |
| Tesla EPS | 5 | 3 | Operating data and secondary EPS estimates; original GAAP history still missing |
| Tesla revenue | 2 | 0 | CDN error reference and an unrelated daily calendar |
| Apple revenue | 4 | 2 | FY2026 Q1 release and FY2026 Q3 SEC 10-Q; target-quarter guidance/estimates remain missing |

The review is assistant reading of saved bodies, not a blind human evaluation or
truth certification. All three exported packages retain their original gaps.
Export completion does not establish readiness to forecast.

Consumption: **30 successful Super responses, 303,716 reported tokens**, six
Tavily basic searches (two per question), three Exa logical searches, and three
basic Extract batches. No reported token usage is unknown; no task exceeded the
three-basic/one-Exa limits. Extract batches are separate from basic Search limits.

Priority findings:

1. Shared group rules leak other issuers' URLs into supplemental capture. Every
   task spent supplementary attempts on AAPL's calendar and AMD's earnings page.
   The calendar URL did not honor the intended issuer filter in its saved body.
2. The critical material plan concentrates on a future earnings release. Separate
   the future resolution artifact from already public predictors: prior-quarter
   tables, operating releases, management guidance, and dated estimates.
3. Supplement reparse turns an identical saved CDN error body into usable thin text.
   Preserve negative capture diagnostics for the same content version and identify
   navigation separately from issuer financial evidence.
4. Sibling Tesla questions did not share useful financial evidence. Their common
   capture hash was an unrelated calendar, not an issuer financial report.

The local runner initially hit the release's previous-ledger guard because runner
logs were in the collector directory. The failed launch consumed no model/search
calls. Its failure records, unchanged inputs, and explicit startup migration are
preserved. Collector state now lives under `tasks/<id>/retrieval`, separately from logs.

The executed runner installed credential transport after some release modules
captured their HTTP aliases. That run's transport receipts cover Extract only;
model journals and Search records independently preserve the successful calls.
The next runner revision installs transport before imports; an offline alias test
passes. This fix did not trigger another collection or change the original results.

Artifacts: `E:/metaculus_data/tournaments/market-pulse-26q4/pilots/v105-three-20261008/`:
`manifest.json`, `executed-runner-source.py`, `run-status.json`, `audit.json`,
`quality-review.json`, and per-task original collection/supplement packages.

Use `python -m ForecastAgent.market_pulse.audit --root <pilot-root>` for a capture
audit; `review_pilot` is explicitly scoped to this initial three-question review.
The revised runner's `run` command requires a new experiment root; source-hash
checks prevent an implicit restart of this frozen, completed pilot. Model/provider
credentials must already be loaded securely. `METACULUS_TOKEN` is used only in
parent discovery and removed from acquisition children.

Inventory and runner tests: **13 passing**. The frozen release manifest remains
valid with all 245 existing source files unchanged.

## Financial P0 acquisition overlay

The next pilot uses `market_pulse.collection.collect`, which verifies the v105
release and installs a versioned, acquisition-only financial policy in each
isolated child process. This is a branch experiment, not a production release.
The policy does not change analysis, model routing, resource caps, or submission.

1. **Issuer scope.** Decode explicit group list rows and observed ticker URLs.
   Exclude known sibling issuer routes from the model catalog, free recovery
   frontier and independent supplement queue before truncating candidates.
   Keep shared SEC and unknown independent sources. A hostname is only a routing
   hint; unknown CIK, reporting period and metric remain unverified. A calendar
   URL's symbol filter is checked against the saved body and cannot establish
   issuer identity by itself.
2. **Body quality.** Reject short CDN error references and flattened financial
   menus, while retaining brief substantive financial statements. A saved raw
   version's explicit access rejection survives reparse and generic diagnostics.
   Empty parser failures can still recover. Invalid original and supplementary
   bodies are retained in `financial_audit_pages`, outside active evidence;
   parent capture status, source hashes, gaps and sidecar lineage remain auditable.
3. **Available information.** Supply the same research contract to the system
   guidance and per-turn task view: published financial history, operating
   drivers, management guidance, dated estimates, and the separate eventual
   settlement artifact. An unpublished release is `pending_publication`, not
   missing evidence of current predictors or an event-absence conclusion. The
   contract preserves original rules, dates, GAAP/adjusted distinctions, table
   units, quarter/cumulative columns and historical enforcement.

The new input contains `financial_acquisition_policy` with immutable original
field hashes and SHA-256 hashes of `financial.py`, `quality.py` and `collection.py`.
The pilot manifest binds those hashes. Changing the policy requires an explicit
new experiment; it cannot silently migrate a completed or resumed ledger.
Runtime aliases are restored on exit, including modules imported during the
collector run. Concurrent activation in the same process is rejected.

### Offline acceptance

Run:

```text
python -m unittest ForecastAgent.market_pulse.tests.test_inventory ForecastAgent.market_pulse.tests.test_pilot ForecastAgent.market_pulse.tests.test_financial
python -m ForecastAgent.market_pulse.replay_p0 --root <immutable-pilot-root> --output <new-report-directory>
```

The three-question paired replay uses the identical archived bodies. It blocks
network/model access, checks all original file hashes and preserves accepted text.

| Question | Old reported usable pages | New active body pages | Reviewed useful pages lost |
| --- | ---: | ---: | ---: |
| Tesla EPS | 5 | 3 | 0 |
| Tesla revenue | 2 | 0 | 0 |
| Apple revenue | 4 | 2 | 0 |

Six false inclusions are isolated: two CDN error shells, one pure navigation
page and three calendars without the correct target issuer. The five previously
reviewed useful background bodies remain byte-identical. This verifies routing
and acceptance, not fresh recall improvement or forecast readiness. Tesla revenue
still has no active evidence; a future live pilot must acquire published financial
tables and other available predictors. No provider calls, tokens or budget resets
were used for the offline acceptance. **32 tests pass**; the release's 245 frozen
files remain unchanged.

Evidence archive:
`E:/metaculus_data/tournaments/market-pulse-26q4/experiments/financial-p0-offline-20261008/report.json`.
Per-task `overlay.json` files preserve the separate active/audit page branches.
Do not rerun the completed original pilot root with this runner. Use a new bounded
experiment to measure actual agent source choice and acquisition quality.

## Bounded next batch

The runner accepts one to five distinct numeric IDs, frozen in its manifest.
Existing manifests reject changes to the selected IDs, runner, policy or inputs.
The default remains the original three-question pilot for backward compatibility.
Provider transport must be installed before imported HTTP aliases, and credentials
must already be loaded securely. The parent can discover official questions; the
acquisition child receives no Metaculus token. Model selection remains Super.

```text
python -m ForecastAgent.market_pulse.pilot run --root <new-pilot-directory> --ids 46176,46195,46193,46181,46198
```

This selects Apple EPS, Microsoft revenue, Amazon revenue, Meta EPS and NVIDIA
revenue. It runs two acquisition children concurrently and preserves all task
results; it does not analyze or submit. The current inventory/runner/financial
regression suite has **43 passing tests**.

### Explicit empty-start recovery

The five-question pilot exposed output truncation and instruction duplication
before any discovery or capture. The financial model view now uses compact roles,
exact question handles and a 6,000-token output ceiling. This does not increase
the number of model requests, searches or captures allowed by the release.
Administrative plan projections omit repeated original-field quotations while
retaining their hashes and coordinates. Original task fields, source-reading
replies, full plans and transcripts remain on disk.

`recovery.py` creates a separate recovery directory only for audited, empty
pre-search failures. It checks all completed model receipt hashes, copies every
prior attempt and session, and changes only the financial policy identity and
resumable result. It rejects acquired material, changed question/budget fields,
an exhausted three-execution cap, active/incomplete pilots and reused directories.
No quota is reset. The source directory must remain byte-identical.

```text
python -m ForecastAgent.market_pulse.recovery --source <finished-empty-pilot> --root <new-recovery-directory>
python -m ForecastAgent.market_pulse.pilot run --root <new-recovery-directory> --ids <same-frozen-ids>
```

Use the same Super model and disabled fallback environment during preparation
and execution. Secure provider transport must be installed before HTTP imports.
The recovery receipt records original source-file hashes, cumulative model
attempts and preserved limits. Recovery is acquisition-only; it cannot analyze
or submit forecasts. Passing offline context replay does not certify live recall.

For a still-empty task with no frozen plan, an explicit final recovery can use
`--ids <failed-id> --seed-published-history`. The generic template is validated
by the release tool before execution. It binds published financial-history
material to original question handles; it does not infer settlement facts. Prior
invalid plans, failure counts, sessions and provider attempts remain preserved.
Only the terminal plan latch is removed after successful plan validation. The
same three-execution lifetime guard still applies.

## Financial analysis pilot

`analysis.py` is an additive local prototype over immutable release packages.
It does not change the frozen v105 analysis or the FutureEval worker. It refuses
a process containing `METACULUS_TOKEN`, performs no search/capture/submission,
and requires only already-configured OpenRouter credentials.

The first pilot selects Apple GAAP diluted EPS `46176`, Microsoft revenue
`46195`, Amazon revenue `46193`, and Meta GAAP diluted EPS `46181`. Each input
is bound to its acquisition adapter SHA-256 and original rule-field hashes.
An exact issuer rule row supplies the fiscal period. Revenue history from the
platform background is converted from explicitly stated USD millions to raw USD;
no quarter dates or verified issuer actuals are invented for that background.

The analysis-only view excludes same-body duplicates, unusable shells, observed
foreign issuer routes, filing search navigation and social share pages. Original
packages and their gaps remain byte-identical. Full source bodies remain archived;
selected model input is bounded and does not imply that every body was read.

The prototype uses the Mercury **Decisions** endpoint: one typed distribution
plus financial interpretation diagnostics, then at most one conditional local
reread. The first/second request caps are 40,000/56,000 JSON bytes. They are byte
bounds, not token guarantees. There are at most two durable decision-stage
reservations per task. Credential transport may retry a quota-exhausted key on
the backup; audit its physical receipts separately from those reservations.

Forecast intervals are conditional on valid numeric resolution. Administrative
annulment is not zero revenue/EPS or outside-range mass. Original first-report
rules, units, boundaries and 200-bin metadata remain visible. The output is a
validated 201-point CDF clipped through the existing release formatter. If the
median is in an open tail, its exact numeric value remains unknown; the code
reports the tail boundary instead of inventing a point estimate.

```text
python -m ForecastAgent.market_pulse.analysis --root <new-analysis-directory> --report <financial-five-report.json>
python -m unittest discover -s ForecastAgent/market_pulse/tests
```

The manifest freezes the four input paths/hashes, source report, implementation,
model and caps. Restarting under a changed identity is rejected; provider
reservations are not replenished. Preserve the executed source when changing
implementation after a completed experiment.

### Observed first run: completed, not ready for submission

Local run completed on **2026-10-08 at 13:36 UTC**. All four emitted valid CDFs;
the five physical HTTP requests returned 200 using the primary credential. Apple,
Microsoft and Meta used the first decision; Amazon used a second original-text
read. No forecast was submitted or acquisition budget reset.

| Target | Original model probability below platform lower boundary |
| --- | ---: |
| Apple Q4 FY2026 EPS, below USD 0.995/share | 79.52% |
| Microsoft Q1 FY2027 revenue, below USD 90.2 billion | 69.20% |
| Amazon Q3 FY2026 revenue, below USD 198 billion | 95.01% |
| Meta Q3 FY2026 EPS, below USD 5.345/share | 80.52% |

These are experimental outputs, not verified outcomes. The dominant open-tail
pattern raises a quality gate. Amazon's selected second input contains target
guidance of USD 197–202 billion, yet the model assigns approximately 95% below
USD 198 billion without an inspectable numeric derivation. This is not proof of
an incorrect future outcome, but is insufficient evidence to deploy this pilot.

Two further design limitations were observed: inherited sufficiency rubric
levels still ask for decisive observation coverage despite future-quarter
instructions, and the generic 900-character reread threshold is not a guarantee
that missing financial headers/rows were selected. Do not use format validity
or model diagnostic confidence as financial prediction calibration.

The native Decisions receipts report **536,567 input + 50 output tokens** and
**USD 0**. The original run summary incorrectly assumed `total_tokens` existed;
the independent report contains corrected accounting. The formatter now accepts
the Decisions input/output fields without inventing unknown/zero usage.

Evidence: `E:/metaculus_data/tournaments/market-pulse-26q4/analysis/financial-mercury-four-20261008/`.
Review: `E:/metaculus_data/tournaments/market-pulse-26q4/reports/financial-analysis-four-20261008.json`.
The original manifest, requests, answers, summary, source coordinates, acquisition
hash checks and executed analysis source are preserved. **49 offline tests pass**;
all 245 frozen release source files remain unchanged.

### Proposed next analysis contract

1. Bind an issuer, fiscal quarter, exact metric, unit and first-report rule.
2. Create a quote-bound financial fact table with the complete column header,
   fiscal period, source unit, conversion, publication time and source role.
   Separate official actuals, management guidance and adjusted analyst estimates.
3. Use one short Super financial interpretation over the same originals: revenue
   growth/seasonality and supplied guidance; EPS margins, taxes, diluted share
   count and observed one-offs. Require calculation and exact evidence references.
4. Ask Mercury for the final distribution and independent consistency diagnostics.
   Keep raw probabilities, formatting changes and open-tail limitations visible.
5. Route material unit/period/guidance conflicts to bounded local rereading before
   delivery. Keep service availability and submission status separate from semantic
   readiness, and retain a validated first response on a reread service failure.

This five-step successor was subsequently run on the same four evidence packages.
The measured results and limits are recorded below. Evaluate numeric CRPS,
interval coverage, tail calibration and official scoring after first-report
resolutions arrive; these quantities do not have binary accuracy or Brier scores.

## Financial fact table and short analyst pilot

The local successor uses this sequence:

```text
Immutable saved financial reports
  -> exact numeric tokens, table headers and original source coordinates
  -> Super: explicit assumptions and short named equations
  -> program: unit normalization and arithmetic checks
  -> Mercury Decisions: numeric outcome bins and independent diagnostics
  -> valid 201-point CDF, disagreement checks and uncertainty review
```

`facts.py` discovers rows locally and keeps all candidates. `numeric_binding.py`
binds selected numbers to exact original tokens, retains original unit captions
and headers, and converts currency/share scales and percentages explicitly.
Fiscal-quarter labels and GAAP interpretations remain model claims. A quoted
header is not proof that the model selected the correct column. Publication
times and missing comparable-quarter predictors are not fabricated.

The compatibility layer can resolve a number ID used where a row reference was
expected. It records that repair, rejects unknown/foreign references, and never
changes source values. An explicit conversion identity such as 62.5 billion
times a declared conversion factor of 1000 equals 62,500 million preserves the
physical dollar quantity. An ordinary business multiplier does not receive that
exception. Wrong arithmetic, unnamed constants and thousandfold share-count
errors are rejected before decision scoring.

The fresh entry point is `financial_chain.py`. It has two initial Super
reservations, then one compact derivation repair if necessary: **three cumulative
Super reservations per task**, not a refreshed initial budget. Mercury has one
first decision and at most one same-evidence recheck. State/request hashes,
executed source, original messages, provider receipts and rejected outputs are
preserved. A changed frozen request is rejected. Credential transport receipts
are audited separately from logical reservations.

```text
FORECAST_MODEL=nvidia/nemotron-3-super-120b-a12b:free
python -m ForecastAgent.market_pulse.financial_chain --root <new-directory> --baseline <frozen-four-question-baseline>
```

Load local credentials through the existing hidden DPAPI launcher. Do not put
secret values in command arguments or documentation. This experiment has no
Metaculus credential, new search, acquisition, or submission.

### Observed result on 2026-10-08

All four tasks produced valid distributions over unchanged acquisition packages.
The first strict schema trial failed before Mercury: numeric and reference IDs
were mixed, millions/billions were expressed inconsistently, and real arithmetic
errors were present. The failed responses remain archived. Apple and Amazon were
recovered offline; Microsoft and Meta used one additional compact repair each.
Meta's corrected response required an audited explicit unit-conversion identity;
that second recovery made no additional Super call. Prior Mercury results were
reused only under an identical request identity.

| Target | Super median | Mercury median | Mercury central 80% interval |
| --- | ---: | ---: | --- |
| Apple Q4 FY2026 GAAP diluted EPS | USD 1.9625/share | USD 1.9411/share | USD 1.8952–1.9871/share |
| Microsoft Q1 FY2027 revenue | USD 91.807 billion | USD 91.7950 billion | USD 91.3324–91.9019 billion |
| Amazon Q3 FY2026 revenue | USD 199.5 billion | USD 199.4552 billion | USD 198.8997–200.0449 billion |
| Meta Q3 FY2026 GAAP diluted EPS | USD 6.345/share | USD 6.3925/share | USD 6.3499–6.4352/share |

Those Mercury intervals are **8.6–16.6 times narrower** than the analyst's
intervals. The original format/arithmetic gate passed, but this is not empirical
calibration. A post-hoc diagnostic now routes an interval compressed by more than
four times to uncertainty review without editing the model distribution. Report
that gate as post-hoc when inspecting this already-frozen trial. All four remain
**not ready for delivery** under the financial/uncertainty review.

Apple uses prior Q3 EPS as an explicitly weak Q4 proxy because comparable Q4
evidence is absent. Microsoft's alternative formula uses Q2 as a Q1 seasonal
proxy and its sequential assumption is not independently established. Amazon
has explicit current Q3 management sales guidance of USD 197–202 billion; that
guidance is not a predictive probability interval. Meta assumes Q2 net margin
and diluted shares persist; future tax/expense effects are not separately modeled
by its accepted net-margin equation.

Actual accounting includes failed and repair requests: **10 Super + 4 Mercury
physical HTTP attempts**, all HTTP 200; Super reports **394,987 tokens** and
Mercury **317,367 tokens**, total **712,354**. Every receipt reports usage and
USD 0. No Ultra calls occurred. Request keys stayed on the primary credential.
The four numerical targets are unresolved; no accuracy, CRPS or calibration win
is claimed. Compared with the prior Mercury-only run, context selection,
analyst derivation and the diagnostic rubric changed together.

Structured independent audit:
`E:/metaculus_data/tournaments/market-pulse-26q4/reports/financial-super-mercury-four-20261008.json`.
It rechecks source hashes and exact coordinates, recomputes equations, validates
CDFs, and counts original provider receipts rather than trusting stage counters.
Initial failed and recovery ledgers are byte-identical. **67 offline tests pass**;
the 245 frozen release source files remain unchanged. This isolated pilot has
not been promoted to the production worker.

## Official supplements and formula-only strengthening

The additive local experiment has four separate artifacts:

1. `enrichment.py` saves bounded official HTML/JSON downloads, raw bytes,
   timestamps, request receipts, source hashes and explicit failures. CIKs come
   from observed SEC archive URLs. Its SEC company-facts normalizer excludes
   annual/YTD durations and future filings, retains the earliest available SEC
   filing vintage, and never claims that this proves the first earnings release.
2. `formulas.py` binds reviewed variables to original numeric tokens, unit
   captions and period/header coordinates. Original background series receive
   variable IDs without invented fiscal dates. Shared headers and source spans
   are deduplicated in the model view; full material stays archived. Only
   explicit sibling list rows are omitted, with source hashes and coordinates.
3. `formula_trial.py` asks Super for assumptions and named equations. The program
   computes all values and dimensions. `formula_recovery.py` permits at most one
   additional compact repair after the two initial reservations. Decisions and
   rejected generations are preserved. Mercury scores original evidence
   independently, without the Super center or scenarios. A previous valid
   independent decision can be retained with explicit request provenance.
4. `strengthening_audit.py` replays source and arithmetic bindings independently.
   Exact copies or exact means of referenced source quantities can be linked
   numerically without changing any value. Their future persistence and fiscal
   interpretation assumptions remain unverified. Wrong unit scales stay
   rejected. `projections.py` supplies separately labeled guidance anchors and
   unweighted EPS sensitivities, never silently repaired model forecasts.

`error_distribution.py` requires time-bound forecast/actual pairs, compatible
metric/units and forecasting method, first-release verification, unique target
periods and at least 12 usable samples. That sample threshold is a development
gate, not proof of sufficient calibration. A nondegenerate empirical residual
candidate still needs rolling out-of-sample coverage, CRPS/interval-score and
width checks. Guidance endpoints and unweighted scenarios are not probability
intervals. Platform bounds are not a forecast prior; the full raw distribution
is retained and the existing export adapter clips CDF probabilities to 0.02–0.98.

### Four-question strengthening observation, 2026-10-08

The official supplement used **14 free download attempts**, of which nine
returned readable reports. All four SEC company-facts requests returned HTTP
403; one observed Amazon historical URL returned 404. Original acquisition
snapshots remain unchanged. New materials are archived separately under
`E:/metaculus_data/tournaments/market-pulse-26q4/supplements/financial-strengthening-four-20261008`.
No Tavily, Exa or new collection-agent call was made.

The material now includes Apple Q4 FY2025 EPS, Microsoft Q1 FY2027 total-company
guidance, Meta Q2 FY2026 legal/severance costs and the prior Q3 tax outlier, plus
two Amazon historical guidance/result pairs. Those two release pages have not
been independently authenticated as immutable first-publication vintages; they
are diagnostic history, not an admitted calibration sample.

| Target | Prior Mercury median | Independent enriched Mercury median | Program guidance anchor |
| --- | ---: | ---: | ---: |
| Apple Q4 FY2026 GAAP diluted EPS | 1.9411 | 1.9321 | Not applicable |
| Microsoft Q1 FY2027 revenue, USD billion | 91.7950 | 90.5649 | 90.4000 |
| Amazon Q3 FY2026 revenue, USD billion | 199.4552 | 200.6571 | 199.5000 |
| Meta Q3 FY2026 GAAP diluted EPS | 6.3925 | 6.2364 | See unweighted EPS sensitivities |

All four independently scored CDFs pass the 201-point export format. Three Super
formula outputs can be source-bound and computed after an explicitly recorded
offline compatibility replay. Meta remains rejected: it declares 62.5 raw USD
and 2.566 raw shares rather than their billion-scale source quantities. Its
standalone program baseline and cost-recurrence sensitivities are archived
separately. They are not model repairs or calibrated distributions.

Important semantic gaps remain. Apple Super wrongly interprets an adjustment to
the **2024 comparison** as a missing **2025 GAAP EPS** and invents a difference.
Its two scenario names also reference the same calculation. Microsoft/Amazon
still interpret date-unknown background sequence elements as particular fiscal
quarters. Meta's formula omits the newly supplied expense/tax distinctions.
These findings block delivery; source binding and arithmetic alone do not
validate the reasoning. The new distributions are not empirically calibrated.

Accounting includes the first failed run, continuation and final missing
decision: **10 Super + 4 Mercury = 14 physical provider requests**, all HTTP 200
on the primary key, **259,298 reported tokens**, with no unknown usage and USD 0
reported cost. Prior trial: 14 requests and 712,354 reported tokens. Context
compression reduced reported tokens by **63.6%**; request-count optimization
has **not** succeeded. Logical journal caps and actual credential-transport
attempts are audited separately; no credential failover happened in this trial.
No old reservations were reset. Parent and continuation journals are hash-verified.

Four unresolved targets are mechanism tests, not an accuracy comparison. Material
coverage, analyst schema and independent decision exposure changed together.
There is no historical-error distribution or blending result for these four
tasks, and **zero are declared ready for delivery**. Keep this branch isolated.
All **89 offline tests** pass, and **245 frozen release files** remain unchanged.

Packaged audit: `E:/metaculus_data/tournaments/market-pulse-26q4/reports/financial-strengthening-four-20261008.json`.
The canonical executed audit stays in the analysis run directory. The packaged
report adds download provenance and per-task request-cap checks. It preserves
all original model generations, decisions, source snapshots and failure records.

## Financial source review and typed derivations

The next additive local experiment separates four different checks:

1. `review.py` merges overlapping exact original spans and restores local table
   headers, fiscal labels and distant adjustment footnotes. Source hashes,
   coordinates and the complete archives remain unchanged. Date-unknown
   background series are context only; they cannot become dated predictors.
2. `review_trial.py` asks Mercury for source-interpretation diagnostics, then
   requests one restricted Super formula and one independent Mercury outcome
   distribution. Source approval probabilities are fallible diagnostics, not
   evidence truth or forecast probabilities. This per-variable review is an
   expensive experimental option, not the recommended default for every task.
3. `derivation.py` provides source-bound templates for guidance midpoints,
   after-tax net margin/share projections and same-fiscal-quarter GAAP EPS
   growth. The program owns unit conversion, `(1 + growth_rate)`, fiscal-period
   compatibility and cost-removal sensitivity. Previously reported net income
   is never taxed twice. Guidance already includes management's growth outlook;
   the fixed midpoint template forbids adding that growth again.
4. `template_correction.py` repairs only rejected derivations, with at most one
   additional Super request per rejected task. An unchanged independent decision
   is retained. Changed source material requires a separately bounded Mercury
   decision. Invalid model assumptions remain rejected; a program baseline is
   labeled separately and is never presented as repaired model output.

All new stage reservations are persisted before HTTP. Neither source acquisition
nor an earlier analysis budget is restarted. The workflow keeps original
generations, rejections, request identities, executed source copies and provider
usage records. Later offline gates are explicitly reported as post-hoc.

### Four-question review observation, 2026-10-08

The first review used 12 physical requests and 551,098 reported tokens. It
produced four valid distributions, but inspection found growth rates used as
level multipliers, an adjacent quarter called a year-over-year comparable,
withheld-variable reuse, undeclared constants and duplicate scenario formulas.
Numeric/format success did not establish semantic correctness.

One newly downloaded official Apple PDF was visually checked on pages 1 and 4.
Its current-year quarterly diluted EPS is USD 1.85. The separate non-GAAP
reconciliation concerns 2024, not the 2025 current figure. This resolves the
ambiguous headline interpretation without rewriting the earlier Mercury
rejection. The PDF has a later Last-Modified date, so an immutable original
publication vintage remains unauthenticated.

Three selective Super corrections plus one Apple changed-source Mercury
decision bring the total to **7 Super + 9 Mercury = 16 physical requests**.
The final report includes **619,112 reported tokens**, zero unknown usage and
USD 0 reported cost. All requests returned HTTP 200 on the primary credential.
This is **138.8% more reported tokens** than the preceding 259,298-token trial.
Additional contexts and diagnostic heads changed together; no controlled
model-only comparison or accuracy improvement is claimed.

| Target | Previous Mercury median | Final independent Mercury median | Final derivation observation |
| --- | ---: | ---: | --- |
| Apple Q4 FY2026 EPS, USD/share | 1.9321 | 2.0369 | Same-quarter program formula valid; Super assumes zero growth, only a persistence baseline |
| Microsoft Q1 FY2027 revenue, USD billion | 90.5649 | 90.5792 | Unchanged supported guidance midpoint: 90.4 |
| Amazon Q3 FY2026 revenue, USD billion | 200.6571 | 200.5630 | Super applied 10.5% growth again to current guidance; rejected; separate program midpoint: 199.5 |
| Meta Q3 FY2026 EPS, USD/share | 6.2364 | 6.3519 | Program EPS: 6.9511 under an unverified 50% prior-cost removal assumption |

The executed template validator initially accepted all four numeric derivations.
The later independent semantic gate rejects Amazon's guidance double counting;
the original generation and execution report are preserved. Final status:
**four format-valid CDFs, three accepted model derivations, one separately labeled
program baseline fallback, zero declared ready for automatic delivery**.

Apple's corrective analyst view omitted approved recent EPS growth predictors.
Future template context selection now retains approved supporting context, not
only variables in the final equation. This and the improved source-conflict
diagnostic were verified offline and were not rerun as provider experiments.
Future growth, net-margin persistence, cost recurrence and diluted shares remain
assumptions. Historical error data and rolling probability coverage are still
missing. No automatic probability blend or forecast submission occurred.

**104 offline tests** pass, including rate-as-level, incompatible guidance periods,
adjacent-quarter proxies, GAAP/adjusted EPS, share dimensions and repeated-tax
counterexamples. The **245 frozen release files** remain unchanged. Keep this
trial isolated from the production worker.

Final independent audit:
`E:/metaculus_data/tournaments/market-pulse-26q4/reports/financial-review-four-20261008-final.json`.

## Compact source-bound financial experiment, 2026-10-09

`compact_trial.py` reuses completed source-interpretation diagnostics and hashes
the previous independent request. The original question, exact evidence library
and canonical variable rows must match the previous exposure byte for byte.
Source approvals remain fallible diagnostics; original full archives are kept.
Acquisition, source-review reservations and previous analyst budgets are preserved.

Typed templates now require target-period revenue guidance, both endpoints from
the same source, compatible fiscal-period tax guidance and recent prior net
income/share data. Local original forecast labels such as "third quarter 2026"
are recognized; prior-year comparison clauses cannot establish the target.
"Remaining quarters" tax guidance requires an original report-quarter label and
an explicitly covered year. No fiscal quarter is inferred from a calendar date.

The bounded stage uses no Super call for a fixed revenue-guidance midpoint and
one Super call per EPS template. The assumption schema fixes speculative cost
removal and share changes to zero in the central projection. A nonzero growth
assumption must cite original financial predictors. Historical costs remain
unweighted sensitivity cases; references do not prove a future assumption.
Apple's complete approved context includes observed EPS-growth predictors that
the previous corrective projection omitted.

One Mercury outcome head per task reads the same original source exposure,
without analyst assumptions, calculated centers, scenario values or previous
forecasts. Other independent model diagnostic heads are omitted. Literal source
bindings, dimensional calculations and distribution uncertainty checks run
offline. No automatic average, arbitrary distribution widening or calibrated
probability claim is introduced.

### Four-case local observation

All six physical requests returned HTTP 200 on the primary credential:
**2 Super + 4 Mercury**, **72,889 reported tokens**, zero unknown usage and USD 0
reported cost. The previous four final derivation/decision stages used eight
physical requests and **219,183 tokens**: conditional stage consumption is down
**66.7%**. The entire preceding source-review/correction experiment used 16
requests and 619,112 tokens. That total includes source review now reused, so it
is not a fair from-scratch cost comparison. New questions may need source review.

| Target | Previous independent median | New independent median | Program or analyst observation |
| --- | ---: | ---: | --- |
| Apple Q4 FY2026 EPS, USD/share | 2.0369 | 2.0120 | Super cites 19%/22% observed EPS growth, assumes 20% future growth; program projection 2.22, unverified |
| Microsoft Q1 FY2027 revenue, USD billion | 90.5792 | 90.4753 | Program guidance midpoint 90.4; no analyst request |
| Amazon Q3 FY2026 revenue, USD billion | 200.5630 | 199.3816 | Program guidance midpoint 199.5; no additional guidance-growth multiplier |
| Meta Q3 FY2026 EPS, USD/share | 6.3519 | 6.2446 | Persistence projection 6.3487; speculative cost disappearance excluded from the center |

All four 201-point CDFs are format-valid. Microsoft and Amazon's 10th percentiles
lie below the platform range: respectively **below USD 90.2 billion** and
**below USD 198 billion**. An open tail is preserved rather than filled with an
invented point estimate. Apple and Meta's new 80-percent widths are 0.4639 and
1.5348 USD/share; wider intervals are not evidence of improved calibration.

The executed gate accepted one of two EPS analyst responses. It incorrectly
rejected Meta's zero-growth premise because its references included tax rates.
Tax, one-off expense and diluted-share evidence are valid financial context.
The corrected gate replays the saved generation offline and accepts it, with
**zero additional provider calls**. `compact_audit.py` records original and replay
acceptance separately and preserves the executed source, rejection and raw
response. Independent probabilities are unchanged by this post-generation gate.

**123 offline tests** pass; the 245-file release remains unchanged. Four development
cases support mechanism and cost observations only. First-publication error
history, source-interpretation accuracy, out-of-sample performance and probability
coverage remain unvalidated. Zero questions are marked ready for automatic
delivery; no forecasts were submitted and the production worker was not changed.

Final immutable audit:
`E:/metaculus_data/tournaments/market-pulse-26q4/reports/financial-compact-four-20261009-final.json`.

## Additive saved-report coverage, 2026-10-09

`source_coverage.py` distinguishes **saved original report**, **period-bound fact**
and **actual model exposure**. It reads the target period from the original rules,
then checks the most recent explicitly identified saved report, the immediately
preceding fiscal quarter, the same quarter of the prior fiscal year and existing
target revenue guidance. A pending future release is not a required historical
input. There is no issuer, question ID, fiscal date or financial value in the
reusable mechanism.

Only original fiscal/release/highlights labels establish report periods. Quarter
end dates and URLs cannot establish a fiscal quarter. Comparisons, outlooks,
ambiguous labels and unknown periods remain diagnostic gaps. The latest identified
saved report need not be the latest publicly available report. Coverage alone is
neither proof of fiscal semantics nor factual verification.

`saved_source_trial.py` consumes a frozen cohort and explicitly selected task IDs.
Only missing saved narrative facts receive one new bounded Super extraction.
Numbers, units and literal report labels bind to original offsets and document
hashes. Existing V identifiers, values, archived pages and provider budgets remain
unchanged. New table tokens are withheld until an explicit quarter/annual/YTD
column review can establish applicability. Existing reviewed table facts are
retained. Withheld rows and reasons are recorded, not discarded as absent data.

The independent timestamped supplement includes the new facts and original
adjustment paragraphs. Reported EPS, comparative adjusted growth, gross-margin
components and one-off EPS components remain distinct. No component is silently
subtracted from reported GAAP EPS; future recurrence/removal stays uncertain.
New semantic interpretations are provisionally admitted after literal binding,
not declared independently verified. Old source diagnostics remain fallible.

The EPS task then receives one Super assumption call and one independent Mercury
outcome head. Mercury reads the expanded original exposure, without Super's
projection, program baseline or previous probabilities. Other selected-policy
compatible tasks retain their original templates and predictions and receive
offline coverage checks. `saved_source_audit.py` reconstructs numeric bindings,
exposure and probabilities from saved responses with zero network requests.

### Apple validation and three-case preservation

The frozen Apple package already contained its Q3 FY2026 issuer release, but the
previous fact table and model exposure omitted it. The generic mechanism found
this gap and appended **seven** source-bound interpretations: revenue USD 109.4
billion, revenue growth 16%, gross margin 50.1%, reported diluted EPS USD 2.02,
EPS growth 29%, a roughly two-percentage-point gross-margin refund effect and a
USD 0.11 EPS refund effect. Both refund disclosures remain in the original view.
The original nine variables are unchanged. The latest-quarter metric/exposure
gap is closed; target guidance is still not established from the saved sources.

| Apple Q4 FY2026 EPS, USD/share | Previous independent result | Expanded saved-source result |
| --- | ---: | ---: |
| 10th percentile | 1.8062 | 1.8295 |
| Median | 2.0120 | 2.0416 |
| 90th percentile | 2.2701 | 2.2137 |

Super assumes 30% future EPS growth and produces a separate 2.405 program
projection, compared with the earlier 20%/2.22. Its growth rationale cites recent
headline growth but does not discuss the disclosed refund component. The audit
flags that limited rationale without altering either model output or averaging
the predictions. Mercury's original request contains the refund disclosure.
Expanded evidence and new stochastic draws are not a controlled model A/B or
proof of improved accuracy or calibration.

Actual consumption: **three HTTP requests, two Super and one Mercury**, all
HTTP 200, **38,724 reported tokens**, zero unknown usage, USD 0 reported cost.
There were no search/fetch calls, old budget resets, forecasts or production
changes. The other three cases preserve their exact original source exposure,
templates and predictions. Their new offline diagnostics retain unknown fiscal
mapping/history gaps; those warnings do not revoke prior accepted results.

An execution-only report compatibility bug initially failed after all model
responses were saved: audited reports use `independent_quantiles`, while execution
reports use `quantiles`. Both schemas are now explicitly checked before provider
calls. The original failure is preserved; the final audit recovers the distribution
offline without another model call, reservation reset or probability change.

**141 offline tests** pass, including an unseen synthetic issuer/year, calendar
versus fiscal labels, comparisons, target guidance versus prior actuals, ambiguous
periods, withheld cumulative table columns, original-token preservation and report
schema compatibility. The 245-file frozen release is unchanged. These tests and
four archived cases validate a bounded mechanism; broader company/document
coverage and prospective forecast quality remain unvalidated.

Independent final audit:
`E:/metaculus_data/tournaments/market-pulse-26q4/reports/financial-saved-report-20261009-final.json`.

## Authorized four-question delivery, 2026-10-09

`manual_delivery.py` freezes existing independently generated distributions and
their private explanations. It performs no collection, analysis, quota reset or
scheduling. Fresh authenticated account, tournament participation, leaf rules,
scales, deadlines and own forecast history are checked before delivery. The
existing atomic forecast/private-comment transport reserves each POST and
reconciles uncertain outcomes before any further delivery attempt.

All four authorized forecasts were accepted with HTTP 201 and matching platform
readback: Apple EPS (46176), Microsoft revenue (46195), Amazon revenue (46193)
and Meta EPS (46181). Apple uses the additive saved-source distribution; the
other three retain their exact previously audited distributions. The full
201-point CDFs and private comments were delivered, not only the medians.

The initial burst received HTTP 429 on reads after Apple was accepted. Its
receipt and the original execution/progress were preserved. The resumed run
spaced requests by at least eight seconds and reused Apple's receipt. Only GET
429 responses receive bounded retries that respect `Retry-After`; POST is never
automatically retried. A short adjacent parent-read cache is copied on access
and invalidated before POST so confirmation reads remain fresh.

Four offline pacing/cache tests and 16 existing transport tests passed. Final
confirmation: **4/4 matching own forecasts and private-comment receipts**, zero
new model/search calls. This one-off authorization does not enable a continuous
Market Pulse worker or change the frozen release or production listener.

Frozen plan, original failure, recovery copy, preflight snapshots and receipts:
`E:/metaculus_data/tournaments/market-pulse-26q4/deliveries/financial-four-20261009/report.json`.

## Remaining open-question campaign, 2026-10-09

`open_campaign.py` is a local, one-off orchestration adapter. It freezes a fresh
authenticated tournament/account inventory, skips existing forecasts, shares
exact saved issuer bodies with capture-owner provenance, and runs missing issuer
acquisition with the original v105 financial collection overlay. No listener or
production release is changed. Rules, source packages and delivery plans remain
independent immutable records.

`batch_analysis.py` applies source-bound financial fact extraction, Mercury
source-compatibility checks, optional Super assumption analysis and an independent
Mercury outcome distribution. The scorer never receives Super's forecast or a
program midpoint. Analysis subprocesses receive no platform or search credentials.
Quarterly/annual/YTD columns, accounting basis, stock/share units and one-offs
remain explicit. Source compatibility is a fallible diagnostic; accepted format
is not prospective accuracy or proven calibration.

The authenticated inventory contained **19 open leaves**. Four already had
forecasts. **Eleven additional forecasts were accepted with HTTP 201**, private
comments and exact CDF readback: Tesla EPS/revenue, Apple revenue, Microsoft EPS,
Amazon EPS, Meta revenue, AMD EPS/revenue, SpaceX EPS/revenue and NVIDIA EPS. The
four original forecasts still match the latest authenticated readback and were
not reposted. Total current coverage is **15 of 19**.

Four leaves remain explicitly held, with no POST reservation or forecast:

- NVIDIA revenue (46198): the independent distribution remains inconsistent with
  the scale of supplied prior quarterly actuals. A bounded original-source unit
  reread did not resolve the disagreement; the original candidate is preserved.
- NVIDIA guidance revenue, GAAP gross margin and GAAP operating expenses
  (46248/46249/46250): title/criteria target a different fiscal guidance quarter
  from the fine print, and the stated release timing also requires clarification.

Recoveries preserved all earlier attempts and original bodies. They addressed
omitted EPS rows in bounded context, duplicated source-context metadata, invented
fact identifiers, official-page HTTP 403 responses and a transient decisions
HTTP 429. Tesla's observed official financial PDFs were rescued with basic
Extract. A further parser counterexample exposed `$28.2B` being truncated to
`28`; full decimal currency tokens and explicit B/M unit literals are now bound
without silently rewriting saved facts. The failed Tesla revenue output was
never submitted. All eleven accepted analyses pass the offline complete-token
audit after this repair.

Actual new consumption: **48 Super and 27 Mercury HTTP attempts**, including one
Mercury 429; **2,225,298 reported tokens**, one attempt with unknown usage, and
USD 0 reported cost for responses with reported billing. New acquisition used
**four Tavily basic searches, three Exa searches and five basic Extract batches**.
Reused issuer bodies incur no new searches. Tesla's original two searches plus
one supplementary search remain at the lifetime maximum of three. New AMD,
SpaceX and NVIDIA acquisition each used one basic and one Exa search. No existing
budget was reset, and every reused capture-parent hash remains unchanged.

**153 Market Pulse tests and 16 official transport tests passed.**
`campaign_audit.py` independently reconstructs counts, provider attempts, numeric
bindings, parent hashes and fresh submission readback from saved records.

Aggregate report and all per-leaf preserved stages:
`E:/metaculus_data/tournaments/market-pulse-26q4/reports/market-pulse-open-20261009.json`.
Campaign root:
`E:/metaculus_data/tournaments/market-pulse-26q4/campaigns/open-20261009/`.

### Held-question diagnostic review, 2026-10-09

`held_review.py` preserves the four held candidates and obtains fresh official
GET snapshots. All four semantic rules and API grids were unchanged. Public
comments returned HTTP 403; the browser was unavailable, so no staff
clarification is claimed. No forecast or public comment was sent.

The NVIDIA revenue question has raw-USD cutpoints of USD 10.5–11.6 billion,
while its saved official Q2 actual is USD 96.221 billion and Q3 management
guidance is USD 108 billion. A readable USD/billion representation and an
independent three-region decision were prepared without changing those official
cutpoints. Mercury returned HTTP 429 twice, including one explicitly bounded
transport retry after cooldown. Both reservations remain in the ledger. The
second decision head was not called; the proposed Mercury repair is unvalidated.

One independent Super diagnostic reused the same source exposure and returned
10/50/90 percentiles of USD 95/108/121 billion. Its limitations field violated
the requested array schema, and its unclipped region probabilities were extreme.
The diagnostic supports investigating scale/interval anchoring, but is not an
accepted replacement distribution or a calibration result. No CDF was created
from it and no arbitrary probability was inserted.

`guidance.py` provides an issuer-independent, quote-bound adapter for guidance
revenue, GAAP gross margin and GAAP operating expenses. It separates publishing
release, forward quarter and administrative annulment conditions; distinguishes
billions from raw USD and percentage points from fractions; and models primary
guidance midpoint/single-value rounding. Exact halfway rounding ties require
review. The adapter never clears a delivery hold itself.

A finer reading of the three guidance leaves identifies the Q3-in-Q2 clause as
an annulment prerequisite, rather than automatically treating it as a competing
definition of the future Q4-in-Q3 numeric target. The prior official Outlook
contains all three guidance metrics, so that prerequisite is not triggered under
its literal reading. The stale August expected publication date still needs
official clarification; previous Q3 figures must never be submitted as future
Q4 guidance. This interpretation is disclosed, not presented as staff approval.

**162 offline Market Pulse tests passed**, including 9 new unit, target, fiscal
rollover, rounding and malformed-output counterexamples. The aggregate audit
preserves the previous 75 model attempts and every original capture-parent hash.
New diagnostics used two failed Mercury attempts and one Super attempt, no new
searches, no new forecasts and no production changes. Diagnostic report:
`E:/metaculus_data/tournaments/market-pulse-26q4/reports/market-pulse-held-four-20261009.json`.
