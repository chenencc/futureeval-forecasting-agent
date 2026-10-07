# Timestamped targeted supplement

`ForecastAgent.supplement.targeted` is a development-only recovery executor
based on the immutable `v1.0.5` release. Production remains pinned to that tag;
this module is not automatically invoked by the competition worker.

The executor receives an explicit plan referencing immutable parent collection
directories, previous supplement ledgers and optionally a validated prior repair.
It stores a separate ledger, exact response captures and `supplement-package.json`
for each question. The original request, snapshot timestamp and parent SHA-256
remain distinct from every new capture's retrieval timestamp.

## Execution

```powershell
python -m ForecastAgent.supplement.targeted --plan frozen-plan.json --root timestamped-child-directory --execute
```

Local encrypted credentials are loaded using the project's existing launcher.
Search and Extract use the configured Tavily key. Exa uses the independently
audited primary/backup credit transport. No credential belongs in the plan or
artifact. No model, analysis or forecast submission is executed.

## Budgets and recovery

Parent search and fetch attempts, failed operations and unknown reservations all
consume their original allowance. Previous supplement HTTP/browser attempts and
the previously validated IMF Extract probe are included in cumulative counts.
Every new network attempt is reserved durably before execution. A finished child
can be re-exported with zero provider calls; changed parent bytes, executor source
or plan identity cannot silently start another allowance.

The bounded route reuses prior captures, attempts one available basic Extract
batch on observed failed sources, and if still empty uses at most one remaining
basic search and one remaining metadata-only Exa search. Free capture reads only
observed results, within inherited initial/supplement HTTP caps. Browser fallback
retains its original two-render and per-render request limits. Public URL, TLS and
access checks remain active. No URLs are invented.

## Interpretation limits

After execution, `python -m ForecastAgent.supplement.targeted_review --root
timestamped-child-directory` produces `checked-supplement-package.json` and a
body-screen report without network or model calls. It isolates obvious short
login and redirect shells while preserving their original response captures.
Each checked package binds to the SHA-256 of its immutable raw package. Short
numeric records remain eligible; this mechanical check does not validate dates,
entities, metrics or source claims.

Usable body text does not certify that the material concerns the exact target
period or supplies a resolution. Missing official MiniBench rules are retained
as a gap. Pages may contain only context, earlier reports or navigation to a
future release. The supplement is stored separately for later analysis; it is
never silently backdated into the original prospective snapshot. Later outcome
labels and authoritative rules must also be stored separately.
