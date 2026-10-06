# ForecastAgent

For the patched production release and its immutable entrypoint, see
[release 1.0.3](../RELEASE_1_0_3.md). Its analysis core remains release 1.0.1.

For the release-based, acquisition-only development plan with frozen analysis,
see [Acquisition V2 design](docs/ACQUISITION_V2_DESIGN.md). This is an experimental branch;
release 1.0.3 promotes the bounded acquisition candidate to production. The opt-in
[Intelligent acquisition V1](acquisition/README.md) implements material planning,
version-bound gap assessment, independent supplement and a compatible package.

The official competition worker is pinned to release 1.0.3: patched V3 acquisition → independent supplement → unchanged release 1.0.1 Mercury original-evidence conditional rereading → validated automatic submission. The machine-readable policy is [1.0.1.json](releases/1.0.1.json).

For bounded current-information queues of up to 100 questions, see [collection campaigns](docs/COLLECTION_CAMPAIGNS.md). Closed packages with gaps are reported separately from complete acquisition; these campaigns do not submit forecasts.

For classified retries, circuit breakers, raw acquisition states and offline batch validation, see [bounded batch operation](docs/BATCH_COLLECTION.md).

For original-response acquisition with interpretation deferred to later analysis, see [raw recall](docs/RAW_RECALL.md). Fresh-budget comparison experiments require explicit authorization and preserve earlier ledgers.

An information acquisition agent with a model-independent execution protocol and a separate [saved-evidence analysis pilot](analysis/README.md). The latest [referenced analysis protocol](analysis/REFERENCED.md) uses fixed evidence IDs, local saved-body reading, condition coverage and durable review state. The pilot uses Ultra for structured evidence analysis and Mercury Decide for typed event probabilities, without performing new searches or submitting forecasts. Codex operates and maintains the repository; the runtime has no Codex SDK, host skill directory or login dependency.

The [runtime contracts](docs/RUNTIME.md) define preflight tool validation, bounded
model context with persistent loaded skills, durable progress measurements and
explicit session recovery/termination states. None of these mechanisms resets
existing task quotas or submits forecasts.

## Acquisition pipeline

New tasks default to `collection`. The acquisition agent plans information needs, chooses tools, reads sources and stores exact excerpts. Completion exports `intelligence.json` without a fact-check, probability, verdict or fused score. Saved pages alone are valid output. Search snippets remain discovery leads.

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
python -m ForecastAgent acceptance --task-dir snapshots/retrieval/question-123
python -m ForecastAgent refresh --task-dir snapshots/retrieval/question-123 --urls https://example.org/saved-source
python -m unittest discover -s ForecastAgent/tests
```

Input requires `question` and `resolution_criteria`. Live collection uses `"mode": "live"`; historical modes additionally require `as_of_utc`. Runtime credentials are `OPENROUTER_API_KEY`, `TAVILY_API_KEY` and `EXA_API_KEY`. New collection tasks require one bounded Exa discovery attempt before normal completion; an unavailable key is an explicit acquisition gap. Existing ledgers retain their frozen policies and budgets. See [Exa integration](docs/EXA.md). Catalog, inspection, replay, export and local reading require no credentials. The default acquisition model is `nvidia/nemotron-3-ultra-550b-a55b:free`; set `FORECAST_MODEL` to select a compatible OpenRouter backend. The raw campaign enables `FORECAST_MODEL_FALLBACK_SUPER=1`: two consecutive service failures select free Super for the rest of that dispatch, without resetting any quotas. Existing campaigns require an audited model migration. See [model-independent acquisition](docs/MODEL_INDEPENDENT_ACQUISITION.md) for generic prompts, effective clocks, reading gates and separate transport budgets.

## Tools and output

The independent [local data synchronizer](docs/LOCAL_DATA.md) pulls Actions
artifacts into `E:\metaculus_data`, retaining immutable archives and a structured
SQLite index. Its Windows schedule does not acquire evidence or submit forecasts.

- `list_channels`: supported channels, formats, credential names and limits.
- `search_tavily`: general, news or finance discovery with optional official-domain and exact-entity targeting.
- `search_exa`: optional independent metadata discovery, one frozen attempt per new enabled task.
- `fetch_page` / `fetch_pages`: free reading, including automatic Yahoo/ALFRED adapters for recognized discovered URLs.
- `extract_failed_pages`: basic Extract rescue for eligible failed free reads.
- `list_documents`: paginated document/page/row inventory.
- `read_document`: bounded local reading with character coordinates and continuation offsets.
- `search_saved_text`: paginated, case-sensitive literal search in saved documents.
- `record_excerpt`: exact source slices with character/page/row provenance and need IDs, without a truth claim.
- `finish_collection`: captured material and explicit gaps, without requiring an audit.
- `list_official_datasets` / `collect_official`: BLS CPI, unemployment and payroll series, Treasury debt data, and Federal Register document discovery.
- `collect_polymarket` / `read_market_snapshot`: save current Gamma responses and inspect saved child contracts without declaring equivalence.
- `refresh_sources`: update saved live sources in the same ledger, preserve old versions, and report new/changed/unchanged content.
- `record_quote`: copy an exact passage; the program locates it and preserves its source version. No guessed coordinates.
- `find_passages`: lexical navigation of saved text with reusable excerpt coordinates; no search API calls.
- `collection_checkpoint`: bounded acquisition gaps and next-tool suggestions; The acquisition agent chooses the next tool or explicitly defers a channel.
- `select_sources`: maintain a reading list without treating every outbound navigation link as required reading.
- Free RSS/Atom reading preserves publisher dates, entry metadata, links and original bytes. PDF magic-byte detection handles mislabeled PDF responses.
- `collection_acceptance`: check capture integrity, exact excerpt coordinates, resource limits and acquisition gaps, without judging truth.

Agent-facing results use `tool_result_v1`: `tool`, `ok`, `status`, `data`, `error`, `provenance` and `budget_remaining`. Provider-native responses stay in the ledger. The channel catalog is frozen per task. See [tool architecture](tools/README.md) and [collection contract](docs/COLLECTION.md).

## Layout

`tools/` contains schemas and channel definitions; `providers/` contains search, download, structured data and model transport; `readers/` contains local parsing and navigation; `evidence/` contains documents, snapshots and export; `runtime/` owns orchestration and budgets. Flat modules, root modules and `scripts/` are compatibility entry points. All maintained code, skills, fixtures and tests live here.

Skills are loaded on demand and frozen with their content hashes in each ledger. They guide source acquisition and cannot override program limits. Collection mode excludes evidence-review tools and the evidence-review skill. Domain skill instructions are subordinate to the collection-only system instruction.

## Limits

The manual [historical batch campaign](docs/HISTORICAL_BATCH.md) freezes 135 blind
questions and supports batches of at most five, durable resume, model transport
records, temporal provenance and portable archives. It never loads outcome labels
or submits forecasts. Initialization alone makes no provider calls.

- At most **three Tavily basic attempts per task**, including failures and resumed runs.
- Initial collection has eight free HTTP attempts shared by page reads, official adapters and Polymarket; one basic Extract batch with at most five URLs. Failures consume attempts.
- Local reading and export do not spend search or fetch budget.
- Task input, pipeline, skill versions and budget state survive restarts.
- Historical strict requires pre-cutoff captures; publication filters do not restore old pages or remove model knowledge leakage.
- Readers expose bounded parsed text and report truncation. PDF OCR, table reconstruction and dynamic browser rendering are not implemented.
- Current Polymarket and official adapters refuse historical modes. Yahoo/ALFRED retain their existing cutoff-aware behavior.
- Official discovery and Polymarket pagination are explicit and bounded; no automatic extra requests. BEA, SEC-specific and general official-site adapters are not implemented.
- After completion, saved live sources have a separate update budget: three free HTTP attempts per UTC day and 24 per task lifetime. Before completion, refresh uses the original eight attempts. Updates never renew Tavily's three lifetime basic searches or the single Extract batch. Updates are operator initiated.
- Interrupted tasks resume the original ledger. A dead local process or a lock restored from a verified completed Actions run can be recovered; an active or unknown owner remains blocked. The monitor retries incomplete tasks with backoff, stops after five failed attempts, and never labels an incomplete result as researched.
- The manual `Three live Ultra collection trials` workflow freezes three unresolved questions outside the restored monitor inventory, restores their cumulative budgets on reruns, and exports source captures without forecasts.
- Acquisition does not submit forecasts or trades. Legacy forecasting templates contain publication code and are not called by this interface.

Actions and command-line operators use the same engine and snapshot paths. Codex does not need to remain online. GitHub may delay scheduled runs.

See [adapter and incremental collection details](docs/ACQUISITION_CHANNELS.md). Acceptance reports are technical checks, not reliability or forecast scores.

## Acquisition design references

- [nostreambot architecture](https://github.com/No-Stream/nostreambot-metaculus-bot/blob/main/docs/architecture.md): separate designated resolution-source reading, market snapshots and bounded gap-filling; retain failures and raw artifacts. ForecastAgent adapts these acquisition ideas with the acquisition agent and the existing three-search limit.
- [TradingAgents changelog](https://github.com/TauricResearch/TradingAgents/blob/main/CHANGELOG.md): explicit vendor availability, bounded tool rounds, source settings and point-in-time disclosure. ForecastAgent uses explicit channel decisions and capture provenance; provider failures never establish event absence.

These are design references, not copied modules or a claim of tournament performance. Browser rendering, OCR, SEC/BEA-specific adapters and automatic update scheduling remain future work. The acquisition checkpoint guides tool selection without forcing unnecessary paid calls. Autonomous use of the new guidance still requires a scoped live acquisition trial; cached completed tasks are never reopened to renew budgets.
# Latest collection runtime

See [single-case debugging](docs/DEBUGGING.md) for resuming one existing task,
offline compressed-body repair and immutable provider-budget checks.

See [Collection v3](docs/COLLECTION_V3.md) for free main-text/table extraction,
bounded local OCR, exact passage navigation, shared live caching, context
projection, date-bounded official data and acquisition metrics. New monitored
tasks retain three Tavily basic attempts. Polling and collection use separate
Actions workflows; the local freshness watchdog only dispatches overdue GitHub
polls and never runs collection locally.

See [Tool delivery protocol](docs/TOOL_DELIVERY.md) for confirmed model-visible
reading receipts, exact continuation ranges, legacy restore migration, and the
audited lifecycle of acquisition requirements. The protocol is independent of
the model and the source channel.
