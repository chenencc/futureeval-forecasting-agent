# Repaired fifty Mercury replay

Freeze all fifty final analysis inputs from free repair run 37103408115. Use the
retained `mercury-evidence-chain-v2` implementation: focused diagnostic judgments,
source-balanced original passages, and a conditional second original-text read.
This experiment does not use the alternative matched condition binding route.

Each task has at most two physical Mercury requests, one per stage. First-read
and reread journals are durable. A valid first forecast remains available if a
second service request fails. Cases without readable bodies remain explicit
failures without invented probabilities. Saved raw probabilities and probabilities
clipped to [0.02, 0.98] are both preserved.

The manual `analysis_mercury_evidence_twenty.yaml` workflow supports
`mode=repaired50`, ten sequential batches of five, and `resume_run` to restore
exact prior journals. Do not start another fresh run over these inputs to retry
failed requests. It calls neither retrieval nor forecast submission endpoints.

An independent evaluation job opens the fifty evaluation labels only after all
analysis outputs are uploaded and probabilities are checked against saved model
responses. Its reports include coverage, clipped Brier, natural-log loss,
threshold-0.5 accuracy, outcome groups, recorded acquisition gap groups, physical
attempts and reported usage. A failure is excluded from prediction metrics and
included in coverage. The majority-class baseline uses the same scored subset.

This is retrospective source analysis. Current saved pages and model knowledge
may reveal outcomes. Label isolation does not remove that leakage. Metrics cannot
establish prospective forecasting skill or an official tournament ranking.

## Paired comparison against before-extension evidence

The `before48` mode uses the fifty preserved original handoffs in parent run
37101299893, excluding 43900 and 44799 before any model call. The same Mercury
chain, byte limits, clip and physical attempt bounds are retained. Ten batches
produce the original snapshot forecasts; after-extension forecasts from run
37106094306 are reused without another request.

The independent evaluator requires matching chain hashes, model, cohort and label
identity before reporting paired metrics. It separates questions with new bodies
from those without additions, records wrong-to-correct and correct-to-wrong
transitions, and reports unpaired failures. Body equality and gap-metadata equality
are separately audited. A fresh before replay versus a reused after replay cannot
isolate stochastic model variation or changes in visible gap metadata.
