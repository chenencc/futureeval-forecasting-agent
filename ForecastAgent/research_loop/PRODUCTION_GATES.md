# Graph-loop production readiness gates

Objective: make the integrated acquisition/map/scoring chain ready for production
and at least as reliable as the current release. Passing a graph schema is not
evidence of readiness. This document defines the whole gate before further trials.

## Frozen baseline

On 2026-10-11 the current GitHub `main` official worker pins
`v1.0.5-recovery.2`, Super directly, and `ForecastAgent.releases.v1_0_5`.
The fetched workflow and immutable tag identity are retained in the local gate
directory. A local stale workflow or an older release tag is not the control.
The verified tag resolves to `bf9a425f8425c9a685bd3b24979935b7cfe04981`.

## Current candidate mechanisms

- The operating clock is explicit in map delivery. It is not a historical
  evidence cutoff. Future realizations remain unknown; obtainable baselines,
  indicators and procedural inputs are separate acquisition needs.
- `complete_bound_originals_first_v1` is opt-in. It pins complete source groups
  before ranking remaining passages. Both scoring arms get the same originals.
  Byte-identical saved aliases retain their original URL; normalized text never
  authorizes rebinding. Oversized groups are omitted atomically.
- Capability fingerprints and the duplicated acquisition contract remain in
  local immutable packages, outside the decision question. Every other request
  field remains unchanged, including unfamiliar platform exceptions. The same
  byte cap can then carry more original evidence without changing the registry.
- Navigation-only bodies are separately diagnosed. Diagnostics are conservative
  extraction heuristics, not a truth/relevance score. All original captures remain.
- Final map-review receipts are linked in a derivative package. Actual HTTP
  attempts and missing usage remain charged, separate from native stage totals.

An archived-source replay proves delivery/accounting, not better acquisition or
prediction. A development manifest refresh is a candidate freeze, not promotion.

## Required evidence

| Gate | Evidence required | Failure behavior |
| --- | --- | --- |
| Acquisition | Fresh paired unresolved questions with matching rules, model and source budgets; useful current baseline/indicator coverage independently audited; raw hashes, gaps and actual consumption retained | Navigation/failed bodies are not counted as useful target evidence; future realizations are explicit unknowns |
| Original binding | Complete selected reference ranges and provenance survive the final common source packet; hidden/changed/decoded-only references are rejected | Original-only fallback with precise reason, never made-up quotes or silent URL rebinding |
| Material loop | Every captured source scope has an honest processed/deferred receipt; stale revisions and empty loops are bounded | Preserve pending work and source scopes; no quota renewal or successful-completion fiction |
| Scoring | Binary, choices, numeric, date and discrete valid payloads; graph and control receive identical originals/heads; no missing cases from map failure | Existing release-compatible direct scoring remains usable; failed provider requests remain explicit |
| Resource accounting | Original native, supplement and final-review HTTP receipts reconciled, including unknown usage; resume/failover shares lifetime caps | No second dispatch, hidden retry or budget reset |
| Recovery | Native process interruption, expired service request, duplicate worker, artifact restore and all-empty source states tested through the actual orchestrator | Preserve reservations; submit receipts prohibit repeat submission |
| Linux worker | Dependencies, browser containment, packaging and restored checkpoint verified in the runner environment without submitting | Candidate remains development-only until the gate passes |
| Comparison | No meaningful reliability regression against the frozen release; at least one verified improvement in useful evidence delivery or failure recovery on cases not used for repairs | Matching fallback forecasts are not a successful graph A/B |
| Readiness audit | Requirements mapped to current artifacts, test scope, known gaps, rollback pin and exact candidate identity | No promotion on partial, indirect or missing evidence |

Unresolved outcomes do not establish Brier or forecast skill. Later outcomes must
be joined to frozen pre-outcome inputs and independently audited for public-result
leakage. Operational readiness and forecasting accuracy are reported separately.

Production is not changed by this gate. Historical provider allowances and
finished submissions remain immutable. New experiments have separately frozen
budgets and never silently reopen a parent ledger. The initial aim is a bounded
three-to-five-case gate, then broader frozen stress cases; a failed gate requires
a mechanism repair before expansion.
