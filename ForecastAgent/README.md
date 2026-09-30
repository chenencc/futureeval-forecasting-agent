# ForecastAgent

An independent Ultra-led information acquisition agent. Codex operates and maintains the repository; the runtime has no Codex SDK, host skill directory or login dependency.

## Acquisition pipeline

New tasks default to `collection`. Ultra plans information needs, chooses tools, reads sources and stores exact excerpts. Completion exports `intelligence.json` without a fact-check, probability, verdict or fused score. Saved pages alone are valid output. Search snippets remain discovery leads.

Existing ledgers without a pipeline field retain `legacy` behavior and budgets. A request may explicitly specify `"pipeline": "legacy"` for old regression experiments. Completed tasks return their saved results; upgrades do not restart searches. Existing and interrupted ledgers can be exported without a model or network call.

## Run from the repository root

```sh
python -m pip install -r ForecastAgent/requirements-retrieval.txt
python -m ForecastAgent channels
python -m ForecastAgent skills
python -m ForecastAgent run --input question.json --task-dir snapshots/retrieval/question-123
python -m ForecastAgent inspect --task-dir snapshots/retrieval/question-123
python -m ForecastAgent replay --task-dir snapshots/retrieval/question-123
python -m ForecastAgent export --task-dir snapshots/retrieval/question-123
python -m unittest discover -s ForecastAgent/tests
```

Input requires `question` and `resolution_criteria`. Live collection uses `"mode": "live"`; historical modes additionally require `as_of_utc`. Runtime credentials are `OPENROUTER_API_KEY` and `TAVILY_API_KEY`. Catalog, inspection, replay, export and local reading require no credentials. The model remains `nvidia/nemotron-3-ultra-550b-a55b:free`.

## Tools and output

- `list_channels`: supported channels, formats, credential names and limits.
- `search_tavily`: general, news or finance discovery with optional official-domain and exact-entity targeting.
- `fetch_page` / `fetch_pages`: free reading, including automatic Yahoo/ALFRED adapters for recognized discovered URLs.
- `extract_failed_pages`: basic Extract rescue for eligible failed free reads.
- `list_documents`: paginated document/page/row inventory.
- `read_document`: bounded local reading with character coordinates and continuation offsets.
- `search_saved_text`: paginated, case-sensitive literal search in saved documents.
- `record_excerpt`: exact source slices with character/page/row provenance and need IDs, without a truth claim.
- `finish_collection`: captured material and explicit gaps, without requiring an audit.

Agent-facing results use `tool_result_v1`: `tool`, `ok`, `status`, `data`, `error`, `provenance` and `budget_remaining`. Provider-native responses stay in the ledger. The channel catalog is frozen per task. See [tool architecture](tools/README.md) and [collection contract](docs/COLLECTION.md).

## Layout

`tools/` contains schemas and channel definitions; `providers/` contains search, download, structured data and model transport; `readers/` contains local parsing and navigation; `evidence/` contains documents, snapshots and export; `runtime/` owns orchestration and budgets. Flat modules, root modules and `scripts/` are compatibility entry points. All maintained code, skills, fixtures and tests live here.

Skills are loaded on demand and frozen with their content hashes in each ledger. They guide source acquisition and cannot override program limits. Collection mode excludes evidence-review tools and the evidence-review skill. Domain skill instructions are subordinate to the collection-only system instruction.

## Limits

- At most **three Tavily basic attempts per task**, including failures and resumed runs.
- Eight new free fetch attempts; one basic Extract batch with at most five URLs.
- Local reading and export do not spend search or fetch budget.
- Task input, pipeline, skill versions and budget state survive restarts.
- Historical strict requires pre-cutoff captures; publication filters do not restore old pages or remove model knowledge leakage.
- Readers expose bounded parsed text and report truncation. PDF OCR, table reconstruction and dynamic browser rendering are not implemented.
- Polymarket remains a standalone module, not an acquisition tool in this release.
- Acquisition does not submit forecasts or trades. Legacy forecasting templates contain publication code and are not called by this interface.

Actions and command-line operators use the same engine and snapshot paths. Codex does not need to remain online. GitHub may delay scheduled runs.
