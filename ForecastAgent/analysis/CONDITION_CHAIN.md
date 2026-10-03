# Condition binding experiment

Development branch: `dev_formal`. Production remains pinned to `v1.0.1`.

The experiment replays the original acquisition and saved independent supplement.
No searches, capture requests, outcome labels or forecast submissions occur during inference.

1. Mercury answers five independent condition questions: entity, metric and units, time and vintage, stage and scope, and outcome evidence. Each choice selects a supporting/refuting passage ID or insufficient/not applicable. Full candidate distributions are saved; passage selection probabilities are not independent event probabilities.
2. The program binds every assessment and full probability distribution to exact evidence IDs, offsets and body hashes. Provenance validation does not validate semantic truth. Contextual or mismatched observations do not establish event failure.
3. A second Mercury request receives the original passages and these uncertain assessments and forecasts the original event. It sees no earlier event probability.

Original AND/OR branches and exceptions remain authoritative. Facets are not additional resolution requirements. The program neither multiplies condition probabilities nor invents extracted quantities or arithmetic. This first experiment tests explicit dependencies; automatic semantic rule decomposition and validated numeric extraction remain future work.

Each task has at most two physical HTTP attempts, one per stage. Exact request journals are reused on resume; changed identities are rejected. If the final stage fails, preserve condition judgments and mark the task failed rather than fabricate a forecast. Original 22,000 and 28,000 serialized-byte bounds apply; candidate-option and receipt overhead reduces selected original text and must be audited against the baseline.

The dispatch-only workflow `analysis_p0_forty.yaml` on `dev_formal` defaults to five binary pilot cases. Set `pilot_only=false` only for the authorized frozen forty-question expansion. Distribution grids and clipping match existing analysis. Date grids lacking official metadata remain research-only.

Retrospective results are paired workflow diagnostics, not leakage-free forecasting accuracy. Review Brier/CRPS, original-text coverage, entity/date/unit mistakes, condition contradictions, actual attempts, tokens and failed stages. No production promotion occurs automatically.
