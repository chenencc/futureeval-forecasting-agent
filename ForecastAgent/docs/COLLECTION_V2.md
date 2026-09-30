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

Search summaries with explicit English/ISO dates on or after the cutoff are
quarantined. Dated lines in captured text are conservatively hidden from model
views and cannot be banked as excerpts. Original parsed text and raw bytes remain
untouched. Character offsets are preserved. The filter also blocks explicitly
dated future schedules; distinguishing valid historical announcements requires
separate provenance. Undated later edits, localized date formats, model knowledge
and unaudited question rules remain leakage risks. This is not a clean backtest.

`read_sources` batches up to four accepted free reads and locates complete
paragraphs for up to eight queries. Cached pages cost no fetch attempts.
`record_excerpts` validates up to eight exact slices independently in one turn.
Lexical association is not semantic coverage or fact-checking. Full results stay
in the transcript; model replies omit raw payloads/links and bound previews.
The compact reply declares truncation and points to targeted local reading.

The improvement target is 8-12 model HTTP calls per question, compared with 18
in the previous mixed-quality five-question trial. Provider retries still count.
Different questions mean a topic-coverage pilot, not a controlled A/B experiment.
The test has its own durable Actions state and is automatically mirrored into
the local data store. Rerunning completed pilot tasks never renews their budgets.
