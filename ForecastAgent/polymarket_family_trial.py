"""Frozen-response family-pool replay with optional bounded direction ranking."""
import argparse,hashlib,json
from pathlib import Path
from ForecastAgent.polymarket_pool import build_pool,annotate_direction
from ForecastAgent.polymarket_rank_trial import rank


def run(inputs,replay,output,rank_ids=(),model='nvidia/nemotron-3-super-120b-a12b:free'):
    inputs=Path(inputs);replay=Path(replay);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    baseline=json.loads((inputs/'report.json').read_text(encoding='utf-8'))
    frozen=[];case_inputs=[]
    for case in baseline['cases']:
        ident=case['task_id'];paths=sorted((inputs/'responses').glob(ident+'-*.json'))+sorted(replay.glob('**/'+ident+'-extra-search.json'))
        if len(paths)!=len(list((inputs/'responses').glob(ident+'-*.json')))+1:raise ValueError('Missing or duplicate replay response')
        payloads=[]
        for p in paths:
            raw=p.read_bytes();frozen.append({'file':p.name,'sha256':hashlib.sha256(raw).hexdigest()});d=json.loads(raw)
            if d.get('payload'):payloads.append(d['payload'])
        case_inputs.append((case,payloads))
    identity={'schema':'family_pool_replay_v2','sources':frozen,'model':model,'rank_ids':list(rank_ids),'model_http_cap':len(rank_ids),'public_api_cap':0,'market_cap':120,'family_cap':60}
    manifest=output/'manifest.json'
    if manifest.exists() and json.loads(manifest.read_text())!=identity:raise ValueError('Frozen experiment changed')
    manifest.write_text(json.dumps(identity,indent=2))
    result={'schema':'family_pool_replay_report_v2','cases':[],'public_http_attempts':0,'tavily_calls':0,'exa_calls':0,'forecast_submissions':False}
    for case,payloads in case_inputs:
        ident=case['task_id'];built=build_pool(payloads,case['question']);rows=built['rows']
        (output/f'{ident}-family-pool.json').write_text(json.dumps(built,indent=2,ensure_ascii=False),encoding='utf-8')
        old=[]
        for p in replay.glob('**/report.json'):
            d=json.loads(p.read_text(encoding='utf-8'))
            old.extend(c for c in d.get('cases',[]) if c.get('task_id')==ident)
        old=old[0] if len(old)==1 else {}
        old_ids={r['market_id'] for r in old.get('ranked',[])}
        ranking={'status':'offline_only','picks':[]}
        if ident in rank_ids and rows:
            ranking=rank({'title':case['question'],'rules':case.get('resolution_criteria'),
                'required_direction_schema':{'direction':'same|inverse|partial|unknown','target_proposition':'Copy the exact QUESTION title verbatim, including Will and question mark','market_yes_proposition':'exact contract YES meaning'},
                'direction_instructions':'For every selected object include direction, target_proposition and market_yes_proposition. Same means same proposition orientation, inverse means mutually exclusive complementary proposition; partial means overlapping but non-complementary. A removal contract is not automatically the exact complement of participation: eligibility, removal deadline, withdrawal and participation conditions must coincide. When incomplete use partial or unknown. Never convert probabilities. Top-tier requires same direction and all exact rule conditions.'},rows,output/f'{ident}-rank.json',model)
        picked=[annotate_direction(p,rows[p['i']],target_question=case['question']) for p in ranking.get('picks',[])]
        result['cases'].append({'task_id':ident,'question':case['question'],'old_pool_size':old.get('pool_size'),'coverage':built['coverage'],
            'old_selected_count':len(old_ids),'old_selected_retained_in_new_pool':len(old_ids & {r['market_id'] for r in rows}),
            'ranking_status':ranking['status'],'ranked':picked,'usage':ranking.get('response',{}).get('usage')})
        (output/'report.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    result['summary']={'questions':len(result['cases']),'families_represented':sum(c['coverage']['families_represented'] for c in result['cases']),
        'old_selected_total':sum(c['old_selected_count'] for c in result['cases']),
        'old_selected_retained':sum(c['old_selected_retained_in_new_pool'] for c in result['cases']),
        'incomplete_pools':sum(not c['coverage']['pool_complete'] for c in result['cases']),
        'ranked_calls':sum(c['ranking_status']=='ranked' for c in result['cases']),
        'failed_calls':sum(c['ranking_status']=='failed' for c in result['cases'])}
    (output/'report.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps(result['summary']))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--inputs',type=Path,required=True);p.add_argument('--replay',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--rank-ids',default='');a=p.parse_args();run(a.inputs,a.replay,a.output,tuple(x for x in a.rank_ids.split(',') if x))
