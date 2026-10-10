"""Frozen paired Super map trial over saved originals; no retrieval or scoring."""
import argparse
import copy
import hashlib
import json
import os
import time
from pathlib import Path

from ForecastAgent.acquisition.pipeline import reject_outcomes
from ForecastAgent.analysis.pilot import Journal, digest, load, save
from ForecastAgent.providers.model import ask_model, SUPER_MODEL, configured_model
from ForecastAgent.research_loop import decision, grounding, simple_map, state, target_logic
from ForecastAgent.research_loop.acceptance import accept
from ForecastAgent.runtime.task_lock import task_lock

SCHEMA = 'saved-target-logic-paired-trial-v1'
ARMS = ('control', 'target_linked')
MAX_CHARS = 28000
OUTPUT_TOKENS = 6500


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze(path, value):
    if path.exists() and load(path) != value:
        raise ValueError('Frozen trial inputs changed: ' + path.name)
    save(path, value)


def common_input(bundle):
    """Preserve whole saved R spans; select once for both arms without a model."""
    reject_outcomes(bundle['request'])
    material = state.catalog(bundle)
    old = bundle.get('research_loop', {}).get('current') or {}
    pinned = {r['evidence_id'] for n in old.get('nodes', []) for r in n.get('bindings', [])}
    by_source = {}
    for span in material['spans'].values():
        by_source.setdefault(span['url'], []).append(span)
    candidates = [r for r in material['spans'].values() if r['evidence_id'] in pinned]
    for i in range(max((len(rows) for rows in by_source.values()), default=0)):
        candidates.extend(rows[i] for rows in by_source.values() if i < len(rows) and rows[i]['evidence_id'] not in pinned)
    payload = {'immutable_question': {k:bundle['request'][k] for k in
        (*state.RULE_FIELDS, 'id','question_type','options','unit','as_of_utc','open_time','close_time','scheduled_resolve_time') if k in bundle['request']},
        'targets': target_logic.targets(bundle), 'expected_revision':0,
        'frozen_research_time_utc':bundle['request'].get('as_of_utc') or bundle.get('created_at'),
        'material_sha256':material['material_sha256'], 'evidence':[],
        'instruction':'Same frozen saved originals in both arms. No new sources, outcome labels, probabilities or forecasts. Unread/omitted text is not absent evidence.'}
    omitted = []
    for span in candidates:
        row = {k:span[k] for k in ('evidence_id','url','body_sha256','start','end','text','coordinate_space') if k in span}
        trial = {**payload, 'evidence':payload['evidence']+[row]}
        # Check the larger actual paired request, including JSON string escaping.
        # A guessed system allowance can overflow on Markdown/URL-heavy bodies.
        rendered = [{'role':'system','content':simple_map.SYSTEM + target_logic.GUIDE},
                    {'role':'user','content':json.dumps(trial,ensure_ascii=False)}]
        if len(json.dumps(rendered, ensure_ascii=False)) > MAX_CHARS:
            omitted.append(span['evidence_id'])
        else:
            payload = trial
    if not payload['evidence']:
        raise ValueError('No whole original span fits the trial input')
    return payload, {'selected_reference_ids':[r['evidence_id'] for r in payload['evidence']],
                     'omitted_reference_ids':omitted, 'selection':'Previously bound spans, then source-round-robin whole spans.',
                     'selection_bias':'Repair regression snapshots, not unseen recall or forecast accuracy.'}


def run(parents, directory, *, execute=False):
    root = Path(directory)
    if not 1 <= len(parents) <= 5:
        raise ValueError('Use one to five saved parent packages')
    root.mkdir(parents=True, exist_ok=True)
    with task_lock(root):
        ids = [str(load(p)['request']['id']) for p in parents]
        if len(set(ids)) != len(ids):
            raise ValueError('Use distinct question IDs')
        identity = {'schema':SCHEMA, 'parents':{str(Path(p).resolve()):sha(p) for p in parents},
                    'implementation':decision.implementation_hashes(), 'model':SUPER_MODEL,
                    'http_per_case_arm':1, 'output_tokens':OUTPUT_TOKENS,
                    'reasoning':{'max_tokens':512}, 'new_searches':0, 'new_fetches':0,
                    'mercury_calls':0, 'submission':False, 'original_quotas_reset':False}
        freeze(root/'identity.json',identity)
        if execute and configured_model() != SUPER_MODEL:
            raise ValueError('Frozen model must be Super')
        cases = []
        halt = None
        for index,parent in enumerate(parents):
            b = load(parent); ident=str(b['request']['id']); folder=root/'cases'/ident
            common, selection = common_input(b)
            freeze(folder/'common-originals.json',common); freeze(folder/'selection.json',selection)
            row = {'id':ident,'original_graph_nodes':len((b['research_loop'].get('current') or {}).get('nodes',[])),
                   'parent_sha256':sha(parent),'equal_original_input':True,'arms':{}}
            order = list(ARMS) if index % 2 == 0 else list(reversed(ARMS))
            row['order']=order
            for arm in order:
                output=folder/arm
                candidate=copy.deepcopy(b);candidate.pop('research_loop',None)
                candidate['request'].pop(target_logic.FIELD,None)
                if arm=='target_linked': candidate['request'][target_logic.FIELD]=target_logic.POLICY
                tool=copy.deepcopy(simple_map.TOOL)
                # The old arm sees exactly the archived source-first contract.
                tool['function']['parameters']['properties']['nodes']['items']['properties'].pop('target_links',None)
                if grounding.enabled(candidate):
                    tool['function']['parameters']=grounding.schema(tool['function']['parameters'])
                if arm=='target_linked':
                    tool['function']['parameters']=target_logic.schema(tool['function']['parameters'])
                system=simple_map.SYSTEM + (target_logic.GUIDE if arm=='target_linked' else '')
                messages=[{'role':'system','content':system},{'role':'user','content':json.dumps(common,ensure_ascii=False)}]
                if len(json.dumps(messages,ensure_ascii=False)) > MAX_CHARS:
                    raise ValueError('Local model context ceiling exceeded')
                freeze(output/'request.json',{'messages':messages,'tools':[tool],'output_tokens':OUTPUT_TOKENS})
                result_path=output/'result.json'
                if result_path.exists():
                    row['arms'][arm]=load(result_path);continue
                if not execute or halt:
                    row['arms'][arm]={'status':'prepared' if not halt else 'held'};continue
                try:
                    message_path=output/'message.json'
                    if message_path.exists(): message=load(message_path)
                    else:
                        message=ask_model(messages,os.environ['OPENROUTER_API_KEY'],tools=[tool],
                            forced_tool='update_research_state', observer=Journal(output/'http',1),
                            deadline=time.monotonic()+180,max_output_tokens=OUTPUT_TOKENS,reasoning={'max_tokens':512})
                        save(message_path,message)
                    calls=message.get('tool_calls') or []
                    if len(calls)!=1 or calls[0]['function']['name']!='update_research_state':
                        raise ValueError('Expected exactly one declared map tool call')
                    proposed=json.loads(calls[0]['function']['arguments']);save(output/'proposal.json',proposed)
                    accepted=accept(candidate,proposed,allowed=selection['selected_reference_ids'],
                        map_protocol=simple_map.PROTOCOL,stage_grounding=grounding.enabled(candidate))
                    graph=candidate['research_loop']['current'];save(output/'map-package.json',candidate)
                    result={'status':'map_completed','acceptance':accepted['acceptance'],
                        'target_coverage':target_logic.audit(candidate), 'node_count':len(graph['nodes']),
                        'node_kinds':{k:sum(n['kind']==k for n in graph['nodes']) for k in ('observation','driver','assumption','unknown')},
                        'relation_count':len(graph['relations']), 'material_requests':graph['material_requests'],
                        'original_pages_preserved':candidate['pages']==b['pages'],
                        'old_provider_ledgers_preserved':all(candidate.get(k)==b.get(k) for k in ('model_attempts','searches','exa_searches','fetch_attempts'))}
                except Exception as exc:
                    result={'status':'failed','error_type':type(exc).__name__,'error':str(exc)[:1000],
                            'acceptance':getattr(exc,'report',None)}
                    records=[load(p) for p in (output/'http').glob('*.json')]
                    if any(r.get('status')!='received' for r in records):
                        halt={'id':ident,'arm':arm,'reason':'provider_or_unknown_transport_requires_review'}
                save(result_path,result);row['arms'][arm]=result
                print(json.dumps({'id':ident,'arm':arm,'status':result['status'],'target_links':result.get('target_coverage',{}).get('target_link_count')}),flush=True)
            row['parent_preserved']=sha(parent)==row['parent_sha256'];cases.append(row)
            save(root/'report.json',aggregate(root,identity,cases,halt))
        return aggregate(root,identity,cases,halt)


def aggregate(root,identity,cases,halt):
    records=[load(p) for p in root.glob('cases/*/*/http/*.json')]
    usages=[r.get('response',{}).get('usage') or {} for r in records]
    for case in cases:
        for arm,entry in case['arms'].items():
            local=[load(p) for p in (root/'cases'/case['id']/arm/'http').glob('*.json')]
            usage=[r.get('response',{}).get('usage') or {} for r in local]
            entry['physical_model_http']=len(local)
            entry['known_tokens']=sum(u.get('total_tokens',0) for u in usage)
            entry['unknown_usage_requests']=sum(not u for u in usage)
    report={'schema':SCHEMA,'identity':identity,'cases':cases,'halt':halt,
            'physical_model_http':len(records),'known_tokens':sum(u.get('total_tokens',0) for u in usages),
            'unknown_usage_requests':sum(not u for u in usages), 'new_searches':0,'new_fetches':0,
            'forecasts_generated':False,'submitted':False,'accuracy':None,
            'limitation':'One sample per arm on known saved materials; structural and scope review only. No demonstrated acquisition recall or forecast quality gain.'}
    save(root/'report.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--parents',nargs='+',required=True)
    parser.add_argument('--directory',required=True)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    result=run(args.parents,args.directory,execute=args.execute)
    print(json.dumps({k:result[k] for k in ('physical_model_http','known_tokens','unknown_usage_requests','halt')}),flush=True)
