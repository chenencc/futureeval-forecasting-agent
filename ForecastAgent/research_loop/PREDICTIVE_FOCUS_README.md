# Predictive focus pilot

This opt-in development policy keeps collection and map maintenance in one agent
loop. It does not change production, source budgets, model routing, or scoring rules.

## Interface

`intelligence.research_map.enable(request, predictive_focus=True)` enables the
reference-bound map interface and the `obtainable_inputs_and_scope_risks_v1` policy.
Native turns and final saved-material review use the same authoritative graph
prompt. The model selects delivered original IDs; code binds unchanged full spans.
The legacy quoted-observation interface remains available when this policy is off.

Resolution conditions stay immutable. Prediction inputs can be baselines, history,
leading indicators, event procedures, or contrary evidence. The graph declares their
relationship and limitations; a link is not a validated causal mechanism.

## Missing material

Every new material request declares a purpose and one information state:

| State | Program behavior |
| --- | --- |
| `saved_unread` | Require an exact saved readable URL; read locally, then update/receipt. No network permission. At most two update attempts per gap. |
| `obtainable` | Eligible for bounded gap acquisition. Existing budgets and observed-URL rules still apply. |
| `future_unknown` | Require `future_outcome` and `future_event`; exclude from network gap scheduling. A landing page is not a future result. |

Contradictory or incomplete declarations are isolated and audited. Invalid requests
cannot erase separately valid original observations. Declarations about current
observability are model judgments, not external verification.

## Interpretation review signals

The program records suspicious absence/intent assertions, unqualified assertions in
unbound hypotheses, and numerical literals absent from selected originals and rules.
These are conservative review signals with possible false positives. They never
certify entity, period, metric, unit, stage, causality, or truth. They do not reject
raw material or silently rewrite a model explanation. Mercury receives the signals
for the exact delivered map subset and can reject the graph.

No Bayesian network, conditional probability table, independent extra judge, or
probability propagation is added. Original-text and original-plus-map arms must
receive identical originals, scoring heads, and Mercury configuration.

## Validation

On 2026-10-11, 260 focused offline tests passed with zero socket attempts. This proves
interface and routing behavior, not forecasting improvement. Replay of archived
model outputs does not measure the effects of a new prompt. Unresolved prospective
forecasts are retained for later evaluation and are never submitted by the pilot.
