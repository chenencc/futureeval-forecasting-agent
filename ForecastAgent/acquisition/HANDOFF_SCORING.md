# Paired Mercury prediction-score validation

This experiment reuses the **candidate collection bundles** from run
`37316638264` for questions `36871`, `43494`, `43501`, `43991`, and `44801`.
Each question is scored with old and new evidence packing. It never recollects,
supplements, searches or submits a forecast. Production wiring stays unchanged.

## Frozen comparison

- Model: `inception/mercury-decide:free` through the existing Decisions endpoint.
- Unchanged release decision questions, instructions, diagnostic routing and
  probability clipping to `[0.02, 0.98]`.
- First request: at most 22,000 serialized bytes, one physical attempt.
- Conditional reread: at most 28,000 bytes, one physical attempt, only if the
  release diagnostics request it and at least 900 genuinely new original
  coordinate characters can be delivered.
- At most **four attempts per paired question, twenty across all five**.
- New packing retains the original first-pass evidence, exposes extracted
  document coordinates, and removes already-covered body ranges from the
  conditional proposal frontier. Renaming evidence IDs cannot manufacture
  new text or consume the entire reread allowance with duplicate passages.
- No fitted calibration, score fusion, alternate backend or paid fallback.
- Route order alternates by question. Failed attempts remain consumed; a failed
  second read retains the validated first score. A failed first read yields an
  explicit failure, never a synthetic 0.5 or an automatically restarted budget.

The first-pass paired clipped Brier is the primary comparison. Final clipped
Brier after bounded conditional rereading is secondary. Report raw probabilities,
raw/clipped Brier, log loss, classification accuracy, route diagnostics, actual
HTTP records, known tokens and unknown usage separately.

## Labels and limitations

Evaluation labels come from the archived ForecastBench Metaculus resolution
records at revision `5e184cb7ccf4d577787c8201e08875e2fe070a43`. Four answers are No
and one is Yes. Checkpoint dates are not guaranteed settlement timestamps.
The helper opens the label file only after verifying sealed predictions and
actual provider requests against reproducible frozen source packing.

These are five repaired development cases, with unrestricted current captures
and possible model knowledge of outcomes. Results are retrospective diagnostics,
not leakage-free forecasting, an untouched holdout, or a tournament-level score.
No release promotion is implied by improved Brier on this small sample.

## Operation

```sh
python -m ForecastAgent.acquisition.handoff_scoring \
  --root snapshots/intelligent-frontier-review-37316638264 \
  --output snapshots/handoff-score --question-id 36871 --dry-run

# Authorized real Mercury scoring, using the existing environment credential.
python -m ForecastAgent.acquisition.handoff_scoring \
  --root snapshots/intelligent-frontier-review-37316638264 \
  --output snapshots/handoff-score --question-id 36871

# Offline audit and paired metrics, after the original artifacts are restored.
python -m ForecastAgent.acquisition.handoff_scoring \
  --root snapshots/intelligent-frontier-review-37316638264 \
  --output snapshots/handoff-score --review snapshots/handoff-score-review.json
```

The existing manual-only `retrieval_trial.yaml` has an explicit `handoff_scores`
choice. Its source artifact parent is fixed; `resume_run` restores exact scoring
journals instead of restarting attempts. The workflow refuses GitHub reruns and
archives each paired case even on failure. The model credential uses the secret
name `OPENROUTER2`; credentials never enter request records or artifacts.

`identity.json`, exact first/second states, request/response journals,
`routing.json`, `result.json`, and `seal.json` make prepared, attempted, failed,
first-only and conditionally reread states distinguishable. Do not interpret a
successful workflow as proof that either prediction is correct.
