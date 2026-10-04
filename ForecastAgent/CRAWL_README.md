# Crawl and Evidence Acquisition

This document describes the acquisition code maintained in `main`. It covers
raw collection, saved-body inspection, independent supplementation and the
capture acceptance experiments. The official competition worker currently checks
out the immutable `v1.0.1` release; merging code into `main` does not update that
release or change the official worker's checked-out code.

## Pipeline

The `dev_formal` material-review experiment adds [saved table reading and
record-level review contracts](supplement/MATERIAL_CONTRACTS.md). It does not
change the immutable competition release.

```text
Question and resolution criteria
  -> Acquisition agent and on-demand skills
  -> Bounded Tavily / Exa discovery and free source tools
  -> Original response bytes, parsed documents and attempt ledger
  -> Capture-state inspection and repair inventory
  -> Independent supplement using remaining supplement allowances
     - verified saved-byte reparse
     - observed detail-page discovery / public alternatives
     - bounded HTTP fetch or explicit JavaScript rendering
  -> Supplement captures, provenance and unresolved gaps
  -> Separate downstream analysis
```

Collection preserves sources. Readable text does not establish relevance,
truth, outcome, or equivalence to a question's resolution conditions. Search
snippets are discovery leads, not substitutes for source bodies. Probability
estimation and forecast submission belong to separate stages.

## Code map

| Responsibility | Module |
| --- | --- |
| Acquisition agent, source tools and durable task state | `runtime/retrieval.py` |
| HTML, PDF, feeds, JSON and CSV snapshot normalization | `readers/loader.py` |
| Observable transport/body/integrity state | `readers/capture_status.py` |
| Offline failure inventory | `runtime/gap_repair.py` |
| Independent repair executor and evidence overlay | `supplement/stage.py` |
| Observed detail URLs and public alternatives | `supplement/discovery.py` |
| PDF text/table reader and text fallback | `readers/pdf.py` |
| Bounded saved-byte PDF failure diagnosis | `readers/pdf_diagnostics.py` |
| Public browser capture | `readers/browser.py` |
| Artifact import and local indexing | `local_sync.py` |
| Frozen paired acquisition experiments | `experiments/capture_acceptance.py` |

Additional tools include government-data adapters, market snapshots and local
long-document reading. See [acquisition channels](docs/ACQUISITION_CHANNELS.md),
[raw recall](docs/RAW_RECALL.md) and [runtime contracts](docs/RUNTIME.md).

## Budgets and recovery

| Resource | Limit / behavior |
| --- | --- |
| Tavily basic search | At most 3 initial calls per task; persisted ledger is authoritative |
| Exa discovery | Up to 1 initial search under the configured task policy |
| Initial free HTTP/source fetch attempts | Shared maximum of 8 |
| Tavily basic Extract rescue | Up to 1 batch under the existing rescue policy |
| Supplement HTTP fetch | Default maximum of 2 reservations per task |
| Supplement browser render | Default maximum of 2 reservations per task |
| Allowed browser requests per render | Maximum of 25, including page dependencies |
| Supplement local reparse | Maximum of 8 reservations per task |
| PDF full reader | Maximum of 100 pages; existing stream limits apply |
| PDF failure diagnosis | Sample at most 5 pages; up to 8 saved diagnoses per supplement task |
| Supplement download / saved-byte repair | Maximum of 8 MB |

The acquisition and supplement ledgers are separate. Discovery fetches share the
existing supplement HTTP allowance; they do not create additional allowances.
Browser rendering is not a single HTTP request, so inspect the request audit as
well as the render count. Failures and interrupted reservations consume their
allowance. Resuming the same output directory retains those reservations and
checks the frozen input and budget identity. Do not delete output to restart an
existing experiment. Completed original tasks are not reopened by supplementation.
Historical-strict mode blocks new network repair. Retry deadlines remain enforced.

## Detail discovery and public alternatives

Discovery reads only existing question links, saved Tavily/Exa hits, source leads
and links in captured pages. Planning performs no search, DNS lookup or download,
and invents no paths. The HTTP executor checks public addresses and redirects.

Candidates are labeled as exact rule sources, same-host details or public
alternatives. Exact rule URLs rank first; other candidates require lexical topic
overlap. Platform FAQ/help/account links are excluded. Up to three candidates
per eligible gap are retained with their origin, matched terms and parent gap.
Already-readable exact URLs are reused rather than fetched again.

An official host or rule link can still lead to a homepage or general policy
page. Candidate ranking is experimental. An alternate capture does not silently
close the original inaccessible-URL gap, and equivalence is not verified.

## PDF empty-result handling

Original bytes and hashes survive parsing failures. Diagnoses distinguish a
non-PDF response, encryption, malformed/truncated data, page-limit exhaustion,
page extraction errors and no text layer in the sampled prefix. Unsampled pages
remain unknown. Diagnosis does not run OCR or an external model.

When layout extraction produces no text, the reader tries ordinary pypdf text
extraction before optional local OCR. Each page records its extraction method.
Recovered text may have spacing or table limitations; original bytes remain
available for later inspection. The optional OCR behavior remains separately
configured and bounded; it is not enabled by this change.

## Stored data

Original tasks retain `bundle.json`: question rules, searches, response bytes,
parsed documents, hashes, request attempts, excerpts and collection state.
Independent supplement output contains:

```text
manifest.json                  # Frozen parent identity and allowances
gap-inventory.json             # Original failures and body-quality gaps
summary.json                   # Per-task technical counts
tasks/<id>/supplement.json     # Reservations, captures, discovery and handoff
  captures/<hash>.json         # Bytes, text, documents, source and request audit
```

Supplement records link to the original bundle hash. Captures carry source
provenance and their JSON hashes. Diagnostics bind to the original response hash.
Unresolved original-source gaps and unread discovered candidates are reported
separately. These are technical acquisition gaps, not negative event findings.

GitHub Actions uploads artifacts; `ForecastAgent.local_sync` imports them into
`E:/metaculus_data` and maintains the local index. Structured quality reports are
stored in `E:/metaculus_data/reports`.

## Usage

Run commands from the repository root. Configure provider credentials through
environment variables or GitHub Secrets, never committed files.

```sh
python -m pip install -r ForecastAgent/requirements-supplement.txt
python -m playwright install --with-deps chromium

# Original agent acquisition; requires the configured model/search credentials.
python -m ForecastAgent run --input question.json --task-dir snapshots/question-123
python -m ForecastAgent acceptance --task-dir snapshots/question-123

# Offline inventory; parent.zip contains campaign.json and tasks/<id>/bundle.json.
python -m ForecastAgent.runtime.gap_repair --archive parent.zip --output inventory.json

# Independent supplement. Use the same output directory when resuming.
# Omit --network for saved-byte-only operation; select at most five unique IDs.
python -m ForecastAgent.supplement.stage --archive parent.zip --output supplement-output --ids 123,456 --network

# Import a preserved artifact into the local data store.
python -m ForecastAgent.local_sync import --root E:/metaculus_data --archive artifact.zip --run-id RUN_ID
```

The supplement requires no model or search credentials. Its default command uses
the limits above. Experiments may explicitly freeze smaller equal limits in
both arms. A fresh experimental allowance requires explicit authorization and
must preserve earlier ledgers.

## Validation and current findings

The [paired acceptance manifest](experiments/capture_acceptance_20.json) and
[structured results](experiments/capture_acceptance_20_result.json) compare
baseline `673c464` against `1260b4c`. Runs
[37090996456](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37090996456)
and [37091294223](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37091294223)
cover twenty active-gap questions plus three already-rescued no-repeat controls.
Inputs and allowances match across arms; arm order alternates per question.

| Twenty-question primary cohort | Baseline | Current |
| --- | ---: | ---: |
| Readable additions | 3 | 18 |
| Direct or qualified partial-context additions | 2 | 9 |
| Questions with useful additions | 2 | 7 |
| HTTP request records, including redirects | 4 | 45 |
| Allowed browser requests | 43 | 43 |
| Novel decisive target-event evidence | 0 | 0 |
| Remaining original unreadable-URL gaps | 47 | 49 |

The PDF fallback recovered 2,238 characters from task 40967's saved two-page PDF
without network calls. Discovery increased related-source recall, but did not
add previously missing decisive event information in this sample. Nine of the
eighteen readable additions were background-only or rejected for event, metric
or period mismatch. The three direct-event additions duplicated existing packet
evidence. Six questions improved in useful-addition count, one regressed and
thirteen tied. Manual review was not blinded, and the selected gap-heavy cohort
is not representative of all tournament questions.

Prioritizing alternatives before original HTTP repair crowded out two original
URL recoveries, including one useful partial-context page. Follow-up work should
reserve original-repair capacity, improve exact entity/metric/date/detail-page
ranking, and avoid additional context already covered by the existing packet.
These results do not establish improved forecasting accuracy.

For implementation and audit details, see [capture status development notes](docs/DEV_CRAWL_STATUS.md).

## Focused regression checks

```sh
python -m unittest ForecastAgent.tests.test_discovery ForecastAgent.tests.test_capture_status ForecastAgent.tests.test_supplement ForecastAgent.tests.test_gap_repair ForecastAgent.tests.test_readers ForecastAgent.tests.test_collection_handoff ForecastAgent.tests.test_raw_recall ForecastAgent.tests.test_retrieval_v3
```

Experimental live workflows are manual/branch-scoped, not scheduled competition
workers. Re-running a live acceptance experiment requires restoring its previous
state or explicitly authorizing a separately recorded experiment. Never treat a
fresh runner filesystem as permission to reset an existing ledger.
