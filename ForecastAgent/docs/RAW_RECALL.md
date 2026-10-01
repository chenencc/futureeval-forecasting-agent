# Raw recall acquisition experiments

Set `acquisition_focus: raw_recall` on a current-information collection request.
The model discovers and routes source captures. Full original responses and
parsed documents/data are delivered to later analysis; excerpt selection and
source interpretation do not gate capture acceptance.

`read_sources` captures up to four accepted URLs without required paragraph
queries. Deep reading, passage review and excerpt-selection tools are absent in
this focus. Search and source attempt ledgers remain unchanged: Tavily basic at
most three, one required Exa attempt, eight shared source HTTP attempts, and one
basic Extract batch of up to five failed important pages per task.

`raw_capture_report` records raw hash integrity, bytes, parsed characters,
document and row counts, observed ranges, hosts, source acquisition methods,
candidate routing associations, parsing gaps, failed attempts and discovery
backlog. Extract vendor text is distinguished from original HTML/PDF/API bytes.
Failed parsing preserves available original responses for later processing;
blank or blocked text is not counted as a readable body.

Raw acceptance does not establish source relevance, authority, exhaustive recall,
event truth or forecast correctness. Unfetched discovery hits remain a backlog.
Agent comments and interpretation diagnostics stay separate from mechanical
capture failures. Review the actual original documents when comparing source
coverage; counts alone cannot demonstrate an adequate intelligence package.

The manual `recall_five.yaml` experiment uses the same five frozen question
inputs as run `36837727870`. It requires explicit fresh-budget authorization and
creates a separate run-specific root, with no prior-state import. It refuses an
existing experiment root or a GitHub workflow rerun to prevent an accidental
second reset. Prior experiments remain unchanged. Each question has one bounded
execution; all artifacts upload even when acquisition fails.

Compare both cohorts using the same raw metrics and question inputs, retaining
the original model transport and capture files. This is an independent
same-question comparison with a different acquisition objective and stochastic
discovery, not a controlled A/B or historical forecasting backtest.
