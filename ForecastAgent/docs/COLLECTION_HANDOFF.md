# Checked collection handoff

Development sequence:

```text
Original acquisition agent
    -> program inspection and exact-body duplicate inventory
    -> independent supplement using preserved original bodies and ledgers
    -> post-repair inspection and deduplicated analysis view
    -> Mercury original-evidence analysis and conditional rereading
```

The original collector retains its model policy, tools, search limits and durable
reservations. This middleware makes no model or search requests. Supplementation
uses the existing independent executor: at most two HTTP captures, two browser
renders and eight saved-response reparses per question. Its separate attempts are
reserved before execution and never reset by resuming a handoff.

## Program checks

- Hash exact extracted text. Keep one copy in the analysis view and retain other
  source URLs, timestamps and hashes as aliases. Copies do not count as independent
  corroboration. Different text is not merged by fuzzy similarity.
- Detect empty bodies, navigation shells and login controls. Preserve their
  original captures and explicit gaps. A successful HTTP response is not proof of
  useful text. Login restrictions are not bypassed.
- Compare explicit SEC filer fields and declared issuer identities when available.
  SGML filer records and HTML filer headings are supported. Opaque document URLs
  do not establish issuer identity. Unverified affiliate relationships remain gaps.
- Leave temporal eligibility, legal effect, event stages and relevance judgments
  to analysis. Readability does not establish any of these properties.

Inspection precedes repair but duplicate originals remain available to the repair
executor. The final analysis view is screened again after repair. Original question
identity and acquisition ledger hashes must match before that view is accepted.
Unresolved gaps are passed to analysis; missing evidence is not negative evidence.

## Durable output

Each task directory contains `manifest.json`, `raw-bundle.json`,
`inspection-before-repair.json`, `repair-input.json`, the `repair/` child lineage,
`inspection-after-repair.json`, `report.json` and `analysis-input.json`.
The final view preserves removed originals in `handoff_excluded_pages` and aliases
in `source_aliases`. Original model, search, fetch and session reservations remain
unchanged. A changed parent or implementation refuses resume rather than silently
starting fresh repair budgets. Cached output is verified and returned without
repeating repair.

## Enable only in a development run

Set `FORECAST_CHECKED_HANDOFF=1` in the development competition worker or nonbinary
full-pipeline entry point. With the flag absent, the existing route is preserved.
The official workflow remains pinned to release `v1.0.1`; this development change
does not activate it in the tournament.

For offline replay of a saved bundle:

```sh
python -m ForecastAgent.evidence.collection_handoff --bundle bundle.json --output snapshots/checked-handoff/question-123
```

The standalone command performs no collector, inference or forecast submission.
Network repair requires an explicit `--network`. Offline replay establishes
preservation and screening behavior, not live repair success or forecast accuracy.
