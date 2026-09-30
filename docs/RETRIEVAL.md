# Retrieval evidence bundles

Ultra plans and extracts evidence; Tavily basic discovers URLs; free HTTP fetching saves original response bytes (base64), hashes and readable text. This module does not forecast or submit anything.

## Collect and replay

Create a JSON object containing `question`, `resolution_criteria`, optional `fine_print`/`background`, and `mode` (`live`, `historical_exploratory`, or `historical_strict`). Historical modes require `as_of_utc` with timezone. Do not include outcome labels or community probabilities.

```
python -m scripts.retrieval_agent --input question.json --task-dir snapshots/retrieval/task-123
python -m scripts.retrieval_agent --task-dir snapshots/retrieval/task-123 --replay
```

Collection uses `TAVILY_API_KEY` and `OPENROUTER_API_KEY`. Replay reads only local files and requires no keys. Use the SAME task directory for retries; changing inputs there is rejected. A new directory is a new task, not a retry. Never delete bundle files to recover budget.

## Hard resource gates

- At most **3 Tavily basic network attempts per task**, with no search retries. Each reservation is saved before the request. Failures and interrupted reservations consume budget.
- Model cannot select depth or override cutoff. Each search retains up to 10 new canonical URLs.
- Ultra chooses `topic=general/news/finance`, up to ten bare official `include_domains`, `include_domains_mode=prefer/restrict`, and `exact_match` for each attempt, explaining the choices in its reason. Prefer preserves wider coverage; restrict requires domains and returned URLs are also checked locally against that domain boundary. Exact matching requires a double-quoted entity/phrase in the query. Domain choice is a model assessment, not verification of official ownership. Empty results do not trigger automatic fallback calls. Parameters are saved before the request alongside the durable attempt. Depth stays basic, automatic parameter selection is disabled, and cutoff stays fixed. Existing callers without these options retain general search defaults.
- Up to 8 page fetch attempts, including failures. Successful pages are reused.
- After free fetching fails, Ultra may select important pages for ONE basic Extract batch of up to FIVE URLs. Accepted search URL, prior failed free fetch, existing evidence need and an importance reason are required. Cached and temporally quarantined pages are ineligible. No automatic Extract retry; failed/crashed reservations consume the batch across restarts. This separate allowance does not increase the three-search limit.
- Extract requests force `extract_depth=basic`, omit query reranking to preserve full context, and retain both results and failed_results, provider response and usage. Tavily bills basic Extract at 1 credit per 5 successful URLs; reported usage can be zero before accumulated successes reach five. Zero usage never restores the batch allowance. The per-task allowance is at most five submitted URLs (one nominal credit of basic extraction), not a guarantee of an isolated account billing increment.
- Extract snapshots explicitly identify vendor-produced Markdown, not original HTTP bytes. Their hash proves the saved extracted text, not the authenticity of source HTML. Historical strict mode forbids Extract; exploratory captures keep the current-body warning and cannot establish clean historical sufficiency.
- Exclusive task lock prevents concurrent collectors. After a process crash, inspect the bundle and confirm the process is dead before removing its lock. Existing reservations remain charged.
- Monitor task directories use stable question IDs. State artifacts include retrieval ledgers. Missing latest state stops the workflow rather than restoring an older budget. Workflow concurrency serializes runs.

## Evidence and audit

Ultra first freezes critical/useful evidence needs, expected authorities and proposed queries. Each search references a need and explains the missing information. Fetched text must contain every recorded supporting quote. Formal evidence includes stance, event time, source dates, retrieval timestamp, quality reasons and original-source grouping. Search snippets are leads only. Coverage records which facts support each need; semantic relevance and source independence remain model assessments.

`bundle.json` retains the request, plan, raw search responses, accepted hits, quarantined hits, page failures, page snapshots, tool transcript, evidence, coverage, gaps/conflicts and resource counts. Completion is `sufficient`, `partial`, `conflicted`, or `failed`. Sufficient requires all critical needs covered and no declared gaps/conflicts; it does not imply a correct forecast.

## Historical limits

The fixed Tavily `end_date` is the day before the UTC cutoff, conservatively excluding its entire calendar day. Unknown or later publication dates are quarantined before the model sees them. The raw search response is retained for audit but not supplied to Ultra.

Exploratory mode can fetch today's body of an old article, clearly labeled `current_capture_possible_later_edits`. Updated time is unknown unless independently established. It is NOT a clean historical backtest.

Strict mode refuses live page fetching. An optional `historical_snapshot_bundle` input points to a previously saved LOCAL bundle: imports require pre-cutoff capture timestamps, original response bytes, matching SHA256 and readable text. These pages can be read without searches. Capture timestamps rely on the supplied bundle's provenance, not independent notarization. Automatic web archive lookup is not implemented; without suitable captures strict mode returns insufficiency. Publication filtering alone cannot establish old page versions or remove model-knowledge leakage. Date claims in model output do not override program eligibility.

Do not use retrieval outcome accuracy as its only quality measure: verify budget, citation accuracy, critical coverage, provenance, source independence and temporal leakage instead. Mocked tests exercise these without consuming Tavily credits.
