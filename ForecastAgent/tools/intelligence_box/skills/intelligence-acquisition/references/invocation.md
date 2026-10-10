# Invocation and continuation

## Host setup

```python
from ForecastAgent.tools.intelligence_box.core import Toolbox, TOOL_DEFINITIONS

# The host assigns the root and cap from its existing task/campaign policy.
box = Toolbox(task_root, max_requests=task_http_cap,
              configuration=host_configuration,
              allowed_domains=reviewed_extra_hosts)
try:
    result = box.call("intelligence_catalog", {"domain": "finance"})
    # Register TOOL_DEFINITIONS with the chosen model. Execute only the returned
    # function name/JSON arguments, then give the tool result back to the agent.
finally:
    box.close()
```

Do not paste configuration values into prompts. `configuration` can contain
`SEC_USER_AGENT` (real identifying contact) and `CONGRESS_API_KEY`; alternatively
the host sets these environment variables. Key onboarding catalog entries marked
`implemented=False` are plans, not available tools. Missing configuration must
not trigger a series of failing network calls.

## Full document from an index

```python
index = box.call("intelligence_discover", {
    "url": "https://www.govinfo.gov/rss/plaw.xml", "kind": "rss", "limit": 10
})
# Inspect status, candidate identity, eligible, target_kind and link_role.
# Pick the required package and rendition, not merely index 0.
original = box.call("intelligence_acquire_link", {
    "capture_id": index["id"], "index": selected_zero_based_index
})
```

Both operations charge the same ledger. `acquire_link` revalidates the selected
candidate against the parent raw index hash and allowed host/profile path policy.
Indexes contain explicit rejected candidates; do not turn `eligible=False` into
a body request. A child sitemap requires another discover call. Use
`coverage.native_metadata.next_offset` to fetch the next index window; a repeated
index may be served from cache, but distinct windows are separately budgeted.

## Exact text, table or late page without another fetch

```python
outline = box.call("intelligence_outline", {"capture_id": original["id"]})
hits = box.call("intelligence_search", {
    "capture_id": original["id"], "query": "Total revenues", "limit": 10
})
part = box.call("intelligence_part", {
    "capture_id": original["id"], "unit_id": hits["hits"][0]["unit_id"],
    "max_chars": 12000, "row_start": 0, "max_rows": 40
})
```

Check `hits` before indexing. Search is literal, case-insensitive, not semantic.
`unit_id` comes from outline/hits. PDF pages are one-based (`page:27`); text and
row offsets are zero-based. `next_start` paginates text and `next_row` paginates
HTML rows independently. `next_offset` paginates outline units. Preserve table
headers, spans, adjacent unit context and PDF page references. PDF grids are
heuristic; rotated/scanned pages can remain incomplete. Raising `max_pages`
within its bound is offline but still has CPU/memory costs.

`intelligence_links` and `intelligence_tables` inspect accepted saved HTML.
`intelligence_provisions` reads exact saved UK CLML provision IDs; omit the ID
for its index. HTTP-error/truncated originals are rejected. Explicit navigation
may recover intact HTTP-200 bytes from a prior parser failure, but never rewrites
that capture's original status.

## Versioned bill texts

```python
params = {"congress": 119, "bill_type": "hr", "bill_number": 1}
detail = box.call("intelligence_fetch", {"source_id": "congress_bill", "parameters": params})
texts = box.call("intelligence_fetch", {"source_id": "congress_texts", "parameters": params})
body = box.call("intelligence_bill_text", {
    "capture_id": texts["id"], "version_index": selected_version,
    "format_index": selected_format, "detail_capture_id": detail["id"]
})
```

Inspect version type/date/format before selecting. Public/private-law selections
require the matching official law record from the same bill detail. Actions keep
native action dates and source systems; explicit pages use offset/limit.
Enrolled text or chamber passage is not an enactment/commencement conclusion.

## Delivery record

Return each selected capture's ID, URL, raw hash, capture time, native identity,
readable excerpt/record and source coordinates. Include response/window limits,
remaining budget and unresolved configuration/HTTP/parser gaps. Never label a
metadata page or article lead as a complete source body.
