# Supplemental acquisition

Pipeline: collection artifact → supplement stage → analysis overlay.

The stage reads the immutable parent archive and produces a separate artifact.
It does not reopen collection tasks, submit forecasts, search, or call a model.
Readable captures do not establish relevance, factual support or historical
availability. Original semantic gaps remain in the handoff alongside failed URLs.

## Tools

- Reparse hash-verified saved bytes: HTML, PDF, JSON, CSV, XML and spreadsheets.
- Render observed JavaScript shells with isolated headless Playwright Chromium.
- Retry selected format, size and transport failures with a bounded HTTP reader.
- Retain restricted sources as gaps. No automated login or challenge solving.

Each selected task has at most two browser renders, two HTTP repair operations,
and eight local reparses. Each browser render allows at most 25 GET/HEAD
subrequests, blocks unnecessary media, and uses a 20-second navigation budget.
DNS checks and browser shutdown can add overhead. Browser records distinguish
rendered DOM bytes from original HTTP response bytes. Failed/reserved operations
consume their allowance. No URL is attempted twice by the same supplement.
HTTP redirects are validated, recorded and limited to five; redirect hops are
not counted as separate repair operations. All limits are separate from original
search/provider quotas. Historical strict tasks never fetch current content.

## GitHub Actions

Run `supplement_acquisition.yaml`. The runner installs the pinned Python package
and Chromium with system dependencies. It checks an actual dynamic-page fixture,
read-only request routing, and XLSX parsing before processing live candidates.
No Metaculus, OpenRouter, Tavily or Exa secret is passed to the repair stage.
The GitHub token is used only to download artifacts.

To continue an existing experiment, supply its last completed `resume_run`.
This restores reservations and counters. Re-running without restoration creates
a separate experiment and must not be treated as continuation or a quota reset.
Source-run identity, parent file hashes, selection and limits must stay fixed.
Input archive packaging may differ between runners; original member hashes bind
the frozen input. Terminal reserved entries are retained as uncertain attempts.

```powershell
python -m ForecastAgent.supplement.stage --archive parent.zip --output supplement --ids 43259,43682,40005 --network
```

Without `--network`, only eligible saved-byte reparses execute. The stage saves
`manifest.json`, `gap-inventory.json`, per-task `supplement.json`, capture files
and `summary.json`. The `analysis_overlay` interface checks parent identity and
capture hashes and returns a copy containing supplementary pages. It does not
mutate the input. Existing frozen prediction experiments are not automatically
rerun or rewritten; a new analysis experiment must explicitly consume the overlay.

Current boundaries: no general crawler, semantic source search, historical
archive retrieval, automatic source substitution, OCR or private data access.
Spreadsheet rows/columns and total text are bounded and truncation is explicit.
Keep formula text; do not silently recompute cells or treat formulas as values.
