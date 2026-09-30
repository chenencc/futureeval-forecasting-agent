# Collection contract

## Completion and export

`finish_collection` accepts only `gaps`. The program derives `collected`, `leads_only` or `empty` from stored pages and source leads. These states describe captured material, not completeness or truth. No audit, excerpt, probability or quality score is required.

The task keeps `bundle.json` as its durable ledger and writes `intelligence.json` on completion or interruption. `python -m ForecastAgent export --task-dir ...` regenerates the package offline without modifying the ledger. It includes the frozen input, plan, channel catalog, source leads, searches and raw responses, page captures and raw bytes, exact excerpts, failures, quarantine and resource counts. Export stays in the task directory.

Packages also include separate `market_snapshots`, `page_history`, incremental `updates` and a mechanical `acceptance` report. Old source versions remain available for previous excerpt coordinates. Raw response and parsed-document hashes distinguish source bytes from parser layouts.

## Navigation

`list_documents` returns up to 100 metadata records per request. `document_index` is one-based within a source, independent of PDF page or CSV row metadata. Omit it in `read_document` to read combined saved content; include it to read a specific parsed document. Maximum read size is 18,000 characters.

`search_saved_text` performs case-sensitive literal matching, returns up to 20 matches with bounded context, and supports an offset for subsequent matches. It searches saved parsed documents only. Original bytes beyond parsing/truncation caps are not searched. Scanned PDF content is unavailable without OCR.

`record_excerpt` derives text from start/end positions instead of trusting a model-supplied quote. The end is exclusive; maximum slice size is 4,000 characters. Excerpts retain coordinate space, page/row metadata, source hash, capture time, temporal status and need IDs. Repeated coordinates reuse the excerpt and merge need IDs. Source text is not independently verified.

## Compatibility

New ledgers default to collection; pre-existing ledgers lacking the pipeline field retain legacy behavior. Frozen input cannot be changed to reset budgets. Completed tasks are not rerun automatically. Provider functions and direct runtime execution retain native return formats; agent-facing messages and transcript results use the shared envelope.

## Acceptance

Tests cover raw-page completion without audit, result envelopes, persisted catalogs and budgets, zero-network reading/export, exact Unicode/page/row coordinates, pagination, truncation, interruption, recovery and rejection of analysis tools in collection mode. Earlier network provider checks remain separate from local navigation tests.
