# GitHub-to-host lossless pack acceptance

This branch runs an isolated transport experiment. It does not change production
monitoring, acquisition, scoring, provider counters, local synchronization policy
or the prediction-market release selector.

The source is successful market worker 38504204984, artifact 11867073491. Five
question keys and ten complete saved reading/analysis input files are frozen by
path, original byte length and SHA-256 in `fixtures/bandwidth_pack_five.json`.
The original large ZIP is read only inside GitHub; it is not downloaded to the
user's computer. No original bodies are committed to Git.

Actions produces three artifacts:

- `bandwidth-trial-baseline`: the same ten files in a normal Deflate ZIP.
- `bandwidth-trial-packed`: per-question SHA-addressed objects in tar.xz packs.
- `bandwidth-trial-heads`: an independent small head and exact source/pack index.

Already compressed payloads use upload-artifact compression-level zero. The
baseline and packed output cover exactly the same original byte strings. Complete
JSON is retained, including capture clocks, raw responses and material metadata.
Local restoration verifies pack hash, exact object set, original byte size and
SHA; it never reformats JSON. This validates transport for the selected files,
not a complete migration of all worker journals or production recovery state.

The host experiment shares one cumulative 24 MiB artifact-payload allowance in
an isolated experiment directory. It does not reset the existing production
100 MiB/day ledger. Both signed range authentication and redirect boundaries use
the existing authorized GitHub CLI. Signed URLs and credentials stay in memory.
Metadata/API responses and network headers are outside payload accounting.

Run five host phases using the exact successful producer run: head, baseline,
partial, resume, repeat. `partial` stops after a saved range before a pack becomes
complete. `resume` uses those same range caches and cumulative allowance. `repeat`
must verify local originals and need zero additional artifact-payload requests.
Compare actual response bytes with producer sizes and the fresh baseline; retain
failed requests, incomplete usage and restored hashes in the final report.

Deployment remains a separate step after this experiment. Large legacy history
requires a cloud-side migration, not a full download through the user's VPN.
