# Saved evidence analysis pilot

The independent analysis path consumes acquired bundles without modifying acquisition state or performing retrieval. Codex operates the repository; it is not a runtime dependency.

1. Freeze question fields and deterministic source segments, hashes, omission counts, and capture dates.
2. Ultra decomposes the rules, builds an event tree, examines outside-view support, and records opposing cases with verbatim citations.
3. Ultra reviews its draft for scope, date, contradiction, and coverage errors. Software validates citations against visible saved segments. Citation validity does not establish claim entailment.
4. Mercury Decide receives the qualitative report and citations, excluding Ultra's numeric baseline. A `noul` question gives P(YES); a separate ordinal `score` measures evidence sufficiency. Neither score nor confidence is multiplied into the event probability.
5. Freeze inference outputs before a separate evaluation command opens binary resolution labels. Compare Brier and clipped log loss; report failures rather than fabricate probabilities.

## Model and endpoint

The analysis pilot explicitly uses `nvidia/nemotron-3-ultra-550b-a55b:free` and `inception/mercury-decide:free`. Mercury is a structured decision model, not a chat model. OpenRouter exposes the System One request schema at `https://openrouter.ai/api/alpha/decisions`. Its advertised probability calibration has not been established for these questions. Free endpoints remain rate limited.

- [Mercury model card](https://openrouter.ai/inception/mercury-decide:free)
- [OpenRouter decision model API explanation](https://openrouter.ai/blog/insights/what-is-jev/)
- [TypeSafe System One schema](https://docs.typesafe.ai/api)

## Durable limits

Each task has a separate analysis journal: at most three physical Ultra HTTP attempts across both stages, and one Mercury request. The pilot has one additional Mercury health request. Reservations are written before HTTP. Restarting the same output directory never replenishes attempts. Completed predictions are reused only if evidence hashes match. Collection budgets and statuses are never reset or edited. No Tavily, Exa, market API, or forecast submission is called.

Evidence selection uses at most 10,000 characters per saved source. All omitted characters and upstream truncation are recorded. The initial pilot does not offer interactive reading of omitted passages; missing context must appear as a gap. Polymarket is not supplied when no saved market snapshot exists. Analysis and decision interfaces are separate so the reasoning model can later be replaced without rewriting scoring.

## Run and evaluate

```sh
python -m ForecastAgent.analysis.pilot run --root PATH_TO_CAMPAIGN --output PATH_TO_NEW_ANALYSIS --ids 44801,44693,43688
python -m ForecastAgent.analysis.pilot evaluate --output PATH_TO_ANALYSIS --labels snapshots/forecastbench-history/resolved_metaculus.jsonl --report PATH_TO_REPORT.json
```

`OPENROUTER_API_KEY` is required only for inference. The `Saved evidence analysis pilot` workflow obtains it from the existing Actions secret. Evaluation is local and offline. Archives preserve requests, responses, model identity, provider usage, exact question rules, selected evidence, drafts, reviews, gaps, and frozen probability hashes.

## Interpretation

These current saved sources may contain post-resolution evidence, and models may know outcomes. Even separated labels do not remove this leakage. This pilot is a retrospective integration and diagnostic test, not a leakage-free historical forecast benchmark. Three cases cannot establish calibration or superiority of either model. Prospective frozen unresolved questions and subsequent outcomes are needed for that comparison.
