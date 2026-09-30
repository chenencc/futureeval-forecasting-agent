# Read-only research scores and future forecast evaluation

The project keeps three numbers separate. They answer different questions and
must never be substituted for one another.

1. **Evidence quality** describes how directly and reproducibly a fetched page
   supports a claim under the Metaculus resolution rules. This version stores
   `high`, `medium`, `low`, or `unverified`, the source type, the exact claim,
   and a reason. It is an audit aid, not a probability that the event resolves
   Yes. Search rank and publisher reputation alone are insufficient.
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
   Report performance by topic and lead time and compare the same questions
   against the old baseline. A historical resolved-question demo is a smoke
   test, never a backtest.

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
  calibrated for FutureEval without out-of-sample testing. Jev is not called
  in the current free-model workflow.

As prospective snapshots accumulate, compare three versions on identical
questions: the original one-shot Ultra probability, the evidence-led Ultra
agent, and an optional Jev-assisted quality gate. Only then consider a
calibration transform fitted on older resolved questions and evaluated on a
later untouched cohort. Keep all original probabilities as well as any
calibrated version, with the model and code version used for each.

References: [FutureEval methodology](https://www.metaculus.com/futureeval/methodology/),
[TypeSafe Score](https://docs.typesafe.ai/primitives/score),
[TypeSafe Noul](https://docs.typesafe.ai/primitives/noul).
