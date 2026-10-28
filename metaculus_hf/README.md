# Metaculus Agent

Isolated Metaculus runner inside `chenencc/futureeval-forecasting-agent/metaculus_hf/`. Code is public; evidence, forecasts,
submission receipts and recovery state are stored in a **private** Hugging
Face dataset. No GitHub Actions result ZIP or artifact is used.

Current implementation status: source/recovery tests and deployment validation
are in progress. Production activation is fenced until an explicit, verified
server handoff. Installing or running tests never enables submission.

## Pipeline

Discover eligible tournament questions -> shared current evidence collection
-> body review and immutable snapshot -> private HF context -> existing
Mammoth typed-decision analysis with bounded original-evidence rereading -> private
HF analysis -> current-rule validation -> forecast/comment submission ->
authenticated readback -> durable receipt -> guarded local cleanup.

The collector is the hash-pinned acquisition implementation extracted from
the current prediction-market pipeline, including independent queries, 5xx
retry, saved-candidate recovery, supplements and body-bound citations.
Metaculus eligibility remains separate from market tags and opening rules.
The shared source lock prevents untracked divergence; update it deliberately
and run the shared regression tests when updating either consuming project.

New Metaculus captures additionally require one durable, question-specific
Nemotron 3.5 research query after the existing base Exa and Tavily searches.
The identical generated query is searched once with each provider before page
reads, even when a rule URL is already available. The original two Tavily gap
search slots remain separate: at most two Exa plus four Tavily logical searches
per ordinary new capture. Physical credential failover and bounded model 5xx
retry can add attempts; existing provider/daily budgets and deadlines still apply.

The query, rationale, input identity, model result and channel observations are
saved locally and join the existing one-final-HF-commit policy. Restoring the
capture reuses the saved query/results rather than issuing new calls. Ambiguous
reserved calls are recorded and not blindly repeated. If query generation is
unavailable or invalid, both search slots use an explicitly labeled deterministic
degraded query; this never claims a successful model generation. This new search
stage does not itself fix unrelated-link prioritization or prove body quality.

The shared `topic-bound-independent-candidates-v1` policy separates Metaculus
FAQ/help and prediction-question pages from independent event information.
Their exact URLs and meaning remain in the original rules, but these pages are
not fetched as evidence or expanded into unrelated primary-source leads. Ordinary
subject-specific FAQs on other sites remain eligible. Background links explicitly
present in the question are registered with their provenance; the existing coarse
`rule_url` discovery enum is qualified by `locator.question_link_role=background`
and `question_context=description`, without claiming official or factual approval.
Observed primary links must match the topic/identifier or the targeted parent
article, not merely share a domain and contain words such as report or data.
Repeated searches reuse results and try eligible unread candidates before stopping,
within the same page/search/deadline caps. Literal body admission remains separate.

## Data

Analysis now reuses the collector's saved Nemotron review bindings to prioritize
exact original passages, with no additional selection-model call. The derived
`review-bound-original-packing/1` reading library preserves whole Markdown table
rows with their literal headers, captions, recognized notes and visible document
dates. Identical body hashes share one model-input body while all source aliases,
observation metadata and original context remain archived. Different bodies are
never removed as near duplicates. Bound citations select locations; their review
prose is not treated as evidence or rewritten into a summary.

Budget checks apply to the full Mammoth envelope. A readable body that cannot fit
produces an explicit input-budget error rather than being labeled missing evidence.
Unselected rows and passages remain in the saved context and packing audits;
selection is not a completeness or truth claim. Native numeric grids stay in the
immutable context and forecast conversion, while the compact model view omits
their redundant 201-point expansion, including on the prior-only path.

Private dataset: `chchen13/forecastagent-data`.

```
metaculus/start_date=YYYY-MM-DD/question_id=<leaf-id>/run_id=<id>/
  context.parquet
  analysis.parquet
  manifest.json
  submission.json
metaculus/_control/checkpoint.json
metaculus/_control/objects/<sha256>
```

`start_date` is first actual acquisition start in Asia/Shanghai; all precise
timestamps are UTC. Parent post ID is separate from leaf question ID. Context
uses the same Zstd/checksummed Parquet envelope and `intelligence-package/2`
payload as market collection (version 1 remains readable). Original bodies
and final review overlays remain distinct. There are no fictitious book or
trade files. Analysis retains actual outputs, model/release/input identities,
citations and failure records, bound to its exact frozen evidence.

Every upload is downloaded at its exact commit and checked before advancing
state or deleting local payloads. Interrupted analysis restores saved bodies;
it does not start a new search. Reservations are persisted before HTTP.
Ambiguous platform submissions are reconciled and never blindly reposted.

## Local checks

Python 3.12, from the repository root first run `cd metaculus_hf`:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m metaculus_agent.doctor
python -m unittest discover -s tests -v
python -m playwright install --with-deps chromium
```

Credentials are supplied only via GitHub repository secrets or local hidden
environment configuration: `HF_TOKEN`, `METACULUS_TOKEN`,
`OPENROUTER_API_KEY`, `OPENROUTER_API_KEY2`, `TAVILY_API_KEY`,
`TAVILY_API_KEY2`, `EXA_API_KEY`, `EXA_API_KEY2`. Do not put values in Git,
workflow inputs, command arguments, issue comments, logs or artifacts.

The scheduled workflow is inert until `METACULUS_SCHEDULE_ENABLED=true`.
`METACULUS_SUBMIT_ENABLED=true` also requires an HF checkpoint with verified
`migration_complete=true` and matching writer identity. Missing state blocks
execution; it never creates an empty budget. Dependency caches contain no
runtime state. Scheduling is best effort, not an exact five-minute SLA.

## Cutover

1. Pass clean Windows/Linux checks and exact-HF readback/restart tests.
2. Run bounded collection/analysis acceptance with submission disabled.
3. Drain the old server writer; verify it exited. Import its terminal task,
   dedup, rule and reservation state. Do not reopen expired/accepted tasks.
4. Fence writer ownership and enable the new workflow. Never run two submitters.
5. Verify actual authenticated submission readback and subsequent state restore.

Raw runtime files and operational state are never committed here. Public CI
uses synthetic fixtures and no provider credentials. See `sources.lock.json`
and `NOTICE.md` for provenance. This repository does not require a private
GitHub checkout or a running Vultr server.

Repository credentials are reused with explicit workflow aliases: OPENROUTER/OPENROUTER2, TAVILY_KEY3/TAVILY_KEY4, EXA_API/EXA_API2 and METACULUS_TOKEN. Only HF_TOKEN is an additional repository secret when absent. Existing workflows and their enable flags are unchanged.

## Three historical arrival debug

`metaculus_hf_debug.yml` runs exactly three explicit historical questions in
sequential dependent jobs. Each job starts on a new GitHub-hosted machine.
Any failed case skips all later cases. A unique 32-hex session scopes every
checkpoint, content-addressed object, dated product, analysis and audit under
`metaculus/_debug/<session>/`; production data/control are never written.

The harness reuses `Runner.ingest`, shared collection, the official analyzer,
exact-revision cloud verification and native delivery serialization. Only
eligibility and prior-own-forecast visibility are simulated at the debug
client boundary. Original question rules and model-input clocks are retained.
Evidence is collected now; this is not a historical-as-of forecast test.

Actual platform POSTs are prohibited. Delivery/readback and duplicate delivery
are exercised using an in-memory API sink with a durable intent; that does not
prove real platform acceptance. Each case verifies product/manifest bindings,
fresh-root state and budget recovery, duplicate discovery without recollection,
and local retirement. Failed inputs remain in the debug checkpoint.

The debug ledger starts from the known production consumption floor and carries
its own increments between all three machines. Credentials still share actual
provider allowances. The debug workflow uses the production concurrency group
to avoid overlapping paid work; ordinary cron dispatches can wait behind it.
It does not reset or mutate the production budget or submit the old questions.

### Analysis route recovery

Collection uses `nvidia/nemotron-3.5-super-vl:free`; decision analysis uses
`baseten/mammoth-1:free` through the typed Decisions endpoint, not chat or
generated JSON. Model metadata must confirm a decisions output modality,
available endpoints, observed context limits and zero input/output prices.
No other decision model or paid fallback is selected implicitly.

The adapter retains the pinned original-evidence selector and conditional
rereading criteria. It validates exact question/option identity, finite
probabilities, distribution sums and ordinal expectations before converting to
the native binary/categorical/CDF payload. Diagnostic sufficiency is never
multiplied into event probability. The 16K observed context uses conservative
UTF8 envelope budgets, retaining complete question rules and exact source spans;
this is not an exact tokenizer count. At most two logical decision stages run.
All physical HTTP attempts, including explicit credential-quota failover, are
still durably counted. The analysis records the actual Mammoth identity and
protocol, not a Mercury label. A failed second read retains a valid first
decision with an explicit reread gap; failed first decisions stop the task.

Authentication/access failures, account quota, route absence, and temporary
provider outages have distinct codes and persisted retry times. They do not
consume the invalid-analysis attempt cap or mark the question invalid. Physical
HTTP reservations and the shared lifetime run budget still accumulate. New
attempt folders preserve all prior failures and frozen evidence inputs.

For the failed first debug case, dispatch the same session with
`resume_first_analysis=true`. It restores and hashes the existing context,
forbids search/fetch reservations and skips collection. Case 2 and 3 remain
dependent on a durable successful first-case receipt. Metaculus POSTs remain
prohibited; only the in-memory delivery simulation runs.

### One HF commit per question

The production runner and three-question debug runner checkpoint to the
GitHub-hosted machine during collection, analysis and submission. Local files
are flushed before external-call reservations or submission intents proceed;
these local checkpoints are explicitly not HF receipts. No intermediate stage
creates an HF commit, and an empty discovery poll creates none.

At a question boundary, its context, analysis, submission receipt (when present),
current raw runtime journal, provider budget and updated queue state are sent
in one atomic HF commit. A normal failed attempt archives its partial state in
one failure batch. Superseded intermediate object versions are not uploaded.
The task's dated `question-runtime.json` references its archived journal hashes;
accepted task payloads are omitted from the active restore index. Exact-revision
cloud readback must succeed before local task files are removed. A failed flush
retains the local batch; retrying delivery does not repeat provider work or the
platform POST. A lost commit acknowledgement is reconciled against target bytes
before a retry, so it cannot create a duplicate successful commit.

The debug HF audit is `debug_case_prepared` inside that single commit. The job
reports `debug_case_passed` only after exact-revision cold readback and a fresh
state/budget restore; it does not create another commit merely to mark that
verification. A later debug case independently verifies the previous products
before accepting that prepared boundary.

Normal exceptions retain their partial question in the final batch. If the
GitHub machine is forcibly lost before that batch, its local-only phase is not
recoverable from HF, including unflushed budget increments. Platform own-forecast
readback still precedes another submission; this is not a promise of remote
mid-question recovery or exactly-once provider usage after abrupt machine loss.

### Complete search responses

New collections add `raw_search_responses` to `context.parquet`, alongside the
unchanged normalized search records in `intelligence.searches`. Each Exa/Tavily
physical search attempt retains the entire HTTP entity consumed by the existing
transport: `body_base64`, SHA-256, byte count, status, safe response headers,
credential-free query/parameters, actual dispatch/body-completion clocks and the
original capture run ID. Unknown provider JSON fields, formatting, error bodies
and invalid JSON are retained. Bytes are the requests-decoded HTTP entity, not a
compressed network packet. Decode `body_base64` to read the original JSON.

This is an observer of existing calls, not another search request. It does not
change providers, quotas, failover, normalized candidates, source admission,
snapshot identity, original question metadata or the analysis input selection.
Bodies are excluded from model input and published in the same final question
HF batch, with local per-attempt journals available on ordinary failures.

Existing response caps and deadlines still apply. An interrupted/oversized body
is explicitly incomplete; a connection or pre-dispatch failure cannot fabricate
a response. Credential echoes are redacted and marked `credential_redacted`,
with `byte_exact=false`. Authorization/request headers and cookies are never
archived. Old saved snapshots have no new raw responses retroactively; they are
left unchanged and remain readable.

### One job, three historical questions

`metaculus_hf_debug_batch.yml` accepts one fresh 32-hex session and runs three
questions sequentially in one hosted job. As requested for debug, it excludes
the previous three debug IDs but permits other old questions even if previously
forecasted. Existing own forecasts are hidden only by the debug simulation and
no real POST is permitted. Optional internal strict selection can exclude all
production/debug/acceptance task IDs and existing own forecasts; it is not the
default and does not trigger a broad history scan. Only posts at least 24 hours
old qualify. Each case retains its own final HF commit;
the first failure stops subsequent acquisition. Production cron inputs remain
unchanged and all debug Metaculus POSTs remain prohibited.

The terminal log includes per-question context/analysis sizes, direct metadata,
exclusive runtime-object bytes and a shared session remainder without double
counting. These are current-revision file sizes from HF metadata, not Git history,
compressed physical storage or a billing estimate. Size inspection is read-only
and does not add an HF commit. Results/evidence remain in private HF debug.

### Forecast fallback policy

Eligible questions with valid identity and platform metadata use three explicit
levels: original-evidence Mammoth analysis; question/rules-only Mammoth prior if
no usable independent body exists; then a deterministic operational distribution
if collection/model/provider/format failures leave no valid model output. Binary
defaults are 0.5, categorical defaults have equal option mass, and numeric/date/
discrete defaults are uniform in authoritative platform-grid coordinates with
2% mass per open tail. All pass the same native payload validators.

`forecast_origin`, `fallback.level`, trigger code, model-generated/evidence-based
flags and comment text identify the downgrade. A collection failure retains its
partial searches/bodies plus `collection_failure.capture_succeeded=false`; it is
never relabeled a successful collection. Provider errors/budgets remain recorded,
and authentication/configuration faults retain operator-action flags. No extra
retrieval or implicit alternate model is used for fallback. Question hashes,
identity/rules/scale mismatches, unsafe paths, corrupted data and persistence
failures still block rather than submit against unverified input. Platform
closure/deadlines/permissions and unreadable submission receipts are unchanged;
generating a score is not a guarantee that the platform accepted it.

`resume_stopped=true` in the debug batch restores the failed saved analysis and
then only later cases. A zero-provider-call injected outage probe validates the
operational default on that same saved input before normal resumed analysis.
The branch-only validation allowance is restricted to the named fallback branch;
production cron continues to use main until the experiment passes and is merged.

### Development: body quality and evidence budget

`metaculus-mammoth-original-evidence/3` reserves the existing byte limits for
evidence by compacting diagnostic instructions, omitting redundant model-only
grid/routing metadata and encoding the measured UTF-8 JSON envelope exactly as
sent. Native forecast grids, option IDs, interval boundaries, original question
rules/fine print and saved provenance remain intact. Span URL/hash references
are carried once in the wire source map and fully in saved audit files.

`review-bound-original-packing/2` prioritizes short resolution-designated bodies,
literal recent season/year rows and complete repeated winner records. Recognized
table labels, event identities and publication dates accompany selected spans.
Navigation-heavy or zero-overlap filler is deprioritized. This is deterministic
selection of original bytes, not a generated summary or a guarantee of exhaustive
semantic coverage. All omitted bodies remain in the immutable context; selected
offsets/hashes and available versus delivered row counts are audited.

ClinicalTrials observed search links with exactly the supported intervention and
`status:rec` filter use the official v2 studies endpoint. Unknown filters are not
silently dropped. The adapter preserves the API response UTF-8 text, exact query,
response SHA, request/receive clocks and the original page URL. Its first page is
limited to 20 projected records (ID/title/status/intervention/update date); the
official total count and record pagination completeness are separate. It checks
recruiting status, study identity and count consistency. Current counts are not
future resolution results or an as-of historical reconstruction.

An unsupported affirmative season/value claim in a review cannot approve that
claim; the original series remains available for independent reading. This
narrow guard does not certify arbitrary review prose. If final review leaves no
usable body, closing recovery may read at most two more saved candidates within
the original page/deadline/provider budgets, even when an earlier planner
handoff was used. It does not issue a new search or overwrite the old snapshot.


### Development: research-query marginal value

`goal-aligned-research-query-v1` retains one mandatory Nemotron3.5 query and the
same query on Exa and Tavily. The prompt binds the research goal to the exact
metric, population and historical period. A narrow quarterly guard rejects a
historical-baseline plan that searches only for the future target quarter;
unrecognized semantics are not claimed as validated. Degraded fallback is
explicitly marked. Platform/forecast-mirror candidates are excluded from query
inputs. Once-only request slots, physical-attempt accounting and budgets remain.

Before final review, opt-in capture can read at most one eligible additional URL
observed in the research results and absent from base results and attempted/rule
URLs, within the existing page and time budgets. It rejects concatenated-protocol
paths and prefers a different document to a known path's query variant. Unknown
query parameters are not silently removed. Source ranking is a retrieval cue,
not evidence approval. A malformed model review date keeps the binding pending;
independent original-body reading strips the unsupported review. Valid future
dates, FAQ/navigation pages and explicit hard mismatches remain excluded.

`review-bound-original-packing/3` defers weak quarterly background while retaining
review-bound drivers and a readable-body fallback. Literal metric paragraphs in
quarterly documents and complete delivery table cells carry their saved date,
period, unit/header context. No values are synthesized or inferred. Selected
bytes/offsets are auditable; weak-only fallback and omissions are explicit.
The heuristics are narrow and do not certify semantic completeness or forecast
accuracy. Evaluate queries by distinct relevant bodies actually delivered to
analysis, not URL novelty alone. Old-question current-data tests are not historical
as-of backtests. Development evidence and raw failures remain outside this repo.


### Development: Mammoth decision input assessment

Default `METACULUS_DECISION_INPUT_PROFILE=stable-v1` preserves the prior request
format and byte budgets. Eight saved-question replays reproduced all twelve
baseline request byte strings and native forecast payloads exactly. No production
workflow, upload path or submission setting is changed by this development work.

Explicit `METACULUS_DECISION_INPUT_PROFILE=role-aware-v2` enables experimental
source-role/review-status cues, compact audit metadata on the model wire, an
independent question-background reading policy, and a forecast-usefulness rubric.
Original questions, exact rules, units, option IDs, native numeric grids, source
bodies, hashes and offsets remain in saved artifacts. Historical baselines are
not resolving values; a role label is not evidence approval. Future uncertainty
is not the same as unusable evidence. Diagnostic answers are parallel judgments,
not a sequential reasoning chain or observed facts. The changed sufficiency
rubric makes old/new self-scores unsuitable as a quality comparison.

The experiment can use `METACULUS_DECISION_TOKENIZER_PATH` pointing to a locally
provisioned, hash-verified `baselabs/mammoth-1` tokenizer. Optional dependency is
listed in `requirements-token-counting.txt`; the forecasting path never downloads
or executes model code. Exact tokenizer revision/hash are in `decision_tokens.py`.
Provider context and non-null prompt caps are both checked. Initial/re-read caps
are 6,000/9,000 local JSON tokens with 4,096 tokens reserved for hosted templates
and question branches, plus 24,000/36,000 byte bounds. Actual hosted input usage
must still be audited: local JSON token counts are not provider billing counts.
Without a tokenizer, conservative original byte caps remain. Stable mode never
opts into expanded budgets merely because a tokenizer is present.

For recognizable literal Winner records, the experimental packer calculates
exact distinct labels only over delivered, hash-bound source slices. It lists
labels, record references and selected-versus-available coverage per source.
It performs no person-identity normalization, cross-source reconciliation,
full-season inference, truth verification or forced probability clamp. Unknown
layouts receive no calculation. Calculations participate in the same input
budget as evidence and are recomputed as selected spans change.

The local assessment used eight identical saved bundles in paired runs, ten
capability/sensitivity probes and one integrated literal-count run. It found
material probability sensitivity to metadata formatting, a missed raw-record
count corrected by a deterministic reading aid, and successful financial amount
extraction from both full and focused original text. These are operational and
bounded extraction checks, not forecast-calibration evidence. Old-question bodies
were not reconstructed as of historical forecast cutoffs. Experimental expansion
therefore stays opt-in; do not promote it based on higher self-scores, more text,
or agreement with an expected forecast. A separate time-clean held-out evaluation
and format/irrelevant-text sensitivity gates are required before promotion.


### Development: canonical input and conservative relevance admission

`METACULUS_DECISION_INPUT_PROFILE=canonical-v3` is a separate opt-in profile;
`stable-v1` remains the default and `role-aware-v2` is unchanged. It does not
silently enable expanded budgets, alter rules/options/native grids, or deploy a
workflow. The analysis adapter builds a local source/span index from hash-checked
saved bodies. The private index is never sent to the model. Its digest binds the
analysis identity; normalization receipts retain original-to-canonical IDs,
offsets, hashes and excluded candidates.

The renderer fixes object-key order, source order and source/excerpt IDs. Within
each source it restores original character order so table headers/rows are not
alphabetically scrambled. Option lists and other semantic arrays remain in their
original order. Missing audit-only offsets are recoverable only when the literal
text identifies one indexed span. Unknown source candidates are excluded with a
local receipt. Changed text/hash/rules, dangling IDs or ambiguous offset recovery
stop the call. No readable corpus is silently replaced with a prior-only forecast.
Raw snapshots are never edited by this model-view operation.

This provenance boundary is NOT a semantic irrelevant-text detector. A separate,
optional `decision_relevance.review_documents` helper uses the existing Nemotron
3.5 route on saved text only. An exclusion needs a full-document review, explicit
irrelevance, entity and metric mismatches, no causal relevance, and one exact
40-600-character quote bound to the saved body. Partial, ambiguous, failed or
unbound reviews retain the original. A question-designated source and existing
direct/baseline/driver bindings are protected from conflicting new exclusions.
Review outputs never become factual evidence or forecast probabilities.

The host may attach validated receipts under `analysis_selection_reviews[url]`
in an isolated analysis bundle; `canonical-v3` rechecks question/body binding before
using them. It does not automatically call this reviewer during every forecast.
Persist and reuse exact question/body/policy-bound receipts in the development
harness; no new collection, snapshot replacement or remote checkpoint is needed.
A body becoming stale, a changed question or a changed policy needs a new review.
Model relevance judgments remain fallible despite literal quote verification.

The bounded local regression restored identical requests for all 64 prior
format/noise variants using the frozen capture boundary. An additional test
explicitly admitted the same unreviewed synthetic noise: some probabilities still
moved, exposing the distinction between format and semantic robustness. After
clarifying the review taxonomy, all eight noise cases were excluded while eight
relevant selections were retained; a separate synthetic counterevidence/driver
control was retained and a different off-topic fixture was excluded. All eight
filtered requests matched their canonical baselines byte-for-byte; two fresh
Decisions calls also matched their baseline answers exactly. This is a narrow
input-stability result, not a population relevance guarantee or improved forecast
accuracy. Changing from stable to canonical still changes probabilities, so
promotion requires a separate held-out, time-clean forecasting evaluation.
