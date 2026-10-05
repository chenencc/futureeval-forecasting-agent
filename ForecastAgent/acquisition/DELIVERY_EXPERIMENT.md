# Delivery before soft stall: paired development pilot

The primary comparison is concurrent V3 against V3 with one opt-in runtime
control: `drain_unseen_reads_before_stall=true`. This is not the preceding V2
versus V3 experiment. Prompts, rule/date contracts, review limits, source tools,
question fields, body pool, model, arm order, and budgets remain unchanged.

## Mechanism

A soft stall can occur immediately after a successful local document read.
Execution saves the reply, but only a later model request can deliver it and
acknowledge its exact visible coordinates. The new control inspects the newest
complete assistant/tool group before a soft stall stop. It allows the existing
context and review protocol to deliver a nonempty saved slice that matches the
current original raw hash and parsed coordinates and adds uncovered text.

This check does not grant progress, create a receipt, reset a counter, or change
a budget. A successful model response commits the existing delivery receipt;
failed transport does not. Repeated catalog navigation, overlapping delivered
reads, failed/blocked replies, stale versions, incomplete batches, and older
unseen groups do not extend the dispatch. Hard decision/HTTP limits, deadline,
tool failure stops and explicit agent finish remain binding. The supported
drain source is `read_document`, covering saved HTML, PDF and table documents;
dataset replies without an immutable source-version attestation are not given
this exception.

## Offline evidence

The saved model decisions from run `37308208275` reproduce the hack and gas
stall sequences on the unchanged original pool. A paired mocked replay checks
that both old sequences stop before the new read is delivered, and the opt-in
arm delivers and reviews it without source access or quota resets. Separate
negative cases check version corruption, overlapping reads, hard stops and
provider failure. Mocked responses are not live cost or quality measurements.

## Live registration

[materials_delivery_paired_protocol.json](../experiments/materials_delivery_paired_protocol.json)
freezes five cases, 33 original captures and 15 source-specific probes. The
model remains `nvidia/nemotron-3-super-120b-a12b:free`, with no fallback. Each arm
has at most eleven physical model HTTP attempts, twelve decisions and a
900-second deadline. Five pairs have a maximum of 110 physical attempts, with
at most two questions running concurrently. No search, source fetch, Extract,
supplement, analysis or forecast submission is permitted.

Use the existing manual development workflow with `candidate_policy=delivery`.
Fresh identities prevent older ledgers from being migrated. Artifacts use
`intelligent-frontier-delivery-<question_id>` and the existing checksum/local
import path. Read actual request contents, drained slice inclusion receipts,
remaining undelivered reads, exact excerpts, execution stops and physical usage.
Provider failures and budget-censored cases remain in the report. No implicit
redispatch or production promotion is allowed.

Material probes are not an exhaustive semantic adequacy judgment. The five
repaired cases cannot establish generalization. Historical unrestricted material
cannot establish cutoff-safe forecast accuracy. Fixed analyst projection is an
offline packaging measurement only; analysis itself remains frozen.
