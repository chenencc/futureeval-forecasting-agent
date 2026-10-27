# Metaculus Agent

Isolated Metaculus runner inside `chenencc/futureeval-forecasting-agent/metaculus_hf/`. Code is public; evidence, forecasts,
submission receipts and recovery state are stored in a **private** Hugging
Face dataset. No GitHub Actions result ZIP or artifact is used.

Current implementation status: source/recovery tests and deployment validation
are in progress. Production activation is fenced until an explicit, verified
server handoff. Installing or running tests never enables submission.

## Pipeline

Discover eligible tournament questions -> shared current evidence collection
-> body review and immutable snapshot -> private HF context -> existing
official Mercury analysis with the authorized reasoning fallback -> private
HF analysis -> current-rule validation -> forecast/comment submission ->
authenticated readback -> durable receipt -> guarded local cleanup.

The collector is the hash-pinned acquisition implementation extracted from
the current prediction-market pipeline, including independent queries, 5xx
retry, saved-candidate recovery, supplements and body-bound citations.
Metaculus eligibility remains separate from market tags and opening rules.
The shared source lock prevents untracked divergence; update it deliberately
and run the shared regression tests when updating either consuming project.

## Data

Private dataset: `chchen13/forecastagent-data`.

```
metaculus/start_date=YYYY-MM-DD/question_id=<leaf-id>/run_id=<id>/
  context.parquet
  analysis.parquet
  manifest.json
  submission.json
metaculus/_control/checkpoint.json
metaculus/_control/objects/<sha256>
```

`start_date` is first actual acquisition start in Asia/Shanghai; all precise
timestamps are UTC. Parent post ID is separate from leaf question ID. Context
uses the same Zstd/checksummed Parquet envelope and `intelligence-package/2`
payload as market collection (version 1 remains readable). Original bodies
and final review overlays remain distinct. There are no fictitious book or
trade files. Analysis retains actual outputs, model/release/input identities,
citations and failure records, bound to its exact frozen evidence.

Every upload is downloaded at its exact commit and checked before advancing
state or deleting local payloads. Interrupted analysis restores saved bodies;
it does not start a new search. Reservations are persisted before HTTP.
Ambiguous platform submissions are reconciled and never blindly reposted.

## Local checks

Python 3.12, from the repository root first run `cd metaculus_hf`:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m metaculus_agent.doctor
python -m unittest discover -s tests -v
python -m playwright install --with-deps chromium
```

Credentials are supplied only via GitHub repository secrets or local hidden
environment configuration: `HF_TOKEN`, `METACULUS_TOKEN`,
`OPENROUTER_API_KEY`, `OPENROUTER_API_KEY2`, `TAVILY_API_KEY`,
`TAVILY_API_KEY2`, `EXA_API_KEY`, `EXA_API_KEY2`. Do not put values in Git,
workflow inputs, command arguments, issue comments, logs or artifacts.

The scheduled workflow is inert until `METACULUS_SCHEDULE_ENABLED=true`.
`METACULUS_SUBMIT_ENABLED=true` also requires an HF checkpoint with verified
`migration_complete=true` and matching writer identity. Missing state blocks
execution; it never creates an empty budget. Dependency caches contain no
runtime state. Scheduling is best effort, not an exact five-minute SLA.

## Cutover

1. Pass clean Windows/Linux checks and exact-HF readback/restart tests.
2. Run bounded collection/analysis acceptance with submission disabled.
3. Drain the old server writer; verify it exited. Import its terminal task,
   dedup, rule and reservation state. Do not reopen expired/accepted tasks.
4. Fence writer ownership and enable the new workflow. Never run two submitters.
5. Verify actual authenticated submission readback and subsequent state restore.

Raw runtime files and operational state are never committed here. Public CI
uses synthetic fixtures and no provider credentials. See `sources.lock.json`
and `NOTICE.md` for provenance. This repository does not require a private
GitHub checkout or a running Vultr server.

Repository credentials are reused with explicit workflow aliases: OPENROUTER/OPENROUTER2, TAVILY_KEY3/TAVILY_KEY4, EXA_API/EXA_API2 and METACULUS_TOKEN. Only HF_TOKEN is an additional repository secret when absent. Existing workflows and their enable flags are unchanged.

## Three historical arrival debug

`metaculus_hf_debug.yml` runs exactly three explicit historical questions in
sequential dependent jobs. Each job starts on a new GitHub-hosted machine.
Any failed case skips all later cases. A unique 32-hex session scopes every
checkpoint, content-addressed object, dated product, analysis and audit under
`metaculus/_debug/<session>/`; production data/control are never written.

The harness reuses `Runner.ingest`, shared collection, the official analyzer,
exact-revision cloud verification and native delivery serialization. Only
eligibility and prior-own-forecast visibility are simulated at the debug
client boundary. Original question rules and model-input clocks are retained.
Evidence is collected now; this is not a historical-as-of forecast test.

Actual platform POSTs are prohibited. Delivery/readback and duplicate delivery
are exercised using an in-memory API sink with a durable intent; that does not
prove real platform acceptance. Each case verifies product/manifest bindings,
fresh-root state and budget recovery, duplicate discovery without recollection,
and local retirement. Failed inputs remain in the debug checkpoint.

The debug ledger starts from the known production consumption floor and carries
its own increments between all three machines. Credentials still share actual
provider allowances. The debug workflow uses the production concurrency group
to avoid overlapping paid work; ordinary cron dispatches can wait behind it.
It does not reset or mutate the production budget or submit the old questions.
