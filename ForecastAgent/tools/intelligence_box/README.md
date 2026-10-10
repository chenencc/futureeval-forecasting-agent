# Intelligence Toolbox v0.6

Independent read-only source acquisition for an agent of any model or framework. This package does not import a worker, call an LLM, produce forecasts, or submit predictions. It currently runs inside the ForecastAgent repository and reuses its document readers.

## Acquisition expansion: official discovery and original downloads

This is an acquisition capability, separate from saved-original navigation.
The catalog now exposes `discovery_routes`; there are fourteen agent functions
in total, including the three offline navigation tools.

- `intelligence_discover(url, kind, profile_id=None, offset=0, limit=40)` fetches
  one RSS/Atom feed, sitemap or curated issuer IR page. `kind` is `rss`, `sitemap`
  or `ir`. IR requires a curated issuer profile. The index raw bytes, candidate
  labels, feed publication fields or sitemap lastmod fields are retained.
- `intelligence_acquire_link(capture_id, index)` downloads one zero-based candidate
  original, preserving the parent capture/hash, original link and timestamp.
  The candidate is revalidated against saved raw bytes and current host policy.
  An explicitly reviewed CDN policy expansion does not rewrite the parent; the
  binding records parent eligibility and current allowed hosts.

There is no automatic recursion, redirect following, retry or mass download.
Each network operation reserves the same task request ledger before fetching;
failure still counts. A child sitemap is a lead for a separate discover call.
Host/profile-path and credential-query restrictions apply before original fetch.
URL fragments are deduplicated; distinct query strings are not merged. HTML
base URLs on allowed hosts are honored. Issuer product-download pages outside
the IR paths remain rejected leads. Labels/hosts do not verify document metrics.

```python
index = box.call("intelligence_discover", {
    "url": "https://www.federalreserve.gov/feeds/press_all.xml",
    "kind": "rss", "limit": 10
})
original = box.call("intelligence_acquire_link", {
    "capture_id": index["id"], "index": 0
})
```

Discovery response and decoded-byte caps are 2 MB. Output windows are at most
100 records; `next_offset` denotes additional saved-index candidates, not a claim
of archive completeness. Calling discover for another window performs another
bounded acquisition unless its identical request is cached. Lastmod/publication
fields do not prove historical availability of the current body.

Original files use the existing frozen document byte cap. Supported readers can
produce `usable` material; Office MIME types are saved as `captured_unparsed`
with `original_download_complete=true` and `readable_document=false`. This state
can be cached without downloading again. It does not certify a valid/readable
Office structure. No external conversion service or Office parser is introduced.
HTML interstitials remain failed evidence reads despite a complete HTTP download.

Validation: 65 focused tests pass. A separate public pilot used its frozen ten
HTTP attempts, with no resets: five complete index responses, three readable
linked originals and one complete DOCX original awaiting a parser. The tenth
response was an explicitly preserved 301 redirect. Two index responses used the
same Microsoft page before/after path-policy adjustment. FEC sitemap reported
12,452 candidates, with only a bounded output window exposed; the Fed feed held
20 announcements. These are discovery counts, not documents fully downloaded.

All ten available raw hashes verified; the audit left the pilot ledger unchanged.
The DOCX attempt originally failed binary text parsing, and that old capture is
preserved. The current code's `captured_unparsed` behavior was validated offline.
No further live calls were made after the cap was exhausted. No model/search
credits or new keys used. See `DISCOVERY_VALIDATION.json`. Raw pilot artifacts
remain in the ignored `.tmp/discovery-acquisition-pilot-20261010` directory.
Production/native budget integration and Linux validation remain pending.

Official protocol references: [Sitemaps](https://www.sitemaps.org/protocol.html),
[Federal Reserve feeds](https://www.federalreserve.gov/feeds/feeds.htm) and
[FEC published sitemap locations](https://www.fec.gov/robots.txt).

## Agent interface

```python
from ForecastAgent.tools.intelligence_box import Toolbox
from ForecastAgent.tools.intelligence_box.core import TOOL_DEFINITIONS

box = Toolbox("artifacts/my-task", max_requests=30)
try:
    sources = box.call("intelligence_catalog", {"domain": "finance"})
    capture = box.call("intelligence_fetch", {
        "source_id": "worldbank",
        "parameters": {"country": "US", "indicator": "NY.GDP.MKTP.KD.ZG",
                       "date": "2020:2024"}
    })
    remaining = box.call("intelligence_budget", {})
finally:
    box.close()
```

Pass `TOOL_DEFINITIONS` to the model's function/tool API, then execute returned calls through `box.call`. Tool availability does not grant additional requests. Source parameters come from the catalog. Read discovered original links with `intelligence_read`; explicitly add any new publisher domain through `allowed_domains` when constructing the toolbox. Arbitrary hosts, credentialed URLs, credential-like query parameters and private network targets are rejected. Public DNS is checked before requests and redirects; network-level egress controls are still needed against DNS rebinding in a hostile multi-tenant deployment.

## Initial channel coverage

| Domain | Executable channels | Appropriate use |
| --- | --- | --- |
| Finance | World Bank, FRED graph CSV, Federal Reserve RSS | Indicator observations, rate series, announcement leads |
| Politics | Federal Register, UK Parliament bills | Document discovery, regulatory type, legislative stage |
| Health | ClinicalTrials.gov | Trial identity, registered stages and reported results |
| Environment | USGS, NASA EONET | Geolocated observations and natural-event leads |
| Science | Crossref | Bibliographic metadata and original publication discovery |
| Documents | Existing HTML, PDF, CSV and JSON readers | Original body acquisition with observable extraction diagnostics |

There are 18 API/feed source contracts and five curated issuer/election document profiles. This is not worldwide coverage. US court dockets, real-time quotes and broad independent news coverage remain expansion work. Tavily, Exa, Polymarket and browser/Crawl4AI already exist elsewhere in ForecastAgent; their native adapters and budgets have not been wired into this independent dispatch interface. They should be integrated through a budget-preserving adapter, not reimplemented.

## Priority domains and saved-material tools

| Domain | Available detail capability | Required interpretation |
| --- | --- | --- |
| Company financials | SEC company concepts and submissions; Tesla and Microsoft official IR profiles | Exact issuer/CIK, fiscal period, units, GAAP versus adjusted basis, filing versus reporting date |
| Elections | California SOS and Brazil TSE official portals; FEC authority directory; calendars and original links | Jurisdiction, election round and announcement stage; a calendar is not a certified result |
| Legislation | Exact UK bill detail and publication index; Federal Register document detail; UK legislation CLML and exact provisions | Bill stage versus enacted statute, regulation type, document version, commencement and later amendments |

Fourteen callable tools are exposed in `TOOL_DEFINITIONS`: `intelligence_catalog`, `intelligence_fetch`, `intelligence_read`, `intelligence_budget`, `intelligence_profile`, `intelligence_links`, `intelligence_tables`, `intelligence_provisions`, `intelligence_bill_text`, `intelligence_outline`, `intelligence_search`, `intelligence_part`, `intelligence_discover`, and `intelligence_acquire_link`.

Use the catalog to select a source/profile. Inspect a saved index with `intelligence_links`, then explicitly read a matching original detail link. Profiles constrain issuer/authority hosts; they do not establish target relevance. `intelligence_tables` returns saved HTML cells, row/column spans and preceding context. It does not infer units or normalize merged grids. `intelligence_provisions` reads an exact saved CLML clause, even beyond the initial 500-record excerpt; not-found, ambiguity and truncation remain explicit. These three saved-material tools consume zero HTTP requests and verify raw hashes.

```python
box = Toolbox("artifacts/priority-task", max_requests=25,
              max_document_bytes=16_000_000)
try:
    page = box.profile("microsoft_ir")
    if page["status"] == "usable":
        cells = box.tables(page["id"])
finally:
    box.close()
```

SEC endpoints require `SEC_USER_AGENT` containing a real identifying contact email, as specified by SEC access guidance. Set it locally; do not put contact details in chat or tracked files. Missing configuration returns `configuration_required` before reserving a request. This is not an API key. SEC fixtures pass. A subsequent two-request live validation returned HTTP 200 for Tesla submissions (1,000 recent rows) and the selected revenue concept (45 observations). See `SEC_VALIDATION.json`; records across periods and filing versions must not be treated as distinct target-quarter results.

On Windows, `configure_sec.ps1` opens a local input dialog and stores the contact value using Windows user encryption in `D:\metaculus\.local\sec-user-agent.clixml`. It does not change GitHub secrets or global environment variables. Load it into the same PowerShell process that launches the agent:

```powershell
. .\ForecastAgent\tools\intelligence_box\configure_sec.ps1 -LoadOnly
```

The live `verify_sec` helper uses the existing priority-pilot ledger with two counted SEC requests. Contact headers and values are not written to its report.

## Evidence and quality contract

Each capture retains a UUID, original request URL, capture start and completion times, raw response file and SHA-256, native records, response status, retry hint, limitations and remaining budget. Raw source fields remain intact: observation time is never replaced by fetch time, bill introduction is never interpreted as enactment, and bibliography/RSS entries are never labeled full article bodies.

`usable` means a supported structure or readable extraction exists. `empty` is a valid zero-result response. `failed` means transport, status, byte-limit, structure or body validation failed. Neither `usable` nor `empty` establishes relevance or factual truth. These flags remain false until a downstream verifier acts.

Publication-vintage safety is not established by filtering observation dates. Latest financial series can contain revisions. World Bank unit fields may be blank and the FRED CSV does not include units; request official indicator/series metadata before numerical comparison. Federal Register search can match incidental full-text phrases. Crossref can return datasets or software, not just papers. PDF text can mix columns and adjacent notices; table-unavailable diagnostics must remain visible.

## Budgets, cache and recovery

- The root directory owns a persistent SQLite attempt ledger. Reopening cannot change its cap.
- A request is reserved before transport. Failed and interrupted attempts count. No automatic retries, pagination or paid fallback occur.
- API responses are bounded at 2 MB; documents default to 8 MB. A new task may explicitly freeze a different document cap, up to 32 MB; reopening cannot change it. The priority pilot froze 16 MB. Oversized raw prefixes are retained and rejected as incomplete.
- Redirects are not followed automatically. Inspect the recorded Location and explicitly read an allowed destination; each physical request reserves its own attempt.
- The task-scoped PDF decoder honors the frozen document cap without changing the production reader's global 8 MB policy. Native PDF limits still apply: bounded pages/text and structured tables on the first 20 pages. Later pages retain text and explicit table gaps; rotated text and charts can remain incomplete. Optional `pdfplumber` improves tables but does not certify values.
- Single-response pagination indicators and scope are preserved. A returned window is not claimed to be an archive.
- Successful captures are reusable for 900 seconds. Cache hits preserve original timestamps and consume no new HTTP attempt. Raw hashes are checked; missing or corrupt bodies require a newly counted request.
- A process interrupted before completion leaves a counted `reserved` attempt. It does not automatically reschedule or overwrite evidence.
- New captures use new UUID directories. This is append-only naming, not operating-system write protection. Reports and source files should be archived separately for long-term integrity.
- This standalone ledger is for new experiments/tasks. Production adoption must bind native reservations, retries, provider caps and source identity through a tested adapter. Existing campaign ledgers must not be reset.

## Verification

```powershell
python -m unittest ForecastAgent.test_intelligence_box -v
python -m ForecastAgent.tools.intelligence_box.verify --root .tmp/toolbox-public-pilot-20261010
python -m ForecastAgent.tools.intelligence_box.followup --root .tmp/toolbox-public-pilot-20261010
python -m ForecastAgent.tools.intelligence_box.audit --root .tmp/toolbox-public-pilot-20261010 --output ForecastAgent/tools/intelligence_box/VALIDATION.json
```

The bounded pilot used 13 public HTTP attempts. Nine source channels produced records after an exact trial lookup; the original broad trial query returned zero records. Two of three original document reads were readable: a Federal Reserve HTML announcement and a linked official PDF. Federal Register HTML returned an access interstitial, correctly rejected; the PDF remained available. This is availability and structure evidence, not a forecast improvement benchmark. Sixteen offline tests cover shells, valid empty feeds, interrupted budgets, 429 hints, corrupted caches, unsafe URLs and redirects.

See `VALIDATION.json` for the offline replay audit. Raw captures and the attempt ledger remain in the local pilot directory; they are not included in Git. No model calls, search credits, workflow dispatches or production changes were used.

### Priority-domain validation

The separate v0.2 pilot used 14 of 25 frozen public HTTP attempts: ten original captures were usable, one valid empty, and three failed. All available raw hashes matched. Tesla's index returned HTTP 403; a previously observed official PDF link downloaded successfully, but the inherited decoder initially rejected its 10.55 MB body. The saved original was reprocessed under this task's declared 16 MB policy: 33 readable pages, zero additional HTTP requests, original failed capture unchanged. Financial pages 4, 27 and 30 were visually checked; later structured-table and rotated-text gaps remain.

Microsoft yielded 11 HTML tables / 181 rows. California's 2026 calendar yielded 19 PDF pages. Brazil's official election portal and the FEC authority directory were readable. A UK bill-list timeout was retained; an exact previously saved bill ID yielded its stage detail and a legitimate empty publication index. A 2025 enacted statute yielded saved XML; exact section 138 was read beyond the initial 500-record excerpt without a request. Federal Register discovery and exact detail both worked. Availability is not evidence of factual or forecasting improvement.

Thirty-one offline tests cover identity binding, configuration preflight, exact parameters, table spans, saved provision lookup, explicit redirects, immutable byte policies and task-scoped PDF decoding, in addition to the v0.1 recovery tests. See `PRIORITY_VALIDATION.json` for the compact audit. To reproduce its offline audit:

```powershell
python -m ForecastAgent.tools.intelligence_box.audit_priority --root .tmp/toolbox-priority-pilot-20261010 --output ForecastAgent/tools/intelligence_box/PRIORITY_VALIDATION.json
```

`verify_priority` and `follow_priority` perform counted network requests; they are not offline tests. Reopening their root preserves the same caps. Production integration remains pending.

## Optional onboarding: keys and configuration

The original unkeyed channels/profiles need no additional API key. Congress acquisition requires `CONGRESS_API_KEY`; SEC uses identifying contact configuration rather than a key.

| Requested capability | Local environment name | Official application | Status |
| --- | --- | --- | --- |
| FRED/ALFRED full API and vintage observations | `FRED_API_KEY` | https://fred.stlouisfed.org/docs/api/api_key.html | Optional; adapter not implemented |
| US Congress bill detail, actions and text | `CONGRESS_API_KEY` | https://api.congress.gov/sign-up/ | Implemented and live validated; worker integration pending |
| US campaign finance | `FEC_API_KEY` | https://api.open.fec.gov/developers/ | Optional; adapter not implemented |
| SEC issuer filings and XBRL | `SEC_USER_AGENT` | https://www.sec.gov/search-filings/edgar-application-programming-interfaces | Adapter implemented; live submissions/concept validated with identifying contact configuration |

Never place credentials in chat, source files, catalog entries or capture URLs. Congress uses header-only authentication restricted to its exact API host. Redirects never forward credentials automatically. If the API reflects the configured key, the response is redacted before storage and marked as not byte-identical to the wire response.

### Congress local onboarding

`configure_congress.ps1` stores `CONGRESS_API_KEY` in a separate Windows-encrypted local file. Dot-source it with `-LoadOnly` before launching local tools. The configuration probe uses header authentication, rejects redirects, reserves an exclusive one-request marker and excludes reflected request metadata from saved data. An initial default Python User-Agent request returned HTTP 403; one separately recorded diagnostic request using an explicit application User-Agent returned HTTP 200. This confirms the key is usable, but does not prove the cause of the first failure. Congress source adapters are now implemented; workflow environment injection and native worker integration remain pending. A GitHub Secret existing by name does not establish worker use.

## Congress bill acquisition

Use `congress_bill`, `congress_actions` and `congress_texts` through `intelligence_fetch`. All require exact Congress number, lowercase bill type and bill number. Actions/text accept explicit `limit` (1..250) and `offset` (0..100000). Official envelopes, response hashes, request identity, latest action, law references, action dates/source systems, text version labels/dates and format links are retained. Invalid identities fail while preserving the raw response. A valid empty text index is not proof that a law does not exist.

```python
identity = {"congress": 119, "bill_type": "hr", "bill_number": 1}
detail = box.fetch("congress_bill", identity)
actions = box.fetch("congress_actions", dict(identity, limit=20, offset=0))
texts = box.fetch("congress_texts", identity)
# Inspect texts["records"] before choosing zero-based indices.
original = box.bill_text(texts["id"], version_index=0, format_index=0)
# A Public/Private Law selection also requires detail_capture_id=detail["id"].
```

`intelligence_bill_text` only reads a URL present in the hash-verified saved index, on Congress.gov or GovInfo, with the exact bill identifier. Public/private-law URLs additionally require a hash-verified bill detail carrying the matching official law number. The resulting body capture retains the index hash, selected version/date/format and any supporting law-detail hash. These links do not infer commencement. An enrolled bill is a text version, not independent enactment proof.

Actions retain a bounded page and original pagination. `more_available` can include records outside the current page; `next_page_available` specifically indicates a subsequent page. Each explicit page/read/redirect counts against the same persisted task budget. No automatic retries, full-history walk or model call is performed. Original bodies are preserved; the agent text projection is capped at 150,000 characters and exposes truncation.

Live validation used seven additional requests within the existing 25-request priority ledger (23 used, two remaining). For 119 HR 1: detail, six text versions, three action pages (20+20+19=59) and both enrolled-bill and Public Law HTML were usable. The official detail carries Public Law 119-21 and its native latest-action date. All seven raw hashes matched and the configured key was absent from saved captures and ledger. Page reads have separate capture times, so atomic cross-page consistency is not claimed. This single bill validates mechanics, not broad availability or predictive quality.

Forty-five offline tests cover this expansion and earlier toolbox behavior. Run:

```powershell
python -m unittest ForecastAgent.test_intelligence_box ForecastAgent.test_intelligence_congress -q
```

See `CONGRESS_VALIDATION.json` for the audit. `verify_congress_chain` and `verify_congress_followup` are counted live helpers; `audit_congress` is offline. Production budgets/workflows are unchanged.
# Saved-original navigation (v0.4)

Three additional agent tools read hash-verified saved captures without fetching:

| Tool | Purpose | Continuation |
| --- | --- | --- |
| `intelligence_outline` | List sections, provisions, PDF pages and HTML tables | `next_offset` |
| `intelligence_search` | Literal case-insensitive local search; returns unit IDs and text offsets | Narrow the query or raise the bounded hit limit when `hits_truncated` is true |
| `intelligence_part` | Read an exact unit, text window and table row window | `next_start`, `next_row`; PDF table row truncation is reported per table |

Recommended sequence: inspect the outline, search exact entities/metrics/dates,
then read the matching unit and adjacent context. Follow continuation fields;
do not infer completeness from the first excerpt. These tools do not verify
relevance, truth, reporting period, legal status or historical availability.

```python
outline = box.call("intelligence_outline", {"capture_id": capture_id})
hits = box.call("intelligence_search", {
    "capture_id": capture_id, "query": "Total revenues", "limit": 10
})
part = box.call("intelligence_part", {
    "capture_id": capture_id, "unit_id": hits["hits"][0]["unit_id"],
    "max_chars": 12000, "max_rows": 40
})
```

Supported originals: HTML, XML, PDF and plain text. HTML includes a full visible
text fallback so structural navigation does not silently discard mixed markup.
Preformatted legal labels are heuristic sections, explicitly identified in the
outline; a table of contents remains evidence text, not a substantive provision.
XML identifiers and HTML XPath locations are retained. PDF pages use one-based
page numbers. Text offsets refer to the normalized unit text, not raw bytes.
Every result binds the original raw hash, capture time and parser version.

Defaults: 100 PDF pages, 40 outline entries, 12,000 read characters and 40 table
rows. Explicit limits: 500 pages, 100 entries/hits, 50,000 characters and 200 rows;
decoding is bounded at 32 MB. Incomplete wire responses and tampered originals
are rejected. PDF parsing is local and lazy by page; scanned PDFs need a separate
OCR capability. Page/table bounds are not a hard CPU timeout. Linux memory and
cancellation behavior still require validation before production integration.

HTML table output retains header cells and spans without numeric conversion.
PDF table extraction is heuristic; tables may contain missing or misaligned
cells. Inspect units, columns and original page layout before relying on values.
No newly installed library, model call, API credential or paid search is needed.

Validation: 55 focused offline tests passed. Six existing originals were replayed
with network connections blocked; all selected parts were readable. Tesla page
27 text was compared with the previously saved page image. Raw hashes and the
pilot ledger stayed unchanged (23 reserved HTTP attempts of 25).
See `NAVIGATION_VALIDATION.json`; this is reader validation, not measured target
relevance, recall improvement or forecasting accuracy. Original failed capture
statuses remain unchanged even when intact bytes can be read offline.


# No-key news, statistics and government originals (v0.6)

Five typed source contracts add four channels through `intelligence_fetch`.
The tool count remains fourteen; the catalog now contains 23 sources.

| Source ID | Retrieval | Required parameters |
| --- | --- | --- |
| `gdelt_news` | Article-list JSON leads, not full article text | `query`; optional `maxrecords` 1..50, relative `timespan`, or both UTC bounds |
| `dbnomics_series` | One exact series, native observations and provider/dataset metadata | `provider`, `dataset`, `series` |
| `bls_series` | One exact BLS series using no-key v1 GET | `series` |
| `govinfo_feed` | Recent collection RSS window | `collection`: BILLS, PLAW, FR, CHRG, CREC or DCPD |
| `govinfo_text` | Exact package public HTML rendition | `package`, copied from an official source |

```python
news = box.call("intelligence_fetch", {
    "source_id": "gdelt_news", "parameters": {
        "query": '\"Tesla\"', "maxrecords": 10, "timespan": "1week"
    }
})
data = box.fetch("dbnomics_series", {
    "provider": "INSEE", "dataset": "IPC-2015",
    "series": "A.IPC.SO.00.00.INDICE.ENSEMBLE.FE.SO.BRUT.2015.FALSE"
})
cpi = box.fetch("bls_series", {"series": "CUUR0000SA0"})
index = box.discover("https://www.govinfo.gov/rss/plaw.xml", "rss", limit=10)
# Inspect candidate URLs and select a zero-based original explicitly.
original = box.acquire_link(index["id"], 0)
law = box.fetch("govinfo_text", {"package": "PLAW-119publ21"})
```

## Provider and evidence limits

- GDELT news is discovery. Returned titles/URLs/indexing times are not article
  bodies or verified publication times. A saturated result window does not prove
  completeness. Keep at least five seconds between DOC API calls; an HTTP 429
  still consumes a reservation. There is no retry loop. Shared egress/provider
  traffic can limit access even when this toolbox makes very few calls. The
  caller must schedule retries/cooldowns; this adapter is not a global limiter.
- Explicit GDELT UTC bounds remove the default relative timespan; inconsistent
  date arguments are rejected before reservation. Search date filters do not
  establish historical body availability or prevent model knowledge leakage.
- DBnomics retains provider/dataset/series identity, original period/value arrays,
  missing markers, unit/dimension metadata and release information when present.
  Mismatched identity or arrays fail parsing. `latest` releases are rejected.
  Revised observations are not as-of publication vintages. No SDK is installed.
- BLS GET uses the provider default three-year window. Arbitrary year ranges
  require a separate POST adapter, not undocumented GET arguments. Preserve
  annual M13, string values, footnotes and seasonal-adjustment identity. A valid
  zero-observation envelope is `empty`, never usable numeric evidence. No-key
  access has a provider/IP daily quota independent of this task request ledger.
- GovInfo RSS paths are lowercase, while collections/package IDs retain native
  uppercase identity. RSS is a recent discovery window, not a complete archive.
  `govinfo_text` requests a public HTML rendition without api.govinfo.gov keys;
  not all packages have that rendition. The returned path must match the package.
  HTML availability does not establish enactment or commencement. Other formats
  can be selected from saved official discovery with an explicit budgeted read.

All calls reuse immutable raw captures, SHA-256 verification and the existing
persisted task cap. Failed HTTP/parse attempts count; cache hits preserve capture
identity/time. No model, Tavily or Exa calls occur. The standalone toolbox is not
registered in production workers: Pipeline must bind native quotas before use.

Run offline checks:

```powershell
python -m unittest ForecastAgent.test_intelligence_box ForecastAgent.test_intelligence_congress ForecastAgent.test_intelligence_navigation ForecastAgent.test_intelligence_discovery ForecastAgent.test_intelligence_public_channels -q
```

`PUBLIC_CHANNELS_VALIDATION.json` contains bounded live evidence and limitations.
The initial pilot and explicitly corrected follow-up have separate frozen
ledgers; neither existing task nor campaign quotas were reset. This validates
mechanics on selected public examples, not broad recall or forecast accuracy.

Live validation: 12 physical HTTP attempts across frozen 6/4/2-request trials,
9 usable captures and 3 preserved failures (one GDELT 429 and two initially
incorrect uppercase RSS 404s). Corrected RSS and a later explicitly spaced
GDELT probe succeeded. GDELT returned 5 leads, DBnomics 36 observations, BLS 34
CPI observations and GovInfo 99 feed entries. The GovInfo RSS discovery includes
separate original-rendition candidates extracted from its native description;
`intelligence_acquire_link` revalidates them against the original raw feed.
One such public-law HTML body yielded 1,586 readable characters with parent
hash/link/date binding; a separate exact Public Law 119-21 text also downloaded.
All 12 raw SHA-256 hashes matched. No broad recall, latest-publication coverage,
Linux runner or forecasting-quality claim is made. 79 focused tests passed.

# Agent invocation skill and acceptance

The repository-owned, model-independent entry point is
[skills/intelligence-acquisition/SKILL.md](skills/intelligence-acquisition/SKILL.md).
It routes acquisition versus offline reading and documents actual result states,
continuations, configuration, original-rendition selection and immutable budgets.
Supporting references contain domain routes, fourteen executable call shapes and
current JSON function schemas. Loading the skill does not register tools: the host
must expose TOOL_DEFINITIONS and dispatch with the existing task-scoped box.call.

Agent dispatch now checks required arguments, known fields, exact types, enums
and numeric/length bounds before execution. The catalog schema includes news.
Legacy HTML link/table and CLML extraction reject failed/incomplete originals;
explicit navigation remains available for intact HTTP-200 parser-recovery cases.

86 focused tests pass, including a fixture-backed JSON execution trace of all
fourteen functions. Six saved public pilots were replayed offline:58 captures,
57 available original hashes verified,49 accepted captures reparsed with zero
parser failures. One earlier UK-bill transport failure has no raw body. Original
capture files and all six ledgers stayed unchanged. No new HTTP, model, Tavily or
Exa calls were needed. Rotated PDF/table gaps are retained. See AGENT_ACCEPTANCE.json
and the skill's references/validation.md for commands and remaining Linux/native
integration gates. This does not claim production deployment or universal accuracy.


# Native Capability export

The latest inspected development host uses a repository Capability registry,
code-controlled handlers, native cumulative budgets and frozen task identity.
`compatibility.py` exports twelve matching declarations with required need IDs,
network/material effects, native HTTP categories and URL/capture navigation.
It grants no allowance, registers nothing automatically and imports the host
Capability lazily. The standalone fourteen-function interface remains available.

The newer discovery/download pair is explicitly pending and hidden in native
mode, preventing accidental classification as zero-cost local reads. Five typed
new channels already work through intelligence_fetch. Native reference handles
must come from the host adapter, not guessed normalized-unit offsets.

90 standalone tests and 48 isolated latest-host overlay tests passed. All transport
was mocked; peer/production checkouts were untouched. The tests check native shared
caps, five source adapters, persistence/replay, 429 and pending-tool exclusion.
See NATIVE_COMPATIBILITY_VALIDATION.json and the skill native-compatibility
reference for atomic registration handoff and remaining gates.
