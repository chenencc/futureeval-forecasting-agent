"""Frozen copied-quote versus reference-bound Super maps; unchanged saved originals."""
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
from ForecastAgent.research_loop import decision, grounding, simple_map, state, target_logic, reference_map
from ForecastAgent.research_loop.acceptance import accept
from ForecastAgent.runtime.task_lock import task_lock

SCHEMA = 'saved-reference-map-paired-trial-v1'
ARMS = ('copied_quote', 'reference_bound')
MAX_CHARS = 32000
OUTPUT_TOKENS = 6500


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze(path, value):
    if path.exists() and load(path) != value:
        raise ValueError('Frozen trial inputs changed: ' + path.name)
    save(path, value)



def run(parents, originals, directory, *, execute=False):
    root = Path(directory)
    if not 1 <= len(parents) <= 5 or len(originals) != len(parents):
        raise ValueError('Use one to five saved parent packages')
    root.mkdir(parents=True, exist_ok=True)
    with task_lock(root):
        ids = [str(load(p)['request']['id']) for p in parents]
        if len(set(ids)) != len(ids):
            raise ValueError('Use distinct question IDs')
        identity = {'schema':SCHEMA, 'parents':{str(Path(p).resolve()):sha(p) for p in parents},
                    'original_packets':{str(Path(p).resolve()):sha(p) for p in originals},
                    'implementation':decision.implementation_hashes(), 'model':SUPER_MODEL,
                    'http_per_case_arm':1, 'output_tokens':OUTPUT_TOKENS,
                    'context_character_cap':MAX_CHARS,
                    'reasoning':{'max_tokens':512}, 'new_searches':0, 'new_fetches':0,
                    'mercury_calls':0, 'submission':False, 'original_quotas_reset':False}
        freeze(root/'identity.json',identity)
        if execute and configured_model() != SUPER_MODEL:
            raise ValueError('Frozen model must be Super')
        cases = []
        halt = None
        for index,parent in enumerate(parents):
            b = load(parent); ident=str(b['request']['id']); folder=root/'cases'/ident
            common = load(originals[index])
            reject_outcomes(common['immutable_question'])
            if str(common['immutable_question']['id']) != ident or common['material_sha256'] != state.catalog(b)['material_sha256']:
                raise ValueError('Frozen original question/material identity mismatch')
            material = state.catalog(b)
            for span in common['evidence']:
                actual = material['spans'].get(span['evidence_id'])
                if not actual or any(actual[k] != v for k,v in span.items()):
                    raise ValueError('Frozen original reference/text mismatch')
            selection = {'selected_reference_ids':[s['evidence_id'] for s in common['evidence']],
                         'original_packet_sha256':sha(originals[index]),'source_text_selection_changed':False}
            freeze(folder/'common-originals.json',common); freeze(folder/'selection.json',selection)
            row = {'id':ident,'original_graph_nodes':len((b['research_loop'].get('current') or {}).get('nodes',[])),
                   'parent_sha256':sha(parent),'equal_original_input':True,'arms':{}}
            order = list(ARMS) if index % 2 == 0 else list(reversed(ARMS))
            row['order']=order
            for arm in order:
                output=folder/arm
                candidate=copy.deepcopy(b);candidate.pop('research_loop',None)
                candidate['request'][target_logic.FIELD]=target_logic.POLICY
                candidate['request'].pop(reference_map.FIELD,None)
                if arm=='reference_bound': candidate['request'][reference_map.FIELD]=reference_map.POLICY
                tool=copy.deepcopy(simple_map.TOOL)
                # The old arm sees exactly the archived source-first contract.
                tool['function']['parameters']['properties']['nodes']['items']['properties'].pop('target_links',None)
                if grounding.enabled(candidate):
                    tool['function']['parameters']=grounding.schema(tool['function']['parameters'])
                tool['function']['parameters']=target_logic.schema(tool['function']['parameters'])
                system=simple_map.SYSTEM + target_logic.GUIDE
                if arm=='reference_bound':
                    tool['function']['parameters']=reference_map.schema(tool['function']['parameters'])
                    tool['function']['description']='Select exact supplied original R IDs; the program binds full original spans. Explain target relationships separately.'
                    system=reference_map.prompt(bundle=candidate)
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
                    # Candidate ledgers are derivative; the parent and its quotas are immutable.
                    hydrated, bindings = reference_map.prepare(candidate,proposed,selection['selected_reference_ids'])
                    save(output/'reference-binding-receipts.json',bindings)
                    accepted=accept(candidate,hydrated,allowed=selection['selected_reference_ids'],
                        map_protocol=reference_map.PROTOCOL if arm=='reference_bound' else simple_map.PROTOCOL,
                        stage_grounding=grounding.enabled(candidate))
                    if arm=='reference_bound':
                        event=candidate['research_loop']['events'][-1]
                        event['acceptance']['reference_binding_receipts']=reference_map.finalize(bindings,event['state'])
                        event['acceptance']['reference_submitted_proposal_sha256']=digest(proposed)
                        event['event_sha256']=digest({k:v for k,v in event.items() if k!='event_sha256'})
                        accepted['event_sha256']=event['event_sha256']
                        accepted['acceptance']=copy.deepcopy(event['acceptance'])
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
    parser.add_argument('--originals',nargs='+',required=True)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    result=run(args.parents,args.originals,args.directory,execute=args.execute)
    print(json.dumps({k:result[k] for k in ('physical_model_http','known_tokens','unknown_usage_requests','halt')}),flush=True)
