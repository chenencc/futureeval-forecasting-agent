"""Bounded deterministic context; full conversations and bodies remain on disk."""
import copy
import json
from ForecastAgent.runtime.collection_v2 import eligible, model_view
from ForecastAgent.runtime.search_policy import requirement
from ForecastAgent.readers.quality import body_diagnostics

MAX_CONTEXT_CHARS = 28000


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def bounded(value, text_limit=700, item_limit=12):
    if isinstance(value, str):
        return value if len(value) <= text_limit else value[:text_limit]+' [truncated; read durable source]'
    if isinstance(value, list):
        result = [bounded(v, text_limit, item_limit) for v in value[:item_limit]]
        if len(value) > item_limit: result.append({'omitted_items':len(value)-item_limit})
        return result
    if isinstance(value, dict):
        return {k:bounded(v, text_limit, item_limit) for k,v in list(value.items())[:40]}
    return value


def excerpt_view(excerpt):
    """Expose navigation and explicit preview completeness without inventing coverage."""
    text = excerpt['text']
    return {'id': excerpt['id'], 'url': excerpt['url'], 'need_ids': excerpt['need_ids'],
            'preview': text[:800], 'preview_is_truncated': len(text) > 800,
            'full_chars': len(text), 'location': excerpt.get('location', {}),
            'start_char': excerpt.get('start_char'), 'end_char': excerpt.get('end_char'),
            'source_sha256': excerpt.get('source_sha256'),
            'instruction': 'Preview only. Use read_document at the saved document index and coordinates for complete source text. Preview omission cannot establish missing evidence; saved coverage is not factual verification.'}


def navigation_bound(value, text_limit, item_limit, key=''):
    """Compress inventories while retaining exact navigational identifiers."""
    if isinstance(value, str):
        if key in {'url', 'source', 'passage_id', 'need_id', 'id', 'source_sha256', 'source_parsed_sha256'}:
            return value
        return bounded(value, text_limit, item_limit)
    if isinstance(value, list):
        result = [navigation_bound(v, text_limit, item_limit, key) for v in value[:item_limit]]
        if len(value) > item_limit:
            result.append({'omitted_items': len(value) - item_limit})
        return result
    if isinstance(value, dict):
        return {k: navigation_bound(v, text_limit, item_limit, k) for k, v in value.items()}
    return value


def fit_inventory_group(group, functions, maximum):
    """Fit summaries before rejecting a group; never shorten reading payloads here."""
    for text_limit, items in ((300, 8), (180, 4), (100, 2)):
        if len(encode(group)) <= maximum:
            break
        for message in group:
            if message.get('role') != 'tool':
                continue
            name = functions.get(message.get('tool_call_id'), {}).get('name')
            if name in {'read_document', 'read_dataset_rows'}:
                continue
            try:
                payload = json.loads(message['content'])
            except (ValueError, TypeError):
                continue
            payload = navigation_bound(payload, text_limit, items)
            payload['projection_notice'] = 'Inventory preview shortened to fit delivery. Original reply and source bodies remain saved. Use navigation tools; omitted text is not missing source material or full source delivery.'
            message['content'] = encode(payload)
    return group


def collection_context(task, recent_turns=2, max_recent_chars=12000, max_chars=MAX_CONTEXT_CHARS):
    from ForecastAgent.runtime.delivery import ensure_delivery_state, project_reply, stage_read_passages, recover_read_passages, projected_visibility
    ensure_delivery_state(task)
    recover_read_passages(task)
    b = task.bundle
    messages = b['messages']
    blocked = {u for u,p in b['pages'].items() if task.verified_only and not eligible(p, task.cutoff)}
    loaded = [b['skill_bank'][name] for name in b.get('loaded_skills', [])
              if name in b.get('skill_bank', {}) and name != 'evidence-review']
    instructions = messages[0].get('content', '') if messages and messages[0].get('role') == 'system' else 'Collect source material only.'
    # Frozen skill instructions survive omitted tool replies. Catalog-only skills are not injected.
    skill_text = '\n'.join('Loaded skill '+s['name']+' ('+s['sha256']+'):\n'+s['content'] for s in loaded)
    system = {'role':'system', 'content':instructions+'\n'+skill_text}
    from ForecastAgent.runtime.task_protocol import task_view
    from ForecastAgent.runtime.collection_actions import next_action
    from ForecastAgent.runtime.collection_actions import pending_passages
    from ForecastAgent.runtime.needs import reconciliation, inventory
    from ForecastAgent.readers.datasets import observation_range
    state = {'schema':'collection_context_v2',
             'collection_temporal_policy':b.get('collection_temporal_policy'),
             'effective_cutoff_utc':task.cutoff.isoformat() if task.cutoff else None,
             'effective_mode':b['mode'],
             'next_acquisition_action':next_action(task),
             'delivered_ranges':list(b.get('progress',{}).get('reads',{}).values())[-12:],
             'working_memory_ranges':list(b.get('progress',{}).get('visible_reads',{}).values())[-12:],
             'task_protocol':task_view(task),
             'plan':b['plan'], 'entity_card':b.get('entity_card'), 'budget':task.budget(),
             'need_status':{ident:{k:row[k] for k in ('status','reason') if k in row} for ident,row in b.get('need_status', {}).items()},
             'needs_to_reconcile':reconciliation(task),
             'acquisition_inventory':inventory(b),
             'channel_plan':b.get('channel_plan', []), 'session_state':b.get('session_state', 'running'),
             'search_policy':b.get('search_policy', {}),
             'exa_requirement':requirement(task),
             'repaired_source_urls':b.get('control',{}).get('repaired_source_urls', []),
             'progress':b.get('sessions', [{}])[-1].get('turns', [])[-2:] if b.get('sessions') else [],
             'sources':[{'url':u, 'saved':u in b['pages'], 'readable':u in b['pages'] and u not in blocked and body_diagnostics(b['pages'][u].get('content',''))['usable_text'],
                         'audit_only':u in blocked} for u in task.catalog()][:20],
             'documents':[{'url':u, 'sha256':p.get('sha256'), 'chars':len(p.get('content', '')),
                           'document_count':len(p.get('documents') or [None]), 'rows':len(p.get('rows', [])),
                           'readable':u not in blocked and body_diagnostics(p.get('content',''))['usable_text'], 'audit_only':u in blocked,
                           'dataset':p.get('dataset'), 'unit':p.get('unit'), **observation_range(p),
                           'identity_preview':p.get('content','')[:450],
                           'navigation':'Filter saved dataset rows by the target date range before reading pages.' if p.get('rows') else 'Inspect document identity and use local passage navigation.'} for u,p in b['pages'].items()][:20],
             'excerpts':[excerpt_view(e)
                         for e in b['excerpts'] if e['url'] not in blocked][-12:],
             'pending_passage_ids':[p['passage_id'] for p in pending_passages(task)],
             'passage_dispositions':[{k:row[k] for k in ('passage_id','action','reason') if k in row}
                 for row in b.get('passage_dispositions',{}).values()][-16:],
             'loaded_skills':[{'name':s['name'], 'sha256':s['sha256']} for s in loaded],
             'channel_decisions':b.get('channel_decisions', {}),
             'instruction':'Full records remain on disk. Read/list saved sources to retrieve omitted material. Excerpt previews are not full excerpts: do not report source material missing merely because it is absent from a preview. Read the saved coordinates when completeness matters. Copy exact URLs and need IDs. Omitted or truncated IDs/URLs must be rediscovered before use. Located material is not truth verification.'}
    if getattr(task,'raw_recall',False):
        from ForecastAgent.runtime.acquisition import checkpoint
        state['acquisition_inventory']=checkpoint(task)
        for key in ('needs_to_reconcile','delivered_ranges','working_memory_ranges','excerpts','pending_passage_ids','passage_dispositions'):
            state.pop(key,None)
        for document in state['documents']:
            document['navigation']='Raw capture retained. Interpretation and excerpt selection are deferred.'
        state['instruction']='Preserved originals and rows are the deliverable. Use capture forms, identifiers and observed periods to select missing sources. Do not require comprehension, excerpts or page-by-page reading. Full recall and truth remain unverified.'
    state = model_view(state, blocked)
    projected = [system, {'role':'user', 'content':encode(state)}]
    # Keep complete assistant/tool groups only. Interrupted replies are closed by the runtime.
    start = b.get('control', {}).get('dispatch_message_start', 0)
    groups = []
    for message in messages[start:]:
        if message.get('role') == 'assistant': groups.append([message])
        elif groups and message.get('role') in {'tool', 'user'}: groups[-1].append(message)
    recent = []
    pinned = []
    for number, group in enumerate(reversed(groups[-recent_turns:])):
        copied = copy.deepcopy(group)
        # Runtime checkpoints are already represented by the projected state.
        copied = [m for m in copied if not (m.get('role') == 'user'
            and '"acquisition_checkpoint"' in (m.get('content') or ''))]
        expected = {c['id'] for c in copied[0].get('tool_calls', [])}
        answered = {m.get('tool_call_id') for m in copied if m.get('role') == 'tool'}
        if expected != answered: continue
        functions = {call['id']: call.get('function', {}) for call in copied[0].get('tool_calls', [])}
        reader_count = sum(f.get('name') in {'read_document', 'read_dataset_rows'} for f in functions.values())
        for message in copied:
            if message.get('role') == 'tool':
                try: payload = model_view(json.loads(message['content']), blocked, text_limit=None)
                except (ValueError, TypeError): payload = {'message':message.get('content', '')}
                function = functions.get(message.get('tool_call_id'), {})
                try: args = json.loads(function.get('arguments', '{}'))
                except (ValueError, TypeError): args = {}
                if function.get('name') in {'read_document', 'read_dataset_rows'}:
                    payload = project_reply(payload, function['name'], args, text_chars=max(500, 6000 // max(1, reader_count)))
                    data = payload.get('data', payload) if isinstance(payload, dict) else {}
                    if function['name'] == 'read_document' and isinstance(data, dict) and data.get('content'):
                        data['passages_to_review'] = stage_read_passages(task, data, args)
                        data['bank_instruction'] = 'Use review_passages to keep relevant exact span IDs or reject them with a reason. The program copies saved text and coordinates; do not retype long tables or invent line breaks.'
                else:
                    payload = bounded(payload)
                    if isinstance(payload, dict):
                        payload['projection_notice'] = 'This is a bounded tool view. Truncated strings and omitted items are not complete source delivery. Use saved-source navigation for exact text.'
                message['content'] = encode(payload)
            elif message.get('role') == 'user':
                message['content'] = bounded(message.get('content', ''), 500)
            elif message.get('role') == 'assistant' and message.get('content'):
                message['content'] = bounded(message['content'], 700)
        if not pinned:
            copied = fit_inventory_group(copied, functions, max_recent_chars)
        if len(encode(copied+recent)) > max_recent_chars:
            if not pinned:
                raise ValueError('Newest tool group exceeds the delivery budget; preserve state and reduce the requested batch size.')
            continue
        recent = copied+recent
        if not pinned:
            pinned = copied
    # Reserve the newest complete tool group before compressing task summaries.
    # Execution is not delivery: dropping this group would strand its source.
    newest = pinned
    state['working_memory_ranges'] = list(projected_visibility(task, recent).values())
    projected[1]['content'] = encode(state)
    if len(encode(projected+recent)) > max_chars:
        recent = newest
        state['working_memory_ranges'] = list(projected_visibility(task, recent).values())
        projected[1]['content'] = encode(state)
    if len(encode(projected+recent)) > max_chars:
        for text_limit, items in ((1200, 16), (600, 10), (250, 5)):
            projected[1]['content'] = encode(bounded(state, text_limit, items))
            if len(encode(projected+recent)) <= max_chars: break
    if len(encode(projected+recent)) > max_chars:
        # Durable catalogs and evidence inventories are navigable tool data.
        # Evict their summaries before evicting the just-requested source text.
        optional = {'sources','documents','excerpts','passage_dispositions','entity_card','progress'}
        lean = {k:v for k,v in state.items() if k not in optional}
        lean['omitted_sections'] = sorted(optional)
        lean['omission_instruction'] = 'These inventories remain on disk. Use list_sources/list_documents and saved-source tools; absence from this context is not absence from the ledger.'
        projected[1]['content'] = encode(bounded(lean,250,5))
    if len(encode(projected+recent)) > max_chars:
        # Keep the exact objective and newest source reply before optional prose.
        minimal = {k:state[k] for k in ('schema','task_protocol','budget','effective_mode',
            'effective_cutoff_utc','exa_requirement','need_status')}
        minimal['needs'] = [{'id':n['id'], 'priority':n['priority'],
            'condition_preview':n['condition'][:180]} for n in b.get('plan') or []]
        minimal['acquisition_inventory'] = {
            'needs':[{'need_id':n['need_id'],'material_state':n['material_state'],
                      'excerpt_ids':n['excerpt_ids'][:4],
                      'associated_source_count':len(n['usable_associated_sources']),
                      'acquisition_status':n['acquisition_status']}
                     for n in state['acquisition_inventory']['needs']],
            'projection_notice':'Compact inventory; full source handles, excerpts and row ranges remain saved. Counts establish associations only, never adequacy or absence.'}
        action = state.get('next_acquisition_action') or {}
        minimal['next_action'] = {'tool':action.get('tool'),
            'passage_ids':[p['passage_id'] for p in action.get('candidates',[]) if 'passage_id' in p]}
        minimal['projection_notice'] = 'Optional inventories omitted. Saved data and excerpts remain available; omission never proves absence. Navigate saved sources before declaring a gap.'
        projected[1]['content'] = encode(minimal)
    projected.extend(recent)
    # If immutable instructions alone exceed the ceiling, fail without a model HTTP call.
    if len(encode(projected)) > max_chars:
        raise ValueError('Context ceiling cannot fit loaded instructions; preserve ledger and reduce skill scope.')
    task._projected_visible_reads = projected_visibility(task, projected)
    b.setdefault('context_projections', []).append({'original_chars':len(encode(messages)),
        'projected_chars':len(encode(projected)), 'max_chars':max_chars,
        'retained_recent_messages':max(0, len(projected)-2), 'loaded_skills':[s['name'] for s in loaded],
        'policy':'bounded_state_complete_tool_groups_frozen_loaded_skills_v3'})
    return projected
