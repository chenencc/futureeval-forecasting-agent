# Competition decision envelope

The immutable acquisition and forecasting releases remain selected independently.
The current policy is `official-decision-envelope-v2-market`, using the exact
shared `scoring_boundary.project_bundle` function installed at the market seam.
It removes only `capability_code_identity` and `predictive_information_contract`.
Unknown platform extensions are retained verbatim. Previously frozen v1 envelopes
retain their exact input and request allowance; they are not silently upgraded.

The following describes the original v1 repair and its historical acceptance.
The competition host applies `official-decision-envelope-v1` at the boundary
between a saved acquisition package and the existing Mercury evidence reader.

## Contract

- Retain exact title, resolution criteria, fine print, background, platform
  options, scaling, units, question identity and information/deadline clocks.
- Exclude acquisition implementation fingerprints, provider/channel controls and
  derived research plans from the question passed to the decision reader.
- Keep all source bodies, original packages, existing requests, provider
  reservations, candidate payloads and submission receipts unchanged.
- Reject outcome labels on the original request before projection.
- Keep the frozen Mercury model, prompts, typed registry, clipping policy,
  first/second request byte limits and two-attempt limit unchanged.
- Save a deterministic projection receipt and separate analysis input. Reuse the
  same revision directory on restart. An existing request/reservation uses its
  original analysis route; it never gets another model allowance.
- Migrate a pre-request failure only when its saved original packet identity
  exactly matches the original input. Never rewrite the failed packet.

## Operational recovery

Only the exact diagnosed `Existing state exceeds decision byte bound` failure
can be reconciled. A known expired case becomes `deadline_missed` with its prior
error/history and counters preserved. An unexpired case with saved analysis input
and no reserved request can resume. Accepted cases and any submission/candidate
are excluded. This operation itself invokes no providers or submissions.

## Verification

Run the offline integration suite against commit
`751145e604406278d6d39d75135668bf30a93b78`:

```text
python ForecastAgent/infrastructure/test_competition_context.py --release PATH -v
```

Real frozen replay of questions 46281 and 46333 reproduced the failure before
HTTP: empty request states were 23,864 and 27,303 bytes against a 22,000-byte
first-request cap. Projection produced valid first reading requests of 21,938
and 21,538 bytes with 4/5 exact source spans from three sources each. Rules,
options, information clocks and saved bodies were unchanged. This is an input
delivery regression result, not an actual forecast or recovered submission.
Both original cases passed their saved deadline before this repair; no late
submission is authorized by this mechanism.
