# Collection v2 pilot

The fixed five-question pilot uses BTC prices, an AI browser release, an SEC
filing, ocean temperature and the World Cup. None of its five IDs were in the
previous captured-page index. It uses the same Ultra model and collection-only
engine, without outcome labels or prediction submission.

Only new requests explicitly selecting `acquisition_profile=collection_v2` receive
the frozen five-attempt Tavily basic limit. Existing ledgers retain three. Calls
four and five require `recent` or `official_gap`; failures and reservations count.
A `recent` search has a program-owned 60-day start bound and the original cutoff
end bound. At least one such search is required before historical-pilot closure.

Date filtering alone failed the pilot: undated Wikipedia outcomes, localized
release notes and later change logs reached the model. Historical v2 tasks now
default to `historical_body_policy=verified_snapshots_only`: current HTML is saved
for audit, but its body cannot be read, located or excerpted. Search hits are
URL/date discovery leads in the model view. Local pre-cutoff captures or exact
Wayback replays are required for usable bodies. Model knowledge and unaudited
question rules remain uncontrolled; this is not a clean backtest. An explicit
`date_filtered_exploratory` policy retains the weaker behavior for diagnostics.
Old completed packages are never retroactively declared clean. An unfinished old
model context without a frozen body policy cannot resume as cutoff-safe.

`read_sources` batches up to four accepted free reads and locates complete
paragraphs for up to eight queries. Cached pages cost no fetch attempts.
`record_excerpts` accepts up to eight returned `passage_id` values with `need_ids`.
The program resolves coordinates and checks the saved source version. IDs survive
restarts; changed source versions invalidate them.
Lexical association is not semantic coverage or fact-checking. Full results stay
in the transcript; model replies omit raw payloads/links and bound previews.
The compact reply declares truncation and points to targeted local reading.

The improvement target is 8-12 model HTTP calls per question, compared with 18
in the previous mixed-quality five-question trial. Provider retries still count.
Different questions mean a topic-coverage pilot, not a controlled A/B experiment.
The test has its own durable Actions state and is automatically mirrored into
the local data store. Rerunning completed pilot tasks never renews their budgets.

## Structured data and archived pages

`list_dated_datasets`, `collect_dataset` and `read_dataset_rows` expose Binance
BTCUSDT daily OHLC, Climate Reanalyzer North Atlantic daily SST and SEC issuer /
submissions data. Each physical HTTP attempt shares the existing eight-fetch
ledger. Cache hits do not spend calls; failed requests remain reserved. Original
responses, normalized rows, units, date ranges, withheld counts, pagination and
revision caveats are retained. Large histories have bounded text previews and
local row pagination.

Exchange candles must close before the cutoff; BTCUSDT is not a USD price proof.
SST raw arrays use day-of-year coordinates in each actual year. Non-leap padding
is omitted. Preliminary / climatology series are not observations. A conservative
15-day availability delay is applied to finalized SST rows, but does not prove
the historical publication or revision vintage. Both adapters return current
vintages with date filters; `historical_strict` refuses them.

SEC requires `SEC_USER_AGENT` containing the operator's real contact email.
Configure it as a GitHub Actions secret. CIKs must come from saved issuer lookup
or a discovered SEC filing URL. Ticker lookup is not exhaustive for private
companies. Exact form, filing date and acceptance timestamp filters apply.
Historical submissions filenames must be returned by saved issuer metadata;
their retrieval consumes another HTTP attempt. Empty results never prove absence.

`collect_archive` requires a catalog URL and two remaining HTTP attempts. The CDX
index and exact replay are reserved separately. Missing captures, a later index
timestamp or replay redirects fail closed; no current-page fallback is allowed.
Archive timestamps rely on the archive operator, not independent notarization.

Run `python -m ForecastAgent.verify_collection_repairs` for free, read-only smoke
checks. It makes no Ultra or Tavily requests and reports unavailable channels
explicitly. The pilot's five completed ledgers are not reopened by this command.
