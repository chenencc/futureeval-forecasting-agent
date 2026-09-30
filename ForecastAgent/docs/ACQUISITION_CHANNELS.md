# Market, government and incremental acquisition

## Polymarket

`collect_polymarket` searches active Gamma events using a short entity query and an explicit page (1-3). One request consumes one shared free HTTP attempt. The raw response, hash, endpoint, time, event/child relationships, rules, outcomes, token IDs, display prices and pagination are saved in `market_snapshots`. Candidate similarity still uses the full task question. Empty candidates do not establish that no market exists.

Identical queries reuse a cached snapshot. `refresh=true` creates a new snapshot linked to the previous one. `read_market_snapshot` lists saved contracts with pagination or reads a specific child contract without network calls. Display prices are not executable quotes. No candidate is approved for edge calculations, and historical modes refuse live market requests.

Reference: [Gamma search](https://docs.polymarket.com/api-reference/search/search-markets-events-and-profiles).

## Government data

`list_official_datasets` describes exact identifiers, labels, units and restrictions. `collect_official` supports:

| Dataset | Endpoint | Capture |
|---|---|---|
| BLS CPI | CUUR0000SA0 | CPI-U, not seasonally adjusted |
| BLS unemployment | LNS14000000 | Seasonally adjusted unemployment rate |
| BLS payrolls | CES0000000001 | Seasonally adjusted total nonfarm employment |
| Treasury debt | Fiscal Data Debt to the Penny | Up to 100 rows per explicit page |
| Federal Register | Document search API | Up to 20 document records per explicit page |

Raw JSON, provider metadata/messages, observed periods, footnotes, units and row coordinates are preserved. Unit labels come from the adapter catalog, not inferred model calculations. BLS defaults to its recent single-series window; no hidden pagination is performed. Treasury and Federal Register pages are explicitly requested, at most page 3 per query. Returned document links enter the source catalog for subsequent free reading.

Current captures do not establish historical release vintages. These new adapters refuse both historical modes. FederalRegister.gov is an informational rendition rather than the official legal edition; retain the linked official PDF. This stage does not decide whether a proposed rule is enacted or a filing means an IPO has occurred.

References: [BLS API](https://www.bls.gov/developers/api_signature_v2.htm), [Treasury dataset](https://fiscaldata.treasury.gov/datasets/debt-to-the-penny/debt-to-the-penny), [Federal Register API](https://www.federalregister.gov/developers/documentation/api/v1).

BEA is not implemented. SEC issuer/submission adapters are now available through `collect_dataset` and require `SEC_USER_AGENT`; see COLLECTION_V2.md. General official web pages can still use the discovered-URL reader.

## Incremental acquisition

`refresh_sources` accepts one to five distinct saved URLs in a live collection ledger. CLI refresh takes the same exclusive lock as the agent. There is no new task, budget reset, model call or Tavily search. Government sources reuse their stored adapter request; Yahoo/ALFRED reuse existing adapters. Failures preserve the old capture and consume their reserved attempt.

Changes are tracked using saved content hashes. Raw responses may change only because a response-time field changed: this is reported as `state=unchanged, raw_changed=true`. Distinct raw or parsed versions are retained so earlier excerpts remain resolvable. Old capture times are not rewritten as new availability dates. Ordinary sources support conditional HTTP validators; a 304 retains the original body and capture time while recording revalidation separately.

The initial shared cap is eight attempted HTTP acquisitions. After collection, the separate worker can refresh important open live sources after 12 hours within three update attempts per UTC day and 24 lifetime attempts. This does not provide indefinite updates after exhaustion. See COLLECTION_V3.md.

## Acceptance

`collection_acceptance` and the offline CLI `acceptance` check raw hashes, excerpt locations across preserved versions, attempt limits, truncation, failures, quarantine and unread source counts. Status is `accepted`, `accepted_with_gaps` or `failed`. These are technical output states, not truth or prediction scores. No absence conclusion follows from a failed search or an unread source.

## Verification on 2026-09-30

Live GET checks captured 32 rows for each of three BLS series, 100 Treasury rows and 20 Federal Register records. Incremental BLS recapture correctly distinguished unchanged data from changed response metadata. A separate real Metaculus Anthropic S-1 question captured five related market candidates and full contract rules; none was approved as equivalent. Both verification ledgers used zero Tavily and zero model calls.

Local regression tests cover historical refusal, shared budgets/cache/failures, official identifiers, row metadata, pagination, market snapshots, mechanical tampering checks, unchanged/changed source handling, retained excerpt versions and refresh locks. Reports are in `snapshots/acquisition-verification/` and `snapshots/polymarket-integration/`; raw captures are not committed to Git.
