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
