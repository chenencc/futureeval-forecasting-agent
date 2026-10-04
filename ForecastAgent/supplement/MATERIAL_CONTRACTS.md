# Saved Material Review Contracts

## Reading

`readers/material_passages.py` preserves exact complete lines and table rows.
Tables up to 20,000 characters stay together. Larger tables split only at row
boundaries; their header spans are delivered separately when capacity permits.
Every span retains exact saved-body offsets and a SHA-256 identity. The packet
limit is 24 passages and 60,000 characters. Oversized or omitted material remains
unknown; it never proves absence. No row is silently clipped to fit a limit.

`complete_saved_table` describes the table in the saved body. It does not certify
an upstream dataset, calendar completeness, units, release vintage, or event
truth. A partial table cannot fulfill a request for full daily coverage. HTML
table data is not proof of a downloadable CSV. Column meanings still need review.

## Record isolation

Complete JSON or a single complete tool call is required. Truncated output and
invalid root shapes remain hard failures. Unknown references and invalid binding
records are recorded in `rejected_records`; independent valid records survive.
An entirely invalid response remains a failure.

Priority/deferred overlaps are recorded in `conflicted_urls`, removed from
executable priorities and retained as deferrals. Bindings to those sources cannot
close a need. Their saved text and reasons remain available. A valid next-search
recommendation survives an unrelated source conflict. Invalid queries cannot
discard valid bindings. None of this grants new provider capacity.

## Roles and applicability

The rule clock is now retained separately from the planner's condition text.
An explicit release-month rule requires a publication-date witness; observing a
value for that month does not fulfill it. First-release selection is retained in
the contract but is not verified merely from one document. Unknown publication
dates stay unresolved. Generic policy paragraphs cannot fulfill specific passed
resolution or committee-report needs, and numeric needs require a quoted metric
observation. These checks are conservative material witnesses, not event verdicts.

Malformed source-action fields, including a string that looks like a list, are
quarantined without parsing or executing the string. Oversized action arrays are
also quarantined. Independent exact bindings survive. Invalid deferral fields
block source closure conservatively. Review errors take precedence over ready
status, while exhausted review capacity stops before a new reservation and never
overwrites the prior successful review.

Issuer publisher entries may use `explicit_official_only`: issuer-original needs
require that maintained origin, while numeric evidence may still use a secondary
report. This avoids treating syndicated reports as official issuer documents.

Source requirements distinguish issuer-original documents from secondary reports
about official results. Explicit per-need domains and the maintained publisher
registry establish issuer origins. Unknown official publishers stay unverified;
the reviewing model cannot invent authority or promote a news quotation to an
original document.

`required_axes` is computed by the program. Numeric observations keep entity,
material type, metric and period requirements. Access-method and metadata needs
do not need an observation value; explicitly dated method versions retain period
requirements. Event-document needs can omit the numeric metric while retaining
their event period. Optional false axes are recorded as not required, never as
verified. Unrecognized needs retain all four requirements conservatively.

## Offline acceptance

Run `python -m ForecastAgent.experiments.replay_material_contracts ROOT REPORT`
against an archived held-out run. It replays complete saved responses against
their original delivered passages and inspects new packets separately. It never
rebinds old model decisions to expanded passages. Original JSON hashes are checked
before and after. This measures deterministic repairs, not fresh model recall,
forecast accuracy, or historical leakage resistance.

Reader implementation hashes are part of continuation identity. Changed reader
code cannot silently resume an old execution or reset consumed attempts.

## Requirement-scoped V3 witnesses (experimental)

`field_contract.py` adds an opt-in evaluator alongside the frozen V2 evaluator.
The program supplies one `required_fields` entry per required axis, with an immutable
field ID, an exact material requirement and optional allowed event stages. The
reviewer returns a separate quotation, literal observed value, verdict, stage and
structured explanation verdict for each field. Original source coordinates are
preserved by the conservative quote binder. Missing, duplicate or unknown IDs,
unbound quotations, values absent from their quotations and conflicting verdicts
cannot close a need. Required stages distinguish an application or timetable from
an approval or completed event without requiring a positive forecast outcome for
ordinary status records. Supporting materials cannot close target-material needs.

V2 observations are not silently migrated: lacking field witnesses means unresolved
V3 review. Free-text entailment is not mechanically proven; coherent but semantically
wrong model claims remain a limitation. The fixed regression tests cover stage
confusion, unrelated field IDs, contradiction, missing witnesses, fabricated values,
negative outcome records and supporting material isolation.

`MATERIAL_FIELD_REGRESSION10.json` reuses the ten saved excerpts from run
37211590352 with unchanged source windows and gold labels. These are now a repair
regression set, not held-out examples. `material_field_regression` performs at most
ten logical calls and fifteen physical requests, with fixed Super and a 4096-token
output ceiling to accommodate field witnesses. This changed output shape and ceiling
must be disclosed when comparing against old V2 responses. Provider records and
failure state persist before additional calls. No search, fetch or submissions are
available. Production gates and the frozen V2 trial remain unchanged.
