"""Replay saved complete responses against their original delivered passages.

New packets are inspected separately. Old model decisions are never rebound to
expanded passages. This utility makes no provider requests and writes only its
explicit report destination.
"""
import hashlib
from pathlib import Path
from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.supplement import material_review, need_ledger, enhanced


def replay(root, output):
    root=Path(root)
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*.json')}
    rows=[]
    for folder in sorted(root.glob('material-heldout10-case-*')):
        if not folder.is_dir():
            continue
        b=load(folder/'parent/intelligence-bundle.json')
        original=load(folder/'review/input.json')
        newer=material_review.packet(b,need_ledger.build(b),enhanced.plan(b)['sources'])
        row={'id':str(b['request']['id']), 'new_packet_characters':sum(len(p['text']) for p in newer['passages']),
             'new_packet_passages':len(newer['passages']), 'complete_saved_tables':[
                 {'url':p['url'],'start':p['start'],'end':p['end'],'text_length':len(p['text'])}
                 for p in newer['passages'] if p['reading'].get('complete_saved_table')],
             'original_response_rebound_to_new_packet':False}
        records=[load(p) for p in sorted((folder/'review').glob('material-model-*.json'))]
        received=[r for r in records if r.get('status')=='received']
        if not received:
            row['status']='no_saved_received_response'
        else:
            try:
                choice=received[-1]['response']['choices'][0]
                decision=material_review.decode(choice['message'],original,choice.get('finish_reason'))
                result=material_review.bind(b,original,decision)
                ledger=need_ledger.build(b,{'material_reviews':[{'result':result}]})
                row.update(status='replayed', bindings=len(result['bindings']),
                    conflicted_urls=result['conflicted_urls'],rejected_records=result['rejected_records'],
                    needs=[{'id':n['id'],'captured':n['target_material_captured'],
                        'blocked':n['blocked_material_bindings'],'source_requirement':n['source_requirement']}
                           for n in ledger['needs']])
            except (ValueError,KeyError,TypeError) as exc:
                row.update(status='rejected',error=str(exc))
        rows.append(row)
    unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in before.items())
    report={'schema':'material-contract-offline-replay-v1','cases':rows,
        'input_files_unchanged':unchanged,'new_model_calls':0,'new_search_calls':0,'new_fetch_calls':0,
        'budgets_reset':False,'truth_verified':False,
        'limitation':'Deterministic saved-response replay and packet inspection; not a fresh model-quality or recall measurement.'}
    save(Path(output),report)
    return report


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('root');parser.add_argument('output')
    args=parser.parse_args()
    result=replay(args.root,args.output)
    print([(r['id'],r['status'],r.get('bindings')) for r in result['cases']])
    print('Input files unchanged:',result['input_files_unchanged'])
