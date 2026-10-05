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
