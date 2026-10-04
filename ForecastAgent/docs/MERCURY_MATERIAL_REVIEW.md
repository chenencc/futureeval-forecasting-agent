# Mercury Decide material review experiment

This development-only experiment replaces free-form material annotation with
finite typed decisions. Production collection and forecasting remain unchanged.

## Official usage checked on 2026-10-05

- [Mercury Decide model card](https://openrouter.ai/inception/mercury-decide:free):
  typed Choice, Score and yes/no decisions, free variant, 32,768-token context;
  it does not generate arbitrary evidence records or explanatory prose.
- [OpenRouter Decisions request](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request):
  POST `https://openrouter.ai/api/alpha/decisions`, body `model`, `state`, and
  `questions`; bearer authentication; typed answers and recorded usage.
- [System One SDK route](https://openrouter.ai/docs/guides/community/typesafe-sdk):
  POST `/api/v1/systemone` supports the same contract. The existing audited
  ForecastAgent adapter uses the documented alpha Decisions route.
- [Shared primitives](https://docs.typesafe.ai/primitives): ask a narrow judgment
  per question, reference state fields explicitly in instructions, and batch
  independent questions using the same state. Question IDs alone are not prompt
  instructions. Add an insufficient/none option to incomplete answer spaces.
- [Confidence](https://docs.typesafe.ai/confidence): Choice confidence summarizes
  the option distribution; it is not the selected option's probability and is
  not a certificate of correctness. Do not borrow a threshold without local
  evaluation. The shared API documentation describes the interface; its Jev
  cookbook performance numbers do not establish Mercury's performance.
- [Citation checks](https://docs.typesafe.ai/cookbooks/citation_check) and
  [pre-parsed extraction](https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook):
  code establishes literal identity and generates candidates; the decision model
  judges relevance or selects a candidate. This trial tests reference selection
  and classification, not arbitrary value extraction.

## Implementation

`supplement/mercury_material.py` reuses the complete saved reading packet and
frozen Super-generated needs. It generates original passage and rule IDs, then
asks four independent Choice questions for each need:

1. What supplies the requested field: source fact, rule definition, calculation,
   inference or insufficient material?
2. Which original reference is most relevant for further reading, including
   background references and NONE?
3. Does original source material support, contradict, contextualize or fail to
   address the condition?
4. Is the full condition supported/refuted, only a field available, or material
   insufficient?

Questions do not see each other's answers. Reference selection is independently
judged and does not secretly become the input for the other three questions.
Cross-head disagreements are explicit flags; original distributions survive.
Each selected passage/rule binds to its original hash and coordinates. Every
source remains in the packet even when NONE is selected. No condition closes
automatically. `observed_value` and `rationale` are null with explicit
not-extracted/not-generated statuses, not fabricated from rule targets or
program-generated descriptions.

This changes the interface as well as the model. Comparing it with Super V2
is a system-level comparison; it cannot isolate model intelligence. Planning
errors, including OR/exclusion omissions in frozen needs, remain outside scope.
An original text binding establishes identity, not semantic entailment.

## Frozen three-case trial

`experiments/MERCURY_MATERIAL_PILOT3.json` fixes three repaired cases (43494,
43501, 43824), 14 existing needs, code hashes, all source bodies, and arm order.
Each case receives one fresh Super V2 review and one Mercury finite review.
Mercury batches 12, 16, or 28 questions in its one request. Both arms see
identical original question, needs and reading coverage; Mercury additionally
gets the program-generated rule catalog and typed question instructions.

This is a new independently authorized trial: at most six logical decisions,
eight HTTP attempts, and four HTTP attempts per arm. Earlier experiments remain
recorded and unchanged (19 cumulative calls in the most recent old journal,
one old logical allowance remaining). No old quota is reset. Provider attempts
are durably reserved before HTTP and original replies are retained. Existing
output directories and GitHub job reruns cannot silently restart allowances.
Search, capture and forecast calls are all zero. No paid model fallback exists.

The application gate checks complete valid outputs from both arms. Manual audit
checks temporal silence, rule/source attribution, exact measure/window, source
identity versus incident qualification, lost evidence and contradictory heads.
Calls, latency, usage/cost, missing usage, source/need identity and reference
binding are reported separately. A three-case regression pilot cannot establish
unseen generalization, calibrated confidence thresholds or forecasting accuracy.

Before production adoption, freeze a stratified unseen cohort and review both
arms without version labels; preserve the failed and successful regressions.
