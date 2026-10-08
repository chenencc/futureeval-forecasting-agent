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
