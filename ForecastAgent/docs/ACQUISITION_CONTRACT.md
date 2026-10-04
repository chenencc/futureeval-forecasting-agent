# General acquisition contract v1

This is an opt-in development interface in `dev_formal`. It does not replace
release 1.0.1, alter the existing collection worker, call a provider, restart
budgets or submit forecasts. The existing V2/V3 paired experiment remains frozen.

## Four capabilities

| Capability | Interface | Responsibility |
| --- | --- | --- |
| Atomic requirements | `requirements(request, proposals)` | Bind each proposed need and target to exact rule coordinates. Quarantine invalid proposals. |
| Requirement-to-text ledger | `bind_annotations`, `coverage` | Bind source IDs and exact saved ranges per need. Retain support, counterevidence, background and unknown annotations. |
| Structured reading | `reading_packet(pages)` | Reuse complete saved lines and table rows; carry exact table headers; preserve whole JSON values. Account for omitted units. |
| Gap-directed actions | `next_action`, `action_plan` | Separate capture failures, omitted content, format repair, discovery, uncertainty and downstream conflict review. Respect caller capacity and executed-action keys. |

The callable adapter is `run(bundle, needs, annotations)`. Model-independent
planning/review prompts and OpenAI-compatible function definitions are exported
as `PLANNING_PROMPT`, `REVIEW_PROMPT` and `agent_tools()`. `agent_packet()` delivers
saved text once with IDs, coordinates, hashes and explicit omissions.

`agent_review(bundle, execute, checkpoint)` orchestrates at most two logical
model decisions: requirement planning and saved-material annotation. Its
injected executor receives `(phase, prompt, payload, tool_definition)` and must
reserve persistent quotas before each actual HTTP attempt, retain provider
records and return complete parsed arguments. Its checkpoint callback must
durably save completed phases. Invalid response envelopes are saved before
raising; automatic retries are not performed. Empty reading packets skip the
annotation call. Logical decisions are not actual HTTP-attempt counters.

This bridge has been exercised with an offline test executor and a live provider
pilot that failed before material annotation (see below). It is not wired into
the production worker. Interrupted
bridge execution requires the caller to use preserved phases and reservations;
calling the bridge again from the beginning is not an automatic resume path.

## Requirements

A proposal contains `id`, `condition`, `critical`, `origin` and `targets`.
Origin identifies `question`, `resolution_criteria` or `fine_print` plus exact
character start/end offsets. The program reconstructs quotes. If a quote is
provided, it must match exactly. Every target has its own origin.

Optional target dimensions are entity, measure, unit, scope, observation time,
publication time, effective time, stage and artifact. Require only dimensions
needed by the question. Stage values use domain language. Separate independent
conditions; alternative ways of acquiring the same material belong to one need.

An explicit publisher restriction requires `required_source_domains` and a
`source_origin` that contains each domain. A secondary article remains saved
even when it cannot meet that restriction. Host checks use domain boundaries.
Rules specifying an agency name without a domain require a separately audited
publisher registry in the caller; this interface does not guess that mapping.

Literal rule binding cannot prove that a model interpreted the rule correctly
or identified all conditions. `target_semantics_verified` and
`semantic_completeness_verified` remain false. Test automatic planning against
human-reviewed requirements before adopting it in production.

## Evidence annotations

An annotation contains `need_id`, `passage_ids`, `relation`, `fit`, `explanation`
and `observations`. Each annotation references one source; multiple annotations
can jointly cover a need. There is no separate global verdict or three-passage
ceiling. The packet and selected-character budgets bound context instead.

The program verifies saved hashes and ranges. Interpreted values remain model
claims. No boolean agreement check is presented as independent semantic review.
A pending application can inform a completion need as counterevidence; it is
not converted into proof of approval. Inapplicable/background materials remain
in the ledger for later use.

Coverage states are `unreviewed`, `context_only`, `partially_covered` and
`covered_by_model_claim`. A complete collection of claimed dimensions does not
verify their correctness or resolve the event. Contradictory support and
counterevidence are retained and flagged for analysis.

Invalid annotations are quarantined per record. A changed body invalidates its
old bindings. An invalid requirement is retained as a repairable gap rather
than silently disappearing from the task.

## Reading and omissions

The reader preserves complete saved lines and table rows. Split tables include
exact header context. Complete JSON objects/arrays are retained as whole values.
Oversized JSON values and oversized saved lines remain omitted; they are not
silently truncated. A future JSON record reader can improve this coverage.

Default delivery capacity is 60,000 characters. Selection is deterministic in
saved-source order, not a semantic relevance ranking. Omitted material stays
explicitly inventoried. Raw HTML cleanup, PDF extraction and browser capture
remain responsibilities of existing tools; this layer consumes their saved
text. Complete saved text does not establish upstream completeness, publisher
authenticity or historical availability.

## Action routing and budgets

| Gap | Proposed action |
| --- | --- |
| Invalid requirement | Bounded requirement repair |
| Failed capture | Alternative reading tool |
| Saved content omitted | Read saved content |
| Invalid annotation format | Bounded annotation repair |
| Inapplicable material or unlocated source | Discovery |
| Semantic uncertainty | Review saved content |
| Conflict or covered model claims | Handoff to analysis |
| No eligible capacity or action already executed | Stop with preserved gap |

`remaining` maps action names to capacities; `attempted` stores executed
`(need_id, gap, action)` keys. Model repair/review defaults to at most one attempt
in this interface. Neither function reserves or executes tools. A runtime caller
must atomically reserve its persistent provider/tool budget before executing
the proposed action, then record the outcome. Shared discovery capacity must
be checked against Tavily/Exa lifetime limits; an action capacity is not a new
provider quota. Existing attempts and reservations must never be reset.

`action_plan` is conservative when omissions cannot be linked to a specific
need. It proposes reading saved content, without claiming the omitted source
is relevant. Per-need capture failures require the caller's acquisition ledger.

## Offline entry point

Input JSON contains `bundle` (request and pages), `needs`, `annotations`, optional
`max_chars`, `remaining`, `attempted` and `review_attempts`.

```sh
python -m ForecastAgent.supplement.acquisition_contract --input input.json --output ledger.json
python -m unittest ForecastAgent.tests.test_acquisition_contract
python -m ForecastAgent.experiments.acquisition_contract_replay --manifest ForecastAgent/experiments/MATERIAL_FIELD_PAIRED_UNSEEN10.json --output replay.json
```

The replay audits source hashes, exact ranges, headers and omission accounting.
It does not generate new model decisions or measure semantic improvement.

## Adoption criteria

Keep three separate gates: program integrity regressions, single-condition
counterfactuals, and unseen end-to-end question planning plus material review.
Evaluate useful material recall, false applicability, actionable gaps, format
failures and actual provider calls/tokens. Include negative evidence, partial
coverage, ambiguous labels and multiple document formats. Retain repaired cases
as regressions; they no longer count as unseen validation. Do not promote this
interface on offline binding success alone.

## First live pilot: application acceptance failed

Run [37218949703](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37218949703)
tested the contract frozen at `6b35064`, using fixed free Super and ten previously
seen saved excerpts with complete original questions and resolution criteria.
No hand-written requirements, prior answers or resolution labels were supplied.
The caller supplied mechanical rule-span coordinates without semantic hints.

All ten HTTP attempts returned model replies, with 38,200 reported tokens and
no unknown usage. Three replies supplied malformed serialized JSON inside the
`needs` string. The other seven replies supplied arrays containing 21 needs;
all 21 failed exact rule-origin binding because offsets/quotes did not agree.
No valid requirement survived, and the material annotation stage made zero
calls. Searches, fetches and forecast submissions were all zero.

The Actions run succeeded operationally. This does not pass the business gate:
seven cases were recorded as completed with empty accepted plans and three as
failed. Future acceptance must distinguish provider completion, valid response
shape, usable requirements and actual evidence annotation. An entirely rejected
plan cannot count as a successfully reviewed question.

The main general design issue is asking a model to calculate character offsets.
Program-owned rule references should replace generated coordinates; incomplete
nested JSON must not be salvaged. This pilot provides no semantic recall,
applicability or full-pipeline improvement result. Keep production unchanged.
Original provider records and the failed planning outputs are retained in the
`acquisition-contract-live` artifact (ID `11310135532`), whose ZIP SHA-256 is
`609297924f87080a9576f9a0e8e71d05c35f6f57ef933165bda75a1b7e891763`.

## Rule-ID interface V2

The independent development module `supplement/acquisition_ids.py` leaves the
failed V1 implementation and its original evidence unchanged. Rule references
use program-generated IDs derived from field hashes and sentence/line ranges.
The model never calculates offsets or copies rule quotes. Each flat need has
one target dimension, including a separate `source` dimension; each flat review
row has one interpreted observation. Binding and gap records retain the shared
saved-body reader and annotation ledger.

Complete serialized arrays may be decoded with an explicit compatibility log;
incomplete JSON, fabricated rule IDs, changed rule text and unbound assertions
are rejected. Source-domain gates are derived only from literal rule URL hosts
selected by an explicit source requirement. Textual agency names remain model
claims requiring later authority matching.

Application states distinguish failed requirements, failed annotation, partial
review and reviewed materials with gaps. Empty or entirely rejected requirements
fail the business gate; so does unreadable review output for readable materials.
An explicitly recorded unknown assessment is a gap, not verified absence.

The first V2 pilot is frozen to three repaired cases: a PDF directive, a security
incident question with multiple conditions, and a structured market record.
It restores the exact V1 parent state (run `37218949703`) and its 10 consumed
HTTP/logical attempts. V2 adds at most 6 logical decisions and 10 HTTP attempts,
within the original cumulative 20/30 limits. Parent request records and counters
are preserved, and parent state/identity hashes must match before any call.
This is repaired regression validation, not a new unseen cohort or a forecasting
evaluation. Production remains unchanged pending actual outcome review.

### V2 pilot outcome

Run [37220371858](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37220371858)
on `455973e` reached both model stages for all three cases. It made 6 new logical
decisions and 6 new HTTP attempts, consuming 29,528 new reported tokens. The
cumulative totals are 16 decisions, 16 HTTP attempts and 67,728 tokens, with no
unknown usage. All 41 preserved parent data files and the previous attempt and
decision records remained unchanged. No searches, captures or forecasts occurred.

All 14 proposed needs were bound to known rule IDs. Six evidence annotations
were bound to saved text, and eight unknown assessments were retained. There
were no format rejections or serialized-array repairs. This demonstrates interface
compatibility on repaired cases; it does not establish semantic correctness.

Manual review identified unsupported inference of non-rescission through a
future deadline, confusion between question-defined constants and externally
observed facts, and an unsupported equivalence of Close and Adjusted Close.
Atomic target dimensions can also be narrower than their compound conditions;
source identity does not establish all qualifying event conditions. The plan
does not independently certify that exceptions and OR relationships are complete.
Keep these cases as regressions and preserve the unverified interpretation flags.

A subsequent offline-only label repair distinguishes `reviewed_uncovered` from
`unreviewed` for explicit unknown assessments. Its replay changes no model claim,
source content or original artifact and consumes zero additional requests. The
three-case manifest still pins the actual tested implementation; it is not
silently repinned to this later change. Any further live experiment needs a new
freeze and the latest cumulative state, not the original parent allowances.

## Provenance review V3 (development only)

`supplement/acquisition_provenance.py` separates model-declared `source_fact`,
`rule_constant`, `derived`, `inference`, and `unknown`. Explicit source facts
require a uniquely aligned original passage witness; rule constants require a
rule witness instead of a page citation. Derived values and interval inferences
retain premise references but cannot become direct source observations. Invalid
rows are quarantined independently, with their original content retained.

Coverage describes field evidence, rule definitions, interpretations, and gaps.
Every compound condition remains `unverified`: literal entity/value matching
does not establish event stage, time coverage, exceptions or the whole condition.
Values absent from a witness remain source interpretations rather than being
silently normalized into literal observations. Classification and relevance are
still model claims. An incorrect source-fact label can remain a semantic error
even when its quotation binds correctly; this requires independent audit.

Recorded interpretations go to analysis, without an unlimited acquisition retry
loop. An explicit unknown or invalid row remains a gap. Original source bodies,
background records and counterevidence are retained. The existing production
pipeline and frozen V1/V2 implementations are unchanged.

### Validation sequence

1. Run provenance counterfactuals and existing compatibility suites. They cover
   rule/source attribution, interval silence, compound conditions, Close versus
   Adjusted Close, negative/background retention, changed captures, malformed
   rows, and cumulative budget preservation. These are engineering checks,
   not model-quality measurements.
2. The frozen `ACQUISITION_PROVENANCE_PAIR2.json` compares two repaired cases
   using the same automatically generated V2 needs and identical reading packet.
   V2 and V3 each get one review call; order is counterbalanced. Model, fallback,
   output and reasoning limits stay fixed. No additional planning, discovery,
   capture, or forecast calls occur. Inherit run `37220371858` and its 16 consumed
   calls; add at most four logical/eight physical attempts within the original
   cumulative 20/30 limits. Original parent records must match exact hashes.
3. Audit original replies against the preregistered checks, not just the new
   labels or Actions status. Count incorrect provenance, unsupported interval
   claims, incorrect measure equivalences, lost useful evidence, format failures,
   and actual attempts/tokens. Two repaired cases do not prove generalization.
4. Before rollout, freeze ten unseen questions stratified by domain and document
   format. Independently record expected material requirements and source facts;
   compare both versions on identical texts under a fixed model/budget, with
   version-hidden audit. Do not expand if useful evidence is lost or condition
   claims remain unsupported. Full rule-plan completeness and OR/exclusion
   extraction need a separate assessment, since the paired review freezes plans.
