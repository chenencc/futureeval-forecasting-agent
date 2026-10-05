# V3 versus V2 frozen-material pilot

This manual development-branch experiment uses the preregistered five-case source
pool. The concurrent control is `intelligent_materials_v2`; the candidate is
`intelligent_materials_v3`. Unlike the preceding experiment, the control is the
previous repaired material protocol, rather than the release paragraph reader.
Historical V2 results remain diagnostic and are not the primary control.

The two arms receive identical original question fields, 33 saved captures,
discovered leads, model, and maximum budgets. Model routing is fixed Super without
fallback. Each arm has at most eleven actual model HTTP attempts and twelve
decisions, with a 900-second deadline. Five pairs therefore reserve a maximum of
110 HTTP attempts. At most two questions run concurrently. Failed/interrupted
arms and unknown usage remain in the audit; there is no automatic redispatch.

No new source/search/supplement or analysis call is permitted. This is a material
selection and control experiment, not a new discovery-recall or forecasting test.
The old source-specific fifteen checks and arm order remain frozen. Useful
alternative-source material requires separate inspection; a missed source anchor
does not mean no useful material exists.

The three V3 mechanisms are tested together: bounded mandatory review, structured
no-gap declarations/program closure, and immutable temporal dependencies. A
positive paired result would support an untouched follow-up pilot, not production
promotion. Review actual tool replies, request contents, excerpt coordinates,
unknown/deferred material, execution stops, and reported versus unknown usage.

Protocol: [materials_v3_paired_protocol.json](../experiments/materials_v3_paired_protocol.json).

The manual-only workflow accepts `candidate_policy=v3` on `dev_acquisition_v2`.
Artifacts are `intelligent-frontier-v3-<question_id>`. Re-running a workflow attempt
with empty ledgers is prohibited. An interrupted saved case cannot silently start
a second session or change policy. Completed artifact imports use the existing
local sync archive and `frontier_review` checksum audit.
