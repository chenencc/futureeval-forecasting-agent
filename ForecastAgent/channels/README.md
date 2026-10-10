# Native tools, source channels and research integration

Status: isolated development integration, `native_channels_v1`. No production
release is claimed. Donor parsing code is pinned to Channels commit `15d9f45`.
The host exposes 14 capabilities, including admitted index discovery and exact
linked-original download. The standalone compatibility export intentionally
retains 12; this host supplies the two additional network adapters.

See [acquisition architecture](../intelligence/ACQUISITION_ARCHITECTURE.md) and
[latest offline integration evidence](LATEST_VALIDATION.json).

## Responsibilities

| Component | Responsibility | Entry |
| --- | --- | --- |
| Tools | Search, fetch, render and saved-original reading | `tools/capabilities.py`, `tools/original_navigation.py` |
| Channels | Source contracts, query identity, credential requirements, native record parsing | `channels/contracts.py`, `channels/official.py` |
| Research | Plan missing material, select actions, maintain provisional source-bound nodes and relations | `research_loop` |
| Runtime | Dispatch, request reservations, task locks, credentials, timeout and restore | `runtime/retrieval.py` |
| Composition | Collection, independent supplement, bounded recovery and final saved-material map review | `intelligence/pipeline.py` |

Tools and channels never call a model internally. Research interpretation is not
source truth. The development model defaults to Super through configuration;
`collection_model` can select another model without modifying tool or research
prompts. Scoring remains a separately selected stage.

## Capability contract

`Capability` declares an ID/version, JSON argument schema, kind, effects, budget
categories, output kind, repository handler and optional policy. `register` rejects
duplicate IDs. Registered handlers are code-controlled, not model-selected import
paths. A new adapter can be registered without adding another name branch in
`RetrievalTask.execute` or another acquisition-effect list in research code.

Existing tools retain native handlers through a compatibility adapter. Agent tool
exposure is validated by the registry. The optional fourteen channel/navigation
definitions come from the same registry. The public catalog omits duplicate
schemas by default; the full manifest is used for frozen identity.

Effects distinguish network acquisition from saved reads and material writes.
Native handlers reserve every physical request before transport. Registration and
planning estimates grant no allowance. A read can create a parsed view while
spending zero HTTP/search/model calls.

## Optional activation

```python
from ForecastAgent.intelligence.pipeline import collect

result = collect(
    question_request,
    task_directory,
    clock_utc=operating_clock,
    research_map=True,
    channel_tools=True,
)
```

The request can also explicitly select `capability_policy="native_channels_v1"`.
The new composition uses a clearly labeled development adapter rather than
impersonating a frozen release. It reuses the unchanged native
collection/supplement runner. Incomplete collections preserve their stage and
budgets and remain resumable; they are not certified complete by the adapter.

## Official channel behavior

- Twenty-three API/feed contracts and five curated issuer/authority profiles.
- GDELT offers article leads, DBnomics offers exact statistical series, BLS
  preserves series/period/footnote identity, and GovInfo offers recent feeds and
  exact package HTML. Availability is not target applicability or truth.
- One physical request consumes one existing native HTTP slot. Search, browser,
  Extract and model caps remain owned by their existing ledgers.
- The toolbox SQLite journal is a capture mirror. Its internal capacity is not
  exposed as production allowance; returned budgets come from the native task.
- SEC needs an observed CIK and configured contact User-Agent. Congress needs
  its configured key. Missing required configuration spends no physical request.
- Typed endpoint construction validates source parameters. Detail reads require
  a saved discovered URL; curated profile indexes are explicit source contracts.
- No automatic pagination, retries or redirect following. Credentials remain
  host-specific. Failed and empty responses are preserved separately from bodies.
- Identical successful/failed operations replay saved results without HTTP.
  A completed capture left by an interrupted projection can be recovered using
  its recorded native attempt and journal ID. Unknown interrupted transport
  outcomes remain blocked for review.
- Current revised observations are not historically archived vintages.

## Index discovery and original download

Use `intelligence_discover` for a catalog route or an exact task-discovered
RSS, sitemap or issuer index. Issuer discovery requires its curated profile.
Inspect the returned zero-based candidates, then call
`intelligence_acquire_link(capture_id, index, need_ids)` for a selected original.
The host compares immutable parent fields and reparses the saved raw index
before any request reservation. Downloads retain the parent capture ID, raw
hash, candidate index and publisher binding. Cache budgets are not source identity.

Discovery captures and feeds populate source leads rather than readable pages.
Structured observations retain native rows and source roles. Downloaded Office
files remain `captured_unparsed`; raw bytes are kept without inventing a body.
Only new readable originals/observations trigger native material-arrival events.
These request graph processing; they do not certify relevance or close gaps.

The catalog adds current native budget, missing configuration names and GDELT
cooldown. The agent menu narrows sources and saved capture/URL arguments to
current prerequisites. GDELT reserves a five-second task-local interval even on
transport failure; a 429 Retry-After extends it. The tool returns immediately
without sleeping or retrying. Provider/IP limits across concurrent tasks are
separate; this is not an account-wide rate limiter.

## Saved-original reading and coordinate contract

Use `intelligence_outline`, `intelligence_search`, then `intelligence_part` with
exactly one saved URL or capture ID. Section/page/unit IDs are navigation IDs,
not research node IDs or evidence references.

Normalized unit coordinates and native saved-text coordinates are separate.
When a selected unit is missing from the native parsed text, a derivative parsed
view is appended with parser/unit/offset/hash provenance. Original bytes and
capture timestamps are unchanged; previous parsed versions remain in
`page_history`. These are parsing views, not later fetched information.

The part response includes native reference handles. Exact quote validation uses
the current native body hash and saved text, never guessed unit-to-body offsets.
HTML row selection delivers the requested rows and a separately bound contiguous
header prefix. PDF grids remain heuristic; grid cells are not silently certified
as literal page-text observations. Closed tasks allow reading but do not mutate
their material through these tools.

Views are bounded at 128 entries and two million projected characters per task.
Wire prefixes, raw checksum failures and ineligible historical captures fail
closed. No OCR is performed. Hard Linux CPU/memory containment is not established
by local fixture tests.

## Material feedback and restore

`material_events.observe` creates one hash-chained source-arrival stream for
native stores and imported/supplement-stage materials. Unchanged versions do not
create new arrival records. Readable new versions request map processing; they do
not certify relevance or complete a gap. Network tools and local parsed-view tools
use the same research action feedback selected by capability metadata.

Request/configuration, adapter/parser/handler code and the registry schema are
frozen. Code/configuration changes cannot silently resume an old ledger. Archive
the entire task directory, including `channel-tools` SQLite/raw captures, native
bundle, page history and research journals. A bundle-only copy is insufficient
for UUID capture replay.

## Verification boundaries

The 2026-10-10 absorption run passed 222 targeted offline tests, including 15
new host integration gates and the existing research scheduling, material
processing, decision-count and tool-menu suites. Transports were fixtures; no
live provider or model requests were made. The committed donor assets were
checked against `15d9f45`. Linux execution, fresh task agent selection and actual
recall improvement still require independent bounded trials.

The [structured validation record](VALIDATION.json) records 118 passing targeted
tests and a six-original replay across issuer HTML, US bills/law, UK statute XML
and PDFs. Replay preserved original captures and the donor journal, and spent
zero HTTP, search or model calls. It verifies integration and reference integrity,
not source relevance or prediction improvement.

Integration tests cover shared caps, failed/empty responses, credential preflight,
source constraints, exact quote coordinates, altered-number rejection, restore,
duplicate suppression, closed tasks and material feedback. `channels.replay`
reuses an operator-provided saved-original cohort with network blocked and checks
raw/ledger hashes before and after.

The offline Ubuntu CI job is configured without provider secrets. Its execution,
fresh unresolved-question agent behavior and prediction quality remain separate
gates. Old release and historical experiment manifests are not regenerated to
make new development source pass their original hash checks.

The complete baseline/candidate regression comparison is not green: both reported
25 failures and 31 errors across the same 52 test IDs, including historical hash
gates and Windows child-process fixture constraints. Candidate edits also change
the first rejected frozen dependency. No frozen manifest is rewritten. Final
localized fixes are covered by the 118-test targeted run rather than a second
complete run; this evidence alone cannot certify production compatibility.
