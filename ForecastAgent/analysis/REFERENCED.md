# Referenced analysis

This experimental module consumes immutable `acquired` bundles. Collection is finished; the module does not restart it, perform searches, or submit forecasts. All prompts and analysis schemas are backend-neutral. The pilot workflow selects the free Ultra backend; Mercury Decide remains the probability adapter.

## Evidence references

The saved source library gives each contiguous excerpt a stable evidence ID, source ID, URL, parsed-body hash, and original character span. The reasoning model returns evidence IDs rather than quotations. Software materializes the exact saved text. A source reference establishes provenance, not the truth of its interpretation.

The initial packet selects up to 10,000 characters per source using lexical recall. Omitted characters and upstream truncation remain visible. `read_saved_source` exposes omitted text from the same saved body, up to 6,000 characters per call and eight local calls per task. It never retrieves an updated page or spends a search credit.

## Analysis and review

The model decomposes resolution conditions and attaches evidence references, an observation window, declared coverage (`full`, `partial`, `unknown`), support status, and a specific gap. It also supplies factual interpretations, event scenarios, outside-view support or its absence, opposing arguments and source dependence. A second call reviews these claims against their actual evidence. Reading and provider retries share each backend's three-attempt HTTP budget; if they consume it, an unreviewed draft remains explicitly provisional.

Coverage and entailment are model judgments, not program-verified facts. The software rejects unknown references, supported conditions without references, and incomplete coverage without a gap explanation. It does not treat a few point observations as a deterministic proof that a time-window event never happened.

## Scoring and eligibility

Mercury receives the question, reviewed qualitative analysis, exact referenced source spans, and quality flags. It does not receive the reasoning model's numeric baseline. Native P(YES), ordinal evidence sufficiency and eligibility flags are separate. Incomplete conditions, remaining gaps or absent review produce `review_required`. No empirical probability calibration is fitted, and automatic use remains disabled. The module does not invent a shrinkage formula or turn evidence quality into a probability multiplier.

## Durable experiment state

The initial v2 experiment used the acquisition transport's 3,000-token output limit and exposed truncated structured replies. V3 preserves that experiment and uses a 6,000-token completion allowance, including a requested 1,500-token reasoning budget, supported by the selected free backend. Tool use is required and invalid-output feedback excludes verbose reasoning. Collection's original transport defaults are unchanged. Provider support for generation settings must be checked before switching backends.

The manifest freezes source-campaign identity, protocol, prompt/tool contract, selected IDs and configured reasoning model. Each task freezes its full original bundle hash. Sessions atomically retain messages, local-read library, draft and consumed HTTP index. Received provider responses can be replayed after interruption without another HTTP request. V5 gives each task at most three physical Ultra attempts and, on fallback, three independent Super attempts, plus one Mercury attempt. Previous Ultra records are preserved; switching does not erase them. There is only one authorized fallback, at most six reasoning attempts in total, and restoration never replenishes either model's allowance. Terminal-tool enforcement uses the active model's remaining allowance. Completed outputs retain separate hashes for evidence, analysis and scorer input. The workflow uploads `referenced-analysis-v5`; this new policy does not silently reopen older frozen experiments.

V4 uses free Ultra by default and switches to free Super after two consecutive service failures, including upstream 502 responses or transport failures. Super remains selected for the rest of that experiment dispatch. A fresh experiment tries Ultra again; restoration replays preserved transport records and retains the fallback state. Account quota, credential and invalid-request errors do not activate fallback. Both models share the same three-attempt task cap and generation settings. With two initial service failures, only one attempt remains for a provisional Super draft. Predictions record actual requested models, and provider journals retain each transition. The workflow uploads `referenced-analysis-v4`; v2/v3 exhausted journals remain separate and are not reopened.

Run `36953666625` verified two Ultra 502 failures followed by a successful Super tool response. That response requested local reading instead of a terminal report, leaving no valid draft within the three-attempt cap. The terminal tool policy now re-evaluates remaining capacity before each physical retry and forces `record_analysis` on the last attempt, including provider fallback inside a single logical call. This repair has offline regression coverage but has not yet passed live validation. The earlier run remains exhausted and preserved.

V5 run `36954705847` verified the independent allowance: two Ultra service failures and three successful Super responses. Super performed two local reads, then called `record_analysis` with read-tool arguments, which strict validation rejected. No probability was produced. The terminal request now exposes only the selected output tool's schema; both read and output schemas were previously visible. This schema restriction passes offline regression checks and still needs live validation. The exhausted run retains all five requests and is not resumed under a changed contract.

```sh
python -m ForecastAgent.analysis.referenced --root PATH_TO_CAMPAIGN --output PATH_TO_EXPERIMENT --ids 44801,43688,41140
python -m ForecastAgent.analysis.evaluation --output PATH_TO_EXPERIMENT --source-archive ORIGINAL_CAMPAIGN.zip --labels snapshots/forecastbench-history/resolved_metaculus.jsonl --report OUTPUT_REPORT.json --baseline OPTIONAL_V1_REPORT.json
```

The `Referenced evidence analysis pilot` workflow runs the same interface against a completed collection artifact and uploads the full state. Its optional resume input restores the existing experiment. Every fresh protocol experiment is separate from v1 and from collection budgets; provider requests remain subject to the account's shared limits.

Current saved sources and model knowledge can contain outcomes. Evaluation after frozen inference is a retrospective diagnostic, not a clean historical forecasting benchmark. Old v1 outputs remain available for exploratory comparison; a changed protocol and changed evidence context are not a controlled test of model superiority.

Evaluation reopens the original campaign archive and verifies every materialized span, full source-bundle identity and frozen analysis/scorer-input hashes before loading outcomes. It reports per-task conditions, gaps, HTTP attempts, known and unknown token usage, native probabilities and Brier/log loss. Optional v1 comparisons include only shared question IDs; no missing case is silently treated as zero cost or perfect prediction.

## Pilot status

V6 retains the same per-model allowances and clarifies source IDs versus evidence IDs in the schema and prompt. Validation reports all unknown references in one response and lists representative existing evidence IDs for mistaken source IDs, without automatically replacing them or asserting support. This addresses VIX case `43257` in run `36956609534`, whose three Super replies repeatedly cited source IDs as evidence. Earlier outputs and exhausted journals remain preserved; a corrected-protocol validation uses a separate experiment.

V2 run `36950597546` produced one provisional scored case out of three. Two cases failed structured-output validation; the completed case still requires review because time-window evidence is incomplete. V3 run `36952076125` attempted one case and received three upstream 502 errors without a usable model response. The revised generation policy therefore remains unvalidated in a successful live run. Both experiments retain their exhausted attempt journals. A subsequent validation must use a separately identified experiment; restoration must never reopen those budgets.
