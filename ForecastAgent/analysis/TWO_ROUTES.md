# Two-route analysis v7

This protocol compares direct reasoning probability with Mercury's probability
from the same qualitative reasoning report. It is not an independent model
ensemble: both routes share the reasoning analysis and evidence. Ultra is the
default reasoning backend; the authorized service-error fallback to Super remains
audited. Outputs record the actual backend producing the reasoning probability.

## Evidence inputs

The original collection bundle is immutable. Optional supplemental evidence is
loaded through a hash-verified overlay. A full supplemental campaign is resolved
through its frozen task-to-batch map. Original and overlaid bundle identities,
supplement campaign identity, sidecar hash, source timestamps, capture metadata,
source truncation and outstanding repair gaps are retained. Local source reads
continue to use stable evidence IDs and original character offsets.

Terminal `acquired`, `closed_with_gaps` and `needs_attention` collection tasks can
be diagnosed. A task with no saved usable body fails explicitly. Collection state
and budgets never change. Readability, retrieval gaps, citation provenance,
interpretation accuracy and event probability are distinct properties.

## Probability routes

1. The reasoning model analyzes and reviews the evidence, producing its own
   probability. This route is frozen in `routes.json` before calling Mercury.
2. Mercury receives qualitative analysis, materialized citations and quality
   flags, excluding the reasoning model's numeric probability.
3. Once both routes exist, software records their equal arithmetic mean and
   absolute disagreement. No additional model call is needed for fusion.

`--mode reasoning_only` runs route 1 without any Mercury call. `--mode both`
runs both routes. A Mercury failure leaves route 1 available, reports the failure
and leaves the mean unavailable. It never imputes the missing probability. The
route mode is part of the frozen experiment identity and cannot change on resume.

Calibration is identity (`a=0, b=1`): no parameters are trained on retrospective
evidence. Evidence sufficiency is not multiplied into probability. Automatic
forecast use remains disabled, and this module never submits predictions.

## Execution and evaluation

The `Referenced evidence analysis pilot` workflow optionally downloads
`acquisition-supplement-100-v1`, uses the configured free reasoning model, and
uploads `referenced-analysis-v7`. Resume accepts only a matching v7 experiment.
Older experiments remain untouched. Per task lifetime limits remain three physical
requests per authorized reasoning backend (at most Ultra plus Super), eight local
reads, and one Mercury request. Actual attempts, failures and unknown token usage
are preserved; restoring an experiment does not replenish limits.

```sh
python -m ForecastAgent.analysis.referenced --root COLLECTION_ROOT --supplement-root SUPPLEMENT_ROOT --output EXPERIMENT_ROOT --ids 43259 --mode both
python -m ForecastAgent.analysis.evaluation --output EXPERIMENT_ROOT --source-archive ORIGINAL_COLLECTION.zip --supplement-root SUPPLEMENT_ROOT --labels LABELS.jsonl --report REPORT.json
```

Evaluation verifies original and overlaid bodies, exact cited spans, frozen
analysis/scorer inputs and probability consistency before opening outcome labels.
It reports Brier and clipped log loss for each available route and the equal mean.
Each route has its own count; paired metrics use only questions with both routes.
Failures, including partial Mercury failures, remain explicit. A partial failure
does not remove a valid reasoning result or create an artificial paired result.

These historical cases and current supplemental pages may contain outcomes.
Their results diagnose the pipeline, not prospective forecasting ability.
Use unresolved frozen questions with subsequent outcomes to learn calibration
or aggregation weights. Do not choose weights or tune prompts on a held-out test
set and then describe its performance as independent validation.
