# V3 material control protocol

This is an opt-in experiment on `dev_acquisition_v2`, based on release `v1.0.1`.
Set `acquisition_strategy` to `intelligent_materials_v3` in a **new** pipeline input.
The default remains V2. Existing V1/V2 tasks, frozen comparison identities and
production release code are not migrated. Code/input changes cannot resume an old
pipeline identity; use a separate task root. Source, search, model and supplement
reservations are unchanged.

## Bounded review instead of compulsory queue exhaustion

V3 prioritizes the existing critical-need queue and pins at most four complete
saved spans, totaling at most 6,000 text characters, in one request. Full source
versions and character coordinates remain exact. Only two successful review
batches may force the next action over the task lifetime; this routing counter
persists on resume. After that, review is advisory and navigation remains available.
This limit is not a provider quota and does not cap voluntary local review calls.

`review_passages` accepts `keep`, `reject`, or `defer`. Deferral records an explicit
reason without creating an excerpt, rejecting relevance, or erasing source data.
Deferred candidates remain in the terminal report and may be voluntarily reviewed
later if their source versions are still valid. Deferred/unreviewed candidates
remain recall limitations; fewer mandatory reviews do not certify coverage.

## Separate declarations, closure and execution state

V3 assessments use `missing_items=[]` for adequate and a list of concrete gaps for
other statuses. The optional legacy string accepts only exact neutral aliases
(`None`, `No further material needed.`, or blank, case/space normalized). Real gap
text cannot coexist with adequate. No semantic negative-text guessing is used.
The original wire declaration remains saved alongside normalized state. Entire
assessment batches validate before mutation; exact excerpts and source versions
are still required.

Once all active targets have current adequate declarations, with no unresolved
required rule metadata, required discovery obligation, or named-primary rescue,
the program exports without another model closing request. Assessments still do
not add acquisition progress or renew budgets.

`material_adequacy_status` and `execution_report` describe different things. A
stall, budget stop or transport interruption is execution state; it does not
manufacture a generic missing-material claim. Actual missing associations,
unassessed targets, deferred candidates, stale references and unknown rule fields
remain recorded. Acceptance warnings can still prevent `acquisition_complete`.
An adequate agent declaration never implies factual verification or full recall.

## Immutable temporal dependencies

Each V3 target supplies `rule_time_fields`: required platform timing dependencies,
or `[]` if no metadata field is required. Program bindings copy original
`open_time`, `close_time`, `scheduled_close_time`, and `scheduled_resolve_time`
values. Missing/invalid values stay unknown. A target depending on an unknown field
cannot be declared adequate. These bindings survive context compression,
interruption and task restoration.

Explicit target calendar dates must have lineage in immutable question fields or
valid platform metadata. New historical research/observation dates belong in the
query, not target eligibility prose. The guard recognizes ISO and English named
calendar dates; it is deliberately not a complete semantic rule interpreter.
It does not prove that an original date was assigned the correct role. Agent
omission of a necessary dependency is also not semantically certified. Whole-field
question binding remains structural. Downstream analysis must apply original rules
and retain unknown boundaries, rather than trusting target prose as a verdict.

The saved hack plan invented May 7 as the question opening and is now rejected.
The saved gasoline plan also introduced January 1 as a collection start date; that
research window must move into its query. This protocol correction preserves all
original bodies; it does not assert missing source evidence.

## Offline evidence

The audit verifies 42 original provider records and all five original bundle
hashes from run `37297914625`, the unchanged 33-source pool, and 47 frozen release
dependencies. The saved-argument fixture preserves exact original tool arguments.
Fourteen targeted regressions cover the real queues, atomic rejection, narrow
alias normalization, interruption, rule unknowns, original bytes, resume counters,
and V2 compatibility. The full offline suite passes 477 tests.

- Stablecoin loop replay exports after six mocked request decisions instead of
  the original eight requests. It adds explicit V3 wire fields, not a new model
  decision, and preserves both target rows.
- Court control replay groups four exact spans per mandatory batch and stops
  forcing review after two batches. Remaining and deferred spans remain saved.
- Unknown rule boundaries cannot be supplied as arbitrary dates or satisfy an
  adequate declaration through a metadata-dependent target.

No model/search/fetch request or forecast submission occurs in this validation.
These are control mechanics, not new token savings, live relevance, forecast
quality or generalization evidence. A separately frozen V3 provider pilot is
required before promotion.

```sh
python -m unittest ForecastAgent.tests.test_material_protocol -v
python -m ForecastAgent.acquisition.protocol_replay \
  --source-root snapshots/intelligent-frontier-review-37297914625 \
  --output snapshots/intelligent-v1/materials-v3-offline-control-audit.json
```

Structured evidence: [offline control audit](../experiments/results/materials_v3_offline_control_audit.json).
