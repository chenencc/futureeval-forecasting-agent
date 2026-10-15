# Shared acquisition for competition and prediction markets

Both hosts execute the same immutable callable:
`ForecastAgent.market_repricing.worker.acquire`, including its
`repair_materials` stage. The initial shared core is
`1a88d3b3390a6d6b92172ccd223b5f8a7948bfce`; its native engine is
`c17c1ec4ee3e2458108031a324b45853aa9e7502`.

## Execution

```text
Metaculus listener                 Market listener
       |                                |
original typed question            venue contract
       |                                |
unified_competition.py             market worker
       |                                |
       +-- same pinned worker.acquire --+
                  |
        Super collection agent
        mixed search / official channels / saved-document tools
        eager original-body capture / native research map
        independent supplement and data recovery
        HTTP/browser repair / post-supplement map review
                  |
        immutable package, bodies, clocks, hashes and gaps
                  |
       +----------+---------------------+
       |                                |
unchanged competition Mercury     unchanged market score arms
typed payload and safe delivery   observational price tracking
```

`FORECAST_ACQUISITION_SHA` is a required full Git commit. Both production
workflows verify it; the market selector must agree with it. The competition
host runs acquisition in a separate interpreter, so its immutable release
analysis and delivery imports remain intact. It preserves binary, multiple
choice, numeric, date and discrete contracts, including options, scales, units
and CDF metadata. It never manufactures a binary replacement question.

## Common policy

- Super collection with native map and channel tools, plus data recovery.
- At most two Tavily basic searches, one optional advanced search, three total;
  at most one Exa search. These are lifetime logical collection allowances.
- Initial fetch allowance eight; eager capture at most four URLs per search.
- Existing market supplement: four HTTP and two browser recovery attempts.
- Native model decision/HTTP caps and physical provider guard remain active.
- One final supplement/map package; the competition adapter does not supplement
  it a second time. Raw failures and incomplete coverage remain recorded.

## Recovery and credentials

Original contracts, package hashes and core identities are frozen. Interrupted
children resume their original journals and clocks. Existing terminal tasks and
delivery receipts are never reopened. A legacy acquisition already in progress
blocks migration rather than receiving a fresh quota. Existing candidates may
finish their original delivery path.

The shared provider ledger imports previous host transport receipts once, using
portable relative identities and original UTC dates. Each actual primary or
backup transmission enters the market physical guard separately. A local denial
is retained as a non-transmitted denial, not provider account exhaustion.
Daily and dispatch holds wait for their recorded retry conditions and do not
consume a lifetime collection execution. Their receipts and all actual HTTP
reservations remain charged. A lifetime physical cap remains terminal.
Credential selection remains platform configuration: competition uses its
existing pairs; markets use their configured market pairs. Keys are never stored
in acquisition identities or reports.

## Validation and promotion

`test_unified_acquisition.py` tests replay, identity corruption, interrupted
processes and no quota reset. `verify_unified_engine.py` invokes the actual pinned
market acquisition and recovery callable for five question types, with only
model/network boundaries replaced by offline fixtures. It also exercises physical
fallback accounting. `unified_competition_validation.py` checks unchanged release
payload formatting and hashes every restored original competition file before
and after the gate. The Linux workflow additionally restores current production
market state and runs its native promotion preservation gate.

These gates prove integration, preservation and output compatibility. They do
not establish forecast accuracy or live recall improvement. The next newly
admitted question supplies the prospective live acquisition acceptance case.
