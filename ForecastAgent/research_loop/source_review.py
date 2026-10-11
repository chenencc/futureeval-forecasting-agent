"""Source-isolated final reading within an existing post-supplement reservation.

Local inspection consumes no model slot. Each pending source gets at most one
proposal in this round; failures and deferred interpretations remain pending.
This does not acquire material, certify relevance, or renew any allowance.
"""
import copy
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from ForecastAgent.runtime.records import save
from ForecastAgent.research_loop import dispatch, gap_feedback, runtime, state, fusion


def messages(task, reading, choice):
    from ForecastAgent.research_loop import post_supplement, reference_map, target_logic
    system = post_supplement.SYSTEM.split('Copy material_sha256')[0]
    if reference_map.enabled(task.bundle):
        system += reference_map.TARGET_GUIDE
    system += ("\nThe program owns expected_revision and material_sha256; emit neither. "
               "Review exactly the scheduled source with its inspected R IDs. "
               "Omit unchanged nodes. Never rebind or retire protected observation IDs. "
               "Use a distinct ID for another source, even when bytes match. "
               "Duplicate needs this source's references and a related material ID. "
               "Deferred preserves unfinished interpretation, not a coverage credit.")
    packet = {
        'question': {k: copy.deepcopy(task.bundle['request'].get(k)) for k in
                     (*state.RULE_FIELDS, 'id', 'question_type', 'options', 'scaling', 'as_of_utc')},
        'reading': reading, 'current_map': post_supplement.prompt_map(task),
        'scheduled_source': dispatch.review_scope(task, choice),
        'instruction': 'One source-specific material_review; no search, capture or score.'}
    if target_logic.enabled(task.bundle):
        packet['targets'] = target_logic.targets(task.bundle)
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
    for source in sources:
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
        clock = {'started_at_utc': datetime.now(timezone.utc).isoformat(),
                 'clock_source': 'local_utc_client', 'server_time_verified': False}
        try:
            reading = task.execute('inspect_research_state', {'url': source['url'], 'limit': 3}, '')
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
            tools = dispatch.tools_for(task, runtime.configure(task, []), choice)
            tools = [t for t in tools if t['function']['name'] == 'update_research_state']
            packet = messages(task, reading, choice)
            save(folder / 'request-input.json', {'messages': packet, 'tools': tools, 'choice': choice})
            dispatch.record_selection(task, choice)
            reply = call(packet, os.environ['OPENROUTER_API_KEY'], tools=tools,
                         forced_tool='update_research_state', observer=observer,
                         deadline=min(deadline or time.monotonic() + 180, time.monotonic() + 180),
                         **fusion.model_options(task, 'update_research_state'))
            clock['received_at_utc'] = datetime.now(timezone.utc).isoformat()
            save(folder / 'proposal.json', reply)
            calls = reply.get('tool_calls') or []
            if len(calls) != 1 or calls[0]['function']['name'] != 'update_research_state':
                raise ValueError('Exactly one scoped update call required')
            args = json.loads(calls[0]['function']['arguments'])
            save(folder / 'emitted-arguments.json', args)
            bound, _, proof = dispatch.bind_arguments(task, 'update_research_state', args, tools, choice)
            save(folder / 'binding.json', proof)
            outcome = runtime.execute(task, 'update_research_state', bound)
            step.update(accepted=True, status='validated_proposal', outcome=outcome)
        except (ValueError, KeyError, TypeError) as exc:
            step.update(status='proposal_rejected', error=str(exc)[:1000],
                        acceptance=getattr(exc, 'report', None))
        except Exception as exc:
            step.update(status='provider_or_budget_failure', error_type=type(exc).__name__)
            status = 'provider_or_budget_failure'
        finally:
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
