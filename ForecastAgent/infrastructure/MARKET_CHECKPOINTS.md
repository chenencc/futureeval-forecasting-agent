# Market checkpoint transport

The forecasting implementation remains selected by `MARKET_REPRICING_SHA`.
Transport is selected separately by the exact controller commit used by the
Actions run. Metaculus competition workflows are unchanged.

## Recovery

1. Resolve the latest trusted market workflow checkpoint using the existing
   fail-closed source selection. Never bootstrap past a missing mutated ledger.
2. Stream the signed archive to runner disk. Check the Actions artifact digest,
   size, paths, member uniqueness, encryption and symlinks.
3. A monitor restores the exact worker ledger, deployment records and each
   task's collection-request/capture identity. It does not restore task bodies.
   Its projection receipt explicitly excludes full-body integrity verification.
4. A worker restores all original files. New checkpoints contain directly
   readable controls plus deterministic per-task XZ packs. Each pack contains
   each distinct original SHA object once. All original bytes are verified
   before the restored directory becomes visible to subsequent steps.
5. Publish only after the original inventory verifies. Preserve the original
   ledger, provider reservations, failed attempts, source clocks and forecasts.
   Mutable duplicate paths are copied independently, never hardlinked.
   Full restoration caches verified compressed task objects outside the runtime
   state; publication hash-checks and reuses unchanged packs without recompression.

## Existing oversized checkpoints

Legacy ZIPs are restored and migrated on GitHub's runner. The old immutable
archive remains untouched. Legacy restoration has a finite 16 GiB logical
expansion limit, 128 MiB original-file limit, 50,000-file limit and 2 GiB ZIP
limit. These are transport/disk guards, not new model or search allowances.
The larger expansion guard enables migration of the known 4.36 GB checkpoint;
the permanent bandwidth improvement comes from compression, duplicate-object
elimination, immutable task packs and monitor projections.

The initial projection of a legacy ZIP still downloads that archive on the
runner. Subsequent packed checkpoints avoid body decompression for monitoring.
The full worker still restores all historical files; splitting cold task packs
from active worker hydration is a separate scalability improvement.

## Local archives and bandwidth

The synchronization catalog lists actual uploaded control and pack members,
not unavailable raw ZIP paths. Existing local synchronization can hash-reuse
unchanged packs across runs and resume missing ranges under its unchanged
traffic allowance. Original paths and hashes remain in `state-pack-manifest.json`.
Packs are lossless archives, not regenerated or summarized evidence. A local
consumer needing unpacked task paths must restore them using `extract_file`;
storing pack members alone is not an unpacked-body import. Never automatically
download the original legacy archive to the user's machine.

## Acceptance

`market_checkpoint_validation.yaml` restores the actual failed-size parent
inside GitHub, repacks it, roundtrips every inventoried file, verifies ledger
identity, and tests a monitor projection. It publishes only a small preservation
receipt. No model/search calls, submissions, trades or budget resets occur.
Production acceptance additionally requires successful external discovery,
automatic worker startup, restoration and a new checkpoint publication.
# Oversized control-ledger recovery

The v2 transport retains the exact state.json bytes and original hash. Ledgers
larger than1MiB are stored as deterministic16MiB XZ control chunks; native task
packs remain independent. Reader and writer share a512MiB state-only bound,
128MiB original body bound,2GiB published payload bound and16GiB expansion bound.
Legacy v1 and ordinary monitor ZIPs remain readable under the same named-file
policy. Integrity failures cannot publish partial restored state.

Both monitor and worker publish through the validated codec. Local catalogs
project the hash-verified unpacked originals while indexing the actual packed
payload. Historical discovery and price observations are retained; the codec
does not prune or reset any ledger. Lifecycle growth beyond these finite bounds
still needs explicit archival migration, never silent deletion or bootstrap.

