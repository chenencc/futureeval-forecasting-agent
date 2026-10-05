# Unified acquisition

`collect(request, folder)` encapsulates the existing collection agent, deterministic
body inspection and exact deduplication, followed by independent enhanced repair.
It exports `analysis-input.json` and an audited `package.json` for any downstream
reviewer. Codex is an operator, not a runtime dependency.

## Stages

1. Original `run_research` preserves searches, raw responses, HTML/PDF/data bodies,
   metadata, model requests and all physical reservations in `raw/`.
2. Program inspection labels empty/shell bodies and exact duplicates. Excluded
   originals remain in the acquisition archive and the handoff exclusion map.
3. Independent repair reads saved raw responses and discovered URLs using local
   parsers, free HTTP and bounded Playwright rendering. It makes no new search or
   model calls. Failed attempts and remaining gaps are preserved.
4. The immutable body package can feed the frozen Mercury V7 selection, assessment
   and conditional scope guard. Review is separate from acquisition and budgeting.

The pilot uses three Tavily basic attempts, one required Exa attempt, eight initial
HTTP captures and one basic Extract batch per question. Original model limits are
12 logical decisions and 16 physical attempts per dispatch, with the existing
Ultra-to-Super service-failure routing. Repair adds at most ten HTTP captures, four
browser renders (each at most 25 requests), and eight saved-response reparses.
These are ceilings, not quotas that must be spent.

The package separately exports collector-declared gaps, unread candidate URLs,
and failed repair captures. An empty `result.gaps` does not mean full recall.
Unread leads are not all required documents, and model-declared gaps are not
verified findings. A cached coverage-disclosure upgrade makes no provider calls
and leaves the raw bundle and analysis body hashes unchanged.

## Fresh pilot and recovery

Run `python -m ForecastAgent.experiments.unified_acquisition_trial --output NEW_DIR`.
The frozen five-question manifest contains questions and requirements only; it
never imports old search hits or page bodies. New experiment budgets do not reset
any existing ledger or provider account allowance. Cross-task caching is forbidden.
The requirements reuse the prior pilot's exact rules for source-quality comparison;
requirement generation for arbitrary new questions is outside this pilot adapter.

For two recorded selection HTTP 422 failures, the isolated
`unified_review_repair` experiment can send each independent selection head with
the identical full original state. It preserves cumulative logical/HTTP/time
caps and all completed phases. Merged typed answers retain their individual
provider records; no source text, criteria, V7 interpretation or acquisition
budget changes. A failed single head stops this repair without blind retries.

To continue an interrupted Actions run, use its exact completed artifact via
`--resume PARENT --output NEW_DIR`. Inputs, caps, reservations and cached phases
remain frozen. A collector with incomplete saved material may still enter review,
but its limitations remain explicit; transport completion is not semantic success.
Failures without a saved body package remain failed and are not silently omitted.

The trial collects current pages for historical questions. It measures acquisition
and review quality, not leakage-free forecasting accuracy. It emits no forecast
probabilities and has no submission path. This experiment does not change the
production release, listener or worker.

See [the fresh pilot result](PILOT_RESULT.md) for actual capture counts, provider
usage, failure preservation and the remaining integration limitations.

## Analysis-only connection

`ForecastAgent.analysis.acquisition_replay` connects immutable body packages to
the retained `mercury-evidence-chain-v2` analysis. It passes exact question fields
and original body spans, not V7 judgments or acquisition model opinions. V7 review
completion is not a scoring prerequisite. Each question permits at most two
Mercury HTTP attempts; an unavailable second read retains a validated first
decision. Probabilities are clipped to 0.02–0.98 for evaluation.

The frozen `UNIFIED_ANALYSIS5.json` manifest pins all five acquired bundle hashes
and the analysis implementation. Inference and outcome evaluation run in separate
steps. Source hashes, character offsets, request byte bounds and provider outputs
are audited before labels are joined. This bridge makes no retrieval or submission
calls and does not alter production. See [the analysis result](ANALYSIS_RESULT.md).
