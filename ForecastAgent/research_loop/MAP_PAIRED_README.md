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

Discrete summaries invert the cumulative bin masses at possible outcome centers;
they do not interpolate a fractional count or extrapolate into an open tail.
The summary also exposes each outcome mass. Saved provider replies and platform
payloads remain unchanged. Continuous numeric and date summaries keep their
existing interpolation policy. Replaying a summary consumes no model requests.

After independently bound outcomes become available, discrete questions use
ranked probability score over ordered outcome masses. An explicit open tail is
an additional category. Impossible fractional outcome labels are rejected;
tail outcomes are not replaced by a fabricated boundary value. Continuous
numeric/date questions retain the bounded linear-CDF diagnostic. These metric
implementations do not supply missing official outcomes or prove predictive skill.

## Sealed current-clock shadow pairs

`evaluate_shadow` reads the separately registered three-case shadow trial without
changing its closed archive or making model requests. It checks the frozen
original hashes, question identity, forecast clock, equal target registries,
separate received replies and forecasts reconstructed from those replies.
Gap-only maps remain `not_independent`; unresolved pairs remain `pending_outcome`.

```text
python -m ForecastAgent.research_loop.evaluate_shadow \
  --root SEALED_TRIAL --output SEPARATE_EVALUATION.json
```

Optional `--labels` accepts the independently archived bindings used by
`research_loop.labels`. An ID-only outcome is quarantined. Numeric metrics are
diagnostics until a reviewed first public outcome time establishes that the
forecast preceded that event. Add `first_public_outcome_at` (timezone required)
and `first_public_outcome_reviewed: true` to the bound label provenance only
after reviewing the archived public event evidence. Official resolution time is
not a substitute for first public event time. Source truth and model-memory
cleanliness remain unverified by this mechanical check.

Official prediction-window eligibility is separate: a later shadow forecast can
precede public resolution while missing the tournament deadline. This original
versus map ablation does not execute the full production reread pipeline.

## Release-schedule analysis ablation

`release_paired_trial` retains the release Mercury registry, diagnostics, selector,
conditional reread and final payload policy. Each mapped call has the exact same
originals, instructions and target registry as its corresponding original call.
The original release arm owns the reread schedule; mapped diagnostic answers do
not change that schedule and are never passed to the original arm. This measures
the map's contribution with matched reading, not independent routing policies.

The harness verifies the pinned release Mercury dependencies. Its saved-source
reader is loaded explicitly from that control and its consumers are patched only
inside an isolated experimental process. This is not a production entry point.
It neither compares fresh acquisition nor the production reasoning-only service
fallback. A failure in the experimental map arm cannot contaminate a successful
original release forecast. A reused fallback is not an independent observation.

```text
python -m ForecastAgent.research_loop.release_paired_trial \
  --parent FROZEN_CURRENT_CLOCK_BUNDLE --root NEW_TRIAL --control FROZEN_RELEASE
```

Preparation uses no provider requests. Execution requires a five-attempt ceiling:
one Super map over the release's first visible originals and up to two Mercury
calls per arm. `--mapped-first` counterbalances the within-stage call order.
No original text is removed to fit a map. A map that cannot fit is explicitly
omitted; later reread text is not represented as if Super had already read it.
Source captures retain their actual dates. A new research clock must be recorded
in a separate parent derivative; it cannot be backdated to collection or replace
an official tournament deadline. Bound labels and first-public-event audits remain
required before interpreting outcome metrics. No search, fetch, submission or
provider account budget reset is performed.

The optional `--map-protocol reference-map-v1` selects the existing reference-bound
interface throughout prompting, schema, program hydration, acceptance and cached
replay. Super selects visible R IDs and explains relationships to frozen targets;
the program binds complete unchanged originals. No copied claim, inferred timing
or factual meaning is certified by an ID binding. Applied binding receipts retain
source hashes and coordinates. This protocol is part of the frozen experiment
identity and cannot be switched after preparation. The default copied-quote
protocol and all sealed prior experiments retain their original identities.

`--source-context` is a separate opt-in reading experiment. It supplies literal
DOM ancestor headings and links around text that matches the selected originals.
Both Mercury arms receive the same bounded sideband, and Super receives it before
selecting references. Archived raw HTML hashes and XPath coordinates are retained;
matching repeated text is ambiguous and does not verify the target event. This
repairs lost local context such as a previous-event card embedded in a current
event page. It does not infer results, dates, source reliability or applicability.
Unsupported/corrupt originals and truncated contexts remain explicit. No original
text is deleted to fit context. The frozen identity records this changed reading
scope: these inputs are not the unaugmented production release reading input.
