# Fresh v1.0.1 five-question reproduction

The frozen `RELEASE_REPRODUCE5.json` manifest selects questions 43494, 43501,
44801, 36871 and 43991. It pins every release ForecastAgent file at commit
`c9bfab44a670f4307ebc2330866479c82aec71ef`. A separately checked-out runtime
supplies the original collector, independent supplement and production Mercury
analysis. The harness imports no development ForecastAgent modules.

The harness calls `agent.run_research`, `competition.live.supplement_bundle`
and `competition.live.analyze`. It does not invoke the official worker or its
delivery functions. No Metaculus token is supplied. Native release fallback and
probability clipping remain intact.

Budgets start fresh in a new experiment directory. Existing task ledgers and
provider account limits remain unchanged. Subsequent collection executions retain
the same task's original ledgers, with at most three executions. Immediate
continuation is restricted to native dispatch/time ceilings. All original
responses, body captures, supplement failures and forecast candidates are archived.

Inputs include the release-standard question background from the existing 2026
question fixture. The previous unified pilot omitted background. Exact title and
resolution criteria match that pilot; labels, community estimates and prior model
outputs are excluded. Missing opening timestamps stay missing. This is a fresh
current-web diagnostic of historical questions, not a leakage-free backtest or a
controlled comparison of code alone.

Inference and evaluation are separate commands. Evaluation validates the immutable
candidate and selected Mercury response before joining outcome labels. Failures
remain in the five-question coverage denominator. Production is unchanged.

## Measured run

[Run 37270797575](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37270797575)
completed successfully on harness commit `0d0bc10`, loading immutable release
commit `c9bfab44a670f4307ebc2330866479c82aec71ef`. Artifact `11328344005` was
imported into the local data store and its archived SHA-256 was verified. The
English audited report is `E:/metaculus_data/reports/release-reproduce-five-37270797575.json`.

All five tasks completed after one collection execution each. Initial model
attempt counts were zero, Tavily budgets started at three and Exa budgets at one.
No prior captures or shared cache entries were reused. Actual use was 34 Ultra
HTTP attempts and eight Mercury attempts, nine Tavily basic searches and five Exa
searches. There was no Super fallback. Known reported tokens totalled 665,249;
two collection responses had missing choices and unknown usage. Reported model
cost was zero, with the unknown-usage caveat retained.

| Question | Prior unified P(YES) | Release P(YES) | Recorded outcome |
| --- | ---: | ---: | --- |
| 43494 | 0.037327 | 0.904651 | YES |
| 43501 | 0.980000 | 0.957912 | NO |
| 44801 | 0.020000 | 0.020000 | NO |
| 36871 | 0.952574 | 0.817574 | NO |
| 43991 | 0.020000 | 0.020000 | NO |

| Metric, matched five questions | Prior unified run 37269591387 | Fresh release run |
| --- | ---: | ---: |
| Accuracy | 40% (2/5) | 60% (3/5) |
| Brier | 0.559067 | 0.319183 |
| Natural-log loss | 2.057811 | 1.002005 |

The earlier repaired capture run `37106094306` scored 80% and Brier 0.189971 on
these same five IDs. That level was not reproduced. Four of five outcomes are NO,
so always-NO classification reaches 80%; a constant 0.5 forecast has Brier 0.25.
No claim of improved general forecasting performance follows from this cohort.

The release recovered the data-center classification. The two remaining errors
are high-probability YES forecasts for Instructure and stablecoins. Four June CMC
historical snapshots were captured, but the June 30 USDT/USDC numeric rows remain
outside the final Mercury request. That page contributes only navigation span
E0085 (characters 0–894); its values occur at 1923 and 2128. The exact rule is
interval-based, so one endpoint alone would still not establish a period maximum.

The Instructure fixture now includes background identifying previous incidents,
but still lacks the exact opening timestamp. The model treats the timing condition
as supported at 87.95%. This demonstrates an unresolved diagnostic inconsistency
in this fixture; it does not establish behavior with complete live opening metadata.
Fresh bodies, background and runtime all changed, so causal attribution requires
an identical-input replay rather than this full-pipeline experiment.
