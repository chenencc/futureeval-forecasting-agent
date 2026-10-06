# Release 1.0.3 same-material delivery experiment

This isolated `dev_v103_handoff` experiment starts from immutable release
`v1.0.3`, commit `a2a3dba9ac28fc6d768536de705dcbfec5cda4fc`. Production,
acquisition, supplement, decision questions and calibration remain at that tag.
The 47-file analysis baseline and release source manifest are verified before
any inference. Added modules do not replace the production entry point.

## Changed variable

Both arms receive the exact same frozen question, original saved bodies and
Mercury model. The release arm reproduces the production first request. The
context arm removes repeated per-span provenance into the existing source ledger,
preserves every release-visible original coordinate, and spends the resulting
room on complete paragraphs, table rows with headers, and original identity,
date and unit context. Oversized or budget-excluded groups are explicit omissions.
Stale excerpts are quarantined; valid saved bodies stay available.

Original quotations are never rewritten. Neither banking nor a complete context
group certifies source relevance, event truth or temporal coverage. Document
views retain separate coordinate spaces and text hashes. Exact source versions,
source-level provenance and extraction warnings survive in the frozen fixture.

The second request uses the unchanged release selector and diagnostic thresholds.
Coordinate accounting prevents renamed compact spans from appearing to be new
material. Both arms retain first text and require at least 900 genuinely new
coordinate characters plus a release diagnostic reason. A failed second request
retains its validated first score. A failed first request has no synthetic score.

## Frozen cases and limits

- Five fixed development cases: 36871, 43494, 43501, 43991, 44801.
- Ten expansion cases, outside this five-case repair set: 44925, 40938, 43703,
  43709, 44247, 43844, 43992, 41090, 43615, 44448.
- The expansion cases appeared in earlier experiments. They are not untouched
  forecasting holdouts. Current historical captures may contain outcomes.
- Alternate arm execution order by question. One replicate is preregistered.
- At most two physical Mercury requests per arm, four per question, sixty for
  all fifteen cases. The first/second limits remain 22,000/28,000 encoded bytes.
- No search, source fetch, new supplement, model fallback, quota reset, forecast
  submission or production promotion occurs in this experiment.

The fixture projection excludes acquisition conversations, judgments and previous
forecasts. Exact original archive hashes and projection hashes are in the cohort.
Resolution labels are separate and opened only after sealed outputs and actual
provider journals pass the offline review. The production first instruction is
reproduced verbatim; closed historical cases remain engineering diagnostics.

## Acceptance and evaluation

Zero-provider preflight covers all fifteen original packages. It checks exact
coordinates, source/view hashes, unchanged question and original bodies,
preserved old-visible text and both request byte bounds. Focused tests cover
limiting paragraph context, table dependencies, stale excerpts, oversized text,
coordinate novelty, restart reuse, tampering and failed-second-stage retention.

Run the five-case provider regression first, then the independently frozen ten
cases. Compare paired first and final clipped Brier and natural log loss.
Classification accuracy, missing results, actual requests and provider-reported
tokens are secondary measurements. Correct classification does not certify
evidence coverage, and additional readable characters do not establish accuracy.
Mixed or adverse results remain reportable; no automatic release promotion is
permitted. These trials cannot establish live tournament skill.

```sh
python -m unittest ForecastAgent.tests.test_v103_context_handoff ForecastAgent.tests.test_v103_handoff_trial
python -m ForecastAgent.acquisition.v103_handoff_trial --preflight --output snapshots/v103-handoff-offline.json
python -m ForecastAgent.acquisition.v103_handoff_trial --output snapshots/v103-handoff --question-id 36871
python -m ForecastAgent.acquisition.v103_handoff_trial --output snapshots/v103-handoff --phase regression --review
```

The registered manual `retrieval_trial.yaml` workflow runs this branch-specific
pilot in Actions. It has no schedule, two concurrent cases, the existing
`OPENROUTER2` secret only, per-case artifacts uploaded even on failure, and an
explicit exact-parent restoration input. GitHub reruns with empty state are
rejected. Main production workflow files, pins, flags and accepted tasks are
unchanged.

## Completed paired pilot, 2026-10-06

Executed commit: `36cc6db3906fb93ae47613cea3011307f4af74cd`.
All fifteen cases and thirty arms returned validated probabilities. Thirteen
focused tests and the all-fifteen offline preflight passed. The completed runs
were [regression 37416882134](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37416882134)
and [expansion 37417154862](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37417154862).
The temporary manual workflow was disabled again after completion.

| Cohort and stage | Release Brier | Context Brier |
| --- | ---: | ---: |
| Five regression cases, first | 0.08104 | 0.02438 |
| Five regression cases, final | 0.06896 | 0.03570 |
| Ten expansion cases, first | 0.17788 | 0.14111 |
| Ten expansion cases, final | 0.17811 | 0.15364 |

Final classification accuracy changed from 4/5 to 5/5 in regression and from
8/10 to 7/10 in expansion. Both arms used 23 actual HTTP attempts. Reported
input plus output tokens were 1,132,430 for release and 949,642 for context,
a 16.14% reduction. All 46 attempts had known usage. The decision endpoint
reported `input_tokens` and `output_tokens`; the independent audit sums those
fields when `total_tokens` is absent. The sealed provider records are retained.

An earlier initialization failure occurred before any HTTP reservation. Its
five failure logs were audited and the directory initialization was repaired
before the successful runs. It consumed no provider, search or fetch quota.

Both batches improved probability loss, but expansion classification regressed.
Context final Brier was worse than context first Brier in both batches. The
candidate remains experimental; these results do not authorize promotion or
establish stable live forecasting skill. Conditional reread value is a remaining
question. Production workflows and acquisition remain unchanged. No quota was
reset and no forecast was submitted.

The English aggregate report is saved as
`ForecastAgent/experiments/v103_handoff_results_20261006.json`. Original archives,
sealed requests, provider responses, coordinate audits and per-question CSVs
were imported into `E:\metaculus_data\reports\v103-handoff-<run_id>`.

## Additional ten-case extension

The next extension adds 43688, 43682, 43171, 42540, 43091, 41206, 43461, 43824,
43496 and 39992. They cover monetary policy, court decisions, sports selection,
space observations, AI benchmarks, securities filings, public health, market
series, election timing and entertainment participation. Saved formats include
HTML, PDF, HTML tables, CSV and series. These cases are outside the earlier
fifteen-case paired delivery trial, but some appeared in older research.

`v103_handoff_extension.py` binds separate immutable cohort, fixture, labels and
protocol paths around the unchanged pilot engine. The previous packing and
scoring source hashes remain identical. First and second caps and routing remain
the same, with at most forty actual HTTP attempts for ten new cases. Original
fifteen-case inputs, results and lifetime journals are retained.

Before provider calls, the expanded sports snapshot failed the frozen original
URL/coordinate audit for both arms. A same-run original raw package passed the
audit and contains substantive saved text; both arms now use that exact package.
The rejected projection, its protocol, wrapper source and zero-provider screen
are preserved in `experiments/v103_extension_screening`. The compatible ten-case
fixture and its replacement rationale are frozen under `v103_handoff_extension_v2`.
This substitution is based on source integrity, with no scored-output criterion.

The extension compares first and final results separately and archives per-case
sources, requests, actual usage and failure journals. It neither acquires new
material nor changes production. Outcome knowledge and current historical bodies
remain a limitation; this is an engineering comparison, not a historical forecast
backtest or an automatic promotion gate.

### Completed extension results

Run [37441544872](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37441544872)
completed successfully on commit `1e94256d4d2221e6dced40e2fe4ca115497332b0`.
All ten cases and twenty arms scored, with Linux source-integrity preflight and
all fifteen focused tests passing. Sealed predictions and actual provider
records were imported and independently audited in `E:\metaculus_data`.

| Stage | Release Brier | Context Brier | Release accuracy | Context accuracy |
| --- | ---: | ---: | ---: | ---: |
| First | 0.024304 | 0.048334 | 10/10 | 9/10 |
| Final | 0.017439 | 0.042289 | 10/10 | 10/10 |

Release used fifteen physical HTTP requests and 718,132 reported tokens. Context
used fourteen requests and 576,873 tokens, 19.67% fewer tokens. Unknown-usage
attempts: zero. The resolved provider model was `inception/mercury-decide-20260930`.
The forty-request cap was respected; no search, source fetch, supplement, quota
reset or forecast submission occurred. The temporary manual workflow is disabled
again. Production remains unchanged.

Eight cases gained original character coverage, totaling 13,878 additional
coordinates, and no old-visible original text was removed. Nonetheless, this
cohort favors release probability loss. The largest final regressions are the
meteor case 42540 and Anthropic filing case 41206. Meteor original coverage is
equal in both arms; only packing changes. One response per arm cannot separate
representation effects from model-call variance, or attribute that difference
to retrieval. Both arms improve aggregate Brier after rereading in this cohort,
which differs from the earlier candidate cohorts.

The exploratory combined twenty-five-case final Brier is 0.092014 for release and
0.085513 for context, with accuracy 22/25 for both. Cohort differences and the
new ten-case regression must remain visible. No candidate is promoted on these
mixed single-replicate retrospective results. Full structured results are in
`experiments/v103_handoff_extension_results_37441544872.json`; the corresponding
local report also preserves per-question CSVs and original transport records.
