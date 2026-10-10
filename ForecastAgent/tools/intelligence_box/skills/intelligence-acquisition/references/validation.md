# Validation and boundaries

Run from the Channels worktree or another checkout containing this package:

```powershell
python -m unittest ForecastAgent.test_intelligence_box ForecastAgent.test_intelligence_congress ForecastAgent.test_intelligence_navigation ForecastAgent.test_intelligence_discovery ForecastAgent.test_intelligence_public_channels ForecastAgent.test_intelligence_agent_interface -q
```

Current acceptance: 86 tests. Tests exercise all fourteen functions through JSON
dispatch, including successful originals, exact local text/table/provision reads,
unknown/missing/wrong-type/out-of-bound arguments, cache timestamps, shared
cross-source budgets, interrupted reservations, resume, HTTP failures, incomplete
bodies, source identity mismatches, raw/candidate tampering and explicit pagination.
The fourteen-function integration test uses fake transport and blocks real socket
connections; it verifies execution contracts, not model decision quality.

Saved-public-pilot replay:

```powershell
python -m ForecastAgent.tools.intelligence_box.audit_saved_pilots --worktree . --output .tmp/acceptance-20261010/replay.json
```

This helper requires the six saved pilot directories listed in its `PILOTS`.
It never fetches missing archives. It opens ledgers read-only, verifies raw hashes,
reparses accepted captures and verifies original captures/ledgers stayed unchanged.
Missing fixtures raise an error; they do not trigger new downloads or cap resets.
The checked run inspected 58 captures: 57 existing raw bodies matched their hashes;
one preserved UK-bill transport failure has no response body. 49 accepted captures
reparsed without parser failures. Earlier failed/unparsed states were preserved.
There were zero new HTTP, model or search-provider requests during this acceptance.

The package-level `AGENT_ACCEPTANCE.json` retains structured results. Earlier
`VALIDATION.json`, `PRIORITY_VALIDATION.json`, `SEC_VALIDATION.json`,
`CONGRESS_VALIDATION.json`, `NAVIGATION_VALIDATION.json`,
`DISCOVERY_VALIDATION.json` and `PUBLIC_CHANNELS_VALIDATION.json` describe specific
public trials, including failed HTTP responses and reader limits.

## Remaining gates

- Current checks run on Windows. Linux runner, cancellation/memory bounds and
  sustained provider-rate behavior need a separate bounded integration test.
- Rotated/scanned PDF, Office originals and heuristic tables are explicit reader
  gaps. Parsing text is not verification of complete cells, units or target relevance.
- Tool contracts and examples are checked against the executable definitions.
  Skill frontmatter passes skill-creator validation. Neither proves a model will
  choose the correct source; measure that on frozen tasks before broad rollout.
- Production worker registration and native campaign/provider budget integration
  belong to Pipeline. This acceptance does not deploy or submit anything.
