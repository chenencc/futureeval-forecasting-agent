# Multiple-choice diagnostic

`ForecastAgent.analysis.categorical` adds an isolated typed diagnostic without
changing the immutable binary v8 baseline or enabling competition submission.
It consumes a saved evidence bundle, retains every exact Metaculus option,
checks quoted evidence against visible original text and requests a complete
probability distribution from the configured reasoning backend.

Ultra is primary. Two consecutive service failures switch to Super. Shared
quota/authentication failures do not switch models. Reservations are persisted
before HTTP; each reasoning backend has at most three lifetime attempts.
Mercury receives the qualitative analysis and the exact option registry, with
the reasoning probabilities withheld. Its choice response has one lifetime
request. Received HTTP responses can be replayed after a parse crash.

Both distributions are validated and their option-wise arithmetic mean is
reported. Rounding normalization is explicit and limited to 2% for Mercury.
Missing or duplicated options, invalid probabilities, invented quotes and
changed frozen inputs are rejected. A failed Mercury request preserves an
explicit single reasoning route; there is no uninformed default distribution.

The diagnostic saves a separate payload preview using a uniform affine floor
of 0.001 per option. This does not modify the raw route or mean probabilities
and does not call a Metaculus submission endpoint. Model distribution outputs
have not been fitted or proven calibrated for forecasting.

The manual `Nonbinary forecasting diagnostic` workflow fetches full question
details using the existing bot token, removes outcomes and community forecasts,
and refuses missing resolution criteria. It checks the original collector for
an existing task before starting an isolated diagnostic ledger. Acquisition
uses the existing maximum three Tavily basic searches and one Exa search;
bounded free supplementation follows acquisition before typed analysis.
Every repeat restores the same artifact, with fail-closed missing-ledger checks.

These closed-question runs use current evidence without a historical cutoff.
They test collection and distribution handling, not historical accuracy or an
accepted competition forecast. Numeric and discrete CDF support remains a
separate implementation task. The live competition queue remains unchanged.

References:
- https://www.metaculus.com/api/
- https://www.metaculus.com/how-to-forecast/
- https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request
