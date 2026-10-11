# Experimental assertion audit

This module is an optional research diagnostic. It does not change acquisition,
scoring, map revisions, material processing receipts, or production defaults.

`assertion_audit.prepare` accepts immutable originals and fallible assertions.
Each assertion names its own references and caller-proposed atomic qualifiers.
It prepares either one whole-assertion decision or one decision per qualifier.
Both modes use the same typed `supported / contradicted / insufficient` rubric.
Use the existing Mercury Decisions transport; do not route it through chat tools.

`assertion_audit.summarize` combines the qualifier judgments conservatively.
Every part must exceed the exploratory threshold to yield `candidate_supported`.
Missing, malformed, incoherent, or uncertain responses stay unreviewed. A
contradicted or insufficient part cannot inherit support from another part.
The threshold is not calibrated, decomposition completeness is not verified,
and candidate support never grants a truth certificate or target coverage.

`bind_decomposition` isolates invalid siblings against the registered limits.
It never truncates a long qualifier list, renames an unknown aspect, retries the
model, or discards every valid sibling because one assertion is malformed.
Missing and duplicated IDs stay unreviewed. Structural acceptance does not prove
that isolated names/dates still express the original subject-predicate relation.

The caller must retain original reference coordinates, capture clocks, physical
HTTP reservations, raw responses, unknown usage, and all preceding counters.
Never infer a future outcome from missing documents, captions, navigation,
historical measurements, or unrelated source versions. A decomposition itself
can omit a critical qualifier; separate tests must inspect that risk.

Evaluate whole and atomic modes on identical frozen source text and hypotheses.
Seal expected source-reading labels before model calls, randomize arm order,
report false acceptance and false rejection separately, and retain failures.
Human-authored mutation probes are diagnostic challenges, not naturally sampled
map claims or prospective forecasts. Passing them does not prove predictive
superiority over a release; that requires sealed matched forecasts and outcomes.
