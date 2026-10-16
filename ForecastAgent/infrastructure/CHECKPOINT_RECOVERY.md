# Shared analysis boundary and checkpoint recovery

## One question projection

Both hosts call `scoring_boundary.project_bundle`. This matches the pinned
market `scoring_delivery` policy: omit only `capability_code_identity` and
`predictive_information_contract`. Retain every other field, including unknown
platform conditions. Rules, options, units, clocks and source bodies are not
summarized or truncated. A market adapter verifies that the native projection
agrees. The model states and scoring registries are unchanged on the real pinned
market implementation acceptance fixture.

Competition keeps its frozen Mercury conditional rereading and delivery guards.
Market keeps its original/map/official-map research arms. Sharing an input
boundary does not replace the two different task contracts or submission rights.
Existing requests, v1 envelopes and HTTP reservations keep their frozen route;
the shared adapter does not grant new attempts or reset quotas.

## Diagnosed publication failure

Monitor run `38811619270` restored 49,943 original files and successfully scanned,
then failed publication at the old 50,000-entry index bound. It published no
checkpoint. Subsequent monitors correctly refused to reset state after an
unpublished mutation. Its source was artifact `12008308014` from `38810374416`.

The complete saved GitHub report was reviewed and hashed:
`7a2b56d7b79bd7373b79fb896a6d2ac5f6df563bfb2973776dd4df6137212108`.
It explicitly reports zero new admissions, no model calls, zero intake HTTP
attempts and 17 previously reserved intake requests that day. Existing cohort
size is 113. This permits a tightly scoped economic-ledger-preserving recovery
from the exact original artifact/state SHA, with a permanent observational gap.
It does not establish that the failed run's raw public observations survived.

Lost observations: 96 public API requests, 142 new first sightings and 449
metadata-change observations. They are not re-created under original timestamps.
The recovery receipt identifies the run, source artifact/state hashes, report
hash, gap clocks and limitations. Unknown lost mutations, changed source hashes,
wrong workflows, incomplete enumeration and ambiguous publications still block.

## Publication hardening

- Bound the index at 500,000 entries; retain all byte, path, hash and expansion
  checks. Report capacity use and warn at 80 percent.
- Before discovery/research, reserve 10,000 index entries. Stop before new work
  when archival is needed, preserving the previous complete state.
- If normal packing/publication fails, upload the sealed raw original state to
  a distinct emergency artifact. This is lossless recovery data, not a new
  forecast or rewritten state. A healthy normal run remains a workflow-success
  requirement for automatic downstream research.
- Restore the newest unambiguous normal or emergency checkpoint. Never skip an
  unknown mutated run. Never select an older checkpoint merely because its
  workflow succeeded.

## Acceptance

Offline gates cover exact market original/map state equality, all five official
payload types, unknown-field preservation, frozen-request allowance preservation,
known versus unknown lost mutations, emergency precedence and atomic restore.
`market_recovery_acceptance.yaml` runs in a development branch with only read
permissions, restores the real original checkpoint on GitHub, verifies exact
state bytes, and repacks without discovery, model/search calls or submissions.
Only small acceptance receipts are uploaded for local review.
