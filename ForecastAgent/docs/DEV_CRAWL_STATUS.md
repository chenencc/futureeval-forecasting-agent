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
