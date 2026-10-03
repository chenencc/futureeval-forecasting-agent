# Capture states and bounded repair routing

This development branch preserves the original collection agent and its budgets.
`readers/capture_status.py` reports transport status, observed body state, original
byte integrity, extraction caveats and retry deadlines. Readability never establishes
relevance, authority, truth or completeness.

The classifier recomputes body quality rather than trusting a stale readable flag.
Real short notices remain allowed. Navigation and login shells retain their
original text in the audit but are not counted as readable evidence. Original raw
bytes are checked before local reparsing. Hash failures are reported rather than
silently replaced by another download.

Routes use the existing supplement allowances: eight local reparses, two HTTP
attempts and two browser renders per question. Browser rendering is proposed only
for an explicit JavaScript shell. Login restrictions and navigation pages require
another discovered public source or detail page; this change does not invent URLs.
Retry-After deadlines are observed. A previous failed or reserved URL keeps its
reservation and is not automatically executed again. No method fallback loop or
new quota is introduced.

Successful HTTP responses with unusable bodies enter the repair inventory as
quality gaps, separate from physical failed requests. All original acquisition
search, model and fetch records remain unchanged. The original collector exposes
the same status metadata to its tool feedback and refuses duplicate failed or
reserved free fetches for the same canonical URL. Existing saved-page reuse remains
available. The independent supplement still performs its own bounded requests.

The first validation uses saved captures and mocked transports. The GitHub Actions
development test workflow needs no inference or search credentials. Network repair
quality requires a later authorized pilot; unit tests do not establish live site
availability. Production remains pinned to release 1.0.1.


## Observed detail discovery and public alternatives

The supplement builds a deterministic `discovery` manifest from question links,
saved Tavily/Exa hits, source leads, and parsed page links. It performs no DNS,
search, model, sitemap download, or invented-path requests during planning.
Exact resolution-source URLs rank first; other candidates require lexical topic
overlap. Same-host details and cross-host public alternatives are explicitly
separate. Host matching is not proof of official ownership or event equivalence.
Up to three candidates per gap are retained, with origin, matched terms and
parent gap URL. Readable existing pages are reused instead of fetched again.

Discovered fetches share the existing per-task HTTP allowance with document
repair. The original URL reservation guard and historical-strict prohibition
still apply. Reservations precede requests and survive resume. Alternative
captures do not silently clear the original blocked-source gap. Execution checks
DNS/public-address safety through the existing HTTP and redirect guards.

## PDF diagnostics

Failed PDF bytes receive bounded diagnostics: non-PDF response, encryption,
malformed/truncated data, page count limit, extraction failure, or no text layer
in the sampled prefix. Diagnostics inspect at most five pages, within existing
byte/stream limits. Unsampled pages remain explicitly unknown. No OCR or model
is invoked. Up to eight saved PDF diagnostics are retained per supplement task,
bound to the source-byte hash. Failed reader exceptions retain the original bytes.

For supported PDFs whose layout extraction is empty, ordinary pypdf text
extraction runs before optional OCR. The extraction method is recorded per page;
raw PDF bytes remain authoritative for tables, spacing and number interpretation.
The 100-page full-reader limit remains unchanged.

Platform FAQ/help/account URLs are excluded from event-source ranking even when
linked as definitions in resolution criteria. Reports separately count original
source gaps and unacquired discovered candidates; the legacy total gap field
includes both and must not be interpreted as physical request failures.

Validation on the five preserved pilot tasks recovered 2,238 characters from
the two-page saved government PDF with zero network calls. The resumed Linux
pilot used four previously unspent HTTP reservations (five requests including
one redirect), recovered a government policy HTML page, and retained all source
restrictions. That policy page is contextual evidence, not proof of the question's
outcome. The court PDF exceeds the 100-page full-reader limit; no full read was
attempted and the sampled diagnostic does not imply all pages are readable.


## Paired acceptance: twenty active-gap questions

Runs 37090996456 and 37091294223 freeze baseline `673c464` and current `1260b4c`.
Three already-rescued PDF cases remain as no-repeat controls; twenty additional
active-gap cases form the primary cohort. Identical original bundle hashes and
per-arm allowances were checked: two HTTP reservations, one browser render,
and eight local reparses per task. Arm order alternates per question. No search,
model or forecast submission occurs. All five experiment artifacts were imported
into the local data store (406 files). The English per-question audit is in
`E:/metaculus_data/reports/dev-crawl-paired-20-quality-37090996456.json`.

| Primary-cohort metric | Baseline | Current |
| --- | ---: | ---: |
| Readable additions | 3 | 18 |
| Direct or qualified partial-context additions | 2 | 9 |
| Questions with useful additions | 2 | 7 |
| HTTP request records, including redirects | 4 | 45 |
| Allowed browser requests | 43 | 43 |
| Novel decisive target-event evidence | 0 | 0 |
| Remaining original unreadable URL gaps | 47 | 49 |

Manual review was not blinded. Six questions improved in useful-addition count,
one regressed and thirteen tied. Of eighteen current readable pages, three
contain direct target-event evidence already present in the original packets,
six offer qualified partial context, five are background only and four are
rejected for wrong event/measure/period. These counts measure additional readable
sources, not forecast accuracy or verified factual truth.

The saved-byte PDF recovery is confirmed. General discovery is experimental:
readability and related-source recall increase, but no new decisive information
was demonstrated. Prioritizing alternatives before original HTTP repair crowded
out two original-URL recoveries, including one useful partial-context page.
Next improvements should reserve original-repair capacity, rank exact entities,
metrics/events, event dates and detail-page type, and check existing packet
coverage before spending on further context. Production remains unchanged.
