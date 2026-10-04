# Material gap acquisition experiment

This development-only workflow uses `collection_workflow: material-gap-v1`, an
explicit experiment ID, and `pipeline: collection`. Production routing is not
changed. The `gap2` cohort runs the existing IMO and Kaub acceptance cases.
The `material_new5` cohort freezes five distinct housing, gasoline, court,
outbreak and case-database acquisition questions. Its ten material requirements
and comparison limitations are preregistered in `COLLECTION_COHORTS.json`.
It additionally freezes `collection_stage_allocation: material-reserve-v1`:
initial acquisition gets four Tavily and one Exa attempts, 20 model decisions,
26 model HTTP attempts and 1,200 seconds. The original totals remain six Tavily,
two Exa, 24 model decisions, 30 dispatch HTTP attempts and 1,500 seconds.
The remaining capacity is reserved for supplementation. Default and older
cohorts keep their allocation unchanged; no existing ledger is upgraded.

## Flow

1. The existing model builds the acquisition plan and discovers sources.
2. The program executes exact discovered reading batches. Each URL is still
   validated against the catalog and charged to existing HTTP capacity. These
   program batches are recorded separately from model HTTP requests and do not
   acknowledge source delivery to a model that was not called.
3. A document ledger separates discovered links, readable saved bodies and
   target-material capture. Lexical candidates never close a critical need.
4. At inventory boundaries, a bounded model adapter reviews saved source
   windows, ranks observed URLs and recommends one missing-material query.
   The program binds exact quotes and source hashes before accepting fit axes
   for entity, document type, metric and period. Negative notices remain useful
   material; document fit never means the event occurred.
5. Independent supplementation processes prioritized existing leads, then searches
   missing critical document families even when other readable bodies exist.
6. Each discovery request is reserved before HTTP. One request per need/provider
   is permitted, with no transport retries. Discovered exact URLs join the bounded
   capture frontier. Bodies and failed attempts remain auditable.
7. Export the material ledger and termination reason for later analysis.

## Files and meaning

- `acquisition/material-needs.json`: initial acquisition handoff.
- `supplement/material-needs.json`: document candidate inventory after repair.
- `supplement/termination.json`: stop reason and unresolved critical need IDs.
- `supplement/state.json`: durable reservations and provider records, including
  an empty journal when no requests were made.
- `intelligence-bundle.json`: original and supplemental capture inventory.

`candidate_captured` means a readable body has observable family/topic signals.
It does not establish the event, exact model identity, temporal equivalence,
correct numerical value, or satisfaction of the resolution criteria. Candidate
URLs carry body hashes and remain subject to later analysis. Future-rollout
language alone does not count as a current-access document candidate.

`acquisition_state` is authoritative for material handoff: `candidate_discovered`,
`readable_body_saved`, `target_material_captured`, or `unlocated`. The older
`status: candidate_captured` is retained as a lexical inventory diagnostic only.
A target material needs quote-bound model fit on every applicable axis, or an
exact structured measurement capture with identifier, date, unit and requested
clock present. This is an acquisition fit assertion, not independent truth
verification. Quote validation prevents invented citations, but cannot guarantee
that a model's interpretation is correct; blind review remains required.

Missing material is distinguished from `search_attempted`, `discovery_failed`,
`read_failed`, and `budget_exhausted`. Timestamp conversion and condition binding
are deferred to analysis. Sources absent from the ledger may still be saved in
the raw inventory. Unsupported fit stays missing even after all candidates were
read. Termination distinguishes `materials_ready`, `material_unlocated`,
`source_unreadable` and `budget_exhausted`; explicit provider/review errors and
underlying reasons are retained. No successful search is required for a forced
bounded exit, and a candidate body alone never establishes readiness.

The frontier retains rule URLs and target data files ahead of agent choices.
Explicitly deferred observed URLs retain their reasons. Duplicate bodies and
context-only captures do not expand child branches. Two consecutive captures
without relevant material on a host defer ordinary details there; primary URLs,
material dependencies and explicit model priorities are exempt. These are
bounded recall heuristics, not proof that deferred pages lack useful evidence.

## Shared limits

The experiment retains six Tavily BASIC requests, two Exa requests, 32 initial
HTTP attempts, 64 combined HTTP attempts, 12 browser renders, and all frozen
model/time/Extract limits. These are task lifetime totals across initial and
supplemental stages, not separate budgets per stage. Default profiles keep their
existing limits. Additional experimental searches accept `gap` and `crosscheck`
roles; the old production role restrictions remain unchanged.

Material review is limited to four decisions including failures, and debits the
remaining initial dispatch model decision/physical HTTP/failure/time allowances. It uses
the existing configurable model interface and routing policy. Requests, known
usage, failed/interrupted reservations and validated decisions are persisted in
`state.json` and `material-model-*.json`. A changed body invalidates its binding.
Inventory fingerprints prevent repeat reviews on resume. Invalid decisions stop
the review adapter and leave gaps open; bounded program discovery can continue.
Tool descriptions, role enums and execution now share `runtime/search_contract.py`.

## Review transport compatibility

Material review uses a compact function contract: existing need IDs, saved
passage IDs, four fit axes, source IDs and at most one bounded query. The model
does not reproduce page quotations or long URLs. The program resolves IDs to
delivered windows, rechecks body hashes and binds exact saved text. Six bindings
and six source selections per batch bound visible output size.

The generation profile is 4,096 total completion tokens with an explicit
768-token reasoning budget. OpenRouter's public model directory reported
`supports_max_tokens: true` for the authorized Ultra and Super free models on
2026-10-04. This is request configuration, not proof that every provider honors
the cap; audit actual `completion_tokens_details.reasoning_tokens` and response
finish reasons during live acceptance. Excluding reasoning text is not a budget
limit and must not be used as a substitute.

Native function arguments are preferred. A complete JSON object in `content`
may use the identical ID and binding validation; prose, unknown IDs, incomplete
JSON and any `finish_reason: length` are rejected. Truncation is recorded as
`output_truncated`, distinct from a missing document. `review_incomplete` and
`critical_unverified_need_ids` expose unresolved fit. The legacy
`critical_unlocated_need_ids` field remains for compatibility and means
unverified fit, not confirmed document absence. Failed calls remain consumed;
compatibility repair never restarts search or model allowances.

Missing credentials disable that channel. HTTP 401/402/403/429 discovery failures
stop further gap discovery and preserve state; they do not trigger a second
provider to bypass an account failure. Failed and interrupted reservations count
as consumed. Resume uses the same parent hash, journals, implementation hashes,
capacity, and enabled channel set; it cannot silently restart allowances.

## Acceptance

Run offline routing, missing-family discovery, resume, accounting, and program
reading tests first. Compare online `gap2` artifacts with run `37134826108` using
the same question definitions and resource limits. This is an exploratory online
comparison, because source/provider responses vary between runs. Inspect actual
saved documents, requests, failures and remaining needs; job success alone is
not material completeness. No forecasts or analysis scores are produced.
