# Source-tool integration based on v1.0.5

This branch retains the immutable production `v1.0.5` tag and analysis core.
The production patch tag `v1.0.5-crawl4ai.1` enables
`source_reading_policy=crawl4ai_v1` in the release collector entry point.
The original production v1.0.5 tag remains immutable. Development manifests are
refused by the CLI submission guard; this production patch has a verified
production manifest. Its worker installs the pinned optional backend and Chromium.

## Agent tools

- `inspect_source_structure`: verify original saved bytes and read bounded,
  paginated source roles, observed resources or HTML table/JSON/CSV rows. It can inspect
  preserved failed HTML captures. It does not fetch or call models/search.
  Requested page sizes above ten are explicitly clamped to ten with a correct
  next offset. This avoids corrective model calls without increasing output limits.
- `follow_source_resource`: select a source-bound resource ID from that parent.
  The parent raw hash must still match. Existing HTTP fetching, public URL checks,
  byte limits, caching and the shared ledger own the request.
- `render_source`: repair an accepted failed/thin or explicitly observed embedded
  HTML source. It cannot invent addresses, render arbitrary files or acquire
  current content in historical modes. A completed earlier reservation, including
  failure or interruption, is not automatically retried.

Normal `read_sources` remains the preferred entry. HTML enrichment preserves
legacy text and raw responses; it adds reading projections and table documents.
Small valid JSON/CSV row collections can be read without the ordinary article's
minimum text length. Rows do not establish a target date, product, unit or outcome.
Failed named HTML sources defer automatic paid Extract until bounded browser
repair has been attempted. Failed PDFs and structured files keep their existing
reader/Extract route. Browser failures or exhausted reservations return to the
existing rescue path; they cannot create a repeated browser loop.

## Budget and recovery

Every new source HTTP request and browser repair uses the existing eight-attempt
initial acquisition ledger. At most two of those attempts may be browser renders.
A Crawl4AI failure and its legacy-browser fallback consume two separate slots.
If the optional package is absent, the legacy renderer is chosen before dispatch,
consuming one slot. There are no extra Tavily/Exa calls and no quota renewal.

Each browser render permits 25 routed requests and a 20-second deadline, with
separately bounded response reads and cleanup. Already-requested JSON/CSV bodies
have independent hashes and timestamps; up to three / 2 MB decoded bytes are
archived. Those bytes are not a wire-transfer cap. Error responses are preserved
as gaps and excluded from usable-page promotion.

Full HTTP bodies remain primary over weaker deadline partial DOMs. Other versions
remain archived. Large nested raw bytes, full Markdown and network response bodies
are excluded from model-facing page previews. Original material stays in the task
bundle and the analysis package. Optional markup reading roles never certify truth.

The independent supplement retains its existing separate stage journal and
allowances. This integration does not renew those allowances or change analysis.
New code/input/policy hashes reject silent migration of earlier task state.

## Validation

The branch acceptance workflow installs the optional pinned backend and Chromium
on Linux, checks actual collector tool execution, shared-budget/restart/timeout
fallback/error/preservation cases, runs v1.0.5 worker regressions and verifies
the unchanged analysis baseline. Eight real browser fixtures cover dynamic HTML,
tables, interstitials, failed/partial renders, JSON data, iframe data and API errors.
The real browser fixture also executes registered release tools, archives chart
response rows and confirms that a resumed duplicate does not issue another HTTP
request. Provider-free worker regressions include binary, numeric, date, multiple-choice,
groups/conditionals, replay and mixed 100-question accounting; they do not test
live platform submission.

The local five-question acceptance reuses frozen official sources and discovery
material. It is a source-tool/Agent selection experiment, not fresh search A/B or
forecast accuracy validation. All new network and model attempts are separately
reported and all parent hashes remain preserved.
