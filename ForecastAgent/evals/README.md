# Frozen model capability campaign

This independent research runner uses only `OPENROUTER2`. It never switches keys
or models, acquires pages, submits forecasts, or changes production routing.

## Frozen design

The completed `capability-router2-500-20261012-v2` campaign keeps its immutable
runtime copy and original report. The current generator is a separately versioned
fixture v2: attendance now asks event status rather than report entailment, and a
gap may cite the literal source explaining why event status is unknown. The
original NONE-only gold labels and old scores are not silently rewritten.

Use `python -m ForecastAgent.evals.audit_campaign --root <completed-campaign>
--destination <new-audit-directory>` for an append-only oracle audit and a
zero-provider fallback replay. This replay verifies existing original Mercury
receipts and source identities; it does not create new forecasts or an additional
independent arm. The old apparent prompt improvement in the ambiguous attendance
family must not be used as evidence of broader forecast improvement.

- A: 60 controlled fixtures, two models, two prompts: 240 requests.
- B: 18 context variants, two models, two prompts: 72 requests.
- C: six fixtures, two models, two additional repeats: 24 requests.
- D: 12 graph attribution fixtures, two models, two prompts: 48 requests.
- E: 12 saved binary questions, one Super brief and two Mercury forecasts: 36 requests.
- 420 primary requests; at most 80 service retries; 500 physical requests overall.
- At most two requests per job, including its retry. Invalid or wrong received
  answers are retained and scored, never rewritten or retried until correct.

The answer keys in A-D are deterministic fictional fixture oracles. They test
source-bounded reading and attribution, not real-world forecasting. The holdout
changes parameters and layouts within generator families; it is not independent
human-labeled document generalization. Repeated templates and stress variants
must not be counted as independent questions for significance claims.

E uses old saved original bodies and a new forecast clock. Every source selection,
omission, original hash and parent hash is recorded. A read-only official API
receipt must match each question ID and show no resolution before scoring. That
receipt is never sent to a model. This does not exclude model-memory leakage or
establish the first public time of an event. Brier/RPS is unavailable until
official resolution and a separate evaluation audit.

## Interfaces and budgets

Super uses Chat Completions with one forced schema tool. Mercury uses Decisions
with typed choice or noul questions. A-D have matched original text, options and
prompt instructions. Citation checks are separate from answer correctness;
Mercury is not asked to generate prose. E shows Mercury the same exact originals
in both arms, with a fallible Super brief added only to the second arm.

Each physical POST has a durable reservation, exact wire hash, response, timing
and usage record. Unsettled reservations block resume. Completed jobs are cached.
Source, code, case, gold and job identities cannot change after preparation.
The runner checks account quota before each batch and stops at a 100-request
account reserve, account/credential errors, or five consecutive service failures.
The counter is per campaign, never replenished by a new batch, day, key or model.

Run preparation without credentials:

```text
python -m ForecastAgent.evals.campaign prepare --root <campaign-directory>
```

Execution requires `FORECAST_EVAL_CREDENTIAL_SLOT=OPENROUTER2`,
`OPENROUTER_API_KEY` containing that local credential, and `METACULUS_TOKEN` for
read-only status checks. Load them from the existing encrypted local store in a
hidden launcher, never a command-line argument, log, repository or chat.

```text
python -u -m ForecastAgent.evals.campaign run --root <campaign-directory>
```

Review `status.json`, `report.json`, `metrics.csv`, `jobs/*/result.json`, and
`http/*.json`. Quota GETs and official metadata GETs are outside the 500-model-POST
counter and recorded separately. Do not equate completed fixtures, valid output
formats or source binding with calibrated real-event predictive improvement.
