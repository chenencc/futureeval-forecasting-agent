# Frozen original-text map experiment

This local research experiment builds an actual map with Super. It does not run
the brief interpretation review or modify the production pipeline.

## Experimental arms

1. **A: Original evidence.** Mercury receives the frozen source spans and exact
   event-only decision registry.
2. **B: Original evidence plus map.** Super constructs observations, hypotheses,
   unknowns, relations, and an optional scoring plan. Mercury receives the same
   original spans and the same target registry, plus the accepted map.
3. **C: Conditional shadow.** Only a map with a supported, uncertain world-event
   pivot can request this arm. Mercury returns P(C), P(Y given C), and P(Y given
   NOT(C)). The program combines probabilities or complete distributions. This
   separate request does not contaminate the primary A/B comparison.

The conditional head is a provisional scenario, not a calibrated causal model.
Reference validation establishes exact source provenance, not truth, relevance,
unit interpretation, causal validity, or forecast accuracy. Missing evidence is
never a negative-event observation or a branch probability.

## Controls and limits

- One to five explicit, frozen inputs; Super is fixed to the free Super model.
- One physical map request per case; one request per scoring arm.
- At most 20 physical model requests across five cases, including failures.
- Original JSON hashes, body hashes, capture times, and acquisition ledgers stay
  unchanged. No search, fetch, outcome label, calibration, or submission occurs.
- Every visible text block or table row has a source-body coordinate reference.
  All text is reconstructed from saved source bodies and independently checked.
- A and B use identical target-only questions and original text. Their model
  responses are separate samples, so an output change alone proves no improvement.
- If a map is absent, gap-only, or does not fit, reuse the A result explicitly;
  do not count fallback as an independent B forecast or fabricate missing scores.
- Exact cached requests and responses are reused on resume. No stage budget,
  timestamp, or original acquisition allowance is renewed.

## Local execution

```text
python -m ForecastAgent.research_loop.map_paired_trial \
  --parent PATH_TO_FROZEN_CONTEXT_TRIAL \
  --root NEW_EXPERIMENT_ROOT --max-http 20
```

Preparation validates all cases without model requests. Add `--execute` only for
an authorized experiment. Load credentials into process environment without
printing them. Inspect `cases/ID/map`, `graph.json`, each scoring request and raw
response, and the cumulative `report.json`.

Review node evidence, periods, metrics and units before interpreting score shifts.
Unresolved cases cannot provide Brier, accuracy, or a measured map-quality benefit.
