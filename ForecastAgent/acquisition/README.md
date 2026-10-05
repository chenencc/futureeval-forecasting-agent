# Intelligent acquisition V1

Experimental entry point on `dev_acquisition_v2`, based on release `v1.0.1`.
The analysis and production delivery modules retain their release file identities.
See [the architecture](../docs/ACQUISITION_V2_DESIGN.md) and the
[frozen baseline manifest](../experiments/acquisition_v2_baseline.json).

The first [paired frozen-frontier experiment](FRONTIER_RESULTS.md) did not show
an improvement. The candidate remains experimental and must not be promoted.

## What is implemented

`pipeline.run` executes one task:

1. Preserve full question input, report missing opening/background fields, and
   reject outcome labels and previous predictions recursively.
2. Verify frozen release dependencies and freeze request, code, model policy, and
   network-mode identities before any provider calls.
3. Run the existing collector under the opt-in `intelligent_materials_v1` strategy.
   The agent binds each material need to exact original question text, chooses
   discovery/read actions, banks exact excerpts, and records material gaps.
4. Freeze the collection parent and run the release's independent deterministic
   supplement with its existing reservations and limits.
5. Export `package.json`, a release-compatible overlay, and a structured report.

The pipeline cannot analyze or submit. Ultra is the default collector. Two
qualifying service failures activate the release Super fallback within a dispatch;
the next dispatch begins with Ultra. Search/capture/model lifetime counters are
not reset. A single-model experiment can explicitly disable fallback.

## Agent-facing material tools

- `inspect_materials`: paginated observed source frontier, material targets,
  existing excerpts, version-bound assessments, and remaining budgets.
- `assess_materials`: batch adequate/partial/unavailable/not-yet-published/unreviewed
  declarations for existing targets. Adequate requires a readable saved source
  and an exact associated excerpt. All references and batch items are validated
  before mutation. Assessments cannot establish truth or add material progress.

Normal source and reading tools remain available. Material selection is chosen
by the agent, with the release's discovery-to-read and failed-primary repair gates.
No event-specific product/keyword rules are added. Critical targets cannot be
removed to manufacture completion. Forced closure exports unreviewed targets and
preserved reservations without requiring another successful search or assessment.

Plans contain exact `question_spans` with `field` and `quote`, not guessed offsets.
Bindings prove that text exists, not that the agent interpreted it correctly.
Large frontier previews disclose pagination; full bodies remain on disk. A source
version change invalidates prior assessments. The supplement may change a source
version, so the final report recomputes stale-reference diagnostics without
reopening collection or inventing a new semantic review.

## CLI

Run from this worktree, with its root first in `PYTHONPATH`.

```sh
# Preflight only: no credentials, provider calls, network, or persistent task.
python -m ForecastAgent.acquisition.pipeline \
  --input ForecastAgent/fixtures/intelligent_acquisition_question.json \
  --root snapshots/intelligent-preflight

# For an authorized real question with complete original fields:
python -m ForecastAgent.acquisition.pipeline \
  --input question.json --root snapshots/intelligent/question-123 \
  --execute --supplement-network

# Offline tests use mocked model/search/fetch responses, not provider quotas.
python -m unittest ForecastAgent.tests.test_intelligent_acquisition -v
```

The synthetic preflight fixture must not be used as a live question. Acquisition
credentials use the existing environment names `OPENROUTER_API_KEY`,
`TAVILY_API_KEY`, `EXA_API_KEY`, and optional `SEC_USER_AGENT`. Do not store keys in
fixtures, inputs, reports, or Git. The supplement itself requires no search/model
key. Live network repair requires the release supplement requirements and Chromium
installation; existing GitHub Actions offline CI runs without secrets.

## Files and recovery

`identity.json` freezes the experiment. `state.json` records collection, supplement,
or complete. `collection/` retains the native bundle, provider records, reading
receipts, and reservations. `parent.zip` binds the exact completed collection bytes.
`supplement/` keeps repair attempts and original captures. `package.json` is the
immutable analyst input; `report.json` records integrity, material declarations,
resources, missing metadata, and unresolved targets.

Re-execution resumes the same stage. An interrupted supplement does not recollect
or renew provider allowance. A completed package returns without model/network
calls after checksum verification. Input/code/model-policy/network-mode changes
fail closed; use a separately identified experiment rather than overwriting a
ledger. Reserved or unknown attempts remain consumed. Package completion means
export completion; it does not certify material sufficiency or future predictions.

## Validation scope

Offline regressions check missing/invented rule bindings, exact table row and
header preservation, unknown/stale sources, atomic assessment batches, critical
scope preservation, forced closure, frontier pagination, unchanged baseline tools,
the actual runtime loop with mocked providers, supplement interruption, completed
package reuse, and tamper detection.

`offline_audit` checks existing real source captures and the fixed analyst's first
request projection without calling a model:

```sh
python -m ForecastAgent.acquisition.offline_audit \
  --bundles saved/question-1/bundle.json saved/question-2/bundle.json \
  --output snapshots/intelligent-saved-body-audit.json
```

This is compatible-input and original-span evidence, not a replay of autonomous
discovery. Actual recall, relevance, calls, tokens, and elapsed time still require
the bounded frozen-frontier and untouched live pair specified in the design.
