---
name: intelligence-acquisition
description: Acquire official statistics, issuer reports, legislation, government originals and news leads through the ForecastAgent Intelligence Toolbox; navigate saved originals with provenance and bounded request budgets.
---

# Intelligence acquisition

Use this skill to obtain source material or read an existing capture precisely.
This package is model-independent. It does not forecast, score evidence, trade,
submit predictions or register itself in a production worker.

## Execution interface

The host provides a task-scoped `Toolbox` instance and exposes `TOOL_DEFINITIONS`
from `ForecastAgent.tools.intelligence_box.core`. Execute JSON calls using
`box.call(name, arguments)`. The instance root is the existing task ledger.
When the host has not registered these tools, use the Python interface described
in [Invocation](references/invocation.md); loading this skill alone does not make
functions available to a model.

When the host uses the native Capability registry, load
[Native compatibility](references/native-compatibility.md) instead of passing
standalone schemas to that runtime. The native interface adds need IDs and native
URL/reference handling; it currently exposes twelve compatible tools. The two
new discovery/download functions remain standalone-only until host handlers exist.

Use `intelligence_catalog` to find current source parameters, profiles and
`discovery_routes`; use `intelligence_budget` to inspect remaining requests.
Only acquire material needed for the task. Follow [Routes](references/routes.md)
for domain-specific choices; do not load unrelated routes.

## Choose the next operation

| Need | Operation |
| --- | --- |
| Exact numeric series or known official API object | `intelligence_fetch` with a catalog source ID |
| Earnings/election authority page | `intelligence_profile` with the matching entity/profile |
| New originals from official RSS, sitemap or issuer index | `intelligence_discover`; then `intelligence_acquire_link` for a selected candidate |
| Known original URL on an explicitly allowed host | `intelligence_read` |
| Versioned Congressional bill text | Fetch `congress_texts`; select with `intelligence_bill_text` |
| A clause, late paragraph, page or table already downloaded | Offline outline/search/part; see [Invocation](references/invocation.md) |

A sitemap child is another index, not a downloaded document. Fetch it with an
explicit `intelligence_discover`. GovInfo RSS includes both detail pages and
`original_rendition` candidates; prefer the actual TEXT/PDF/XML original when
full text is needed. GDELT produces news leads; full articles are separate reads
on caller-reviewed hosts, not implicit follow-ups.

## Accept the result accurately

- `usable`: source structure or readable body passed parsing. Check exact entity,
  indicator, fiscal/calendar period, units, legal stage and response coverage.
  This status does not verify truth or applicability to the question.
- `empty`: valid zero-record response. Record the empty window; do not infer that
  the event or document does not exist.
- `captured_unparsed`: complete original binary saved, without a supported parser.
  Preserve the raw file and parser gap. Do not provide it as readable evidence.
- `configuration_required`: no HTTP attempted. Report only the missing configuration
  names. Existing SEC/Congress settings can be loaded by the host; never print keys.
- `failed`: inspect HTTP metadata and body diagnostics. The attempt remains charged.
  Preserve the gap and original; do not silently replace the failure with a later success.
- Invalid arguments raise `ValueError` before dispatch. Consult the catalog/schema
  and correct the request. Exhausted HTTP allowance raises `RuntimeError`: stop
  network acquisition for this ledger; offline reads remain available.

Follow `more_available`, `next_page_available`, `next_offset`, `next_start`,
`next_row`, `hits_truncated` and PDF scan/table truncation flags. Use only the
continuation supported by that tool. Each explicit network page consumes budget;
local section/table continuations do not. A missing search hit is not event absence.

## Preserve the task contract

Keep capture ID, raw SHA-256, source URL, fetch time, source identity, selected
version and coordinates with every returned excerpt. Originals and ledgers are
immutable; reopening the root does not replenish budgets. Do not create another
root or raise caps to evade exhausted task/campaign allowances. Production hosts
must bind their native quotas before exposing this standalone toolbox.

No automatic retry, redirect, recursion, external viewer, OCR or browser fallback
is performed. Follow a redirect only as a separately budgeted request on an
allowed source host. For HTTP 429, retain Retry-After and let the host schedule a
bounded retry. GDELT DOC calls need at least five seconds of spacing; BLS no-key
usage also has a shared provider/IP daily limit. This toolbox is not an account-wide
rate limiter. See [Routes](references/routes.md) for provider limitations.

Return acquired material, provenance, coverage and explicit gaps. Distinguish
news leads, data observations, index pages and full originals. Historical data
periods/search dates do not establish as-of publication availability.

## Validated usage

[Tool contracts](references/tool-contracts.json) contains the current JSON function
schemas. [Executable examples](references/examples.json) contains all fourteen
call shapes, including offline reads. Replace fixture capture IDs with captures
from the same task root. [Validation](references/validation.md) gives repeatable
checks and the limits of current acceptance.
