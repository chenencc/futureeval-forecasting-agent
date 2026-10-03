# Fresh binary holdout comparison

The frozen manifest `ForecastAgent/fixtures/fresh_binary_holdout20.json` excludes all 54 IDs in the prior aggregate analysis inventory and three-route label registry. Eligible tasks must have an acquired/closed-with-gaps collection state and at least one saved body. Twenty IDs are selected by fixed SHA256 ordering, without loading outcomes or selecting previous scores. Collection gaps remain visible; this is not a cohort of fully verified evidence.

Each case replays original acquisition plus the saved independent supplement. Run the unchanged old Mercury evidence-chain analysis with its adaptive saved-body reread, then run the matched condition route against the old route's actual final successful request. Both routes are fresh. At final scoring, question, evidence, source metadata and passage order must match exactly. New final scoring adds uncertain condition bindings; candidate distributions are not condition truth probabilities.

The old route permits at most two physical HTTP attempts and the new route at most two, for a total cap of four per case. Attempts and failures are durable. No new collection calls, historical labels or forecast submissions occur. Failed cases are not replaced. Exact journals can resume; frozen identity changes are rejected.

Dispatch the existing `analysis_p0_forty.yaml` workflow on `dev_formal` with `experiment=holdout20`. It runs four batches of five with a separate artifact namespace. The production release is unaffected.

Score only after response artifacts are preserved, using the separate ForecastBench Metaculus evaluation labels. Report paired clipped Brier, log loss, accuracy, route failures, actual attempts, known/unknown usage, and the per-case Brier difference. This remains retrospective evidence analysis without a historical cutoff. The old route executes first to establish its final coverage, so inference order is not randomized. No prompt changes occur during the run.
