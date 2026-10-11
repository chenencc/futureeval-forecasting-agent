"""Opt-in map-guided acquisition with existing tools and durable budgets."""
import copy
import hashlib
import json
from datetime import datetime, timezone

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.research_loop import POLICY, simple_map, state
from ForecastAgent.runtime.contracts import ContractError

POLICY_FIELD = 'research_acquisition_policy'
POLICY_NAME = 'map_guided_acquisition_v1'
LINK_FIELD = 'research_node_ids'
from ForecastAgent.tools.capabilities import NETWORK_METADATA
# Compatibility export only. Runtime behavior is selected from the registry.
NETWORK = set(NETWORK_METADATA)


def material_action(name):
    from ForecastAgent.tools.capabilities import produces_material
    return produces_material(name)

GUIDE = '''
Map-guided acquisition: one agent chooses sources AND maintains provisional notes;
no nested model calls. plan_evidence exports stable research_frontier target IDs.
The research product is saved sources PLUS a small honest map, not source counts.
Prioritize the initial map once relevant readable evidence exists; update it to
guide the next acquisition. Hard closure and unavailable evidence override this.
Link network actions with research_node_ids. Intent labels do not verify relevance.
After a useful body batch, inspect_research_state (can batch with reading), then
update_research_state in a later LOCAL-only batch using its current revision,
material hash and exact R handles. At most three updates; no node/relation quota.
Observation: claim_origin=source_quote, gap_reason=none, short CONTIGUOUS literal
quote from one R span. Put scope/interpretation/limitation separately. Drivers and
assumptions: claim_origin=hypothesis. Unknowns: claim_origin=gap, no references.
Source_stated event_time must copy a date phrase from a bound span. Other time is
unknown. Bind exact entity, period, metric, units, stage and release conservatively.
Relations are hypotheses; causal needs a mechanism, necessary/sufficient needs an
immutable rule quote. Separate episodes and conflicting reports; no probabilities,
CPTs, truth certification or target resolution. Keep support AND alternative paths.
Retain existing nodes or explicitly retire them. Use material_requests for precise
gaps, then execute native tools linked to those nodes. After two actions without
useful progress, change query/channel or record a gap. An unpublished future result,
failed fetch or absent source NEVER proves NO; research dated baselines and leading
indicators. Raw bodies and exact rules remain authoritative over fallible notes.
Map updates are optional under forced closure. Stop within unchanged tool/model
budgets and deadline; a perfect map is never required. Old revisions remain saved.
'''


def enabled(task):
    return task.bundle['request'].get(POLICY_FIELD) == POLICY_NAME


def initialize(task):
    from ForecastAgent.research_loop import grounding
    from ForecastAgent.research_loop import gap_feedback
    gap_feedback.initialize(task)
    policy = task.bundle['request'].get(grounding.FIELD, 'disabled')
    if policy not in {'disabled', grounding.POLICY}:
        raise ValueError('Unknown saved-coverage grounding policy')
    if policy == grounding.POLICY and not enabled(task):
        raise ValueError('Saved-coverage grounding requires map-guided acquisition')
    policy = task.bundle['request'].get(POLICY_FIELD, 'disabled')
    if policy not in {'disabled', POLICY_NAME}:
        raise ValueError('Unknown research acquisition policy')
    if policy == 'disabled':
        return None
    if task.bundle['request'].get('research_state_policy') != POLICY or task.bundle['pipeline'] != 'collection':
        raise ValueError('Map-guided acquisition requires the research policy in collection mode')
    rules_hash = digest({k: task.bundle['request'].get(k, '') for k in state.RULE_FIELDS})
    ledger = task.bundle.setdefault('research_acquisition', {'schema': POLICY_NAME, 'rules_sha256': rules_hash,
        'events': [], 'pending_map_update': False, 'last_mapped_material': None, 'truth_verified': False})
    if ledger['schema'] != POLICY_NAME or ledger['rules_sha256'] != rules_hash:
        raise ValueError('Map-guided acquisition rules changed')
    previous = None
    for event in ledger['events']:
        if event.get('previous_event_sha256') != previous or digest({k:v for k,v in event.items() if k != 'event_sha256'}) != event.get('event_sha256'):
            raise ValueError('Map action journal checksum mismatch')
        previous = event['event_sha256']
    return ledger


def targets(task):
    """Stable unverified target conditions inherit the frozen material plan."""
    from ForecastAgent.research_loop.target_logic import targets as target_registry
    return target_registry(task.bundle)


def inspected_references(task, material):
    """Recover delivered coordinates across dispatches; never invent new readings."""
    refs=task.bundle['research_acquisition'].get('inspected_references')
    if refs is None:
        names={c['id']:c['function']['name'] for m in task.bundle.get('messages',[])
            for c in m.get('tool_calls') or []}
        refs=[]
        for message in reversed(task.bundle.get('messages',[])):
            if message.get('role')!='tool' or names.get(message.get('tool_call_id'))!='inspect_research_state':
                continue
            try:
                reply=json.loads(message['content'])
            except (ValueError,TypeError):
                continue
            if not isinstance(reply,dict):continue
            data=reply.get('data',reply)
            if not isinstance(data,dict):continue
            if data.get('material_sha256')==material['material_sha256']:
                refs=data.get('evidence',[])
                break
    return [r for r in refs if r.get('evidence_id') in material['spans']
        and all(material['spans'][r['evidence_id']].get(k)==r.get(k)
            for k in ('body_sha256','url','start','end'))]


def binding_frame(task):
    """Pin a small, exact already-inspected source frame without network access."""
    if not enabled(task):return None
    frame=frontier(task)
    result={k:frame[k] for k in
        ('revision','material_sha256','readable_saved_sources','inspected_reference_handles','pending_map_update')}
    if 'gap_feedback' in frame:
        result['gap_feedback'] = frame['gap_feedback']
        result['last_reading_cursor'] = task.bundle['research_acquisition'].get('last_reading_cursor')
    from ForecastAgent.research_loop import grounding
    if grounding.enabled(task.bundle):
        material=state.catalog(task.bundle,task.cutoff)
        result['source_catalog']=grounding.inventory(task.bundle,material,brief=True)
        contexts=task.bundle['research_acquisition'].get('inspected_context_references', [])
        valid=[r['evidence_id'] for r in contexts if r.get('evidence_id') in material['spans']
            and all(material['spans'][r['evidence_id']].get(k)==r.get(k)
                for k in ('url','body_sha256','start','end'))]
        result['source_context']=[copy.deepcopy(material['spans'][i]) for i in valid[:3]]
    from ForecastAgent.research_loop import delta
    if frame['pending_map_update'] or delta.enabled(task.bundle):
        material=state.catalog(task.bundle,task.cutoff)
        result['inspected_evidence']=[{k:material['spans'][ident][k] for k in
            ('evidence_id','url','body_sha256','start','end','text','coordinate_space','view_sha256','parser','json_provenance') if k in material['spans'][ident]}
            for ident in frame['inspected_reference_handles'][:4]]
        from ForecastAgent.research_loop import gap_feedback
        if gap_feedback.enabled(task.bundle):
            by_url={m['url']:m['material_id'] for m in gap_feedback.materials(task,material).values()}
            for span in result['inspected_evidence']:
                span['material_id']=by_url[span['url']]
        result['instruction']='Exact previously inspected source spans. Other saved spans require inspection. Copy binding headers exactly; navigation is not verification.'
    return result


def frontier(task):
    ledger = initialize(task)
    if ledger is None:
        return None
    current = task.bundle['research_loop'].get('current') or {}
    material = state.catalog(task.bundle,task.cutoff)
    streaks, reading_streaks = {}, {}
    for event in ledger['events']:
        for ident in event['research_node_ids']:
            readable = any(p['usable_text'] for p in event['new_bodies'])
            streaks[ident] = 0 if readable or event['new_source_leads'] else streaks.get(ident, 0)+1
            reading_streaks[ident] = 0 if readable else reading_streaks.get(ident, 0)+1
    navigation = [{'id':n['id'], 'material_need_id':n['material_need_id'],
                   'condition_preview':n['condition'][:180], 'priority':n['priority']}
                  for n in targets(task)]
    result = {'schema': POLICY_NAME, 'targets': navigation,
        'material_sha256':material['material_sha256'], 'readable_saved_sources':len(material['sources']),
        'inspected_reference_handles':[r['evidence_id'] for r in inspected_references(task,material)],
        'nodes': [{k:n[k] for k in ('id','kind','claim','gap_reason')} for n in current.get('nodes', [])],
        'material_requests': copy.deepcopy(current.get('material_requests', [])),
        'revision': task.bundle['research_loop']['revision'],
        'remaining_map_updates': task.bundle['research_loop']['update_cap']-task.bundle['research_loop']['revision'],
        'pending_map_update': ledger['pending_map_update'], 'no_progress_by_node': streaks,
        'next_map_step': 'Inspect relevant saved R handles and update the initial map before further optional discovery.'
            if ledger['pending_map_update'] and not current else 'Use material requests to choose the next gap action; update only after useful new evidence.',
        'no_readable_body_by_node': reading_streaks,
        'instruction': 'Unverified navigation. Link next actions to these IDs. No-progress or future publication gaps are not event evidence.'}
    from ForecastAgent.research_loop import gap_feedback
    if gap_feedback.enabled(task.bundle):
        result['gap_feedback'] = gap_feedback.view(task)
    from ForecastAgent.research_loop import target_logic
    if target_logic.enabled(task.bundle):
        result['target_coverage'] = target_logic.brief(task.bundle)
    return result


def configure(task, tools):
    initialize(task)
    if not enabled(task):
        return tools
    tools = copy.deepcopy(tools)
    from ForecastAgent.research_loop import grounding
    for tool in tools:
        if tool['function']['name'] == 'read_sources' and getattr(task, 'raw_recall', False):
            # Raw capture does not locate paragraphs or associate semantic needs.
            parameters = tool['function']['parameters']
            parameters['properties'].pop('queries', None)
            parameters['required'] = ['urls']
            tool['function']['description'] = (
                'Capture up to four exact discovered URLs. Required: urls, an array of URL strings. '
                'Optional: rescue_failed and research_node_ids. Do not send need_ids or queries; '
                'use research_node_ids to link the capture. Read saved originals afterward.')
        name = tool['function']['name']
        if name == 'update_research_state':
            tool['function']['parameters'] = copy.deepcopy(simple_map.MAP_SCHEMA)
            if grounding.enabled(task.bundle):
                tool['function']['parameters']=grounding.schema(tool['function']['parameters'])
            from ForecastAgent.research_loop import delta
            if delta.enabled(task.bundle):
                tool['function']['parameters']=delta.schema(tool['function']['parameters'])
            from ForecastAgent.research_loop import gap_feedback
            if gap_feedback.enabled(task.bundle):
                tool['function']['parameters']=gap_feedback.schema(tool['function']['parameters'])
            from ForecastAgent.research_loop import target_logic
            if target_logic.enabled(task.bundle):
                tool['function']['parameters']=target_logic.schema(tool['function']['parameters'], bundle=task.bundle)
            node = tool['function']['parameters']['properties']['nodes']['items']['properties']
            node['claim']['description'] = 'For observation, COPY a short CONTIGUOUS literal quote from one supplied R span. No paraphrase, added entity or URL. Put your explanation in interpretation. Other kinds are explicit hypotheses or gaps.'
            if gap_feedback.enabled(task.bundle):
                node['claim']['description'] += ' If only whitespace or HTTP Markdown link display differs, the program may restore a unique exact source slice and record its coordinates. It never repairs numbers, dates, polarity, units or paraphrases.'
            node['event_time']['description'] = 'Default empty. source_stated requires an EXACT date phrase within a bound R span. Never copy the operating/capture date as an event date.'
            node['time_status']['description'] = 'Use unknown unless the event_time phrase is literally present in the bound original text; dates outside its R span are unavailable.'
            tool['function']['description'] = ('Update the current source-bound map after newly read evidence. '
                'Use current expected_revision/material_sha256 from inspect_research_state; retain or explicitly retire existing nodes. '
                'When update_mode is available, prefer merge to add/replace nodes without resending unchanged ones. '
                'Literal quotes are observations, mechanisms are hypotheses. No network inside this tool.')
        elif name == 'inspect_research_state' and grounding.enabled(task.bundle):
            from ForecastAgent.research_loop.runtime import inspection_schema
            tool['function']['parameters']=inspection_schema(tool['function']['parameters'])
            tool['function']['description']='Inspect saved coverage, exact reference handles and source context. Local only. Paginate omitted spans; use URL/query or paired ISO start_date/end_date. source_offset paginates the source catalog. A lexical date match never proves target relevance or event completion.'
        elif material_action(name):
            tool['function']['parameters']['properties'][LINK_FIELD] = {'type':'array', 'minItems':1,
                'maxItems':4, 'items': {'type':'string', 'maxLength':40},
                'description':'Existing research target/map node IDs whose gap this action addresses.'}
            tool['function']['description'] += ' Link to research_node_ids from research_frontier when available.'
            from ForecastAgent.research_loop import gap_feedback
            if gap_feedback.enabled(task.bundle):
                tool['function']['parameters']['properties'][gap_feedback.LINK] = {
                    'type':'array', 'maxItems':4, 'items':{'type':'string','maxLength':32},
                    'description':'Copy gap_id values from gap_feedback.gaps to record the intended decision impact. Before the first map use research_node_ids for frozen targets.'}
    from ForecastAgent.research_loop import reference_map
    if reference_map.enabled(task.bundle):
        for tool in tools:
            if tool['function']['name'] == 'update_research_state':
                tool['function']['parameters'] = reference_map.schema(tool['function']['parameters'])
                from ForecastAgent.research_loop import predictive_focus
                if predictive_focus.enabled(task.bundle):
                    tool['function']['parameters'] = predictive_focus.schema(tool['function']['parameters'])
                tool['function']['description'] = ('Select delivered original R IDs and explain target relations. '
                    'Program binds complete originals. No quotations, dates or offsets to copy. No network inside this tool.')
    return tools


def normalize_need_ids(task, args):
    """Narrow set syntax only; unknown IDs and all semantic fields stay unchanged."""
    known={n['id'] for n in task.bundle.get('plan') or []}
    changes=[]
    def walk(value,path='arguments'):
        if isinstance(value,dict):
            for key,item in value.items():
                field=path+'.'+key
                if key=='need_ids' and isinstance(item,str) and item in known:
                    value[key]=[item]
                    changes.append({'field':field,'from':item,'to':[item],'kind':'known_id_scalar_to_array'})
                elif key=='need_ids' and isinstance(item,list) and all(isinstance(i,str) and i in known for i in item):
                    unique=list(dict.fromkeys(item))
                    if unique!=item:
                        value[key]=unique
                        changes.append({'field':field,'from':item,'to':unique,'kind':'duplicate_id_set'})
                else:walk(item,field)
        elif isinstance(value,list):
            for i,item in enumerate(value):walk(item,f'{path}[{i}]')
    result=copy.deepcopy(args);walk(result)
    return result,changes


def before(task, name, args):
    """Reserve an intent before the existing tool handles network quotas."""
    ledger = initialize(task)
    args, normalizations = normalize_need_ids(task,args)
    from ForecastAgent.research_loop import gap_feedback
    gap_links = gap_feedback.action_links(task,args) if gap_feedback.enabled(task.bundle) else {}
    ids = args.pop(LINK_FIELD, [])
    known = {n['id'] for n in targets(task)} | {n['id'] for n in (task.bundle['research_loop'].get('current') or {}).get('nodes', [])}
    if ids and (not isinstance(ids, list) or any(not isinstance(i,str) for i in ids) or len(ids)>4 or not set(ids)<=known):
        raise ContractError('unknown_research_node', LINK_FIELD, 'Copy existing research_frontier target/map node IDs.', sorted(known))
    if ids and len(set(ids))!=len(ids):
        unique=list(dict.fromkeys(ids))
        normalizations.append({'field':LINK_FIELD,'from':ids,'to':unique,'kind':'duplicate_id_set'})
        ids=unique
    signature = digest({'tool': name, 'args': args})
    from ForecastAgent.tools.capabilities import get
    physical = 'network' in get(name).effects
    from ForecastAgent.channels.official import replay_available
    if physical and not replay_available(task,name,args) and any(e['operation_sha256'] == signature and e['status'] != 'configuration_required' for e in ledger['events']):
        raise ContractError('duplicate_research_action', 'arguments', 'This exact acquisition action was already reserved. Inspect saved material, change action or close with gaps.')
    bodies = {u:hashlib.sha256(p['content'].encode()).hexdigest() for u,p in task.bundle['pages'].items() if p.get('content')}
    leads = set(task.catalog())
    record = {'index': len(ledger['events'])+1, 'tool':name, 'arguments':copy.deepcopy(args),
        'effects':list(get(name).effects),
        **gap_links,
        'research_node_ids':ids, 'map_revision':task.bundle['research_loop']['revision'],
        'argument_normalizations':normalizations,
        'operation_sha256':signature, 'status':'reserved', 'new_bodies':[], 'new_source_leads':[],
        'budget_before':task.budget(), 'at_utc':datetime.now(timezone.utc).isoformat(),
        'previous_event_sha256':ledger['events'][-1]['event_sha256'] if ledger['events'] else None}
    record['event_sha256'] = digest(record)
    ledger['events'].append(record); task.save()
    return args, record, bodies, leads


def after(task, reserved, result=None, error=None):
    args, record, bodies, leads = reserved
    ledger = task.bundle['research_acquisition']
    # No later action may be appended before this reservation is completed.
    if ledger['events'][-1]['index'] != record['index']:
        raise ValueError('Map action completion order changed')
    from ForecastAgent.readers.quality import body_diagnostics
    new = {u:hashlib.sha256(p['content'].encode()).hexdigest() for u,p in task.bundle['pages'].items() if p.get('content')}
    record.update(status='failed' if error or isinstance(result,dict) and result.get('error') else 'completed',
        new_bodies=[{'url':u, 'body_sha256':h,
            'usable_text':body_diagnostics(task.bundle['pages'][u]['content'])['usable_text']}
            for u,h in new.items() if bodies.get(u)!=h],
        new_source_leads=sorted(set(task.catalog())-leads), budget_after=task.budget(),
        error_type=type(error).__name__ if error else None,
        completed_at_utc=datetime.now(timezone.utc).isoformat())
    if isinstance(result, dict) and result.get('status') == 'configuration_required':
        record['status'] = 'configuration_required'
    record.pop('event_sha256', None); record['event_sha256'] = digest(record)
    ledger['events'][-1] = record
    if any(p['usable_text'] for p in record['new_bodies']):
        ledger['pending_map_update'] = True
    task.save()


def mapped(task):
    ledger = initialize(task)
    material = state.catalog(task.bundle,task.cutoff)
    ledger['last_mapped_material'] = material['material_sha256']
    ledger['last_mapped_bodies'] = digest({u:p['body_sha256'] for u,p in material['sources'].items()})
    ledger['pending_map_update'] = False
    task.save()


def planning_snapshot(task):
    if not enabled(task):return None
    ledger=initialize(task)
    return {'revision':task.bundle['research_loop']['revision'],
            'body_sha256':ledger.get('last_mapped_bodies')}


def planning_advanced(task, previous):
    """Distinct new body bindings buy planning progress, never recall credit."""
    if previous is None or not enabled(task):return False
    current=planning_snapshot(task)
    grounded=any(n['kind']=='observation' for n in (task.bundle['research_loop'].get('current') or {}).get('nodes',[]))
    return bool(grounded and current['revision']>previous['revision'] and current['body_sha256'] and
                current['body_sha256']!=previous['body_sha256'])


def local_cycle(task):
    """A bounded local read/update pair may drain fresh bodies before soft stops."""
    if not enabled(task):
        return None
    ledger = initialize(task)
    revision = task.bundle['research_loop']['revision']
    if not ledger['pending_map_update'] or revision >= task.bundle['research_loop']['update_cap']:
        return None
    material = state.catalog(task.bundle, task.cutoff)
    if not material['sources']:
        return None
    current = task.bundle['research_loop'].get('current') or {}
    from ForecastAgent.research_loop import delta
    if current.get('material_sha256') == material['material_sha256'] and not delta.enabled(task.bundle):
        return None
    identity = digest({'material_sha256': material['material_sha256'], 'revision': revision})
    used = ledger.get('local_cycle_steps', {}).get(identity, 0)
    if used >= 4 or not revision and ledger.get('bootstrap_steps', 0) >= 4:
        return None
    return {'id': identity, 'material_sha256': material['material_sha256'], 'remaining_steps': 4-used}


def advisory_forcing(task, forced):
    """Hard closure and mandatory obligations stay; source suggestions are advisory."""
    if enabled(task) and forced not in {'plan_evidence','search_exa','finish_collection'}:
        ledger=initialize(task)
        cycle = local_cycle(task)
        if cycle:
            return ('update_research_state' if inspection_ready(task)
                    else 'inspect_research_state')
    if enabled(task) and forced not in {None,'plan_evidence','search_exa','finish_collection'}:
        return None
    return forced


def inspection_ready(task):
    """A rejected patch invalidates read readiness, not the saved evidence."""
    ledger=initialize(task)
    material=state.catalog(task.bundle,task.cutoff)['material_sha256']
    ready=ledger.get('cycle_inspected_material',ledger.get('bootstrap_inspected_material'))==material
    from ForecastAgent.research_loop import delta
    if delta.enabled(task.bundle):
        ready=(ready and ledger.get('inspected_revision')==task.bundle['research_loop']['revision']
            and not (ledger.get('last_map_feedback') or {}).get('requires_local_read',False)
            and bool(ledger.get('inspected_references')))
    return ready


def map_feedback(task, result=None, error=None):
    """Deliver precise quarantine feedback without changing graph acknowledgement."""
    from ForecastAgent.research_loop import delta
    if not delta.enabled(task.bundle):return
    ledger=initialize(task)
    report=(result or {}).get('acceptance') or getattr(error,'report',{})
    rejected=report.get('rejected',[])
    receipt_only_accepted = bool((result or {}).get('gap_feedback', {}).get('accepted_reviews'))
    retry_read=bool(error or (result or {}).get('committed') is False and not receipt_only_accepted
        or (result or {}).get('material_acknowledged') is False
        or any(r['section'] in {'nodes','material_reviews'} for r in rejected))
    feedback={'revision':task.bundle['research_loop']['revision'],
        'committed':bool(not error and (result or {}).get('committed',True)),
        'status':report.get('status','error' if error else 'accepted'),
        'rejected':copy.deepcopy(rejected),
        'retained_prior_nodes_after_rejected_replacement':report.get('retained_prior_nodes_after_rejected_replacement',[]),
        'error':str(error)[:1400] if error else None,'requires_local_read':retry_read,
        'instruction':'Read substantive saved text with url/query. Correct the quote and reference together; do not resend a rejected paraphrase.' if retry_read else 'Accepted source bindings remain provisional.'}
    from ForecastAgent.research_loop import reference_map
    if reference_map.enabled(task.bundle):
        feedback['instruction'] = (
            'Use the current update_cursor/schema, not the next or last submitted revision. '
            'Select delivered original R IDs; the program binds their complete text. '
            'Correct rejected source/target/receipt fields independently; inspect saved '
            'material only when new reading is needed. No copied quote or guessed offset.'
            if retry_read else 'Accepted original bindings remain provisional; meaning is unverified.')
    ledger['last_map_feedback']=feedback
    if retry_read:
        ledger.pop('cycle_inspected_material',None);ledger.pop('bootstrap_inspected_material',None)
    task.save()


def model_options(task, forced):
    """Leave room for structured map arguments after bounded hidden reasoning."""
    if enabled(task):
        return {'max_output_tokens':6500 if forced == 'update_research_state' else 3000,
                'reasoning':{'max_tokens':1200}}
    return {}


def execute(task, name, args, key, dispatch):
    """Shared native dispatch preserves existing validation, caches and quotas."""
    initialize(task)
    if getattr(task, '_research_action_active', False):
        return dispatch(name,args,key)
    if not material_action(name):
        cycle = local_cycle(task) if name in {'inspect_research_state', 'update_research_state'} else None
        if cycle:
            counts = task.bundle['research_acquisition'].setdefault('local_cycle_steps', {})
            counts[cycle['id']] = counts.get(cycle['id'], 0)+1
            task.save()
        bootstrap = (name in {'inspect_research_state','update_research_state'} and
                     not task.bundle['research_loop']['revision'] and
                     task.bundle['research_acquisition'].get('pending_map_update'))
        if bootstrap:
            task.bundle['research_acquisition']['bootstrap_steps']=task.bundle['research_acquisition'].get('bootstrap_steps',0)+1
            task.save()
        try:
            result = dispatch(name,args,key)
        except (ValueError,KeyError,TypeError) as exc:
            if name=='update_research_state':map_feedback(task,error=exc)
            raise
        if name=='update_research_state':map_feedback(task,result=result)
        if bootstrap and name == 'inspect_research_state':
            task.bundle['research_acquisition']['bootstrap_inspected_material']=result['material_sha256']
            task.save()
        if cycle and name == 'inspect_research_state':
            task.bundle['research_acquisition']['cycle_inspected_material'] = result['material_sha256']
            task.save()
        if name == 'inspect_research_state':
            from ForecastAgent.research_loop import grounding
            contexts=result.get('source_context',[]) if grounding.enabled(task.bundle) else []
            delivered=[{k:r[k] for k in ('evidence_id','url','body_sha256','start','end')} for r in result['evidence']]
            from ForecastAgent.research_loop import gap_feedback
            if gap_feedback.enabled(task.bundle):
                material=state.catalog(task.bundle,task.cutoff)
                previous=inspected_references(task,material)
                delivered_ids={r['evidence_id'] for r in delivered}
                delivered += [r for r in previous if r['evidence_id'] not in delivered_ids]
                # Retain delivered coordinates for receipts; pin only four spans.
                delivered=delivered[:16]
            task.bundle['research_acquisition']['inspected_references']=delivered
            if gap_feedback.enabled(task.bundle):
                task.bundle['research_acquisition']['last_reading_cursor']=copy.deepcopy(result['reading_cursor'])
            from ForecastAgent.research_loop import delta
            if delta.enabled(task.bundle):
                task.bundle['research_acquisition']['cycle_inspected_material']=result['material_sha256']
                task.bundle['research_acquisition']['inspected_revision']=task.bundle['research_loop']['revision']
                feedback=task.bundle['research_acquisition'].get('last_map_feedback')
                if feedback:feedback['requires_local_read']=False
            if grounding.enabled(task.bundle):
                task.bundle['research_acquisition']['inspected_context_references']=[{k:r[k] for k in
                    ('evidence_id','url','body_sha256','start','end')} for r in contexts]
            task.save()
        if isinstance(result,dict) and name in {'plan_evidence','inspect_research_state','update_research_state','collection_checkpoint'}:
            result['research_frontier'] = frontier(task)
        return result
    reserved = before(task,name,args)
    task._research_action_active = True
    try:
        result = dispatch(name,reserved[0],key)
    except Exception as exc:
        after(task,reserved,error=exc)
        raise
    else:
        after(task,reserved,result=result)
        if name == 'intelligence_part' and isinstance(result,dict) and result.get('evidence'):
            material = state.catalog(task.bundle, task.cutoff)
            prior = inspected_references(task,material)
            delivered = [{k:r[k] for k in ('evidence_id','url','body_sha256','start','end')}
                         for r in result['evidence']]
            ids = {r['evidence_id'] for r in delivered}
            task.bundle['research_acquisition']['inspected_references'] = (delivered + [r for r in prior if r['evidence_id'] not in ids])[:16]
            task.save()
        if isinstance(result,dict): result['research_frontier'] = frontier(task)
        return result
    finally:
        task._research_action_active = False
