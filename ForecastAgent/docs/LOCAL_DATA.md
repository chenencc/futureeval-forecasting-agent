# Local data maintenance

GitHub-hosted acquisition cannot write directly to a desktop drive. The local
puller reads completed Actions runs and imports missing original file content. It never
dispatches workflows, calls Ultra/Tavily, submits predictions or trades.

## Storage contract: forecast_local_store_v1

Default root: `E:\metaculus_data`.

- `index.sqlite3`: schema version, run provenance, artifacts, file versions,
  typed records, discovery cursor, import states and sync events.
- `archives/<hash-prefix>/<sha256>.zip`: original downloaded archives.
- `blobs/sha256/<hash-prefix>/<sha256>`: exact original files, deduplicated by
  content. File extensions and paths remain in the files table.
- `logs/sync.log`: local scheduler activity and failure notices.
- `downloads/<artifact-id>/`: persistent, hash-checked compressed ranges. A failed
  request does not delete completed ranges or restart the download from byte zero.
- `sync-catalogs/<stream>/latest-summary.json`: compact live heads, separate from
  evidence and provider originals. An updated summary does not imply complete bodies.
- `transfer-usage.json`: durable artifact payload reservations, including failures.
- `transfer-status.json`: actual received payload bytes, reused compressed bytes,
  source ZIP sizes, deferred imports and file completion reports.

Every artifact has repository, run ID, artifact ID, name, import status, archive
hash and timestamp. Every file has original relative path, content hash and size.
`imported` means the original ZIP is saved; `files_imported` means every declared
original file is locally available and verified, but the original ZIP remains
remote. The latter must never be described as an archived original ZIP. Existing
ZIP archives and their original digests are preserved during migration.
Typed records include questions, page bodies, search results, excerpts, model
attempts, full transport responses, tool steps, market snapshots, collection
results, acceptance reports and temporal provenance. Each record references its
original file hash. Raw JSON documents are retained even when a specific typed
adapter is unavailable. `question_versions` is a queryable view over questions
and their cutoff dates. Older versions are never overwritten by newer runs.
`document_versions`, `search_attempts` and `model_call_versions` expose typed
capture dates, queries, transport usage and timings for downstream analysis.
Portable historical campaign ZIPs wrapped in Actions artifact ZIPs are both
preserved; the inner campaign is indexed separately with a parent artifact ID.

Model telemetry may be absent from older runs. Indexing a source does not verify
it. Data synchronization never changes historical eligibility or consumes task
budgets. Legacy evaluation labels, if present in an old artifact, remain raw
archive material and are never loaded into the collection engine.

## Authentication and scheduling

Use the official GitHub CLI and standard `gh auth login` on this computer.
The synchronizer calls `gh api` using that established session; it does not
extract tokens from Git credentials, browsers or other applications. Explicit
`GH_TOKEN`/`GITHUB_TOKEN` environment variables are also supported by gh.
Actions read access is required. Secrets in the repository are not downloaded.

```powershell
python -m ForecastAgent.local_sync init
python -m ForecastAgent.local_sync sync --gh-path D:\metaculus\.tmp\github-cli\gh.exe
python -m ForecastAgent.local_sync sync --daily-mb 100 --monthly-mb 2048
python -m ForecastAgent.local_sync status
python -m ForecastAgent.local_sync import --archive downloaded.zip --run-id 36734422923
```

`integrations/install_local_sync.ps1` registers a Windows task under the current
interactive user. A `wscript.exe` wrapper prevents console windows from appearing.
It runs hidden every 15 minutes and at user login. The computer
must be on and the user session available. StartWhenAvailable enables catch-up;
after an outage, discovery overlaps the previous cursor by one day. First
discovery covers 90 days. It records every completed repository run, including
failed runs with useful partial artifacts. At most 20 artifacts are downloaded
per poll; remaining work stays queued. Pagination and failed imports retain
progress. Failed downloads back off from 15 minutes up to one day; pending
downloads have priority, so repeated failures cannot starve newly captured data.
Expired artifacts are recorded as gaps. Artifacts containing zero
files are preserved but cannot provide missing source data.

SQLite updates are transactional and file publication uses atomic renames.
Interrupted import retries are safe; duplicate artifact bytes do not add records.
ZIP traversal, symlinks, duplicate names and oversized archives are rejected.
Portable archives with SHA-256 manifests are verified before a committed import.
Artifacts without a manifest receive locally computed hashes; these verify local
consistency, not publisher authenticity. The original GitHub metadata is retained.

The machine cannot guarantee recovery after GitHub retention expires. Keep it
online periodically and inspect `expired`/`failed` states. A successful scheduled
run only means available artifacts were imported; it does not imply all future
Actions runs have already completed or that collection quality passed.

## Incremental transport and bandwidth policy

The default mode is `incremental`. Official and market workflows publish
`forecast-local-catalog-v1-*` separately from recovery checkpoints. Each catalog
lists exact artifact-relative file paths, sizes and SHA-256 hashes. It includes a
small summary with forecast heads, anchor prices, lifecycle and missing states;
full reasoning and original evidence stay in the referenced recovery/evidence
artifacts. Forecast code, provider quotas and cloud recovery are unchanged.
Small mutable top-level JSON originals are also embedded with their exact hashes
and original artifact IDs. This preserves state versions across checkpoint
rotation. Changed evidence is imported before checkpoints; known original bytes
can be linked even if the old recovery ZIP was deleted. Unknown expired originals
remain failures, never synthesized files or completed imports.

The desktop reads catalogs first, reuses known local hashes, and reads only
missing ZIP members through exact HTTP ranges. Selected bytes pass ZIP CRC and
publisher hash checks. GitHub ZIP64 archives are supported. Every successfully
received range is retained across pauses, failures and scheduler restarts.
Unexpected redirects and non-range responses are rejected; GitHub authentication
is never forwarded to signed storage hosts. The authorized CLI session is used
in process memory only, with no credential or signed-URL logging.

Before catalogs exist, the latest sealed market and official checkpoint can be
imported selectively using its existing inventory. Older redundant checkpoints
are `checkpoint_remote_only`. Historical experimental artifacts are `remote_only`
in automatic mode; existing imported material is never deleted. Legacy monitor
snapshots without publisher hashes retain an explicitly weaker local-SHA/CRC
assurance. Missing or expired source artifacts remain recorded gaps.

Every published catalog remains eligible for catch-up, including intermediate
question/evidence updates. References do not extend the source artifact's existing
retention. A deleted or expired source with unknown local bytes is a synchronization
failure, not a completed import. Keep the machine periodically online. Original
ZIP retention and historical full-archive backfill remain explicit operations;
`--mode full` requests full archives and also respects payload limits.

Default limits are **100 MiB per local day** and **2 GiB per local month**. These
are configurable safety limits, not measured VPN totals: API metadata and protocol
headers are excluded. Interrupted reservations are conservatively retained. A
limit produces `transfer_wait`; progress resumes under the same object identities
and research budgets. Small catalogs have priority over body transfer. Configure
`E:\metaculus_data\sync-policy.json`, or override with command-line limits:

```json
{
  "mode": "incremental",
  "daily_payload_limit_bytes": 104857600,
  "monthly_payload_limit_bytes": 2147483648
}
```

Payload savings must be measured with `payload_bytes_received_this_sync` against
the actual compressed source ZIP sizes. Expanded file sizes and local cache reads
are different quantities. No savings percentage is inferred solely from file counts.

For existing analysis tools that expect a directory, restore a verified local
view directly from the object store. This performs zero network requests and
refuses incomplete artifacts or overwriting changed research files:

```powershell
python -m ForecastAgent.local_sync materialize --artifact-id 123456 --output E:\metaculus_data\research_views\run-123
```

The `.local-import.json` receipt records source run, artifact ID and byte
verification. A restored view is not a newly downloaded original ZIP.

Useful SQL:

```sql
SELECT run_id,name,status FROM artifacts ORDER BY CAST(run_id AS INTEGER) DESC;
SELECT DISTINCT question_id,question,as_of_utc FROM question_versions;
SELECT kind,COUNT(*) FROM records GROUP BY kind;
SELECT at,status,detail FROM sync_events ORDER BY rowid DESC LIMIT 10;
```
# Monitor freshness fallback

The separate Windows task `ForecastAgent Monitor Watchdog` checks every five
minutes and can dispatch the GitHub snapshot workflow after 20 minutes without
a successful uploaded poll. It performs no local research. Existing active runs
and recent uncertain dispatches prevent duplicate triggers. This requires the
machine to be awake, the user logged in and GitHub CLI access available.
See [Collection v3](COLLECTION_V3.md) for timing limits and status locations.
