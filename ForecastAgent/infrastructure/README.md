# Public listening and private recovery storage

Production forecasting still checks out immutable `v1.0.1`. These infrastructure
utilities are checked out separately from `main`; they do not alter model routing,
research quotas, probability policy or submission rules.

## Public listener

Deploy `public_monitor/watch.py`, `public_monitor/README.md`, and
`public_monitor/monitor.yaml` (as `.github/workflows/monitor.yaml`) into
`chenencc/futureeval-monitor`. See its README for Secrets and cron-job.org setup.
The private monitor remains manually dispatchable, and its successful completion
starts the original private worker. Only the public listener receives periodic
cron-job.org requests. A daily idle refresh protects recovery artifacts from
expiration. The listener compares the private control ledger to a complete open
scan and also dispatches due retry/reconciliation work. It does not forecast.

## Storage contract

- `futureeval-official-state`: campaign ledger, complete nonterminal task trees,
  terminal candidates/receipts, sealed file hashes, and cumulative archive index.
  Keep the latest three checkpoints, with a 90-day maximum retention each.
- `futureeval-official-control`: small campaign copy for the public listener,
  uploaded only after the full recovery checkpoint succeeds. Keep three copies.
- `futureeval-official-evidence-RUN_ID`: only changed files, with hashes and parent
  manifest identity. Keep seven days for local import. Terminal evidence is not
  repeatedly included in every recovery checkpoint.
- Shared completed source cache is disposable, not a research/provider ledger.
  All task budget and provider records remain in pending task trees or archives.
- If changed evidence or checkpoint export fails, upload full emergency state.
  Superseded recovery artifacts are rotated only after the replacement upload
  has been confirmed. Missing or corrupt recovery state fails closed.

### Local import and safe historical cleanup

Run the existing `ForecastAgent.local_sync sync` with the authorized GitHub CLI.
Complete import before seven-day evidence expiration. Local archives and SQLite
records remain in `E:/metaculus_data`.

```
python -m ForecastAgent.local_sync sync --root E:/metaculus_data --gh-path PATH_TO_GH
python ForecastAgent/infrastructure/prune_imported.py --gh-path PATH_TO_GH
python ForecastAgent/infrastructure/prune_imported.py --gh-path PATH_TO_GH --execute
```

The first cleanup command is read-only. Execution verifies local ZIP SHA-256,
requires no active workflows, protects recent production state artifacts, and writes
deletion receipts. Never delete a remote evidence artifact based on age alone
before confirming its local archive. The original 90-day experiment artifacts
need this one-time cleanup; changing visibility does not erase them.

Seven days is a finite backup window, not a durable cloud archive. A local machine
that stays offline longer needs longer retention or an external archival store.
Large pending tasks can also exceed 500 MB; storage is measured, not guaranteed.

## Acceptance

```
python -m unittest ForecastAgent.infrastructure.test_storage_monitor -v
```

Checks pending budget preservation, terminal evidence archival, no repeated
unchanged exports, corruption detection, pagination host restrictions, deadline
filtering, duplicate suppression, and due retry dispatch. Live acceptance must
also verify Secrets, authenticated Metaculus access from the public runner,
private dispatch acceptance, and checkpoint restore after a second private run.
