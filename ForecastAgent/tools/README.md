# Tools and acquisition channels

`registry.py` owns model-facing JSON schemas. `channels.py` owns the implemented capability catalog and shared `tool_result_v1` response envelope. Entries describe cost, credentials, formats, limits and temporal support; they are not source reliability ratings.

| Layer | Responsibility |
|---|---|
| tools | Tool contracts and channel metadata |
| providers | Tavily, free HTTP, Yahoo/ALFRED and Ultra transport |
| readers | Local parsing and saved-text navigation |
| evidence | Document metadata, snapshots and intelligence export |
| runtime | Task locks, persisted reservations, source admission and recovery |

Network attempts are reserved before provider calls. Credentials and limits are injected by the program, not chosen by the model. Readers do not perform network requests.

Parsed documents retain `page_content` and metadata, including PDF pages or CSV rows. Local read/search results identify their coordinate space; offsets are not raw byte offsets or original HTML positions.

Collection exposes source acquisition, exact excerpt recording and raw export. Legacy review remains isolated for compatibility. A collected package is not a verified answer.

The interface draws on structured tools and document loaders without adding LangChain or LangGraph. RSS/Atom entry reading is available through free fetching. Generic XML, OCR, spreadsheets, table reconstruction and browser rendering are not implemented channels.

`record_quote` computes offsets for an exact copied passage; repeated text requires an explicit one-based occurrence. `search_saved_text` and lexical `find_passages` return reusable `excerpt_args`. These operations preserve source versions and never infer factual support.

`collection_checkpoint` provides bounded next-tool suggestions each turn. The acquisition agent selects which channels to use or records a concrete deferral with `record_channel_decision`. No suggestions invoke providers automatically or renew budgets. `select_sources` associates accepted links with acquisition needs. Question URLs, search hits and selected links count as unread reading leads; unselected outbound links remain in the raw inventory separately.
