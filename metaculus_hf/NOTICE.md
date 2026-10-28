# Source provenance

Shared acquisition source is explicitly extracted with the owner's authorization. Public official analysis is retained at the source revision in sources.lock.json. This extraction does not relicense third-party code. Retain original notices when updating dependencies. No private runtime data or credential files are included.

The acquisition copy now has an explicit opt-in local extension for
`mandatory-question-research-query-v1`: two base searches, one durable Nemotron
research query, and that same query on both providers. This is not represented as
already present in the upstream acquisition commit. `sources.lock.json` retains
the original upstream byte/normalized hashes alongside the installed patched
normalized hash and local-patch description. Other callers remain opt-in; the
Metaculus collector enables the extension for new capture runs.

The local `topic-bound-independent-candidates-v1` extension additionally patches
acquisition, admission, retrieval planning and primary-source tracing to prevent
platform FAQ/prediction pages and unrelated outlinks from becoming independent
event evidence. Original upstream hashes and each installed local patch are
recorded separately in the source lock; this does not claim an upstream release.

`saved-body-quality-recovery-v1` is a further local acquisition patch. It permits
bounded closing recovery when earlier reads/reviews left no usable body,
recognizes the ClinicalTrials empty shell, rejects demonstrable season/value
review contradictions, and excludes generic policy navigation. The narrow
ClinicalTrials API adapter and period validator are new local modules, not
claimed as upstream code. Original bodies and failed review receipts are kept.
