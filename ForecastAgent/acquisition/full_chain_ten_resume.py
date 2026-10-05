"""Preserve collected budget-censored captures through the full-chain pilot.

This adapter advances a qualified raw collection to the existing supplement
stage. It never rewrites its source bundle, semantic gaps or provider counters.
The original ten-case scoring protocol and implementation remain unchanged.
"""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath

from ForecastAgent.acquisition import full_chain_ten as original
from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.local_sync import GitHub

AMENDMENT = original.ROOT/'ForecastAgent/experiments/full_chain_ten_resume_protocol.json'
ELIGIBLE_STOPS = {'program_dispatch_limit','model_dispatch_budget','stalled',
                  'program_forced_close','repeated_tool_errors','review_round_limit'}


def amendment():
    p,c=original.protocol()
    a=load(AMENDMENT)
    if original.scoring.text_sha(original.PROTOCOL)!=a['original_protocol_sha256_lf']:
        raise ValueError('Original frozen scoring protocol changed')
    if original.scoring.text_sha(__file__)!=a['adapter_sha256_lf']:
        raise ValueError('Budget-censored handoff adapter changed')
    return a,p,c


def restore(output, qid, run_id, client=None):
    a,p,c=amendment()
    if str(run_id)!=str(a['exact_parent_run_id']) or qid not in p['question_ids']:
        raise ValueError('Restoration must use the registered exact parent and question')
    client=client or GitHub()
    repo=a['repo']; run=client.api(f'repos/{repo}/actions/runs/{run_id}')
    if run['status']!='completed' or run['head_sha']!=a['exact_parent_commit'] or run['run_attempt']!=1:
        raise ValueError('Parent worker is active or has an unregistered identity')
    folder=Path(output)/qid;folder.mkdir(parents=True,exist_ok=True)
    artifact_name='intelligent-full-chain-ten-'+qid
    meta=client.api(f'repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100')
    matches=[v for v in meta['artifacts'] if v['name']==artifact_name]
    if len(matches)>1:raise ValueError('Ambiguous exact parent artifact')
    if matches:
        artifact=matches[0]
        if artifact['expired']:raise ValueError('Exact parent artifact expired')
        target=folder/'restored-parent.zip'
        client.download(repo,artifact['id'],target)
        with zipfile.ZipFile(target) as saved:
            if sum(v.file_size for v in saved.infolist())>400_000_000:
                raise ValueError('Parent archive exceeds restoration bound')
            for item in saved.infolist():
                path=PurePosixPath(item.filename.replace('\\','/'))
                if path.is_absolute() or '..' in path.parts or ':' in item.filename or (item.external_attr>>16)&0o170000==0o120000:
                    raise ValueError('Unsafe parent archive member')
            saved.extractall(folder)
        receipt={'source_run_id':run_id,'artifact_id':artifact['id'],
                 'archive_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                 'status':'exact_state_restored','budget_reset':False}
    else:
        jobs=client.api(f'repos/{repo}/actions/runs/{run_id}/jobs?per_page=100')['jobs']
        matches=[j for j in jobs if j['name']=='full_chain_ten ('+qid+')']
        if len(matches)!=1:raise ValueError('Missing exact parent job evidence')
        work=[s for s in matches[0]['steps'] if s['name']=='Fresh acquisition, independent supplement, first and reread scores']
        if any(s['conclusion']!='skipped' for s in work):
            raise ValueError('A collection may have started without a recoverable artifact; refuse empty-ledger restart')
        receipt={'source_run_id':run_id,'job_id':matches[0]['id'],
                 'status':'parent_collection_never_executed','budget_reset':False}
    save(folder/'restore-receipt.json',receipt)
    return receipt


def eligible(bundle):
    r=bundle.get('result') or {}
    return (r.get('status')=='collected' and r.get('incomplete') is True and
            r.get('termination_reason') in ELIGIBLE_STOPS and
            r.get('execution_report',{}).get('interrupted') is False and
            any(p.get('content') and p.get('body_diagnostics',{}).get('usable_text') for p in bundle.get('pages',{}).values()))


def bridge(output, qid):
    a,p,c=amendment()
    folder=Path(output)/qid;directory=folder/'acquisition'
    sp=directory/'state.json';bp=directory/'collection/bundle.json'
    if not sp.exists() or not bp.exists():return False
    state=load(sp)
    if state.get('stage')!='collection':return False
    raw=bp.read_bytes();bundle=json.loads(raw)
    if not eligible(bundle):return False
    request=next(r for r in c['requests'] if r['id']==qid)
    frozen=original.pipeline.identity(request,True)
    if load(directory/'identity.json')!=frozen or state.get('identity_sha256')!=digest(frozen) or bundle['request']!=frozen['request']:
        raise ValueError('Original collected input or code identity changed')
    # Preserve all original errors/gaps and reservations, even though the
    # collected raw snapshot may now be consumed by a downstream diagnostic.
    record={'schema':'budget-censored-collected-handoff-v1','original_state':state,
            'bundle_file_sha256':hashlib.sha256(raw).hexdigest(),
            'original_result':bundle['result'],'amendment_sha256_lf':original.scoring.text_sha(AMENDMENT),
            'provider_counters':{k:len(bundle.get(k,[])) for k in ('model_attempts','searches','exa_searches','fetch_attempts','extract_attempts')},
            'model_calls_added':0,'search_calls_added':0,'budget_reset':False,
            'semantic_complete':False,'submitted':False}
    save(folder/'budget-censored-handoff.json',record)
    archive=directory/'parent.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as saved:
        saved.writestr('campaign.json',json.dumps({'input_sha256':state['identity_sha256'],
            'tasks':{qid:{'status':'closed_with_gaps'}}}))
        for source in sorted((directory/'collection').rglob('*')):
            if source.is_file():
                saved.write(source,'tasks/'+qid+'/'+source.relative_to(directory/'collection').as_posix())
    with zipfile.ZipFile(archive) as saved:
        if saved.read('tasks/'+qid+'/bundle.json')!=raw:raise ValueError('Original parent capture changed')
    state.update(stage='supplement',parent_bundle_sha256=hashlib.sha256(raw).hexdigest(),
                 parent_archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                 raw_budget_censored_handoff=True,interrupted=False)
    save(sp,state)
    return True


def run(output,qid):
    a,p,c=amendment()
    if qid not in p['question_ids']:raise ValueError('Unknown case')
    request=next(r for r in c['requests'] if r['id']==qid)
    folder=Path(output)/qid
    adapted=bridge(output,qid)
    if adapted:
        original.pipeline.run(request,folder/'acquisition',supplement_network=True)
    result=original.run_case(output,qid)
    if result.get('stage')=='acquisition' and bridge(output,qid):
        save(folder/'case-result-before-handoff.json',result)
        original.pipeline.run(request,folder/'acquisition',supplement_network=True)
        result=original.run_case(output,qid)
    if (folder/'budget-censored-handoff.json').exists():
        before=load(folder/'budget-censored-handoff.json')
        bp=folder/'acquisition/collection/bundle.json'
        if hashlib.sha256(bp.read_bytes()).hexdigest()!=before['bundle_file_sha256']:
            raise ValueError('Downstream processing changed original collected state')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--question-id',required=True)
    parser.add_argument('--restore-run')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    amendment()
    if args.restore_run:
        print(json.dumps(restore(args.output,args.question_id,args.restore_run)))
    elif args.dry_run:
        print(json.dumps(original.run_case(args.output,args.question_id,dry_run=True)))
    else:
        result=run(args.output,args.question_id)
        print(json.dumps({k:result.get(k) for k in ('question_id','stage','error')}))
        if result.get('stage')!='complete' or any(v.get('probability_yes') is None for v in result['routes'].values()):
            raise RuntimeError('Incomplete preserved case; no implicit retry or budget reset')


if __name__=='__main__':main()
