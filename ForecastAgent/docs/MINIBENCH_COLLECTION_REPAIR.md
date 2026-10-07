# Prospective MiniBench collection recovery audit

This development candidate starts from immutable `v1.0.4` (commit
`478def7ed18d2046afb4be88a79c1c2c56173acf`). It is not a new production
release. The development manifest disables the release CLI submission path.
Analysis prompts, model routing, calibration and submission payloads are unchanged.

## Evidence and scope

The audit uses the 46 original and nine incremental prospective snapshots.
No resolution labels, later rules, provider calls or forecasts are required.
The original archives and their acquisition ledgers are immutable. Later rules
and resolution labels must be stored separately. Do not silently restart a
task under this candidate's changed code identity; migrate to an explicitly
recorded child experiment with the parent hashes and all used reservations.

The existing `complete` marker describes an exported package, not evidence
adequacy. Six raw packages lack usable bodies; two were left in collection and
four exported packages also have no usable bodies. Some of these exports already
contain failed capture diagnostics. They must not be counted as a successful
evidence handoff merely because the process returned normally.

## Repairs

1. Bounded closure is different from network interruption. State reports preserve
   the collector's execution flag, stop reason and resumability.
2. Closed observed leads may reach independent supplementation even when every
   initial body capture failed. Reserved, unknown and transport-failed model
   receipts do not grant this transition. No searches, model calls or task
   budgets are renewed. A repeated closed snapshot skips the collector.
3. A final critical basic Extract rescue uses the existing one-batch allowance
   before program closure. Short issuer names such as IMF, WHO and AAA can match
   observed failed hosts. Generic host/navigation words cannot supply identity.
   Historical restrictions, explicit channel deferrals and prior reservations
   remain binding. The rescue cannot establish source relevance or authority.
4. All-failed free recovery reads are recorded as completed with gaps.
5. An empty repaired export is not sent directly to analysis. Its native result
   remains unchanged; a separately recorded adapter exposes material unavailability.
   Existing market material remains eligible under the previous policy.
6. Resource reports distinguish transport exception classes and failures without
   an HTTP status. Old `URLError` receipts cannot identify DNS, TLS or timeout
   causes if the original journal did not record them.

## Offline acceptance

```bash
python -m unittest ForecastAgent.tests.test_collection_recovery ForecastAgent.tests.test_collection_actions ForecastAgent.tests.test_production_collection ForecastAgent.tests.test_supplement ForecastAgent.tests.test_release_1_0_4 -v
```

The paired replay uses the same original bodies and requests in both policies.
It verifies routing eligibility, short-issuer rescue candidates, budget preservation
and original file hashes. It does not prove live access, increased body recall,
source relevance or improved forecast quality. A real HTTP 403 may remain blocked;
the repair does not relax public-URL checks, certificate verification or access
controls. A provider transport failure remains resumable through the existing
budget-preserving recovery process.
