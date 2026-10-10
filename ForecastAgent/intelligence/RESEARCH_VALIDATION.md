# Research-map integration and forecasting validation

## Architecture

This development branch combines predictive body admission with the existing
map-guided acquisition implementation from commit
`44532209cd4cf25e16093ea2023059bbe31d4f7e`.

```text
Question and exact rules
    -> one native acquisition agent, existing budgets
    -> optional source-bound map and critical-gap actions
    -> independent supplement and bounded data recovery
    -> deterministic body admission
    -> freeze common originals
         A: originals -> Mercury target decision
         B: same originals + accepted map -> same Mercury target decision
    -> sealed forecasts
    -> isolated outcome binding after publication
```

The imported research tools record observations, hypotheses, unknowns, relations,
critical material requests, source-action intents, and the effects of each map
revision. Graph updates share the ordinary collection turns. Search and capture
caps, reservations, failures, and original source versions remain native records.
There is no nested model call in a map tool. A perfect map is not required to stop.

Enable it explicitly with `research_map=True` in
`ForecastAgent.intelligence.pipeline.collect`. The default remains disabled.
Models remain configurable through the existing provider interface. Projection
uses the original native bundle to audit node bindings and the admitted view to
choose common scoring text. It never silently rebinds an excluded source.
`prepare_package` saves both input arms and their map audit, with no inference.
An unusable, oversized, or entirely hidden map is an original-only fallback,
not a successful map experiment. Conditional-probability experiments remain
separate; no automatic multiplication of dependent node probabilities is added.

The modified runtime is a development source tree. Its manifest is marked
development-only and cannot be used as a production release identity. The frozen
production analysis prompts and scoring core are retained. Production promotion
still requires the worker and Linux gates listed in the coordination directory.

## Three different questions

| Validation layer | Observable now | What it cannot establish |
| --- | --- | --- |
| Engineering | Exact source coordinates/hashes, stable task identity, matching A/B originals, valid payloads, bounded closure/resume, failed-case records | Source truth or better probabilities |
| Evidence and reasoning | Human review of entity, metric, period, units, event stage, rule exceptions, source applicability, duplicate chains, unsupported causal arrows | Forecast accuracy before the event occurs |
| Forecasting | Sealed pre-outcome probabilities versus later independently archived outcomes; paired proper scores | General improvement from a few selected cases |

Literal quotation is necessary for an observation, but a literal quotation can
still concern the wrong entity, period, metric, or source role. Source reliability
is not an event probability. A failed fetch or unpublished outcome is not NO.
Human evidence review should hide the version and predicted probability while
checking a predeclared target-field checklist. Keep this review separate from
the subsequent outcome scoring.

## Historical material policy

Use old resolved questions to reproduce extraction, binding, format, and resume
errors. Reuse immutable snapshots without searches when comparing interpretations.
Label all such runs `retrospective_diagnostic`.

A search end date does not freeze page versions. An old publication date does
not prove that the current body existed before the event. Web pages can be
updated, snippets can disclose outcomes, and a current model can remember the
answer even when supplied old text. Prompting it to forget cannot certify an
out-of-sample forecast. Neither a high retrospective accuracy nor a bound outcome
label is sufficient to promote a forecasting strategy.

Do not retroactively call an already-run historical pilot prospective. Do not
relabel later supplements as original information. A model rerun after the event
remains diagnostic even on identical pre-event source text.

## Prospective development protocol

1. Select a fixed chronological batch of unresolved questions with outcomes
   not already public. A closed platform question can still describe an unknown
   future outcome; closed research cases never acquire submission eligibility.
   Prefer several short-horizon questions plus broader tasks;
   short-horizon results are a fast development lane, not a representative test
   of all domains. Archive status and rules separately from model inputs.
2. Predeclare model/settings, code identities, budgets, question IDs, target-field
   checklist, A/B mechanism, and which metric is primary. Leave unused future
   chronological batches as holdouts. Do not use later results to select cases.
3. Collect once and freeze the exact common originals, map, registry, cutoff,
   capture times, and input hashes before either scoring call. This isolates the
   map's analysis contribution. Acquisition recall itself needs a separate paired
   acquisition experiment; it cannot be inferred from this A/B.
4. Score both arms and seal raw result/payload, failures, model HTTP journals,
   actual completion times, and known/unknown usage. Alternate scoring order.
   Keep collected-only, failed, original-only fallback, and incomplete pairs in
   the registered denominator. No probability is invented for failed arms.
5. Publish or independently timestamp the sealed artifact before outcomes are
   known, using a GitHub artifact/commit or another suitable witness. Local hashes
   detect changes but do not independently prove when a file was created. Avoid
   public provider responses or any credential-containing file.
6. After publication, archive the official result and bind it to the exact ID,
   rules, options, units, and scale. Record when the result first became public,
   with a source. Official platform resolution can lag public knowledge; its
   timestamp alone is insufficient.
7. Join labels only in the offline evaluator. Compute paired scores on the same
   eligible cases, report all exclusions, and keep types separate. Binary uses
   Brier and log loss; categorical uses multiclass Brier and log loss; numeric,
   discrete and date use a bounded normalized CDF diagnostic. This is not a
   claim of equivalence to the platform's leaderboard score.
8. Compare total acquisition/map/scoring requests and elapsed time as well as
   scores. Group correlated questions by event family for uncertainty estimates.
   Inspect calibration on sufficiently large future samples, never fit a curve
   and report its performance on those same outcomes. Confirm a chosen change
   on the untouched next chronological batch before production promotion.

While waiting for resolution, develop against the fixed engineering regression
suite, blind target-field audits, source-availability coverage, and cost/failure
metrics. Controlled synthetic or perturbed examples can test logic and units,
but their performance is not a real-world accuracy estimate. Delayed outcomes
do not require pausing engineering work or replacing prediction scores with
an LLM's opinion of its own forecast.

## Sealed validation API

`ForecastAgent.intelligence.validation.freeze_case` registers both scoring
inputs with a fixed configuration. `seal_result` records success or failure
once. `evaluate_case` or `evaluate` reads separately bound outcomes afterward.
Changed inputs, modes, configurations or sealed results refuse silent reuse.
Missing capture times and post-cutoff captures remain explicit exclusions.
Missing official resolution criteria also exclude primary forecast metrics;
title-only material may support acquisition diagnostics but not a complete
platform-resolution comparison.
Primary forecast metrics require prospective registration, a delivered map,
both valid sealed arms, a bound outcome, and first-public-outcome timing after
both forecasts. Unknown timing stays diagnostic. Receipt assertions and local
timestamps are audited protocol claims, not independent proof of truth.

Labels use the existing `research_loop.labels` binding contract. Their provenance
additionally supplies `information_available_at_utc` and
`information_time_source_url`. These fields belong only in the label store.
For finite log-loss diagnostics, probabilities of zero use an explicitly
reported epsilon of 1e-12; inference payloads are not modified by the evaluator.

```text
python -m ForecastAgent.intelligence.validation --root SEALED_CASES --labels ISOLATED_BINDINGS
```

The evaluator makes no provider or platform requests. It retains missing and
failed arms, reports pending outcomes, and never recommends deployment itself.
The old unsupervised graph gate and historical score scripts remain archived
diagnostic tools, not substitutes for this sealed protocol.

## Research basis

[ForecastBench](https://arxiv.org/abs/2409.19839) evaluates future-event questions
whose answers are unknown when predictions are submitted. Its
[official implementation](https://github.com/forecastingresearch/forecastbench)
provides a useful reference for continuously refreshed questions and later
resolution. Our protocol adopts prospective prediction and delayed scoring;
this is a project design, not a claim that the local seal reproduces its full
benchmark methodology.
