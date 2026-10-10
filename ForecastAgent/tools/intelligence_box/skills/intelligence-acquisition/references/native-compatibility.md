# Native Capability compatibility

Inspected host: `codex/official-intelligence-v106`, commit
`aa02db810bdf1366b68a15ace63727e94b8f33f8`. This is isolated development code,
not a verified live production worker. The host registry and runtime own tool
admission, physical request reservation, parsed-view projection and map feedback.

## Two distinct interfaces

| Context | Schema provider | Dispatch | Budget authority |
| --- | --- | --- | --- |
| Standalone toolbox | `core.TOOL_DEFINITIONS` (14) | `box.call(name, args)` | Frozen task SQLite cap |
| Native compatible host | `compatibility.capabilities()` (12) | Existing native task `execute`/Capability dispatch | Native cumulative HTTP ledger |

Do not send native `need_ids` or `url` navigation arguments to standalone
`box.call`. The native adapter consumes those fields, checks task admission and
uses the repository handler. Never wrap a native result by executing the
standalone call again; that would duplicate acquisition and bypass projection.

Native network calls require one to eight existing `need_ids`. Native saved
navigation takes exactly one saved `url` or task-owned `capture_id`. Its normalized
unit offsets are not native evidence offsets. Use returned native reference handles
for map/quote binding; host materialization preserves original bytes/time/history.

## Registration handoff

`compatibility.specs()` declares kinds, effects, budgets, schemas, version and
code-controlled handler. `compatibility.capabilities(factory)` constructs host
Capability objects; it does not register anything or spend requests. The host's
`ForecastAgent.channels.contracts.capabilities` can delegate to it:

```python
def capabilities():
    from ForecastAgent.tools.intelligence_box.compatibility import capabilities
    return capabilities()
```

Apply donor updates and this registry delegation atomically in a reviewed host
candidate. Do not register a second capability under existing IDs. Update the
host donor identity honestly, then let its code/schema fingerprint create a new
task identity. Do not weaken frozen identity checks to resume old tasks; retain
old task directories and preserve channel-tools raw/SQLite alongside native bundles.

The four registered network tools are fetch/read/profile/bill_text. Five newly
added typed sources (GDELT, DBnomics, BLS and two GovInfo contracts) work through
`intelligence_fetch` with native reservations. No model is called within tools.
Current capture eligibility and source admission remain host decisions.

`intelligence_discover` and `intelligence_acquire_link` remain standalone-only.
They are explicitly marked pending network capabilities, not local readers.
Exposing them requires host-specific need/source admission, reservation, projection,
original-link provenance and interrupted-operation replay. The compatibility mode
hides them until that implementation exists; metadata alone is insufficient.

## Verified boundary

An isolated git archive of the inspected host was overlaid with current toolbox
code; only that disposable archive's capability delegation and donor label changed.
The peer checkout, frozen historical tasks and production were untouched.
90 standalone tests and 48 host-overlay tests passed with fake transport.
Five new sources shared native attempts; successful/failed operations replayed
without additional transport after task restore. Quota exhaustion blocked transport,
429 retained Retry-After, and unsupported network extensions were not offered.
See package-level NATIVE_COMPATIBILITY_VALIDATION.json. These are mechanics tests,
not live source selection, Linux stress, historical availability or forecasting
quality evidence.

## New typed-source transport handoff

Eurostat, ECB, six NWS routes and SEC companyfacts still use intelligence_fetch.
They require no extra tool ID or allowance. Register frozen updated source/schema
identity atomically; never relabel code as an earlier donor or silently migrate
old tasks. NWS native injected transport must forward NWS_USER_AGENT (or approved
SEC contact fallback) rather than a contact-free generic agent. Standalone live
validation does not prove this host behavior. SEC large payloads use the already
persisted document byte cap. Keep missing configuration before reservation.
