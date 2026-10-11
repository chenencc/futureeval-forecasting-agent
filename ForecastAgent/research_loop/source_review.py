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


def review_tools(task, choice, *, navigate):
    """Offer one exact saved source, never another channel or a network tool."""
    tools = dispatch.tools_for(task, runtime.configure(task, []), choice)
    tools = [t for t in tools if t['function']['name'] == 'update_research_state' or
             navigate and t['function']['name'] == 'inspect_research_state']
    for entry in tools:
        if entry['function']['name'] == 'inspect_research_state':
            schema = entry['function']['parameters']
            schema['properties']['url']['enum'] = [choice['url']]
            schema['required'] = list(dict.fromkeys(schema.get('required', []) + ['url']))
            entry['function']['description'] += (' Read additional original spans of this exact saved URL. '
                'No fetch or search. Use reading_cursor.next_span_offset with unchanged selectors, '
                'or start a new local query at offset 0. This consumes one model decision.')
    # Existing update-only clients may select tools[0].
    return sorted(tools, key=lambda t: t['function']['name'] != 'update_research_state')


def messages(task, reading, choice, *, prior_readings=None, navigation=None):
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
                tools = review_tools(task, choice, navigate=navigate)
                allowance = {'available':navigate, 'additional_reads_max':MAX_LOCAL_READS-len(prior),
                             'source_actual_http_remaining':source_cap-(count()-start_http),
                             'later_sources_reserved':min(later, max(0,http_cap-count()-source_cap+(count()-start_http))),
                             'same_saved_source_only':True, 'new_search_or_capture_allowed':False}
                packet = messages(task, reading, choice, prior_readings=prior, navigation=allowance)
                turn_folder = folder if turn == 0 else folder/'turns'/f'{turn+1:03d}'
                turn_folder.mkdir(parents=True, exist_ok=turn == 0)
                save(turn_folder / 'request-input.json', {'messages':packet, 'tools':tools, 'choice':choice})
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
                    bound, _, proof = dispatch.bind_arguments(task,name,args,tools,choice)
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
