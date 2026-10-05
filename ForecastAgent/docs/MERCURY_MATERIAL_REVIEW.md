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

## Completed regression pilot

[Run 37245414693](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37245414693)
completed successfully on tested commit `c501e6e`. Both arms returned valid
complete outputs for all three cases. Mercury returned all 56 independent
typed answers for 14 needs; this is an interface pass, not a semantic pass.
The 51 focused tests passed locally and in the workflow.

| Question | Mercury material review | Fresh Super V2 review |
| --- | --- | --- |
| 43494, energy restrictions / PDF | Recognized the 50 MW rule definition; avoided asserting non-rescission from silence. The deadline field and the continuity condition still have different meanings. | Inferred non-rescission from no reversal mentioned and requested an external definition already in the rules. |
| 43501, security incident / HTML | Retained qualification gaps but selected a rule reference for an asserted source fact. The program flagged the disagreement. | Selected the official communication and retained affected-count, duration and ransom gaps. |
| 43824, ILS prices / JSON | Three needs converted insufficient or out-of-window Close records into `condition_refuted`. Two confidence values exceeded 0.94. | Retained missing in-window Adjusted Close values/dates; identified the price-drop threshold as a rule constant. |

Missing applicable data does not refute the event. The current guard caught the
source/rule disagreement but missed the symmetric combination of insufficient
origin and condition refutation. All original claims survive in the artifact;
no condition was automatically closed and no forecast was submitted.

| Actual provider-reported usage | Super | Mercury |
| --- | ---: | ---: |
| HTTP attempts | 3 | 3 |
| Input tokens | 18,807 | 346,663 |
| Output tokens | 3,015 | 52 |
| Reported cost, USD | 0 | 0 |
| Attempts with unknown token usage | 0 | 0 |

Six logical calls and six HTTP attempts consumed the independent trial budget.
There were no retries, searches or captures. Prior experiment counters remain
unchanged. Per-attempt latency was not recorded. Mercury reported 18.43 times
the input tokens, despite batching its questions into three HTTP requests.
This may reflect per-question expansion; the accounting mechanism was not
established. These counters are not unique context lengths, and they do not
establish a context overflow or a latency comparison.

The original artifact `11318783541` was imported into `E:/metaculus_data` and
the locally archived ZIP was independently hash-verified:
`4f2f1bb15ccb2d1a583e1b2d4ede1962996c681d71beda0326eff10295631206`.
The English audit, original decisions/distributions, and per-case evidence are
in `E:/metaculus_data/reports/mercury-material-pilot-audit-37245414693.json`.

Recommendation: keep Mercury experimental for narrow provenance/reference
selection; do not replace the full material-condition review or promote this
protocol to production. First separate document applicability from event truth,
separate field provenance from compound-condition provenance, and detect
disagreements symmetrically. Reduce unnecessary independent heads before a
separately authorized unseen trial. Confidence filtering alone cannot fix the
high-confidence refutations observed here. Production remains unchanged.

## Preregistered two-round follow-up

The user authorized automatic advancement through two rounds. Both rounds and
the V2 protocol are frozen before live execution, with no semantic tuning
between rounds. The comparison now isolates Mercury protocol V1 versus V2,
using the same free decision model; it does not compare model intelligence.

V2 separates a record's applicability from what it establishes about the world.
It provides `nonqualifying_record` without converting it to event refutation.
`exhaustive_refutation` requires complete entity, alternative and interval
coverage. Both positive and negative conclusions are checked for conflicting
applicability/reference answers and remain unverified original model claims.
The requested constraints keep their exact rule bindings in the output;
source references cannot select a rule as an observed source fact. V2 uses three
independent heads (reference, applicability, evidence), rather than four. It
retains all original source text and rules and does not extract observed values.

1. Round 1: fresh paired Mercury reviews of 43494, 43501, and 43824, using the
   exact previously frozen needs and source packets.
2. Round 2: 43343 (sports), 14025 (defense), 43911 (trade), 40967 (politics), and
   44801 (fuel prices). These materials were not used to repair Mercury, but
   they appeared in older trials; they are not globally unseen. One Super plan
   per case is generated from original rules and frozen for both Mercury arms.

Expected maximum consumption is 21 logical calls: 16 Mercury calls and five
Super planning calls. The physical limit is 24 HTTP attempts (6 in round 1,
18 in round 2). No old allowance is reset, no paid fallback exists, and existing
journals/reruns cannot restart this trial. Transport/account failure blocks
automatic advancement and preserves its reservation. Semantic failures are
audited after the frozen experiment; they never silently rewrite outputs.

The reviewer checks these original-material limits, not resolved answers:

- 43494: an announcement does not establish non-rescission through a future
  deadline; 50 MW is a rule definition, not a missing external observation.
- 43501: official communication does not by itself establish counts, duration
  or ransom. An actual source fact cannot bind to a rule citation.
- 43824: outside-window Close data does not establish inside-window Adjusted
  Close, and excludes only the supplied records. It cannot refute all events.
- 43343: distinguish the listed foreign participants from a complete host-team
  semifinal exclusion; do not infer completeness from one match alone.
- 14025: launch and flight to the target are reported; a hit is a separate
  qualifier. Preserve positive components as well as undisclosed details.
- 43911: a nearly finalized text or planned signing does not establish joint
  official announcement and completion before the deadline.
- 40967: political pressure/current office status does not establish departure
  or non-departure throughout the rest of the year.
- 44801: the AAA publisher matches, but a September monthly seasonal record or
  October weekly price does not establish an all-time high before September 6.

These reviewer expectations are not present in either model's request.
Report paired completion, semantic overclaims and lost positive components
separately from token consumption and reference identity. Independent typed
decisions cannot supply arbitrary values or a coherent free-form rationale.

## Two-round results

[Run 37246658675](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37246658675)
completed successfully on frozen commit `7a7e2b5`. All eight pairs returned
complete valid typed outputs. The experiment reviewed 33 material needs:
132 V1 decisions and 99 V2 decisions. The workflow's 86 focused tests passed.
Actual consumption was 21 logical calls and 21 HTTP attempts (16 Mercury
reviews and five Super plans), with no retries, searches, captures or forecasts.
Input/reading identity, binding replay, prior counters and trial caps all passed
the offline audit. The two protocols and cohorts were frozen before either
round; no tuning occurred between them.

| Manual semantic check | V1 | V2 |
| --- | ---: | ---: |
| Unsupported negative judgments, three regression cases | 3 | 0 |
| Unsupported negative judgments, five repair-heldout cases | 4 | 1 |
| Rows with program consistency flags, across 33 needs | 4 | 13 |

Unsupported negatives are manually audited material-need claims, not event
forecast accuracy. The answer spaces changed: record exclusion in V2 is
deliberately distinct from event refutation. More flags do not demonstrate
more model errors or greater accuracy by themselves; several flags expose
different scopes being used by independently answered heads.

The security case now selects the official source for confirmation. The ILS
case excludes wrong-window records without rejecting all possible qualifying
events. Defense retains positive launch/flight and date components. Trade and
sports remain conservative about missing qualifications. However, the politics
case still turns a May current-office article into an exhaustive departure
refutation and mistakes article timing for event timing. V2 flags these claims
but does not repair their semantics. The fuel case excludes wrong-window prices
appropriately while still mishandling the otherwise valid AAA source identity.
The ILS source-authority/ticker heads likewise disagree with their own global
time/measure applicability. The remaining issue includes inconsistent judgment
units, rather than only a model's failure to follow a negative-evidence warning.

| Actual provider-reported usage | V1 Mercury | V2 Mercury | Shared Super planning |
| --- | ---: | ---: | ---: |
| HTTP attempts | 8 | 8 | 5 |
| Input tokens | 674,974 | 517,707 | 5,641 |
| Output tokens | 123 | 99 | 4,291 |
| Reported cost, USD | 0 | 0 | 0 |
| Attempts with unknown token usage | 0 | 0 | 0 |

V2 reduced independent decisions by 25% and reported input usage by 23.3%.
Provider attempt times are preserved, but one paired observation per case
does not establish a stable latency advantage. Reported token counts may be
expanded across independent heads, not unique input text lengths.

The original artifact `11319387032` is locally imported and hash-verified at
`E:/metaculus_data`, original ZIP SHA-256:
`f89a0ebd905f12cc2434cd22a68c793b617f6968cb2a21f28d9f9bc6147e6adb`.
The English audit is
`E:/metaculus_data/reports/mercury-material-two-rounds-audit-37246658675.json`.
Review used visible protocol labels, so it was not fully blinded. No resolved
answers were used and no accuracy/Brier metric was produced.

Keep the protocol experimental. A next design must make field and condition
judgment units explicit: source authority or ticker validity must not inherit
an event window, and event-time support requires a real event witness. Preserve
all material and exact rule bindings. Detection of a disagreement is a separate
result from correcting it; production remains unchanged.

## Unit-aware V3 follow-up

The next authorized development trial reuses all eight previous original
packets and the exact 33 saved needs. It makes no new Super plans. Both V2 and
V3 receive the same complete original question/rules/reading at evidence review.
V3 adds a first Mercury request that classifies what each need asks using only
rules/needs: source identity, entity identity, rule definition, observed
attribute, occurrence, occurrence time, interval coverage, compound condition
or unclear scope. A dimension alone cannot redefine a compound condition.
These unit choices are model claims and can themselves be wrong.

The second V3 request explicitly receives those unit choices and selects a
joint relation/reference option. Source identity ignores unrelated event-window
failures; event-time evidence requires a reported actual event. Rule definitions
select original rule references. Partial, excluded, opposing and contextual
records remain available. The choice schema cannot claim a positive witness
while simultaneously selecting NONE, and it makes no whole-event refutation.
Literal binding and scope classification do not certify entailment or truth.

The trial freezes V2/V3 alternating arm order, input hashes and code before
dispatch. Limits are 24 logical calls and 24 physical attempts: eight V2
reviews plus eight V3 unit requests and eight V3 evidence requests. Prior
consumption is recorded unchanged. There are no searches, captures, forecasts,
paid fallback, or automatic journal restarts. Source/reference completeness,
unit errors, lost positive components, event-time overclaims and actual token
consumption are audited independently. Fewer event-refutation options cannot
by themselves count as a semantic improvement. Production remains unchanged.

## V3 regression outcome

[Run 37251487304](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37251487304)
completed on tested commit `1180b3e`. Both arms returned complete typed outputs
for eight cases and 33 fixed needs. The 69 focused tests passed locally and in
the workflow. Exact input coverage, original-response binding replay, prior
consumption and current caps passed the offline audit. The experiment consumed
24 logical calls and 24 HTTP attempts, with no retries, planning, searches,
captures or forecast submissions.

| Provider-reported usage | V2 | V3 units + evidence |
| --- | ---: | ---: |
| HTTP attempts | 8 | 16 |
| Decision questions | 99 | 66 |
| Input tokens | 519,686 | 375,051 |
| Output tokens | 96 | 101 |
| Reported cost, USD | 0 | 0 |
| Unknown usage attempts | 0 | 0 |

Despite the extra sequential requests, reported input usage fell 27.8%.
V3's measured provider-attempt time was longer in this single run; no stable
speed advantage is established. Token totals may expand per independent head.

The politics case now treats a May current-office report as an opposing
snapshot rather than interval-wide departure refutation, and explicitly
labels the date as publication-only. The ILS case retains Yahoo/ILS identity
while excluding only the wrong-window price records, with bound references.
The 50 MW and price-drop constants bind to original rules. AAA source identity
is retained as partial evidence instead of rejected for its price date, though
it is not yet fully matched.

Two new observation-scope problems prevent semantic acceptance. Fuel needs
`n2` (price greater than $5.016) and `n3` (observation before September 6) are
classified as rule definitions: the constraint is known, but the actual price
predicate/date has not been assessed. A rule match cannot close an observation
obligation. The cybersecurity confirmation need `N4` and integrated missile
test need `n1` also get event witnesses without explicit coverage of the
before-deadline and target-hit qualifiers, respectively; the fresh V2 arm
remains partial on these needs. These are semantic overclaims even though the
joint reference selection is structurally valid. The sports host-identity need
also lacks a selected rule identity reference, although the original rule and
target bindings remain retained.

V3's lack of whole-event refutation options and independently contradictory
heads is a schema property, not a measured zero error rate. Unit classification
can be wrong and compound witnesses still require qualifier coverage. The
next design needs separate requested parameters and external observation
obligations, with no disappearance of the latter when a rule constant is
selected. All original model claims remain unverified; no automatic condition
closure or production promotion occurred.

Artifact `11320009651` was imported into `E:/metaculus_data`; its locally
archived original ZIP was hash-verified at
`71c23df3a1a381d9fbc2b2d2c5c9e283ed3f5af6a5f51614d708f8c05c16c32f`.
The English semantic audit and per-need evidence are in
`E:/metaculus_data/reports/mercury-material-units-audit-37251487304.json`.
Review was not fully blinded. No resolved outcomes, accuracy or Brier metrics
were used. These eight regressions do not establish unseen generalization.

## V4 observation obligations: preregistered trial

`MERCURY_MATERIAL_OBLIGATIONS13.json` freezes eight previous regression cases
and their same 33 needs, plus five questions not used in this material-review
repair: crypto capitalization, music chart peaks, French eligibility law,
IAEA on-site inspections and Linux desktop statistics. They come from saved
collection packets and are not globally unseen. Source bodies can contain
post-event information; this is a material interpretation experiment, not a
leakage-safe forecasting backtest. Saved bodies are retained in full, with the
existing 60,000-character reader recording every omitted unit. Both arms see
identical delivered units. No new search, fetch or forecast is authorized by
this experiment.

V3 remains unchanged. V4 keeps every original need as an immutable root
observation question and separately retains the requested rule parameters.
A rules-only Super call proposes up to six atomic qualifiers per need; it
cannot see bodies or labels. Rule IDs are bound to exact original spans.
Knowing a threshold or deadline never deletes its price/date observation.
Mercury selects each root/qualifier relation and reference jointly. An apparent
complete candidate requires all root/qualifier claims to be explicit and then
receives one conditional Mercury request checking that they describe the same
actual observation. A shared URL is insufficient. Claims stay unverified and
no rule closes automatically, even after a positive coherence answer.

This is a system-level comparison: V4 adds a compiler and an extra conditional
decision. New-question needs are planned once and shared across both arms.
Limits are 70 logical calls, 74 physical attempts, 1,500 seconds, 16 needs and
100 proof heads per case. Requests exceeding the frozen 500,000-byte guard
fail without truncation. Models are fixed to free Super for planning/compiler
and free Mercury for decisions, with no paid or Super fallback. Prior journals
and consumption remain unchanged. The workflow rejects rerun attempts and
always uploads preserved state.

Audit root disappearance, actual event/date overclaims, omitted or invented
qualifiers, loss of independently valid source/entity facts, common-observation
coherence, reading omissions and actual provider attempts/tokens separately.
Format success and fewer available enums do not establish semantic quality.
Production remains unchanged pending the paired audit.

### Preserved-state runtime amendment

Initial run `37253179126` preserved 36 physical/logical attempts in artifact
`11321882212`, then stopped on an upstream Mercury `422` for the first larger
packet. Its baseline evidence heads each had 225 choices; the error did not
document a hard option limit, so size is a working diagnosis rather than a
verified service contract. Seven regression pairs completed. The energy V4
compiler also emitted the meaningful but undeclared `threshold` role.

The continuation keeps the same 70/74 cumulative caps and records 96 seconds
already elapsed. Completed arms, original replies and the failed request stay
preserved; cached plans, unit decisions and compiler replies are reused.
The taxonomy explicitly adds threshold without converting parameters to facts.
For large candidate inventories, both arms group contiguous delivered units
from the same body, with exact member IDs and span hashes. The complete reading
is unchanged and no units are dropped. The crypto case's 56 units become seven
source candidates (29 choices per baseline head). Candidate granularity changes
jointly for the heldout cohort, which must be distinguished from the unchanged
regression pairs. No attempt or token history is reset.

Run `37253866137` completed the energy pair without another compiler call, then
preserved the same crypto `422` at cumulative 38 attempts. Candidate count was
not a sufficient explanation. A local `cl100k_base` proxy estimated 38,411
tokens for the common state alone; the public Mercury page specifies 32,768
context tokens. This is supporting evidence, not its exact provider tokenizer.
The second continuation keeps the full local prepared state but sends only
verbatim source text, URLs, span coordinates, separately saved headers, rules
and relevant claims. Inventories, omission coordinates, source hashes and
duplicate claim quotations/distributions stay in the artifact. Omission counts
and an explicit absence warning remain in model state. The same crypto proxy
falls to 26,007 common-state / 28,051 maximum single-head tokens. Both paired
arms receive the same projection. Invalid-request `422` failures are isolated
to their case; authentication, account limits and service failures still stop
the trial. No hard provider option limit or guaranteed context fit is claimed.

Run `37254353088` reached all 13 cases at cumulative 58 attempts. Eleven pairs
completed; crypto V4 still exceeded the proxy context envelope (33,252 tokens
for its largest head), and the law planner emitted unsupported `effect` as a
dimension. The final bounded continuation retains every completed pair, moves
repeated rule quotations to their existing rule IDs (the full original rule
catalog remains available), and permits one schema-only planner repair. Repair
must preserve every need ID, condition, target and rule ID verbatim; it cannot
invent observations, remove needs or see source bodies. The failed proposals
remain archived. Crypto's projected largest head estimates 27,474 proxy tokens.
The same cumulative 70/74 caps remain unchanged.

## V4 paired outcome

[Final run 37254987807](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37254987807)
completed on `e8258d9`, continuing the exact journals of `37253179126`,
`37253866137` and `37254353088`. All 13 cases and 50 shared needs now have
complete typed outputs for both arms. The 72 focused tests passed locally and
in Actions. Original provider-response binding replay, actual semantic input
coverage and the unchanged parent attempt/call/provider-file prefixes passed.
All four original artifacts were imported and SHA-256 verified in the local
archive. The final original ZIP hash is
`a4d6e71030c3bfdffc6be1c2692b49301ec10bd7c59d8ae0a654218359df4b36`.

| Actual cumulative usage, including failed attempts | V3 | V4 | Shared planning/repair |
| --- | ---: | ---: | ---: |
| HTTP attempts | 28 | 31 | 6 |
| Known input tokens | 824,969 | 2,517,577 | 8,013 |
| Known output tokens | 141 | 14,092 | 5,204 |
| Attempts with unknown usage | 2 | 1 | 0 |
| Provider-reported USD | 0 | 0 | 0 |

The total is 65 physical and 65 logical calls within the unchanged 74/70 caps.
No search, capture or forecast ran, and no prior journal was reset. V4 reported
about 3.05 times V3's input tokens; the richer proof heads have a material
consumption cost even though both endpoints are free. Provider input accounting
can expand per head and is not the unique context size. Missing usage on three
HTTP failures is not zero.

The cyber before-deadline and missile target-hit overclaims become explicit
gaps/inferences. Both fuel observation roots survive instead of disappearing
into rule definitions. Six needs have `complete_model_candidate_unverified`,
two have `coherence_unestablished` and 42 are partial/unassessed. No semantic
truth or automatic condition closure is granted by any of these statuses.

The heldout questions expose transferable limitations:

- Crypto's shared planner changes an any-time "by" condition to an as-of
  snapshot. The common reader also omits captured USDC pages while allocating
  20,509 characters to Ethereum. Exact same coverage across arms does not make
  that coverage adequate.
- The music article reports a weekly peak #42 dated July 18, not the final
  peak through July 31. Conditional coherence blocks apparent completeness,
  but also conflates valid publisher identity with usable chart records.
- The legal case distinguishes the candidacy-blocking effect from an issued
  court penalty incompletely: an active prohibition on the specified date
  cannot be certified merely from the existence of an earlier sentence.
- IAEA source authority retained by V3 is lost in V4's broader credibility
  interpretation. The original Linux rule's August prose versus July URL
  remains ambiguous, while the constant 8% again becomes an external-data gap.
- The energy compiler changes a non-reversal obligation into a positive
  reversal qualifier. Its immutable root prevents replacement, but does not
  prove that the qualifier tree is semantically equivalent.

Consequently structural acceptance passes and semantic acceptance fails.
Production remains unchanged. The next improvements should preserve typed
Boolean/temporal operators, separate literal parameters/identities from
observations, and allocate reading by need rather than insertion order.
The full English per-question audit is
`E:/metaculus_data/reports/mercury-material-obligations-audit-37254987807.json`.
This is a saved-material system experiment, not a forecasting/Brier result,
a fully blinded review, or a homogeneous model-only A/B.

## V5 typed original roots: bounded regression

`MERCURY_TYPED_ROOTS5.json` preregisters a five-case, 19-need comparison using
the original energy, cyber, fuel, stablecoin and legal packets from run
`37254987807`. These are repair regressions, not heldout validation. Original
question text, original needs, rule catalog and delivered reading are identical
between arms. The existing omitted USDC pages remain omitted in this test;
reader allocation is a separate unresolved problem. Bodies may contain future
information. No outcome labels, forecasts or accuracy metrics are requested.

V4 reuses its frozen rules-only compiler proposals and reruns evidence plus
conditional coherence. V5 does not adopt those qualifier propositions. It
first asks Mercury to classify every unchanged original need by obligation
type, temporal operator, polarity, connective and need/rule alignment, using
rules only. A second request independently selects the original evidence and
relation for every original root. Proposed types are fallible, not program
verified logic. Literal parameters/identities have separate dispositions;
they cannot substitute for observations. An unaligned need or type/relation
disagreement retains the raw answer but marks consistency review. No rule
closes automatically and no model claim receives a truth certificate.

This isolates whether typed original roots avoid the previous affirmative
qualifier substitution and unnecessary observation-coherence checks for
literal parameters/identities. It does not establish that fewer evidence
heads preserve every atomic qualifier. Audit that regression explicitly.

Before calls, freeze the five packets, original needs, V4 contracts, alternating
arm order and code hashes. New experiment caps are 24 logical calls, 26 physical
attempts, 1,200 seconds and 500,000 request bytes. The previous 65 attempts and
their 70/74 caps remain unchanged; this is a separately bounded experiment,
not a reset. Mercury stays `inception/mercury-decide:free`; no Super, search,
fetch, paid fallback, forecast or production change runs. Resume requires the
same manifest identity and retains all calls, elapsed time and provider files.

Structural gates check original-span replay, exact reading equality, root
preservation, typed completeness and caps. Semantic review checks negative
non-reversal versus reversal, literal 50 MW, cyber confirmation timing, fuel
observed price versus rule target, crypto any-time-by versus as-of and legally
operative prohibition versus an earlier issued sentence. Identity/parameter
retention must not introduce new world-event overclaims. Improvement requires
manual evidence audit; a successful workflow is insufficient for promotion.

### V5 regression outcome

[Run 37261275819](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37261275819)
completed on `52186e5`: all five paired cases and 19 unchanged needs received
typed outputs. The 82 focused tests passed. Original provider-response replay,
exact paired material equality, source/rule span identity, manifest identity,
archived ZIP hash and unchanged prior history passed. Manifest verification
normalizes the local Windows CRLF to committed Linux LF bytes; this is not a
semantic or ledger change. The full English audit is
`E:/metaculus_data/reports/mercury-typed-roots-audit-37261275819.json`.

| Actual usage | V4 proof/coherence | V5 contract/proof |
| --- | ---: | ---: |
| HTTP attempts | 7 | 10 |
| Known input tokens | 862,016 | 346,756 |
| Known output tokens | 77 | 119 |
| Unknown usage attempts | 0 | 0 |
| Provider-reported USD | 0 | 0 |

The new trial used 17 total HTTP/logical calls within its 26/24 caps, with no
search, fetch or forecast. V5's reported input tokens fell 59.8%, but its call
count increased. Provider token accounting can expand per head; these values
are not unique context lengths.

The energy negative root stays negative/throughout, and its literal 50 MW
definition now binds to the original rule. AAA and Marine Le Pen identities
are retained without requiring event coherence. Cyber source context survives
without inventing counts; a mixed confirmation need selecting only identity
gets a consistency flag.

Semantic acceptance fails. Fuel's actual greater-than price predicate becomes
a pure parameter, and its required observed time is also typed as a parameter.
The model can still replace a world-observation obligation with rule knowledge
even when the raw root remains archived. All four crypto operators remain
as-of; two snapshot needs are incorrectly called aligned with the original
any-time-by rule. USDC omissions remain unchanged. The legal n3 remains an
explicit observation candidate despite a selected July 8 article stating that
the operative ineligibility had already been served and candidacy was legally
possible. Historical issuance remains conflated with active prohibition.

Do not promote V5. The next general contract must retain rule constants,
requested fields and observed predicates separately, and require exact
operator provenance before treating need/rule equivalence as established.
Independent identity/measure matching and event applicability also need
separate dispositions. Reading allocation remains a separate experiment.
No truth certification, automatic closure or forecasting-accuracy claim is
made by the current output statuses.

## V6 parameter/observation ledgers and original operator provenance

`MERCURY_OPERATOR_LEDGERS5.json` freezes the same five packets and 19 original
needs for a fresh V5/V6 paired regression. Both arms rerun rules-only contract
and evidence decisions with the same original question, needs, rule catalog
and delivered reading. V6 additionally reviews original-rule scope for source
claims labeled explicit. This is a system comparison, not a model-only test.
The reader and collection remain unchanged, including the omitted USDC pages.

Each V6 result contains independent requested-parameter, literal-context and
observation objects. A parameter/identity selection may populate literal
context, but it leaves the observation unassessed. A model's kind label cannot
remove an observation obligation or populate an observed value from a target.
Even a literal-only definition keeps the separate unassessed requirement until
its meaning is reviewed; the program does not assert that all such definitions
require external observations.

The program inventories exact lexical by/before/on/as-of, interval, negative,
active-state, operative-effect and definition cues in the original rule spans.
Mercury time, polarity and effect selections reference those exact operators.
A by-only rule does not offer an unanchored as-of choice. A paraphrased snapshot
without a bound original snapshot/date cue receives a program risk, even if
the model says aligned. Unbound negative/effect scopes are similarly retained.
These English lexical cues verify text identity, not full Boolean logic,
jurisdictional meaning, alternative nesting or semantic equivalence. Unknown
wording stays unassessed. They cannot silently repair the original need.

An explicit world-observation claim receives a separate Mercury review against
original rules and source text. The review distinguishes entailed, contradicted,
unestablished and literal-only; a past sentence/announcement is not proof of a
currently operative effect. The original claim and exact binding survive a
negative review. Unknowns, rules, targets and omitted text never prove absence.
No claim is marked true and no condition closes automatically.

New trial caps are 30 logical calls, 32 physical attempts, 1,200 seconds and
500,000 request bytes. Previous 65 and 17 request histories/caps remain unchanged.
Mercury remains free, with no new Super, search, fetch, forecast, reader change
or production promotion. Continuation requires the identical normalized
manifest and reuses completed contracts/proofs, preserving all reservations.

Preregistered review checks: observed-price and observed-time needs remain
independent of constants even under deliberately wrong parameter labels;
crypto by/as-of conflicts stay visible; the energy negative interval is never
replaced by affirmative reversal; active court effect is checked separately
from historical issuance; valid AAA/person identities remain available.
Audit exact spans, paired coverage, journals, usage, false entailment and lost
useful context separately. Structural safeguards and semantic improvement
are separate acceptance claims. These cases are repair regressions and do
not establish unseen generalization or forecasting accuracy.

### V6 paired result and manual review

[Run 37262674376](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37262674376)
completed on `e48d228`. All five pairs and 19 unchanged needs completed; 62
focused tests passed. Original provider replay, exact source/rule/operator
spans, paired delivered materials, preserved prior budgets and the locally
archived original ZIP hash passed. All responses identify
`inception/mercury-decide-20260930`. The English structured review is
`E:/metaculus_data/reports/mercury-operator-ledgers-audit-37262674376.json`.

| Actual usage | Fresh V5 | V6 |
| --- | ---: | ---: |
| HTTP/logical requests | 10 | 11 |
| Known input tokens | 350,298 | 871,483 |
| Known output tokens | 119 | 140 |
| Unknown usage attempts | 0 | 0 |
| Provider-reported USD | 0 | 0 |

V6 used five contract calls, five proof calls and one conditional scope review.
Its reported input usage is 2.49 times V5. Per-head provider accounting is not
unique context length. No call or token efficiency improvement is established.
No search, fetch, forecast or production promotion occurred.

Structural safeguards pass: all 19 durable observation objects remain even
under parameter/identity choices, and literal bindings cannot supply observed
values. All four crypto snapshot paraphrases receive an explicit original-rule
conflict flag. Fuel's greater-than and observed-time roots no longer become
pure parameters. The legal source's already-served term and legally possible
candidacy lead the scope review to reject the earlier explicit active-ban
candidate, retaining its original evidence and decision for audit. AAA/person
identity context and the original negative non-reversal root remain available.

Semantic acceptance still fails. Energy's official August 3 directive changes
from inferred to excluded despite providing useful restrictive-rule context;
the selected span does not establish the 50 MW condition, so this is context
loss rather than a certified false rejection of a complete event. Its negative
interval also becomes any-time-by. The legal literal date receives contradicted
instead of literal-only: the effect check contaminates a date-only field.
Fuel's October price cannot refute an earlier threshold crossing, and measure
identity is still conflated with temporal applicability. Crypto need3 retains
an aligned/partial summary despite its explicit program conflict. Consumers
must inspect the flags. Saved USDC omissions remain unchanged.

Do not promote V6. Retain the non-destructive ledgers and exact provenance,
but separate independent field identity from event scope, normalize conflict
status precedence and compress repeated operator choices before another
bounded experiment. These five repair cases do not establish unseen quality,
truth certification or forecasting accuracy.
