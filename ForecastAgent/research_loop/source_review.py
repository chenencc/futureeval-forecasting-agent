"""Source-isolated final reading within an existing post-supplement reservation.

Local inspection consumes no model slot. Each pending source gets at most one
map proposal and two optional reading decisions in this round. All model HTTP
attempts share the existing reservation; failed and deferred reviews stay pending.
This does not acquire material, certify relevance, or renew any allowance.
"""
import copy
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from ForecastAgent.runtime.records import save
from ForecastAgent.analysis.pilot import digest
from ForecastAgent.research_loop import dispatch, gap_feedback, runtime, state, fusion

MAX_SOURCE_HTTP = 3
MAX_LOCAL_READS = 2


class SourceHttpCapError(RuntimeError):
    """A source-local reservation cannot consume another source's allowance."""


def review_tools(task, choice, *, navigate, source_view=None):
    """Offer one exact saved source, never another channel or a network tool."""
    tools = dispatch.tools_for(task, runtime.configure(task, []), choice)
    tools = [t for t in tools if t['function']['name'] == 'update_research_state' or
             navigate and t['function']['name'] == 'inspect_research_state']
    for entry in tools:
        if entry['function']['name'] == 'update_research_state' and source_view:
            schema = entry['function']['parameters']
            schema['properties']['source_reading_basis'] = {'type':'string', 'enum':
                ['complete_saved_scope', 'explicit_scope_mismatch', 'partial_reading'],
                'description':'Optional reading basis. An irrelevant judgment on a partial window '
                    'requires explicit_scope_mismatch with cited positive evidence of a different scope. '
                    'An unread result or missing row is partial_reading; defer it.'}
            allowed = list(dict.fromkeys(dispatch.review_scope(task, choice)['inspected_reference_ids'] +
                                        [row[0] for row in source_view['rows']]))
            for section in ('nodes', 'material_reviews'):
                schema['properties'][section]['items']['properties']['evidence_ids']['items']['enum'] = allowed
        if entry['function']['name'] == 'inspect_research_state':
            schema = entry['function']['parameters']
            schema['properties']['url']['enum'] = [choice['url']]
            schema['required'] = list(dict.fromkeys(schema.get('required', []) + ['url']))
            entry['function']['description'] += (' Read additional original spans of this exact saved URL. '
                'No fetch or search. Use reading_cursor.next_span_offset with unchanged selectors, '
                'or start a new local query at offset 0. This consumes one model decision.')
    # Existing update-only clients may select tools[0].
    return sorted(tools, key=lambda t: t['function']['name'] != 'update_research_state')


def messages(task, reading, choice, *, prior_readings=None, navigation=None, source_view=None):
    from ForecastAgent.research_loop import post_supplement, reference_map, target_logic
    system = post_supplement.SYSTEM.split('Copy material_sha256')[0]
    if reference_map.enabled(task.bundle):
        system += reference_map.target_prompt(task.bundle)
    from ForecastAgent.research_loop import predictive_focus
    if predictive_focus.enabled(task.bundle):
        system += predictive_focus.GUIDE
    system += ("\nThe program owns expected_revision and material_sha256; emit neither. "
               "Review exactly the scheduled source with its inspected R IDs. "
               "Omit unchanged nodes. Never rebind or retire protected observation IDs. "
               "Use a distinct ID for another source, even when bytes match. "
               "Duplicate needs this source's references and a related material ID. "
               "Every non-deferred receipt needs at least one inspected reference ID, "
               "including irrelevant and duplicate. Deferred preserves unfinished interpretation, "
               "not a coverage credit. Review usefulness for forecasting, not just final resolution: "
               "a current baseline can inform a future target without containing its final value.")
    if source_view:
        system += ('\nThe saved_source_window contains literal originals and a coordinate-indexed '
            'reference table, not a summary. Read its rows as well as the earlier excerpts. '
            'You may cite its exact R IDs directly: the program binds the selected original rows. '
            'Do not infer source-wide absence from a partial window. If the window is partial, '
            'irrelevant requires source_reading_basis=explicit_scope_mismatch and cited positive '
            'evidence of that mismatch; otherwise read further or defer. Complete saved scope '
            'means only the preserved eligible text, not a complete website or verified truth.')
        from ForecastAgent.research_loop import literal_context
        if literal_context.enabled(task.bundle):
            system += ('\nSome reference rows are literal_context_window: contiguous original text '
                'including adjacent qualifiers, not a model summary. Prefer these when a name, '
                'office, measurement or date is split across short rows. Keep each distinct fact '
                'in a separate observation; do not bundle several people or measurements into '
                'one interpretation supported by sample names. A window proves only its literal '
                'contents. If its date or historical membership is absent, retain that as unknown '
                'and do not import it from the resolution rules. No minimum node count is required.')
    if navigation and navigation['available']:
        system += ('\nChoose exactly one available tool. If delivered text is navigation or lacks '
            'the needed row, use inspect_research_state for this SAME saved URL before a map update. '
            'Copy the next span cursor with identical filters, or query the saved body at offset 0. '
            'Do not fetch a page merely because its saved text has not been read. The final slot '
            'is reserved for one update; deferred remains valid when text is still inadequate.')
    projected = copy.deepcopy(reading)
    omitted = []
    # The packet already supplies current map, targets and exact source scope.
    # These named navigation summaries duplicate global state, not source text.
    # Unknown fields, complete evidence/context spans and read cursors survive.
    for key in ('research_state', 'research_frontier', 'source_catalog', 'gap_feedback'):
        if key in projected:
            value = projected.pop(key)
            omitted.append({'field': key, 'sha256': digest(value),
                            'reason': 'Global navigation retained locally; exact scheduled scope is supplied separately.'})
    packet = {
        'question': {k: copy.deepcopy(task.bundle['request'].get(k)) for k in
                     (*state.RULE_FIELDS, 'id', 'question_type', 'options', 'scaling', 'as_of_utc')},
        'reading': projected, 'current_map': post_supplement.prompt_map(task),
        'scheduled_source': dispatch.review_scope(task, choice),
        'reading_projection': {'omitted_global_metadata': omitted,
                               'original_spans_unchanged': True,
                               'full_reading_saved_locally': True},
        'instruction': 'One source-specific material_review; no search, capture or score.'}
    if target_logic.enabled(task.bundle):
        packet['targets'] = target_logic.targets(task.bundle)
    if predictive_focus.enabled(task.bundle):
        packet['current_interpretation_review_signals'] = predictive_focus.brief(task.bundle)
    if source_view:
        packet['saved_source_window'] = source_view
    if navigation:
        packet['local_reading_allowance'] = copy.deepcopy(navigation)
        packet['instruction'] = 'One exact-source local reading OR one material_review; no search, capture or score.'
    if prior_readings:
        # Earlier originals remain exact and explicitly delivered in this packet.
        # Never substitute prior reference IDs for omitted original text.
        history = []
        for prior in prior_readings:
            saved = copy.deepcopy(prior)
            removed = []
            for key in ('research_state', 'research_frontier', 'source_catalog', 'gap_feedback'):
                if key in saved:
                    value = saved.pop(key)
                    removed.append({'field':key,'sha256':digest(value),
                        'reason':'Global navigation retained locally; original spans and unknown fields survive.'})
            history.append({'reading':saved,'omitted_global_metadata':removed})
        packet['prior_local_readings'] = history
    result = [{'role': 'system', 'content': system},
              {'role': 'user', 'content': json.dumps(packet, ensure_ascii=False)}]
    dispatch.validate_delivery(task, result)
    return result


def source_window(task, reading, choice, *, prior_readings=None, navigation=None):
    """Fit whole literal rows in remaining registered context, without a model call.

    Never change the native catalog, original bytes, prior readings or hard cap.
    Small sources are delivered completely; large sources expose a truthful cursor.
    Coordinates refer to the catalog reading view, including decoded/visible scopes.
    """
    scope = dispatch.review_scope(task, choice)
    catalog = state.catalog(task.bundle, task.cutoff)
    spans = sorted((s for s in catalog['spans'].values() if s['url'] == choice['url']),
                   key=lambda s: (s['start'], s['end']))
    native_span_count = len(spans)
    from ForecastAgent.research_loop import literal_context
    context_projection = literal_context.enabled(task.bundle)
    if context_projection:
        windows = [s for s in spans if s.get('reference_kind') == 'literal_context_window']
        preferred = catalog['sources'][choice['url']].get('context_delivery_references')
        if preferred is not None:
            ids = {s['evidence_id'] for s in preferred}
            windows = [s for s in windows if s['evidence_id'] in ids]
        covered = {ident for window in windows for ident in window['constituent_reference_ids']}
        omitted_primitives = sum(s['evidence_id'] in covered for s in spans
                                 if s.get('reference_kind') != 'literal_context_window')
        omitted_contexts = sum(s.get('reference_kind') == 'literal_context_window' and s not in windows
                               for s in spans)
        # Deliver each literal character once. Primitive IDs remain in the
        # native catalog and old bindings, but redundant aliases need not repeat
        # the same text/metadata in this model packet. No summary substitutes it.
        spans = [s for s in spans if (s.get('reference_kind') != 'literal_context_window' and
                                     s['evidence_id'] not in covered) or s in windows]
    ids = {s['evidence_id'] for s in reading.get('evidence', [])}
    anchor = min((i for i, s in enumerate(spans) if s['evidence_id'] in ids), default=0)
    excluded = {'evidence_id','start','end','text','url','source_id','body_sha256'}
    metadata = [{k:copy.deepcopy(v) for k,v in s.items() if k not in excluded} for s in spans]
    shared = {k:copy.deepcopy(v) for k,v in (metadata[0] if metadata else {}).items()
              if all(k in item and item[k]==v for item in metadata)}
    view = {'policy':'literal_saved_source_window_v1', 'url':choice['url'],
        'body_sha256':scope['body_sha256'], 'scope_sha256':scope['scope_sha256'],
        'columns':['evidence_id','start','end','text','representation_metadata'],
        'shared_representation_metadata':shared,
        'rows':[], 'start_span_offset':anchor, 'total_catalog_spans':len(spans),
        'next_span_offset':anchor, 'complete_saved_scope':False,
        'source_truncated':catalog['sources'][choice['url']]['truncated'],
        'omitted_catalog_spans':len(spans), 'whole_original_or_website_verified':False,
        'instruction':'Only rows delivered here are selectable. Unread text is not absent evidence.'}
    if context_projection:
        view.update(total_native_catalog_spans=native_span_count,
                    redundant_primitive_references_omitted=omitted_primitives,
                    redundant_context_references_omitted=omitted_contexts,
                    scope_partition='context_windows_plus_ungrouped_primitives',
                    primitive_bindings_preserved=True,
                    projected_source_character_coverage_unchanged=True)
        if preferred is not None:
            view['record_delimiter_index'] = []
            view['record_delimiter_meaning_verified'] = False
    # Try complete delivery first, even if the last small excerpt was mid-source.
    # If it does not fit, page forward from the current native reading anchor.
    def row(span):
        return [span['evidence_id'],span['start'],span['end'],span['text'],
                {k:copy.deepcopy(v) for k,v in span.items() if k not in excluded and k not in shared}]
    complete = {**view, 'rows':[row(s) for s in spans], 'start_span_offset':0,
        'next_span_offset':None, 'complete_saved_scope':True, 'omitted_catalog_spans':0}
    from ForecastAgent.runtime.context import ContextProjectionError
    def annotate(candidate):
        if context_projection and preferred is not None:
            ids = {row[0] for row in candidate['rows']}
            candidate['record_delimiter_index'] = [copy.deepcopy(s) for s in preferred
                                                   if s['evidence_id'] in ids]
        return candidate
    def fits(candidate):
        annotate(candidate)
        try:
            messages(task,reading,choice,prior_readings=prior_readings,
                     navigation=navigation,source_view=candidate)
            return True
        except ContextProjectionError:
            return False
    if fits(complete):
        return complete
    available = complete['rows'][anchor:]
    low, high = 0, len(available)
    while low < high:
        count = (low+high+1)//2
        candidate = {**view, 'rows':available[:count],
            'next_span_offset':anchor+count if anchor+count < len(spans) else None,
            'omitted_catalog_spans':len(spans)-count}
        if fits(candidate):
            low = count
        else:
            high = count-1
    view.update(rows=available[:low],next_span_offset=anchor+low if anchor+low<len(spans) else None,
                omitted_catalog_spans=len(spans)-low)
    return annotate(view)


def bind_source_window(task, choice, args, tools, source_view):
    """Bind only selected, actually delivered literal originals; certify no meaning."""
    scope = dispatch.review_scope(task,choice)
    native = copy.deepcopy(args)
    basis = native.pop('source_reading_basis',None)
    if basis not in {None,'complete_saved_scope','explicit_scope_mismatch','partial_reading'}:
        raise ValueError('Unknown source reading basis')
    rows = {row[0]:row for row in source_view['rows']}
    allowed = set(rows) | set(scope['inspected_reference_ids'])
    selected = set()
    # Keep native node isolation: a malformed sibling does not invalidate a
    # valid receipt. Never register a node's invented or undelivered handle.
    for entry in native.get('nodes',[]):
        if isinstance(entry,dict) and isinstance(entry.get('evidence_ids'),list):
            selected.update(i for i in entry['evidence_ids'] if isinstance(i,str) and i in allowed)
    for entry in native.get('material_reviews',[]):
        if isinstance(entry,dict) and isinstance(entry.get('evidence_ids'),list):
            selected.update(i for i in entry['evidence_ids'] if isinstance(i,str))
    if not selected <= allowed:
        raise ValueError('Selected source references were not delivered in this exact frame')
    if len(selected) > 16:
        raise ValueError('Selected original references exceed native retained-handle cap')
    for review in native.get('material_reviews',[]):
        if isinstance(review,dict) and review.get('disposition')=='irrelevant' and not source_view['complete_saved_scope']:
            if basis!='explicit_scope_mismatch' or not review.get('evidence_ids'):
                raise ValueError('Partial saved-source reading cannot establish source-wide irrelevance; read or defer')
    if basis=='complete_saved_scope' and not source_view['complete_saved_scope']:
        raise ValueError('Model cannot declare unread source scope complete')
    catalog = state.catalog(task.bundle,task.cutoff)
    delivered = []
    for ident in sorted(selected):
        span = catalog['spans'][ident]
        if ident in rows:
            current = [span['evidence_id'],span['start'],span['end'],span['text']]
            excluded = {'evidence_id','start','end','text','url','source_id','body_sha256'}
            metadata = {k:v for k,v in span.items() if k not in excluded}
            delivered_metadata = {**source_view['shared_representation_metadata'],**rows[ident][4]}
            if current != rows[ident][:4] or metadata != delivered_metadata:
                raise ValueError('Delivered original row changed before binding')
        delivered.append({k:span[k] for k in ('evidence_id','url','body_sha256','start','end')})
    # Register the selected delivery only. The table is a real literal reading,
    # not permission to claim that undisplayed or discarded text was inspected.
    previous = fusion.inspected_references(task,catalog)
    task.bundle['research_acquisition']['inspected_references'] = (
        delivered + [r for r in previous if r['evidence_id'] not in selected])[:16]
    native_tools = copy.deepcopy(tools)
    for entry in native_tools:
        if entry['function']['name']=='update_research_state':
            entry['function']['parameters']['properties'].pop('source_reading_basis',None)
    return native, native_tools, {'policy':'selected_delivered_originals_v1',
        'selected_reference_ids':sorted(selected), 'source_window_sha256':digest(source_view),
        'source_reading_basis':basis, 'complete_saved_scope':source_view['complete_saved_scope'],
        'original_model_arguments_sha256':digest(args), 'factual_arguments_rewritten':False,
        'meaning_verified':False}


def run(task, root, *, http_cap, observer, call, deadline=None):
    """Use the exact native update handler and shared physical request journal."""
    root = Path(root)
    sources = copy.deepcopy(gap_feedback.pending(task))
    # Declared critical source URLs prioritize navigation only, not truth.
    critical_urls = {g.get('source_url') for g in gap_feedback.gaps(task)
                     if g.get('importance') == 'high'}
    sources.sort(key=lambda m: (m['url'] not in critical_urls, m['url']))
    steps = []
    status = 'partial_review'
    for source_index, source in enumerate(sources):
        if len(list((root / 'model-http').glob('*.json'))) >= http_cap:
            status = 'model_http_cap_exhausted'
            break
        if deadline is not None and time.monotonic() >= deadline:
            status = 'wall_time_exhausted'
            break
        if task.bundle['research_loop']['revision'] >= task.bundle['research_loop']['update_cap']:
            status = 'map_revision_cap_exhausted'
            break
        folder = root / 'sources' / source['material_id']
        if folder.exists():
            raise ValueError('Source review reservation already exists; inspect preserved state')
        folder.mkdir(parents=True)
        step = {'material_id': source['material_id'], 'url': source['url'], 'accepted': False}
        start_http = len(list((root / 'model-http').glob('*.json')))
        step['actual_http_start'] = start_http
        clock = {'started_at_utc': datetime.now(timezone.utc).isoformat(),
                 'clock_source': 'local_utc_client', 'server_time_verified': False}
        try:
            reading = task.execute('inspect_research_state', {'url': source['url'], 'limit': 3}, '')
            save(folder / 'reading.json', reading)
            material = state.catalog(task.bundle, task.cutoff)
            choice = {'phase': 'process_update', 'revision': task.bundle['research_loop']['revision'],
                      'material_sha256': material['material_sha256'],
                      'material_id': source['material_id'], 'url': source['url'],
                      'tool': 'update_research_state', 'instruction': 'Review this exact saved source.'}
            scope = dispatch.review_scope(task, choice)
            if not scope['inspected_reference_ids']:
                step['status'] = 'no_inspected_source_reference'
                steps.append(step)
                continue
            count = lambda: len(list((root / 'model-http').glob('*.json')))
            # One request for each later source when possible. Physical retries
            # count too; navigation cannot consume another source's reservation.
            later = len(sources) - source_index - 1
            source_cap = min(MAX_SOURCE_HTTP, max(1, http_cap - start_http - later))
            step.update(source_http_cap=source_cap, navigation=[], actual_http_start=start_http)
            prior = []
            seen = {r['evidence_id'] for r in reading['evidence']}
            selectors = {digest({'url':source['url'],'limit':3,'offset':0})}

            def scoped_observer(phase, record, token=None):
                if phase == 'reserve' and count() - start_http >= source_cap:
                    raise SourceHttpCapError('Scheduled source physical HTTP allowance exhausted')
                return observer(phase, record, token)

            turn = 0
            while count() - start_http < source_cap:
                if deadline is not None and time.monotonic() >= deadline:
                    step['status'] = status = 'wall_time_exhausted'
                    break
                navigate = (len(prior) < MAX_LOCAL_READS and
                            source_cap - (count() - start_http) > 1)
                allowance = {'available':navigate, 'additional_reads_max':MAX_LOCAL_READS-len(prior),
                             'source_actual_http_remaining':source_cap-(count()-start_http),
                             'later_sources_reserved':min(later, max(0,http_cap-count()-source_cap+(count()-start_http))),
                             'same_saved_source_only':True, 'new_search_or_capture_allowed':False}
                source_view = source_window(task,reading,choice,prior_readings=prior,navigation=allowance)
                tools = review_tools(task, choice, navigate=navigate,source_view=source_view)
                packet = messages(task, reading, choice, prior_readings=prior, navigation=allowance,source_view=source_view)
                turn_folder = folder if turn == 0 else folder/'turns'/f'{turn+1:03d}'
                turn_folder.mkdir(parents=True, exist_ok=turn == 0)
                save(turn_folder / 'request-input.json', {'messages':packet, 'tools':tools, 'choice':choice})
                save(turn_folder/'source-window.json',source_view)
                step['source_reading'] = {k:source_view[k] for k in ('complete_saved_scope',
                    'start_span_offset','next_span_offset','total_catalog_spans','omitted_catalog_spans','source_truncated')}
                dispatch.record_selection(task, choice)
                turn_clock = {'started_at_utc':datetime.now(timezone.utc).isoformat(),
                              'clock_source':'local_utc_client','server_time_verified':False}
                try:
                    reply = call(packet, os.environ['OPENROUTER_API_KEY'], tools=tools,
                                 forced_tool=None if navigate else 'update_research_state', observer=scoped_observer,
                                 require_tool=True,
                                 deadline=min(deadline or time.monotonic()+180,time.monotonic()+180),
                                 **fusion.model_options(task, 'update_research_state'))
                    turn_clock['received_at_utc'] = datetime.now(timezone.utc).isoformat()
                    clock['received_at_utc'] = turn_clock['received_at_utc']
                    save(turn_folder/'proposal.json',reply)
                    calls = reply.get('tool_calls') or []
                    if len(calls)!=1 or calls[0]['function']['name'] not in {
                            t['function']['name'] for t in tools}:
                        raise ValueError('Exactly one offered source-local tool call required')
                    name = calls[0]['function']['name']
                    args = json.loads(calls[0]['function']['arguments'])
                    save(turn_folder/'emitted-arguments.json',args)
                    if name == 'inspect_research_state':
                        from ForecastAgent.runtime.contracts import check_schema
                        schema = next(t['function']['parameters'] for t in tools if t['function']['name']==name)
                        check_schema(args,schema)
                        dispatch.review_scope(task,choice)  # Reject stale body or revision before local reading.
                        if args['url'] != source['url']:
                            raise ValueError('Copy the exact scheduled saved URL')
                        selection = digest({**args,'offset':args.get('offset',0),'limit':args.get('limit',3)})
                        if selection in selectors:
                            step['status']='local_reading_no_progress'
                            break
                        selectors.add(selection)
                        next_reading = task.execute(name,args,'')
                        save(turn_folder/'local-reading.json',next_reading)
                        new_ids = {r['evidence_id'] for r in next_reading['evidence']} - seen
                        step['navigation'].append({'arguments':copy.deepcopy(args),
                            'new_reference_ids':sorted(new_ids),'reading_sha256':digest(next_reading),
                            'reading_path':str(turn_folder/'local-reading.json'),
                            'new_capture':False,'meaning_verified':False})
                        if not new_ids:
                            step['status']='local_reading_no_progress'
                            break
                        seen.update(new_ids);prior.append(reading);reading=next_reading
                        turn += 1
                        continue
                    native_args,native_tools,delivery_proof = bind_source_window(task,choice,args,tools,source_view)
                    save(turn_folder/'source-delivery-binding.json',delivery_proof)
                    bound, _, proof = dispatch.bind_arguments(task,name,native_args,native_tools,choice)
                    save(turn_folder/'binding.json',proof)
                    outcome = runtime.execute(task,name,bound)
                    reviews = outcome.get('gap_feedback',{}).get('accepted_reviews',[])
                    accepted = any(r['material_id']==source['material_id'] for r in reviews)
                    step.update(accepted=accepted,proposal_executed=True,
                        status='validated_source_receipt' if accepted else 'source_receipt_rejected',
                        material_still_pending=source['material_id'] in {m['material_id'] for m in gap_feedback.pending(task)},
                        outcome=outcome,final_proposal_path=str(turn_folder/'proposal.json'))
                    break  # At most one map proposal; rejection/deferred never silently retries.
                finally:
                    turn_clock['finished_at_utc']=datetime.now(timezone.utc).isoformat()
                    save(turn_folder/'model-client-clock.json',turn_clock)
            else:
                step['status']='source_http_cap_exhausted'
            step['actual_http_attempts']=count()-start_http
        except SourceHttpCapError as exc:
            step.update(status='source_http_cap_exhausted', error=str(exc))
        except (ValueError, KeyError, TypeError) as exc:
            step.update(status='proposal_rejected', error=str(exc)[:1000],
                        acceptance=getattr(exc, 'report', None))
        except Exception as exc:
            step.update(status='provider_or_budget_failure', error_type=type(exc).__name__)
            status = 'provider_or_budget_failure'
        finally:
            step['actual_http_attempts'] = len(list((root / 'model-http').glob('*.json'))) - start_http
            step['material_still_pending'] = source['material_id'] in {m['material_id'] for m in gap_feedback.pending(task)}
            clock['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
            save(folder / 'client-clock.json', clock)
            save(folder / 'step.json', step)
            task.save()
        steps.append(step)
        save(root / 'steps.json', steps)
        if status == 'provider_or_budget_failure':
            break
    if not gap_feedback.pending(task):
        status = 'reviewed'
    return status, steps
