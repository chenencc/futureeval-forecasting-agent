"""Revalidate immutable material responses; no new acquisition or model calls."""
import hashlib
import json
from pathlib import Path
from ForecastAgent.analysis.pilot import load,save
from ForecastAgent.supplement import material_review,need_ledger


def replay(root,output):
    root=Path(root)
    paths=[p for p in root.glob('solid-collection-case-*/*/*.json')]+list(root.glob('solid-collection-case-*/*.json'))
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    cases=[]
    for folder in sorted(root.glob('solid-collection-case-*')):
        if not folder.is_dir():continue
        b=load(folder/'intelligence-bundle.json');old_state=load(folder/'supplement/state.json')
        state={'material_reviews':[]};reviews=[]
        for path in sorted((folder/'supplement').glob('material-model-*.json')):
            record=load(path)
            if record.get('status')!='received':continue
            payload=json.loads(next(m['content'] for m in record['request']['messages'] if m['role']=='user'))
            choice=record['response']['choices'][0]
            try:
                decision=material_review.decode(choice['message'],payload,choice.get('finish_reason'))
                bound=material_review.bind(b,payload,decision)
                state['material_reviews'].append({'status':'bound','result':bound})
                reviews.append({'record':path.name,'status':'revalidated','bindings':len(bound['bindings']),
                    'isolated_records':bound['rejected_records']})
            except ValueError as exc:
                state['material_reviews'].append({'status':'invalid_or_failed','error':str(exc)})
                reviews.append({'record':path.name,'status':'rejected','error':str(exc)})
        ledger=need_ledger.build(b,state)
        cases.append({'id':str(b['request']['id']),'old_termination':b.get('material_termination'),
            'new_termination':need_ledger.termination(ledger,reason='saved_response_replay',search_available=False),
            'needs':[{'id':n['id'],'condition':n['condition'],'question_clock':n['question_clock'],
                'captured':n['target_material_captured'],'blocked':n['blocked_material_bindings'],
                'accepted_urls':[x['url'] for x in n['material_bindings']]} for n in ledger['needs']],
            'reviews':reviews,'original_statuses':[r['status'] for r in old_state.get('material_reviews',[])],
            'model_attempt_count_preserved':len(old_state.get('material_model_attempts',[])),
            'new_calls':0,'original_decisions_not_rebound_to_expanded_packet':True})
    unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in before.items())
    report={'schema':'validation-five-requirement-replay-v1','cases':cases,'original_files_unchanged':unchanged,
        'prior_audit_correction':'The last House and Newsmax priority fields were strings of 32 and 48 characters, not arrays of 32 or 48 source IDs.',
        'budgets_reset':False,'new_model_calls':0,'new_search_calls':0,'new_fetch_calls':0,
        'truth_verified':False,'scope':'Saved-response contract and acceptance replay, not a fresh model recall measurement.'}
    save(Path(output),report)
    print([(c['id'],c['new_termination']['acquisition_outcome'],[(n['id'],n['captured']) for n in c['needs']]) for c in cases])
    print('Original files unchanged:',unchanged)
    return report


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('root');parser.add_argument('output')
    args=parser.parse_args();replay(args.root,args.output)
