# Competition archive database

This local SQLite projection imports exact saved competition catalogs and native
files. It never calls Metaculus, models, search providers, forecast submission or
trading APIs. GitHub is read only. Original worker states, budgets and receipts
are preserved. The source of truth is the saved native record, not a Web export.

Database: `E:/metaculus_data/competitions/store/competition.sqlite3`.
Health: `latest-sync.json` beside it. Installed reader: `reader-runtime.json`.
Complete Web handbook: `E:/metaculus_data/prediction_markets/store/WEB_DATABASE.md`.

## Scope

- Current official Fall FutureEval monitor inventories and worker checkpoints.
- Hash-indexed older original files reused from the existing local SHA archive.
- Native saved Market Pulse confirmed receipts, matching original analysis
  results and campaign acquisition packages. Experiments/pilots are not scanned.
- An inventory-only question stays inventory-only. An accepted task does not
  certify local body availability. Non-binary option/CDF payloads stay unchanged.

## Read-only integration

```python
from ForecastAgent.competition_store.read import open_readonly, read_index, read_task, read_body

db = open_readonly('E:/metaculus_data/competitions/store')
try:
    db.execute('BEGIN')
    index = read_index(db)
    detail = read_task(db, 'metaculus:46293')
    db.rollback()
finally:
    db.close()
```

Resolve the installed immutable runtime before importing, as described in the
handbook. These Python APIs are implemented; HTTP endpoints and live frontend
wiring remain Web-owned. Do not publicly serve SQLite, native objects, request
headers, raw prompts or private comments. Plain-text bodies use verified display
SHA-256, and recorded clocks remain distinct from the local import clock.

## Receipt and evidence binding

An accepted Web receipt requires a timezone-aware native confirmation and exact
question/payload match to a saved candidate/result. It binds to that specific
forecast vintage, which can differ from a later research version.
`accepted_unbound` preserves a confirmed native receipt whose matching candidate
is not imported; it does not certify the displayed model forecast as submitted.

Selected Mercury state is linked through the saved request identity, native
result hash and candidate payload. Missing fallback/older state chains remain
`input_not_bound`. The reader additionally requires URL plus body-hash binding
to the same analysis batch before showing primary model-input evidence. It
never borrows another batch or a collected-only source to fill that gap.

## Maintenance

```powershell
python -m ForecastAgent.competition_store.read sync
python -m unittest discover -s ForecastAgent/competition_store/tests -v
```

The existing hidden local mirror can call this after market import when its
`competition-policy.json` contains `{"enabled":true}`. The default root is the
separate competition store. Each store has its own lock and health record;
competition failures do not invalidate a completed market import. The local
machine must be online/logged in with the existing authorized GitHub CLI.

Original objects are compressed inside `native_objects`, retaining original
file-byte SHA and uncompressed length. HTTP ZIP members use cached one-MiB
blocks. Objects are bounded to64MiB; older absent objects remain explicit gaps.
No re-search, late replacement evidence, outcome inference or quota reset occurs.
