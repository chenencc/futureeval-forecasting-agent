"""Deterministic collection inspection, independent repair and immutable analysis handoff."""
import argparse
import copy
import hashlib
import json
import zipfile
from pathlib import Path
from ForecastAgent.analysis.pilot import digest,load,save
from ForecastAgent.evidence.source_checks import inspect_body
from ForecastAgent.runtime.task_lock import task_lock

PROTOCOL='checked-collection-handoff-v1'
LEDGERS=('searches','exa_searches','fetch_attempts','extract_attempts','model_attempts','update_attempts','sessions')


def inspect(bundle):
    """Check original bodies without interpreting event truth or time eligibility."""
    pages={}; seen={}; duplicates=[]; gaps=[]
    for url,page in bundle.get('pages',{}).items():
        body=page.get('content',''); check=inspect_body(bundle['request'],url,body)
        sha=hashlib.sha256(body.encode()).hexdigest()
        row={'url':url,'body_sha256':sha,'chars':len(body),'diagnostics':check}
        if not check['eligible_for_evidence']:
            gaps.append({'url':url,'reason':check['reason'],'stage':'collection_inspection'})
        elif sha in seen:
            row['duplicate_of']=seen[sha]
            duplicates.append({'url':url,'representative_url':seen[sha],'body_sha256':sha})
        else:seen[sha]=url
        pages[url]=row
    return {'schema':'collection-inspection-v1','protocol':PROTOCOL,'parent_bundle_sha256':digest(bundle),
        'pages':pages,'duplicates':duplicates,'gaps':gaps,
        'ledger_sha256':{key:digest(bundle.get(key,[])) for key in LEDGERS},
        'no_provider_calls':True,'truth_verified':False,'time_eligibility_verified':False}


def repair_input(bundle,inspection):
    """Keep every physical reservation and body; attach observable repair diagnostics."""
    if inspection['parent_bundle_sha256']!=digest(bundle):raise ValueError('Inspection parent changed')
    view=copy.deepcopy(bundle)
    for url,row in inspection['pages'].items():
        view['pages'][url]['body_diagnostics']=row['diagnostics']['body']
    view['program_inspection']=inspection
    assert all(digest(view.get(k,[]))==inspection['ledger_sha256'][k] for k in LEDGERS)
    return view


def analysis_view(bundle,raw_parent_sha256):
    """Expose one copy of exact bodies with aliases; preserve excluded originals in audit."""
    inspection=inspect(bundle); view=copy.deepcopy(bundle)
    groups={}; excluded={}; aliases={}
    for url,row in inspection['pages'].items():
        if not row['diagnostics']['eligible_for_evidence']:
            excluded[url]=view['pages'].pop(url)
        elif row.get('duplicate_of'):
            excluded[url]=view['pages'].pop(url)
            aliases.setdefault(row['duplicate_of'],[]).append({'url':url,'body_sha256':row['body_sha256'],
                'retrieved_at_utc':excluded[url].get('retrieved_at_utc')})
        else:
            view['pages'][url]['body_diagnostics']=row['diagnostics']['body']
            groups[url]=row['body_sha256']
    view['handoff_excluded_pages']=excluded
    view['source_aliases']=aliases
    gaps=list(view.get('gaps',[]))+list(view.get('result',{}).get('gaps',[]))
    gaps+=['Collection inspection: '+g['reason']+' at '+g['url']+'; original preserved.' for g in inspection['gaps']]
    gaps+=['Independent supplement unresolved: '+g.get('category','unknown')+' at '+g['url']
           for g in view.get('supplement_lineage',{}).get('remaining_gaps',[])]
    view['gaps']=list(dict.fromkeys(gaps))
    view['collection_handoff']={'protocol':PROTOCOL,'raw_parent_bundle_sha256':raw_parent_sha256,
        'inspection':inspection,'source_aliases':aliases,'unique_body_count':len(groups),
        'provider_calls':0,'budget_reset':False,'missing_evidence_is_not_negative_evidence':True}
    return view


def prepare(bundle,folder,*,network=False,repair=None):
    """Resume under the same parent and repair budgets; never start collection or inference."""
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    ident=str(bundle['request']['id'])
    if not ident.isdecimal():raise ValueError('Numeric question identity required')
    identity={'protocol':PROTOCOL,'parent_bundle_sha256':digest(bundle),'id':ident,'network':network,
        'implementation_sha256':digest([Path(__file__).read_text(encoding='utf8'),
            (Path(__file__).parent/'source_checks.py').read_text(encoding='utf8')]),
        'repair_caps':{'http':2,'browser':2,'saved_reparse':8},'budget_reset':False}
    with task_lock(folder):
        manifest=folder/'manifest.json'
        if manifest.exists() and load(manifest)!=identity:raise ValueError('Frozen handoff changed; do not restart repair allowances')
        save(manifest,identity)
        final=folder/'analysis-input.json';report=folder/'report.json'
        if final.exists():
            if not report.exists() or load(report)['analysis_input_sha256']!=digest(load(final)):
                raise ValueError('Incomplete or changed handoff output')
            return load(final)
        save(folder/'raw-bundle.json',bundle)
        inspection=inspect(bundle);save(folder/'inspection-before-repair.json',inspection)
        view=repair_input(bundle,inspection);save(folder/'repair-input.json',view)
        expected_request=digest(view['request'])
        expected_ledgers={key:digest(view.get(key,[])) for key in LEDGERS}
        if repair is not None:
            overlay=repair(copy.deepcopy(view),folder/'repair',ident)
        else:
            from ForecastAgent.supplement.stage import run,analysis_overlay
            archive=folder/'repair-parent.zip'
            with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
                z.writestr('campaign.json',json.dumps({'tasks':{ident:{'status':'acquired'}}}))
                z.writestr(f'tasks/{ident}/bundle.json',json.dumps(view))
            run(archive,folder/'repair',[ident],network=network)
            overlay=analysis_overlay(view,folder/'repair',ident)
            overlay['supplement_lineage']['remaining_gaps']=load(folder/'repair/tasks'/ident/'supplement.json')['remaining_gaps']
        if digest(overlay.get('request'))!=expected_request or any(digest(overlay.get(k,[]))!=expected_ledgers[k] for k in LEDGERS):
            raise ValueError('Independent repair changed original question or acquisition ledgers')
        result=analysis_view(overlay,identity['parent_bundle_sha256'])
        save(folder/'inspection-after-repair.json',result['collection_handoff']['inspection'])
        save(report,{'protocol':PROTOCOL,'raw_parent_bundle_sha256':identity['parent_bundle_sha256'],
            'analysis_input_sha256':digest(result),'raw_page_count':len(bundle.get('pages',{})),
            'analysis_page_count':len(result['pages']),'excluded_page_count':len(result['handoff_excluded_pages']),
            'duplicate_count':len(result['collection_handoff']['inspection']['duplicates']),
            'gaps':result['gaps'],'collection_ledgers_unchanged':True,'models_called_by_handoff':0,
            'analysis_or_submission_performed':False})
        save(final,result)
        return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--bundle',required=True);p.add_argument('--output',required=True)
    p.add_argument('--network',action='store_true');a=p.parse_args()
    prepare(load(Path(a.bundle)),Path(a.output),network=a.network)
    print(json.dumps(load(Path(a.output)/'report.json'),indent=2))
