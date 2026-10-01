"""Resume one selected campaign task; other task ledgers must remain byte-identical."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from ForecastAgent.runtime.retrieval import run_retrieval
from ForecastAgent.runtime.task_lock import task_lock
from ForecastAgent.runtime.temporal_policy import amend_current


def run_one(root, question_id, tavily_key, router_key, current_information=False):
    root=Path(root)
    question_id=str(question_id)
    if not question_id.isdecimal():
        raise ValueError('Use an existing numeric campaign question ID')
    with task_lock(root):
        batch=json.loads((root/'batch.json').read_text(encoding='utf-8'))
        if question_id not in batch['tasks']:
            raise ValueError('Selected question is not in the frozen campaign')
        paths={qid:root/'tasks'/qid/'bundle.json' for qid in batch['tasks']}
        if not all(p.exists() for p in paths.values()):
            raise ValueError('Restore all existing campaign ledgers before debugging one case')
        before={qid:hashlib.sha256(p.read_bytes()).hexdigest() for qid,p in paths.items()}
        selected=json.loads(paths[question_id].read_text(encoding='utf-8'))
        if current_information and amend_current(selected, 'Operator requested current-information acquisition without a historical cutoff'):
            temporary=paths[question_id].with_suffix('.tmp')
            temporary.write_text(json.dumps(selected,ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(paths[question_id])
        result=selected.get('result') or {}
        if result and not result.get('incomplete'):
            raise ValueError('Selected task is closed; no implicit reopening or fresh budget')
        baseline={'question_id':question_id,'prior_bundle_sha256':before[question_id],
            'request_hash':selected['request_hash'], 'limits':selected['acquisition_limits'],
            'searches':selected['searches'],'exa_searches':selected['exa_searches'],
            'model_attempts':selected.get('model_attempts',[]), 'protected_task_sha256':{k:v for k,v in before.items() if k!=question_id}}
        (root/'debug_before.json').write_text(json.dumps(baseline,indent=2),encoding='utf-8')
        try:
            output=run_retrieval(selected['request'],paths[question_id].parent,tavily_key,router_key)
            checks={'request_unchanged':output['request_hash']==baseline['request_hash'],
                'limits_unchanged':output['acquisition_limits']==baseline['limits'],
                'tavily_prefix_unchanged':output['searches'][:len(baseline['searches'])]==baseline['searches'],
                'exa_prefix_unchanged':output['exa_searches'][:len(baseline['exa_searches'])]==baseline['exa_searches'],
                'model_prefix_unchanged':output.get('model_attempts',[])[:len(baseline['model_attempts'])]==baseline['model_attempts']}
            summary={'question_id':question_id, 'resume_not_fresh':True,
                'collection_temporal_policy':output.get('collection_temporal_policy'),
                'temporal_policy_amendments':output.get('temporal_policy_amendments',[]),
                'model_http_increment':len(output.get('model_attempts',[]))-len(baseline['model_attempts']),
                'tavily_increment':len(output['searches'])-len(baseline['searches']),
                'exa_increment':len(output['exa_searches'])-len(baseline['exa_searches']),
                'excerpt_count':len(output['excerpts']), 'parser_repairs':output.get('parser_repairs',[]),
                'result':output.get('result'), 'ledger_checks':checks}
            if not all(checks.values()):
                raise RuntimeError('Single-case resume changed frozen state or a consumed provider prefix')
            (root/'debug_dispatch_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
            return summary
        finally:
            changed=[qid for qid,p in paths.items() if qid!=question_id and hashlib.sha256(p.read_bytes()).hexdigest()!=before[qid]]
            if changed:
                raise RuntimeError('Unselected task ledgers changed: '+', '.join(changed))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--question',required=True)
    parser.add_argument('--current-information',action='store_true',help='Explicitly relax this task cutoff, retain quotas and archive the previous result')
    args=parser.parse_args()
    if not os.environ.get('TAVILY_API_KEY') or not os.environ.get('OPENROUTER_API_KEY'):
        raise ValueError('Configure provider credentials before reserving any task work')
    print(json.dumps(run_one(args.root,args.question,os.environ['TAVILY_API_KEY'],os.environ['OPENROUTER_API_KEY'],args.current_information)))


if __name__=='__main__':
    main()
