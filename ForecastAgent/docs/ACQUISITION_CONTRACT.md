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

This bridge has been exercised with an offline test executor. It is not yet
wired into the production worker or validated with a live provider. Interrupted
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
