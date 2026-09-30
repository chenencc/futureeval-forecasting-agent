# Retrieval evidence bundles

Ultra plans and extracts evidence; Tavily basic discovers URLs; free HTTP fetching saves original response bytes (base64), hashes and readable text. This module does not forecast or submit anything.

## Retrieval v3 improvements

Implemented in the following order, inspired by No-Stream's retrieval modules and forecasting-tools' source archive (design ideas, no vendored code):

1. **Real source catalog.** Resolution criteria, fine print and background seed URLs; accepted search hits and actual outbound links from saved HTML extend the catalog. `list_sources` exposes it without network cost. Fetches still validate public targets and every redirect; Metaculus and credentialed URLs remain excluded. A discovered URL is a lead, not proof of source ownership or historical availability. Old saved HTML without captured link metadata is not silently reparsed or refetched.
2. **Batch collection and evidence.** `fetch_pages` accepts at most five catalog URLs, preserving per-URL reservations/failures; `record_evidence_batch` accepts at most eight facts, independently checked and saved. A partial batch keeps successful work. `fetch_page(start_char=...)` reads an 18,000-character window from cached full text, with `next_start`; pagination costs no extra request. Free PDF extraction uses pypdf, page markers and byte/page/stream caps. Scanned PDFs fail explicitly rather than pretending OCR succeeded.
3. **Structured financial sources.** Yahoo quote/history URLs automatically route to the chart JSON endpoint with explicit dates, a 390-day lookback and conservative exclusion of the cutoff UTC day. This can exclude some already-known same-day data but avoids treating a morning bar timestamp as publication of its close. Current-vintage/adjusted data is still not a verified historical snapshot. FRED/ALFRED series URLs route to a pre-cutoff ALFRED CSV vintage, accepting the response only when its value-column label confirms that vintage. No current-series substitution or historical Extract fallback on a failed financial adapter. Strict historical mode forbids live adapters too. CSV and JSON bytes are saved along with rendered rows. Yahoo was checked against a live free endpoint; the ALFRED live probe timed out, so vintage routing is verified with mocked responses but its current network availability is unconfirmed.
4. **Loop control.** Exact repeat search/fetch/Extract requests are blocked by durable signatures. Three consecutive tool failures force a closing phase. The last two of 24 model turns are reserved for audit/conclusion. Error replies list real next URLs. Budgets and control state survive restarts; no alternate model or automatic search fallback is introduced. A closing model that ignores its tool instruction cannot issue external research requests; interruption still returns a partial/failed bundle with explicit incompleteness.
5. **Identity, time and support scope.** The plan includes a subject/identity/document-form/announcement-window card. ISO event dates and observational quoted dates are checked against the cutoff; future effective dates require a separate known announcement date (`time_role=future_schedule`). An absence/only claim whose quote contains no absence language is rejected conservatively; this heuristic does not prove entailment when absence language is present. `audit_evidence` asks Ultra to review every fact for identity, full-claim support and timing. Rejected facts remain for audit but stop contributing to coverage. A complete rejection means failure. The public result summary for incomplete or historically unverified retrieval is assembled from accepted facts and explicit gaps; the original model summary is retained separately as `model_summary`.

Ultra's audit is self-review, not an independent fact-check or calibrated quality probability. It cannot establish that a page's historical version existed from an old publication date. Generic prose can still contain later edits; use verified snapshots for strict historical work. The date checks are conservative and format-limited, and do not replace checking announcement versus effective time. Provider data availability and revisions remain explicit limitations.

## Collect and replay

Create a JSON object containing `question`, `resolution_criteria`, optional `fine_print`/`background`, and `mode` (`live`, `historical_exploratory`, or `historical_strict`). Historical modes require `as_of_utc` with timezone. Do not include outcome labels or community probabilities.

```
python -m ForecastAgent.retrieval_agent --input question.json --task-dir snapshots/retrieval/task-123
python -m ForecastAgent.retrieval_agent --task-dir snapshots/retrieval/task-123 --replay
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
