# Acquisition improvements and monitor freshness

## Scope and budgets

Ultra remains the collection planner. No analysis, forecast, truth score or
submission tools are introduced. New monitored tasks use `collection_v3`, which
exposes batched reads and passage IDs with **three total Tavily basic attempts**.
The previously authorized v2 pilot retains its frozen five-attempt ceiling;
existing inputs and completed ledgers never receive fresh budgets.

`plan_channels` records need IDs, expected HTTP use and reasons. The checkpoint
flags an over-budget plan. These are planning estimates, not hard reservations:
the durable program ledger still limits eight initial HTTP attempts and one
Extract batch. Exact archive lookup/replay costs two attempts. Failed calls count.

## Reading and diagnostics

HTML uses pinned Trafilatura main-text extraction with a basic fallback. Raw
bytes, parser version, heading metadata, date caveats and captured links remain
available. Bounded table rows are stored as separate coordinate spaces; merged
cells and nested tables remain heuristic. Date metadata is not historical proof.

PDF keeps page coordinates and adds heuristic pdfplumber tables for the first
20 pages. Optional local Tesseract OCR requires `FORECAST_PDF_OCR=1` and the
executable; it is limited to three pages, 120 DPI and 30 seconds per page.
GitHub runners do not automatically install OCR. Empty scanned pages explicitly
remain gaps. Dynamic JavaScript pages are diagnosed and may use the existing
bounded basic Extract rescue; no browser crawler is silently enabled.

Long paragraphs are split into overlapping windows with exact saved offsets.
Phrase matches, uncommon terms, numerical terms and Chinese bigrams aid local
navigation. This does not establish relevance or factual correctness. Passage
IDs are tied to the saved raw and parsed source versions. Original excerpts
remain resolvable across refreshes. Search discovery snippets retain all three
bounded Tavily chunks up to 2,000 characters and declare truncation.

## Context and cache

Collection requests send a deterministic task-state projection and complete
recent assistant/tool turns. Older tool text stays on disk. Large recent tool
text may be shortened with an explicit marker; tool-call pairs are retained.
`context_projections` records original/projected character counts. These counts
are not token billing; actual provider usage remains in model call records.

`FORECAST_SHARED_CACHE_ROOT` enables a 15-minute cross-task live body cache.
Original capture times are retained; raw and parsed hashes are checked. Historical
tasks never import this live cache. Explicit refresh can use ETag/Last-Modified
for ordinary sources. A 304 consumes one request but keeps original body bytes,
capture time and a separate revalidation time. Structured adapters retain their
own request parameters rather than reusing ordinary web validators.

Official BLS, Treasury and Federal Register requests accept paired start/end
dates. BLS bounds select observation months, not release dates; partial-month
requests include that month's observation. Current versions still lack historical
release-vintage guarantees. Saved rows have local pagination without more HTTP.

Acceptance now exports per-source extraction states, tables, PDF reading gaps,
row counts/date ranges, units, capture/publication dates, pagination and per-need
associations with usable material. No source reliability or truth score is computed.

## Scheduled polling and collection

`Monitor FutureEval questions` retains the off-hour `7,27,47` schedule. It captures
questions only, with a ten-minute timeout and a dedicated concurrency group.
`Collect new FutureEval questions` runs after a successful poll, uses a separate
serialized group and restores its own state artifact. The first worker migrates
existing monitor ledgers; a missing known ledger fails closed. Poll snapshots
survive model failures. Manual `snapshot_only=true` suppresses acquisition.

The worker resumes unfinished open binary questions, keeps existing request
hashes and budgets, and collects at most two tasks per run. For completed open
live tasks, critical selected/excerpted ordinary or official sources can refresh
after 12 hours. Updates use the existing cap of three per UTC day and 24 per task
lifetime. There is no new model or Tavily call for refresh. Closed/historical
tasks and exhausted update ledgers are skipped. RSS can use this ordinary source
refresh path; provider release-calendar-driven scheduling is not implemented.

GitHub cron can be delayed or dropped, so changing a cron string cannot guarantee
an exact interval. The local `ForecastAgent Monitor Watchdog` checks every five
minutes while this Windows user is logged in and the machine is awake. After
20 minutes without a successful poll snapshot **and its uploaded artifact**, it
dispatches the existing GitHub workflow. Collection still executes on GitHub.
It waits for active polls, reserves a cooldown before dispatch and reports old
queued runs without repeatedly dispatching. Machine sleep/logout and GitHub
queues can still delay polling. This is a fallback, not a timing SLA.

Install the watchdog with `integrations/install_monitor_watchdog.ps1`, using
the already authorized GitHub CLI. Status and logs live under
`E:\metaculus_data\monitor-watchdog` and `E:\metaculus_data\logs`.

References: [GitHub schedule limitations](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule),
[Trafilatura](https://trafilatura.readthedocs.io/en/latest/usage-python.html),
[Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search).
