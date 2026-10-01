# Model-independent acquisition protocol

The acquisition policy is independent of a model brand. The default backend is
`nvidia/nemotron-3-super-120b-a12b:free`.
Set `FORECAST_MODEL` to another OpenRouter model that supports the required
function-call protocol. This changes the backend, not the prompts, tools,
search allowances, source records or immutable question input.

Existing campaigns retain their frozen backend until an explicit `switch-model`
operation records the old/new models, reason, commit and consumed resources.
The raw campaign workflow accepts `model_change_reason` for this operation and
records provider resumption when a prior backend pause exists. Every task,
search quota and historical transport remains intact; model switching does not
provide fresh search allowances. Verify the target backend with the independent
single-request health workflow before resuming collection.

The core entry point is `providers/model.py:ask_model`. The older
`providers/ultra.py` HTTP implementation and `ask_ultra` import seams remain
compatibility adapters. Provider-specific transport code is not an instruction
to the acquisition agent. Each execution records the actual configured model
and the rendered prompt/tool hashes. A switch to another HTTP provider requires
an adapter; compatibility with arbitrary models is not assumed.

## Prompts and the task clock

`prompts/collection.md` contains the generic acquisition instructions.
`runtime/guidance.py` adds effective temporal policy and program budgets.
`runtime/task_protocol.py` produces the same effective task view for normal
planning and focused supplemental discovery.

For current collection, the operating clock is frozen at the start of each
dispatch. Original question dates and input mode remain in the immutable ledger
and a separate provenance object. They are not the operating date. Event dates
remain part of the question; a completed event window calls for source records,
not a forecast for the remainder of a past month. A conservative preflight
rejects explicit critical past-month forecast needs; it is not a semantic
fact-checker. Historical replay retains its separate effective cutoff routing.

## Discovery must advance to reading

After a current/live discovery advance, a relevant unattempted reading frontier
blocks another discovery call until a real reading batch is attempted or a
usable saved source is inspected. Exact discovered URLs and critical-source
labels guide routing; this does not prove authority or relevance. The required
single Exa supplement may run early after primary discovery.

`read_sources` combines free fetching, fresh body diagnostics, optional rescue
of selected failed named critical sources, and paragraph location. Rescue uses
the existing single basic Extract batch, not new credit. Set
`rescue_failed=false` to defer rescue. Useful excerpts are still selected by the
model; the program does not auto-accept lexical matches as facts.

Government identity banners, access interstitials and empty application shells
are acquisition failures. Failed raw captures remain available for audit. Old
cached bodies are checked again by the current diagnostics. A failed initial
free fetch cannot be silently repeated through the batch tool.

`parameterize_source` registers a supported IEM station-history year/month
variant of an already discovered URL without HTTP. Station and network identity
remain fixed. Arbitrary URL construction remains forbidden. The returned URL
is a reading lead, not proof that observations exist.

## Separate model decisions from transport failures

| Resource | Per dispatch / task |
| --- | --- |
| Model decisions, including closure | At most 12 per dispatch |
| Failed or unresolved model transports | At most 4 per dispatch |
| Physical model HTTP attempts | At most 16 per dispatch, 72 per task lifetime |
| Dispatch duration | At most 900 seconds per question |
| Tavily basic | At most 3 per new v3 task lifetime |
| Exa discovery | One required attempt per enabled new collection task lifetime |
| Initial shared source HTTP | At most 8 per task lifetime |
| Tavily basic Extract | At most one batch of up to 5 failed important URLs |

The HTTP ceiling rose from 12 to 16 only to separate transport failures from
productive decisions. It is a cost bound, not a claim of reduced expenditure.
Failure retries still consume physical and lifetime counters. Existing search
ledgers and policies are not reset. Session records expose their own limit so
older 12-HTTP sessions remain auditable. A failed Exa HTTP attempt satisfies the
attempt obligation but does not count as useful discovery.

Known unsupported Exa publication/domain combinations fail before reservation;
use `general` with scientific/government domains. Interruption exports retain
the durable Exa requirement and acquisition checkpoint. Workflow acceptance
reads provider ledgers rather than relying on a possibly partial summary.

## Validation scope

Offline regressions exercise clock consistency, model substitution, search-to-
read routing, government banner recognition, batch rescue, parameter preflight,
station identity preservation, transport budgets and interruption exports.
They do not establish improved live evidence quality or token savings. Live
validation should resume one existing incomplete case with its original ledger
before expanding the question count.
