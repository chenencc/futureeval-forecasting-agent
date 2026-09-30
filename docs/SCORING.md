# Read-only research scores and future forecast evaluation

The project keeps three numbers separate. They answer different questions and
must never be substituted for one another.

1. **Evidence quality** describes how directly and reproducibly a fetched page
   supports a claim under the Metaculus resolution rules. This version stores
   `high`, `medium`, `low`, or `unverified`, the source type, the exact claim,
   its original evidence chain, and a reason. Code rejects `high` for a
   secondary report and requires another search for an original source if the
   first search yielded only secondary evidence. Reports quoting the same
   dataset count as one evidence chain. This is an audit aid, not a probability
   that the event resolves Yes. Search rank and publisher reputation alone are
   insufficient.
2. **Event probability** is Ultra's `probability` for the question resolving
   Yes. The agent must also state a reference class or say none is available,
   show event paths, contradictions, unknowns, and cited fetched evidence.
   Code checks the range and citation provenance. Evidence quality should
   influence the model's uncertainty, but no fixed arithmetic maps the quality
   labels to the event probability.
3. **Forecast performance** can only be measured later on questions whose
   outcomes were unknown when a forecast snapshot was made. For a binary
   resolved outcome `y` and frozen probability `p`, report Brier loss
   `(p-y)^2` and log loss `-[y log(p)+(1-y) log(1-p)]`. Lower is better.
   FutureEval seasonal tournaments use **spot Peer Score**, derived from the
   difference between a forecast's log score and other forecasters' log scores
   on the same question. Read the official tournament score when available;
   do not label a standalone log loss or Brier loss as a Peer Score. Report
   performance by topic and lead time and compare the same questions against
   the old baseline. A historical resolved-question demo is a smoke test,
   never a backtest. The present demo covers a binary question only; numeric
   and multiple-choice questions need their own output schemas and metrics.

## Outside-view baseline and current-evidence update

Ultra first records `baseline.probability`, its reference class, time window,
rationale, and fetched supporting URLs. A baseline may be null when the
reference class cannot be supported. The tool accepts this record once and
prevents later replacement. The final assessment must explain evidence updates
that support any change in probability, citing its assessed fetched evidence.
`probability_shift` is the arithmetic difference between the final probability
and the recorded baseline, not a calibrated signal or evidence-quality score.
The self-review checklist covers exact criteria, timeframe, status quo, a major
blind spot, and probability consistency. Code validates structure and citation
provenance; it does not certify that a reference class is statistically valid or
that the model's explanation is factually correct.

The same Ultra model plans historical, current, and gap searches. All purposes
share one three-attempt Tavily budget per question. Failures consume budget,
and no hidden retry is made by the Tavily client. Unused calls need not be spent.

## Jev-inspired design

TypeSafe Jev separates `Score` (ordered rubric), `Choice` (discrete class), and
`Noul` (a yes/no probability). We borrow that *interface discipline* now:

- Evidence type and quality are typed classifications with explicit reasons.
- The final Yes probability is a separate field, never inferred by averaging
  evidence-quality grades.
- A future optional Jev experiment could grade one narrow dimension at a time,
  such as directness of an article to a resolution criterion. It should run on
  the same frozen page snapshot as Ultra and be evaluated against human labels.
  Jev's `Score` is an ordinal rubric result; its `Noul` output is not assumed
  calibrated for FutureEval without out-of-sample testing. In particular,
  a Noul judgment that a *page currently supports a factual claim* is not the
  probability that a *future event* will happen. Jev is not called in the
  current free-model workflow.

As prospective snapshots accumulate, compare three versions on identical
questions: the original one-shot Ultra probability, the evidence-led Ultra
agent, and an optional Jev-assisted quality gate. Only then consider a
calibration transform fitted on older resolved questions and evaluated on a
later untouched cohort. Keep all original probabilities as well as any
calibrated version, with the model and code version used for each.

## Prospective experiment

1. Freeze the question wording, resolution rules, retrieval timestamps,
   source pages, raw probability, model version, and code commit before the
   outcome. Preserve failed searches and fetches too.
2. Have a human label a small set of source claims for directness, time-window
   match, traceability, and independence. Use this to test the Ultra rubric,
   and optionally compare a Jev `Score` for each *single* dimension.
3. Once questions resolve, compute paired Brier/log loss on the same question
   set for the old and new agents. Plot calibration by probability bucket and
   score by category and forecast horizon. Use paired bootstrap intervals so
   a few lucky resolutions do not dominate the decision.
4. Only fit calibration on earlier resolved cohorts and evaluate on later
   untouched cohorts. Compare official spot Peer Scores if forecasts were
   actually submitted. Do not infer a tournament score from a read-only run.

References: [FutureEval methodology](https://www.metaculus.com/futureeval/methodology/),
[Metaculus scores FAQ](https://www.metaculus.com/help/scores-faq/),
[FutureEval tournament resources](https://www.metaculus.com/notebooks/38928/bot-tournament-resources-page/),
[TypeSafe Score](https://docs.typesafe.ai/primitives/score),
[TypeSafe Noul](https://docs.typesafe.ai/primitives/noul).
