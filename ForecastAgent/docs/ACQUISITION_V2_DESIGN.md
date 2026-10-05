# Acquisition V2: release-based research design

Status: the first opt-in runtime is implemented; see
[Intelligent acquisition V1](../acquisition/README.md). Production workflows,
provider ceilings, and release analysis remain unchanged. Live acquisition
quality and provider reliability have not been established by offline tests.

## 1. Baseline and experiment boundary

- Branch: `dev_acquisition_v2`.
- Base: release `v1.0.1`, commit
  `c9bfab44a670f4307ebc2330866479c82aec71ef`.
- Worktree: `D:/metaculus/.tmp/dev_acquisition_v2`.
- Production remains pinned to the release. Development experiments cannot submit.
- Codex is the operator; it is not a dependency of either runtime module.
- The original acquisition and deterministic supplement form one acquisition
  module. The existing Mercury analysis and authorized fallback form the analysis
  module. Delivery remains separate infrastructure.
- Freeze the analysis code, prompts, projection, conditional rereading, clipping,
  payload validation, and fallback policy throughout acquisition experiments.
  Existing analysis may make conditional local reads. It may not launch new
  discovery or acquisition inside these experiments.
- The baseline manifest records every tracked release `ForecastAgent` file hash
  and explicitly identifies analysis files that must remain unchanged. Verify
  these identities before a live experiment.

This work does not import earlier development fixes wholesale. A mechanism must
have an identified failure, a bounded implementation, and its own measured test.

## 2. Two modules and one durable boundary

```text
Immutable question + source policy + frozen budget profile
                         |
                         v
                  ACQUISITION MODULE
  plan -> discover -> capture -> read -> bank -> inspect coverage
              ^                                  |
              +------ permitted gap action -------+
                         |
        deterministic independent capture repair
                         |
         local integrity check and compatibility export
                         |
                 Immutable evidence package
                         |
                         v
                   ANALYSIS MODULE
       unchanged v1.0.1 Mercury original-evidence analysis
                         |
              validated forecast artifact
                         |
            separate production delivery adapter
```

The collector selects and preserves information. The analyst interprets event
conditions and produces probabilities. Selecting relevant material inevitably
requires some interpretation, but acquisition annotations are retrieval hints,
not verified facts, event verdicts, or probabilities.

Acquisition does not depend on the analyst's current probability. A higher final
score cannot be used to select an evidence package after outcomes are known.

## 3. Agent role and model choice

### Initial policy

Use the release's generic tool-calling collector with Ultra as primary and Super
as its service-failure fallback. Keep provider configuration separate from
instructions, schemas, tools, skills, and task state.

Do not add Mercury to acquisition in the first candidate. Its existing analysis
role remains frozen. A later optional candidate may use Mercury for a bounded
decision over observed source handles. It cannot generate a missing query or URL
through a choice response, and it cannot replace a free-form collector merely
because its answers have a structured format.

| Model | Proposed role | Evidence and limits |
| --- | --- | --- |
| Ultra free | Primary planner and tool caller | OpenRouter documents long-context planning and tool calls. This is a suitability hypothesis, not a measured acquisition victory. JSON `response_format` enforcement is not advertised; validate actual tool arguments in the gateway. |
| Super free | Same-protocol service fallback; later separate primary-model candidate | Tool calls and JSON schema output are documented. Local runs show it can execute the pipeline, but there is no clean matched acquisition comparison proving superiority. |
| Mercury Decide free | Frozen analyst; possible later bounded triage candidate | Its System One interface accepts supplied state and typed choice/score/yes-no questions. It is different from Mercury chat models. A decision probability is not validated evidence completeness or forecasting calibration on our task. |

Operational fallback preserves the task state and search/capture reservations.
Record the actual backend and reason for every attempt. The release falls back
after two consecutive qualifying service failures; credential, invalid-request,
and shared account quota errors do not trigger it. Preserve that baseline behavior
initially. Any revised reset or per-model allowance policy requires a separate
explicit budget profile, never a silent counter rewrite.

For a model comparison, freeze one model per arm and report failed arms. A mixed
Ultra/Super arm can measure an operational policy but cannot establish Ultra-only
or Super-only quality. Documentation and historical availability do not substitute
for a same-input test. No provider health requests have been made for this design.

## 4. Autonomous acquisition loop

### A. Construct the material specification

Preserve the original question, background, fine print, type, options/scaling,
opening timestamp, deadline, event window, and explicitly designated sources.
Unknown values remain unknown; never infer an opening timestamp from an article.

The agent proposes a small material specification, normally three to six needs:

- The designated resolution source or current status of that source.
- The exact entity, document, series, metric, or event phase.
- Relevant dates, observation period, units, and aggregation convention.
- Useful independent context and base-rate data when discoverable.
- Conflicting or later-corrected source versions, when observed.

A future outcome value is not a required present-day document. Mark genuinely
not-yet-published material as future/unavailable and collect currently useful
context. Lack of a final outcome is not a collector defect.

Each proposed need cites question spans and states a material target. The program
can check binding integrity, not semantic correctness. Preserve immutable rule
requirements. The agent may revise discovery hypotheses with an audit trail, but
cannot delete a hard requirement merely because retrieval failed.

### B. Select the next informative action

Maintain a frontier of observed source URLs, documents, pages, rows, and supported
official adapters. Each action names a need, target handle, expected new material,
and a stop condition. Do not require numeric expected-information-gain estimates
that cannot be validated.

Prefer the action likely to fill a critical uncovered material target:

1. Reuse already captured bytes and local passage/row navigation.
2. Fetch the designated or specifically relevant discovered source.
3. Follow observed detail-page, release-note, appendix, table, or download links.
4. Use a targeted remaining search if the frontier cannot address the gap.
5. Route a failed critical capture through an available repair method.
6. Preserve the gap when no permitted useful action remains.

The agent can choose queries, source roles, tools, and follow-up leads. The runtime
enforces schemas, discovered-handle integrity, reservations, duplicate suppression,
deadlines, and spend limits. Website text cannot change these instructions.

### C. Capture and inspect source material

Reuse release tools and their channel catalog instead of building another crawler.
Keep HTTP, browser, PDF, HTML, datasets, and local reading behind a common result
envelope, with source-specific provenance available separately.

Mechanical capture checks include response type, body hash, empty/login/navigation
shells, parser failures, truncation, and source version. A successful request is
not a successful readable capture. A readable capture is not factual support.

For tables, preserve header/units, relevant complete rows, dates, and identifiers.
For prose, preserve nearby headings, dates, and document identity. Do not repair
missing cells by model generation. Preserve unavailable cells and formulas as such.

Independent supplement remains a bounded deterministic repair stage. Before
adding an agent-driven second discovery round, measure whether better original
planning and delivery already fix the target failures. Avoid two collectors that
repeat the same search or blindly reprocess every page.

### D. Inspect collection coverage and close

Track separate states for each material target:

`lead -> captured -> readable -> locally located -> banked -> handoff eligible`

These states describe information handling. They do not mean the source is true,
the model understood it, or the event condition is satisfied.

Coverage inspection asks what original material is absent and whether there is a
specific permitted action to obtain it. It does not ask whether the event is YES
or NO. Record agent-declared relevance separately from program-observed integrity.

Progress requires new usable material, a previously uncovered relevant range, or
a newly executable lead. Repeated catalogs, copied summaries, rejected parameters,
empty bodies, and repeated reads cannot extend execution.

Stop when critical currently obtainable targets have associated material, no
useful permitted action remains, a deadline/cap is reached, or the existing stall
policy closes the session. Report the precise gap and next possible action. Do
not force another recent search or extra model call just to complete a checklist.

## 5. Evidence package and fixed-analysis compatibility

Use one immutable package per acquisition output. Its manifest includes:

- Question input hash and complete original fields.
- Baseline/candidate code, model, prompt, skill, and budget identities.
- Original provider search results, queries, parameters, and attempt records.
- Source versions, URLs, retrieval/publication/observation times with uncertainty.
- Raw-byte, parsed-text, document/page/row, and selected-span hashes.
- Exact excerpts with offsets, headings/units/dates, source and material-target IDs.
- Observed integrity flags, agent relevance annotations, unresolved conflicts,
  gap classifications, attempted repairs, and continuation state.
- Actual model HTTP attempts by backend, known tokens, unknown usage, search,
  capture, browser subrequests, wall time, and all remaining reservations.

The adapter exports the existing release bundle shape for the unchanged analyst.
Preserve whole bodies even when excerpts are selected. Unsupported new metadata
belongs in a sidecar; do not alter the fixed analyst to make it use those fields.

Measure three distinct quantities: relevant material captured, material banked in
the package, and material included in the analyst's actual requests. If the fixed
analyst omits a correct banked table row, classify it as a handoff/projection gap.
Do not claim collection improvement alone fixed downstream visibility. Changing
the analysis projection would be a later, separate experiment.

Polymarket remains a separate market-signal sidecar. Preserve contract rules,
timestamps, prices, and match differences. Similar titles or price disagreement
cannot certify proposition equivalence, source truth, or evidence completeness.

## 6. Budget policy for the first candidate

Begin with the release profile; do not increase allowances while changing control
flow. Budgets are frozen at task creation and preserved across resume/fallback.

| Resource | Release baseline |
| --- | --- |
| Tavily basic | At most 3 lifetime attempts, failures included |
| Exa discovery | At most 1 lifetime attempt; preserve the task's required/optional policy |
| Original shared source HTTP | At most 8 lifetime attempts |
| Basic Extract | At most 1 batch, up to 5 eligible failed important URLs |
| Independent supplement | At most 2 HTTP repairs, 2 browser renders, 8 local reparses |
| Browser subrequests | Existing 25 GET/HEAD per render ceiling |
| Model dispatch | 12 decisions, 4 failure allowance, at most 16 physical HTTP attempts |
| Model task lifetime | At most 72 physical HTTP attempts |
| Dispatch time | At most 900 seconds per question |

Three Tavily searches are available slots, not mandatory steps. Suggested roles
are designated/primary material, independent evidence, and the remaining critical
gap. The agent adapts queries and stops early. Official-domain constraints and
news/finance/general topics remain task-specific options.

Local reads should be grouped to avoid a model round for each tiny action. Pass
compact source inventories and requested original spans, while retaining full
records on disk. Do not compress essential tables into generated summaries.

A larger free-capture profile can be a later budget-only candidate if useful
frontier sources are demonstrably left unread. Search scarcity, reader failures,
context omission, and agent misunderstanding require different interventions.

## 7. Evaluation that can identify an actual improvement

### Three separate experiments

1. **Saved-response regression:** freeze question fields, search results, raw bytes,
   and model action fixtures. Test parsing, row preservation, deduplication,
   parameter validation, failure routing, stop conditions, and package compatibility
   with no provider calls. This proves engineering behavior, not live discovery.
2. **Frozen-frontier policy replay:** both versions receive the same saved leads
   and bodies. Allow only local reads/model decisions; explicitly reject network
   actions. Record unavailable proposed searches instead of pretending their
   results exist. This measures navigation/selection, not fresh-search recall.
3. **Live acquisition pair:** use untouched non-tournament open questions, identical
   inputs/model/budgets, independent ledgers, and a short interleaved run window.
   Hash shared input versions and record source changes. A common discovery pool
   would test selection, not the complete discovery system. Live web variability
   remains a limitation even with pairing.

For all stages, freeze the case list and useful-material rubric before examining
candidate outputs. Mark version labels as A/B for review. The earlier five hard
cases are development regression only, never held-out acceptance.

### Metrics and promotion gates

Primary acquisition metric: per-question fraction of predeclared currently
obtainable material targets found in readable original source material. Report
captured, banked, and analyst-visible coverage separately. New worthwhile leads
can be recorded as exploratory findings but cannot inflate the frozen denominator.

Secondary measures:

- Wrong-entity/date/metric material selected; missing row headers or units.
- Original-source and independent-source coverage where the rubric requires it.
- Exact-span validity, duplicate content, parser failures, and preserved gaps.
- Completed and incomplete questions, provider failures, attempts, tokens,
  unknown usage, end-to-end time, and budget compliance.
- Fixed analyst output as a downstream diagnostic, not the collector's primary
  target. Retrospective outcomes cannot establish prospective forecast skill.

Predeclare the hypothesis, one candidate, and spend ceiling. Suggested first live
pilot: 5 untouched questions spanning prose, tables, PDFs, structured data, and
dynamic pages where available. If it passes, freeze a new 10-question holdout.
These are engineering pilot sizes, not statistically conclusive samples.

Require zero new integrity violations or omitted questions. Inspect every material
coverage regression, report per-question paired differences, and compare cost and
coverage together. Reject a candidate when measured gain is absent or driven only
by extra budget. Do not promote a one-off score increase. Two inconclusive candidate
rounds should trigger a design review instead of another broad recollection.

## 8. Implementation order

1. Freeze release identities and define the question/package contract. Add local
   regression checks for missing timing metadata and full table rows/context.
2. Wrap release acquisition plus independent supplement in one experimental
   entry point with the existing bundle-compatible export. Keep the analyst fixed.
3. Introduce the explicit material-target/frontier loop as the single candidate
   change. Reuse the existing need IDs, guidance, channel catalog, reading receipts,
   and reservation ledger. Do not duplicate those mechanisms.
4. Run saved-response regression and frozen-frontier replay. Test interruption,
   shell failures, context pressure, and forced closure.
5. Run the bounded untouched live pilot and review paired acquisition coverage.
6. Only after a measured result, consider a budget-only or model-only experiment.
   Mercury triage and changing analysis remain separate optional projects.

No workflow dispatch, provider call, or submission is authorized by a design file.
The user can authorize a later bounded implementation/test explicitly.

## 9. Sources and observed failures

Reviewed on 2026-10-05:

- [Release 1.0.1](https://github.com/chenencc/futureeval-forecasting-agent/releases/tag/v1.0.1)
- [Ultra model and tool support](https://openrouter.ai/nvidia/nemotron-3-ultra-550b-a55b:free)
- [Super model and tool support](https://openrouter.ai/nvidia/nemotron-3-super-120b-a12b:free)
- [Mercury Decide state and typed questions](https://openrouter.ai/inception/mercury-decide:free)

Previous measured diagnostics: release reproduction run `37270797575` completed
all five cases using 34 Ultra and 8 Mercury HTTP attempts, with 665,249 known
reported tokens and two unknown-usage attempts. The same-case experiments changed
background/source inputs, so their prediction differences were not a controlled
code comparison. Saved stablecoin rows were absent from the final selected Mercury
spans; source capture and downstream request coverage must be measured separately.
Some historical inputs lacked an exact opening timestamp. Production's release
`live_request` already carries official timing fields when present; the fixture
failure does not prove production omits them.

These observations motivate testable input and handoff contracts. They do not
prove Ultra is the best collector, Mercury is an adequate autonomous collector,
or more calls will improve future-event forecasts.
