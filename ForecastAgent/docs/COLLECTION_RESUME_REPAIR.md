# Collection dispatch repair and Exa supplement

The five-question v3 pilot at run `36796437198` completed only BTC. Four tasks
reached the 24-turn limit after rereading isolated bodies or rejecting closure
without a recent search. It consumed 116 physical Ultra attempts, 1,622,549
reported tokens and seven Tavily basic attempts. These costs remain in the ledger.

## Control changes

- A historical audit-only body returns an explicit blocked error, empty content
  and no continuation offset. Metadata listings identify unreadable documents.
- Batch reading with no eligible passage or successful read reports no progress.
  Exact repeated local reads and archive requests are blocked across dispatches.
- Links extracted from a current audit-only body remain stored for audit, but
  are excluded from the model's discovery catalog until its parent is eligible.
- Three consecutive failed/nonprogressing turns request a gap export. Collection
  dispatches have twelve turns and twelve physical Ultra HTTP attempts, including
  retries. The existing 72-attempt lifetime ceiling remains unchanged.
- Missing recent discovery is recorded as a gap instead of preventing export.
  Critical needs with no associated usable source are also automatic gaps.
  `acquisition_complete=false` distinguishes an exported package from adequate
  collection. Workflow completion alone does not establish useful evidence.
- A deterministic program export closes a dispatch that used its limit without
  a model export. Ordinary model transport failures remain resumable. A resumed
  incomplete task clears dispatch-local closing latches, preserving physical
  attempt ledgers and request hashes.
- `plan_channels` exposes allowed catalog IDs as an enum and describes every
  required field. IDs are channel capabilities, not names of provider tools.
- Model views use program state and two recent complete turns with a 12,000
  character target for the recent section. Raw conversations remain on disk.
  This is a context projection, not a claimed tokenizer or total token ceiling.

## Explicit Exa allowance

Existing ledgers never silently gain Exa quota when a key is configured.
The operator action `historical_batch enable-exa` requires an authorization ID
and reason. It adds at most one Exa attempt to each unfinished task, writes a
budget amendment and snapshots pre-resume counters. Repeating the same action
does not add another attempt. Completed BTC is reused without another model,
search or Exa call. Tavily's three-attempt lifetime limits do not change.

The manual five-question workflow has an `enable_exa` input, false by default.
The authorized 2026-10-01 repair uses this supplement and restores the prior
campaign artifact before continuing. Credentials stay in Actions Secrets.

## Consumption comparison

Reports must show all three quantities: the old v2 experiment, the interrupted
v3 prefix, and the new resume increments plus cumulative totals. Resume cost is
not a fresh-run performance estimate: it starts with prior material, a partly
spent HTTP budget and one already completed task. Neither lower incremental
cost nor successful export proves that the 135-question batch is ready.
Current-vintage datasets and missing historical snapshots retain their temporal
limitations. No forecast, outcome verdict, trading or scoring is added.
