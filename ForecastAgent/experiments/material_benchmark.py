"""Frozen, paired material acceptance evaluation with masked review packets."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil

HERE=Path(__file__).resolve()
REPO=HERE.parents[2]


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def fixture(case):
    raw=(REPO/case['fixture']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=case['sha256']:raise ValueError('Frozen regression fixture changed')
    return json.loads(gzip.decompress(raw))


def worker(repo,mode,input_path,output):
    sys.path.insert(0,str(Path(repo).resolve()))
    from ForecastAgent.supplement import material_review,need_ledger,binding_guard
    if mode=='regression':
        item=read(input_path);b=item['bundle'];rows={};failures=[]
        for index,r in enumerate(item['responses']):
            payload=json.loads(next(m['content'] for m in r['request']['messages'] if m['role']=='user'))
            choice=r['response']['choices'][0]
            try:
                result=material_review.bind(b,payload,material_review.decode(choice['message'],payload,choice.get('finish_reason')))
                for bound in result['bindings']:
                    passage=next(p for p in payload['passages'] if p['url']==bound['url'] and p['text']==bound['quote'])
                    need=next(n for n in payload['needs'] if n['id']==bound['need_id'])
                    eligible=bound['closure_guard']['eligible_for_material_closure'] and all(bound['axes'].get(a) is True for a in binding_guard.required_axes(need))
                    rows[f"{index}:{bound['need_id']}:{passage['passage_id']}"]={'eligible':eligible,'guard':bound['closure_guard']}
            except ValueError as exc:failures.append({'response_index':index,'error':str(exc)})
        write(output,{'rows':rows,'failures':failures})
        return
    item=read(input_path);b=item['assessment'];payload=item['payload'];folder=Path(output).parent
    # Fresh assessment allowance is separate from immutable acquisition ledgers.
    state={}
    try:
        agent=material_review.callback(os.environ['OPENROUTER_API_KEY'],b,max_reviews=1,review_retry_seconds=180)
        result=material_review.bind(b,payload,agent(payload,state,folder))
        state['material_reviews'][-1].update(status='bound',result=result)
        ledger=need_ledger.build(b,state)
        value={'status':'completed','needs':ledger['needs'],'result':result}
    except Exception as exc:value={'status':'failed','error':str(exc),'needs':[]}
    write(folder/'state.json',state);write(output,value)


def invoke(repo,mode,input_path,output):
    env=dict(os.environ);env['PYTHONPATH']=str(Path(repo).resolve());env['PYTHONIOENCODING']='utf-8'
    subprocess.run([sys.executable,str(HERE),'worker','--repo',str(Path(repo).resolve()),
        '--mode',mode,'--input',str(Path(input_path).resolve()),'--output',str(Path(output).resolve())],
        cwd=repo,env=env,check=True,timeout=300)


def regression(baseline,output,manifest_path=None):
    output=Path(output);manifest=read(Path(manifest_path) if manifest_path else HERE.with_name('MATERIAL_REGRESSION5.json'));cases=[]
    for case in manifest['cases']:
        item=fixture(case);folder=output/case['id'];write(folder/'input.json',item)
        for name,repo in [('baseline',baseline),('candidate',REPO)]:invoke(repo,'regression',folder/'input.json',folder/(name+'.json'))
        arms={name:read(folder/(name+'.json')) for name in ['baseline','candidate']};checks=[]
        for label in item['labels']:
            key=f"{label['response_index']}:{label['need_id']}:{label['passage_id']}"
            if label.get('check')=='issue':
                observed={name:(label['issue'] in arms[name]['rows'][key]['guard']['issues'])
                          if key in arms[name]['rows'] else None for name in arms}
                expected=label['expected'];new_rejection=False
            else:
                observed={name:arms[name]['rows'].get(key,{}).get('eligible',False) for name in arms}
                expected=label['expected_eligible']
                new_rejection=expected and observed['baseline'] and not observed['candidate']
            checks.append({**label,'observed':observed,'expected':expected,'candidate_correct':observed['candidate']==expected,
                           'new_false_rejection':new_rejection})
        cases.append({'id':case['id'],'checks':checks,'failures':{name:arms[name]['failures'] for name in arms}})
    checks=[x for c in cases for x in c['checks']]
    report={'schema':'paired-regression-v1','cases':cases,'labeled_witnesses':len(checks),
        'new_false_rejections':sum(x['new_false_rejection'] for x in checks),
        'candidate_correct':sum(x['candidate_correct'] for x in checks),
        'baseline_correct':sum(x['observed']['baseline']==x['expected'] for x in checks),
        'gate_passed':bool(checks) and all(x['candidate_correct'] for x in checks) and not any(x['new_false_rejection'] for x in checks),
        'new_provider_calls':0,'scope':'Development regression witnesses, not independent held-out accuracy.'}
    write(output/'report.json',report);print({k:v for k,v in report.items() if k!='cases'})
    return report


def live(case,baseline,output):
    from ForecastAgent.experiments import solid_collection
    from ForecastAgent.supplement import material_review,need_ledger,enhanced
    from ForecastAgent.runtime.capacity import DEFAULT
    output=Path(output)
    if output.exists():raise ValueError('Existing experiment requires explicit preserved-state continuation; no duplicate calls')
    output.mkdir(parents=True)
    manifest=read(HERE.with_name('MATERIAL_BENCHMARK10.json'))
    selected=manifest['cases'][case-1]
    if not read(REPO/'snapshots/benchmark-regression/report.json')['gate_passed']:raise ValueError('Regression gate failed')
    solid_collection.run(case,output/'collection',os.environ['GITHUB_RUN_ID'],cohort='benchmark10')
    b=read(output/'collection/intelligence-bundle.json')
    if str(b['request']['id'])!=selected['id']:raise ValueError('Frozen case identity differs')
    payload=material_review.packet(b,need_ledger.build(b),enhanced.plan(b)['sources'])
    assessment={'request':b['request'],'plan':b['plan'],'pages':b['pages'],
        'capacity':{**DEFAULT,'model_decisions':1,'model_http_dispatch':3,'model_http_lifetime':3,'model_failures':3},
        'assessment_parent_sha256':hashlib.sha256((output/'collection/intelligence-bundle.json').read_bytes()).hexdigest(),
        'assessment_budget_scope':'Additional isolated evaluation allowance; original acquisition ledger remains intact.'}
    write(output/'shared-input.json',{'assessment':assessment,'payload':payload})
    write(output/'checklist.json',selected)
    write(output/'blind/corpus.json',{'question':payload['question'],'checklist':selected['required_materials'],
        'pages':{u:{'content':p.get('content',''),'body_sha256':hashlib.sha256(p.get('content','').encode()).hexdigest()}
                 for u,p in b['pages'].items()}, 'same_delivered_passages':payload['passages']})
    complete_pair(selected,manifest,baseline,output,resume=False)


def resume(case,baseline,output,parent_run):
    """Finish missing arms using frozen inputs; never repeat an attempted arm."""
    output=Path(output);manifest=read(HERE.with_name('MATERIAL_BENCHMARK10.json'))
    selected=manifest['cases'][case-1]
    shared=read(output/'shared-input.json')
    raw=(output/'collection/intelligence-bundle.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=shared['assessment']['assessment_parent_sha256']:
        raise ValueError('Acquisition bundle changed')
    if str(json.loads(raw)['request']['id'])!=selected['id'] or read(output/'checklist.json')!=selected:
        raise ValueError('Frozen case identity or checklist changed')
    corpus=read(output/'blind/corpus.json')
    if shared['payload']['passages']!=corpus['same_delivered_passages']:
        raise ValueError('Frozen passage coverage changed')
    for url,page in shared['assessment']['pages'].items():
        if hashlib.sha256(page.get('content','').encode()).hexdigest()!=corpus['pages'][url]['body_sha256']:
            raise ValueError('Frozen body changed')
    before={str(p.relative_to(output)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (output/'collection').rglob('*') if p.is_file()}
    complete_pair(selected,manifest,baseline,output,resume=True)
    after={str(p.relative_to(output)):hashlib.sha256(p.read_bytes()).hexdigest()
           for p in (output/'collection').rglob('*') if p.is_file()}
    if before!=after:raise ValueError('Acquisition journals changed during review')
    write(output/'continuation.json',{'parent_run':parent_run,'case':case,
        'acquisition_files_unchanged':True,'acquisition_file_hashes':before,
        'shared_input_sha256':hashlib.sha256((output/'shared-input.json').read_bytes()).hexdigest(),
        'quota_reset':False,'repeated_attempted_arms':False})


def compare_saved(case,baseline,parent,output,parent_run):
    """New authorized paired assessment; acquisition and earlier arms stay immutable."""
    parent=Path(parent);output=Path(output)
    if output.exists():raise ValueError('New assessment output already exists; no budget restart')
    manifest=read(HERE.with_name('MATERIAL_BENCHMARK10.json'));selected=manifest['cases'][case-1]
    shared=read(parent/'shared-input.json')
    if read(parent/'checklist.json')!=selected:raise ValueError('Frozen checklist changed')
    if hashlib.sha256((parent/'collection/intelligence-bundle.json').read_bytes()).hexdigest()!=shared['assessment']['assessment_parent_sha256']:
        raise ValueError('Frozen acquisition changed')
    for path in ('collection','blind/corpus.json','shared-input.json','checklist.json'):
        source=parent/path;target=output/path
        if source.is_dir():shutil.copytree(source,target)
        else:
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    complete_pair(selected,manifest,baseline,output,resume=False)
    write(output/'round-provenance.json',{'parent_run':parent_run,'round':'role-stage-direction-v2',
        'new_authorized_assessment_allowance':{'logical_decisions_per_arm':1,'physical_requests_per_arm':3},
        'acquisition_reset':False,'prior_assessment_records_erased':False,
        'shared_input_identical_to_parent':hashlib.sha256((output/'shared-input.json').read_bytes()).hexdigest()==hashlib.sha256((parent/'shared-input.json').read_bytes()).hexdigest()})


def complete_pair(selected,manifest,baseline,output,resume):
    """Export masked packets after retaining each existing terminal result."""
    payload=read(output/'shared-input.json')['payload']
    swap=int(hashlib.sha256((manifest['order_seed']+selected['id']).encode()).hexdigest(),16)%2
    arms=[('baseline',baseline),('candidate',REPO)]
    if swap:arms.reverse()
    mapping={}
    for index,(name,repo) in enumerate(arms):
        label='R'+str(index+1);mapping[label]=name
        result_path=output/'private'/name/'result.json'
        if result_path.exists():
            if not resume:raise ValueError('Existing arm cannot be restarted')
        else:
            folder=result_path.parent
            if folder.exists() and any(folder.iterdir()):
                raise ValueError('Nonterminal arm journals require explicit recovery, not a new allowance')
            invoke(repo,'live',output/'shared-input.json',result_path)
        result=read(result_path)
        write(output/'blind'/(label+'.json'),{'question':payload['question'],'checklist':selected['required_materials'],
            'status':result['status'],'needs':[{k:n.get(k) for k in ['id','condition','target_material_captured','material_bindings','blocked_material_bindings']} for n in result['needs']]})
    write(output/'private/arm-map.json',mapping)
    if (output/'review-form.json').exists():return
    write(output/'review-form.json',{'id':selected['id'],'labels':{r:{'core_material_coverage':None,'false_closures':None,'false_rejections':None,'notes':None,
        'checklist_rows':[{'requirement':n,'captured':None,'applicable':None,'witness_url':None,'witness_quote':None,'body_sha256':None} for n in selected['required_materials']]} for r in mapping},
        'promotion_allowed':False,'review_scope':'Unlabeled outcomes remain pending. These arms share the exact same body spans; acquisition was performed once.'})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['regression','live','resume','compare-saved','worker']);p.add_argument('--baseline');p.add_argument('--output',required=True);p.add_argument('--parent-run');p.add_argument('--parent')
    p.add_argument('--case',type=int);p.add_argument('--repo');p.add_argument('--mode');p.add_argument('--input');p.add_argument('--manifest');a=p.parse_args()
    if a.command=='worker':worker(a.repo,a.mode,a.input,a.output)
    elif a.command=='regression':regression(a.baseline,a.output,a.manifest)
    elif a.command=='resume':resume(a.case,a.baseline,a.output,a.parent_run)
    elif a.command=='compare-saved':compare_saved(a.case,a.baseline,a.parent,a.output,a.parent_run)
    else:live(a.case,a.baseline,a.output)
