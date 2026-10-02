# Referenced analysis v2

This experimental module consumes immutable `acquired` bundles. Collection is finished; the module does not restart it, perform searches, or submit forecasts. All prompts and analysis schemas are backend-neutral. The pilot workflow selects the free Ultra backend; Mercury Decide remains the probability adapter.

## Evidence references

The saved source library gives each contiguous excerpt a stable evidence ID, source ID, URL, parsed-body hash, and original character span. The reasoning model returns evidence IDs rather than quotations. Software materializes the exact saved text. A source reference establishes provenance, not the truth of its interpretation.

The initial packet selects up to 10,000 characters per source using lexical recall. Omitted characters and upstream truncation remain visible. `read_saved_source` exposes omitted text from the same saved body, up to 6,000 characters per call and eight local calls per task. It never retrieves an updated page or spends a search credit.

## Analysis and review

The model decomposes resolution conditions and attaches evidence references, an observation window, declared coverage (`full`, `partial`, `unknown`), support status, and a specific gap. It also supplies factual interpretations, event scenarios, outside-view support or its absence, opposing arguments and source dependence. A second call reviews these claims against their actual evidence. Reading and provider retries share the same three-attempt HTTP budget; if they consume it, an unreviewed draft remains explicitly provisional.

Coverage and entailment are model judgments, not program-verified facts. The software rejects unknown references, supported conditions without references, and incomplete coverage without a gap explanation. It does not treat a few point observations as a deterministic proof that a time-window event never happened.

## Scoring and eligibility

Mercury receives the question, reviewed qualitative analysis, exact referenced source spans, and quality flags. It does not receive the reasoning model's numeric baseline. Native P(YES), ordinal evidence sufficiency and eligibility flags are separate. Incomplete conditions, remaining gaps or absent review produce `review_required`. No empirical probability calibration is fitted, and automatic use remains disabled. The module does not invent a shrinkage formula or turn evidence quality into a probability multiplier.

## Durable experiment state

The manifest freezes source-campaign identity, protocol, prompt/tool contract, selected IDs and configured reasoning model. Each task freezes its full original bundle hash. Sessions atomically retain messages, local-read library, draft and consumed HTTP index. Received provider responses can be replayed after interruption without another HTTP request. Each task has at most three physical reasoning HTTP attempts and one Mercury HTTP attempt; restoring the journal never resets them. Completed outputs retain separate hashes for evidence, analysis and scorer input.

```sh
python -m ForecastAgent.analysis.referenced --root PATH_TO_CAMPAIGN --output PATH_TO_V2_EXPERIMENT --ids 44801,43688,41140
```

The `Referenced evidence analysis pilot` workflow runs the same interface against a completed collection artifact and uploads the full state. Its optional resume input restores the existing experiment. Every fresh protocol experiment is separate from v1 and from collection budgets; provider requests remain subject to the account's shared limits.

Current saved sources and model knowledge can contain outcomes. Evaluation after frozen inference is a retrospective diagnostic, not a clean historical forecasting benchmark. Old v1 outputs remain available for exploratory comparison; a changed protocol and changed evidence context are not a controlled test of model superiority.
