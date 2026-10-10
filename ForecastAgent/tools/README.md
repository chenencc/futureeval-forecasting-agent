# Tools and acquisition channels

`capabilities.py` provides the common capability registry and dispatch boundary.
`registry.py` retains compatible native JSON schemas. `channels.py` retains the
native channel catalog and shared `tool_result_v1` envelope. Source-specific
adapters live in `ForecastAgent/channels`; provisional reasoning lives in
`research_loop`. Metadata describes cost, credentials, formats, limits and temporal
support; it is not a source reliability rating.

| Layer | Responsibility |
|---|---|
| tools | Tool contracts and channel metadata |
| providers | Search, HTTP, financial data and configurable model transport |
| readers | Local parsing and saved-text navigation |
| evidence | Document metadata, snapshots and intelligence export |
| runtime | Task locks, persisted reservations, source admission and recovery |

Network attempts are reserved before provider calls. Credentials and limits are injected by the program, not chosen by the model. Readers do not perform network requests.

Parsed documents retain `page_content` and metadata, including PDF pages or CSV rows. Local read/search results identify their coordinate space; offsets are not raw byte offsets or original HTML positions.

Collection exposes source acquisition, exact excerpt recording and raw export. Legacy review remains isolated for compatibility. A collected package is not a verified answer.

The interface uses structured tools and document loaders without requiring
LangChain or LangGraph. RSS/Atom reading is available through free fetching.
Optional Crawl4AI rendering and official XML/PDF navigation have explicit
policies. OCR, arbitrary spreadsheet interpretation and semantic table
reconstruction are not provided by this integration.

`record_quote` computes offsets for an exact copied passage; repeated text requires an explicit one-based occurrence. `search_saved_text` and lexical `find_passages` return reusable `excerpt_args`. These operations preserve source versions and never infer factual support.

`collection_checkpoint` provides bounded next-tool suggestions each turn. The acquisition agent selects which channels to use or records a concrete deferral with `record_channel_decision`. No suggestions invoke providers automatically or renew budgets. `select_sources` associates accepted links with acquisition needs. Question URLs, search hits and selected links count as unread reading leads; unselected outbound links remain in the raw inventory separately.

## Native channel integration

See [the channel integration contract](../channels/README.md). The development
entry point is `intelligence.pipeline.collect(..., channel_tools=True)`. Existing
requests retain their original policy; enabling new tools is not a ledger reset.
