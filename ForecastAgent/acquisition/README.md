# Intelligent acquisition experiments

Experimental entry point on `dev_acquisition_v2`, based on release `v1.0.1`.
The analysis and production delivery modules retain their release file identities.
See [the architecture](../docs/ACQUISITION_V2_DESIGN.md) and the
[frozen baseline manifest](../experiments/acquisition_v2_baseline.json).

The first [paired frozen-frontier experiment](FRONTIER_RESULTS.md) did not show
an improvement. The candidate remains experimental and must not be promoted.
The pipeline now opts into `intelligent_materials_v2`, a mechanical repair of V1.
See [the zero-provider repair validation](MATERIALS_V2_REPAIRS.md) and the subsequent
[fixed-Super paired results](MATERIALS_V2_RESULTS.md). V2 remains experimental;
measured retention improvements do not justify a new production release.

The opt-in [V3 control protocol](MATERIALS_V3_REPAIRS.md) adds bounded mandatory
review batches, explicit no-gap lists, program closure, and immutable timing
dependencies. Set `acquisition_strategy=intelligent_materials_v3` on a new task
input to exercise it. V2 remains the default until a separately frozen provider
pilot demonstrates quality; existing task identities are not migrated.

The [delivery-before-stall pilot](DELIVERY_EXPERIMENT.md) compares concurrent V3
against the same V3 with one opt-in runtime control. It preserves a new exact
saved read for delivery before a soft stall stop, within unchanged hard limits.
This control is disabled by default and does not change production or prompts.

## What is implemented

`pipeline.run` executes one task:

1. Preserve full question input, report missing opening/background fields, and
   reject outcome labels and previous predictions recursively.
2. Verify frozen release dependencies and freeze request, code, model policy, and
   network-mode identities before any provider calls.
3. Run the existing collector under the opt-in `intelligent_materials_v2` strategy.
   The agent binds each material need to original-field handles, chooses
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

V2 plans supply `question_refs` from `original_question_handles`. The program
expands these into exact whole-field `question_spans`, including field, SHA-256,
coordinates and quote. No manual quote copying is required. These structural
bindings do not prove correct interpretation or precise target relevance.
V1 quote-based requests remain recognized; the preregistered paired harness stays
explicitly on V1. Request/code identity changes reject silent task migration.
Large frontier previews disclose pagination; full bodies remain on disk. A source
version change invalidates prior assessments. The supplement may change a source
version, so the final report recomputes stale-reference diagnostics without
reopening collection or inventing a new semantic review.

After a saved read, V2 retains a bounded exact review focus and restricts the next
action to passage disposition. One response may also assess and close, in that
order. The program never auto-accepts passages. Two failed planning operations
close with gaps; forced closure wins even when no plan exists, and does not
require another model call. Pending/unassessed targets remain in the report.

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

## Saved-material delivery

[Experimental source reading](SOURCE_READING.md) documents the optional Crawl4AI
resource discovery, source roles, bounded JSON/CSV archives and observed-resource
follow-up tool. These capabilities are independent of production agent registration.

[Saved-material handoff](HANDOFF.md) documents the opt-in offline first-request
packer, exact source/view bindings, protected old-visible text, omission
manifest, and same-bundle paired replay. It repairs the acquisition-to-analysis
export without changing the frozen analyst or production wiring.

[Paired Mercury score validation](HANDOFF_SCORING.md) defines the subsequent
bounded same-snapshot first/final prediction experiment and label separation.
