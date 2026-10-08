# Experimental source reading and observed resource follow-up

This optional reader is developed on `codex/acquisition-next-crawl4ai` against
the immutable `v1.0.5` acquisition baseline. The development collector now
registers the tools described in [release integration](RELEASE_SOURCE_TOOLS.md).
The production tag, analysis, search allocation and submission remain unchanged.

## Capabilities

1. **Observed resource discovery.** Parse iframe/embed/object addresses,
   alternate links, and recorded browser GET/HEAD data requests, including
   requests denied by the parent's budget. Each address carries its parent
   URL, raw SHA-256, retrieval time and exact markup/audit position. Addresses
   are not invented, automatically fetched or certified relevant. Cross-origin
   addresses remain candidates; execution performs the existing public DNS and
   redirect checks. There is no iframe-depth expansion.
2. **Source sections.** Keep original bytes and legacy text. Add source-bound
   heading, paragraph and table projections with HTML/ARIA roles and disclosed
   CSS fallbacks. Navigation/footer/header/aside remain inspectable, but are
   omitted from the optional reading projection. Unknown content is retained.
   Data tab panels are content, even when their identifiers contain `nav`.
   Nested layout tables are not merged into one article. Explicit publication
   metadata is preserved without inferring its meaning or a target date.
3. **Structured responses.** Read saved JSON/CSV rows with exact field/cell
   values, paths, row indices and truncation flags. Requested query parameters
   are separate from metadata actually present in the response. API errors,
   HTTP errors and metadata-only dictionaries never become observation rows.
   A station metadata array can be a valid generic table; that does not make
   it a tide-observation series. No product, units, date or resolution is inferred.

The existing HTML table reader still supplies structured cells; source sections
add context rather than replacing that reader. The existing complete-body-before-
partial-DOM export policy continues to protect saved HTTP captures.

## Browser response archive

The optional Crawl4AI browser adapter also preserves up to three JSON/CSV
responses that the page already requested. They consume the existing browser
request allowance; reading the browser response adds zero HTTP dispatches.
Each response has its own source URL, time, status and raw decoded-body hash,
separate from the parent DOM hash. Responses survive in the failure audit when
the parent DOM cannot be exported.

Limits are 25 allowed browser requests, a 20-second render deadline, three data
response reservations, and 2 MB of **archived decoded response bytes**. The
last limit is not a network-transfer ceiling: when Content-Length is absent,
the browser response must be read before its size can be checked. Reads and
cleanup have separate bounded timeouts. No supplied authentication cookies, credentials, POSTs,
service workers, stealth mode or WebSocket sessions are enabled. WebSocket
dependence is an explicit transport gap.

## Tool interface

```sh
# Offline: verify the saved raw hash; write source roles/resources or JSON/CSV rows.
python -m ForecastAgent.acquisition.source_tools \
  --input saved-snapshot.json --output source-reading.json

# Explicit live follow-up: selections must exist in that saved parent's observations.
# Example selections.json: [{"url":"https://example.org/dashboard","render":true}]
python -m ForecastAgent.acquisition.source_tools \
  --input saved-snapshot.json --selections selections.json \
  --output snapshots/observed-resource-follow
```

One parent root permits at most two selected URLs/HTTP attempts and one browser
render. The enclosing collector must enforce budgets across parents; this
experimental reader does not replace its task ledger. Inputs, code, selections
and browser channel are frozen before requests. Operations are reserved before
dispatch. Completed, failed and interrupted reservations are not automatically
retried. Resume verifies captured-file hashes and rejects changed identities.
Fresh resources have fresh retrieval timestamps and explicit parent lineage.
Parse failures preserve successfully fetched source bytes as projection gaps.

## Validation and limitations

The acceptance workflow runs offline provenance, reservation, error and
compatibility tests, plus eight real-browser local fixtures on Linux: dynamic
content, tables, partial rendering, blocked dependencies, markup, an observed
JSON response, an iframe with JSON data, and a remote data error. It has no
provider credentials or model/search requests.

The five-question pilot replays the exact previous 33 captures and follows two
observed official resources in a separate timestamped experiment. It is a
reader test, not a fresh acquisition-agent A/B, forecasting benchmark or
proof that the target's future information is already available. Role labels
can be imperfect on unconventional markup. Maps/canvas content without a
readable table or data response remains a recorded gap. HTTP certificate errors
remain errors; certificate verification is never disabled.

References: [Crawl4AI hooks](https://docs.crawl4ai.com/advanced/hooks-auth/),
[NOAA Data API specification](https://api.tidesandcurrents.noaa.gov/api/prod/).
The NOAA station metadata endpoint is not the observation Data API.
