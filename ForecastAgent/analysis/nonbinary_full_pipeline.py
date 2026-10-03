"""Run the original acquisition agent, independent repair, and Mercury rereading."""
import argparse
import hashlib
import json
import os
import zipfile
from pathlib import Path

from ForecastAgent.agent import run_research
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.analysis import mercury_nonbinary_trial as mercury
from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.providers.tavily_search import canonical_url
from ForecastAgent.supplement.stage import run as supplement, analysis_overlay


def collection_request(row):
    request = mercury.request(row)
    request.update(pipeline='collection', mode='live', acquisition_profile='collection_v3',
                   acquisition_focus='raw_recall', exa_search_policy='required')
    request['background'] = 'Official source leads:\n' + '\n'.join(
        url for url in row.get('source_links', []) if mercury.public_source(url))
    return request


def bridge(row, seed, folder):
    """Import consumed physical reservations, preserving every original seed file."""
    request = collection_request(row)
    seed = Path(seed); folder = Path(folder)
    identity = {'source_run':37027378998, 'input_sha256':digest(row),
                'seed_files':{str(p.relative_to(seed)):hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in sorted(seed.rglob('*')) if p.is_file()},
                'tavily_lifetime_cap':3, 'free_fetch_lifetime_cap':8, 'budget_reset':False}
    audit = folder/'seed-import.json'
    if audit.exists():
        if load(audit) != identity:
            raise ValueError('Seed identity changed')
        if not (folder/'bundle.json').exists():
            raise ValueError('Seed import interrupted; inspect preserved state')
        return request
    if (folder/'bundle.json').exists():
        raise ValueError('Unidentified existing acquisition state')
    task = RetrievalTask(folder, request)
    original = load(seed/'bundle.json')
    if original['request'] != mercury.request(row):
        raise ValueError('Seed question identity mismatch')
    journals = sorted((seed/'search-http').glob('*.json'))
    if len(journals) != 1:
        raise ValueError('Expected exactly one preserved basic search reservation')
    journal = load(journals[0]); batch = load(seed/'search.json') if (seed/'search.json').exists() else {}
    task.bundle['searches'] = [{'query':journal['query'], 'depth':'basic', 'need_ids':[],
        'reason':'Imported prior physical search; preserve lifetime allowance.',
        'search_role':'primary', 'status':'completed' if journal['status']=='received' else 'failed',
        'results':batch.get('results',[]), 'raw_response':batch,
        'attempted_at':batch.get('searched_at'), 'seed_record_sha256':digest(journal)}]
    for path in sorted((seed/'captures').glob('*.json')):
        capture = load(path); url = capture['url']; page = capture.get('page', {})
        readable = bool(page.get('content') and page.get('body_diagnostics', {}).get('usable_text'))
        task.bundle['fetch_attempts'].append({'url':url, 'status':'completed' if readable else 'failed',
            'detail':capture.get('error','Unreadable original body') if not readable else '',
            'seed_record_sha256':digest(capture), 'need_ids':[], 'at':page.get('retrieved_at_utc')})
        task.bundle['source_leads'][canonical_url(url)] = {'url':url,'origin':'preserved_seed_capture'}
        if readable:
            task.store_page(url, page)
        else:
            task.bundle.setdefault('failed_captures',[]).append({'url':url,'page':page})
    if len(task.bundle['fetch_attempts']) > 8:
        raise ValueError('Imported fetch reservations exceed original lifetime ceiling')
    task.bundle['seed_lineage'] = identity
    task.save(); save(audit, identity)
    return request


def run(output, seed, batch):
    output = Path(output); seed = Path(seed)
    rows = load(mercury.INPUT); selected = rows[(batch-1)*5:batch*5]
    if len(rows)!=20 or len(selected)!=5:
        raise ValueError('Frozen twenty required')
    identity = {'protocol':'nonbinary-full-pipeline-v1','inputs_sha256':digest(rows),
                'post_ids':[r['post_id'] for r in selected], 'seed_run':37027378998}
    if (output/'selection.json').exists() and load(output/'selection.json')!=identity:
        raise ValueError('Selection changed')
    save(output/'selection.json',identity); results=[]
    for row in selected:
        ident=str(row['post_id']); folder=output/'tasks'/ident
        stage='acquisition'
        try:
            request=bridge(row,seed/'tasks'/ident/'acquisition',folder/'acquisition')
            execution_path=folder/'executions.json'
            executions=load(execution_path) if execution_path.exists() else []
            while True:
                bundle=load(folder/'acquisition/bundle.json')
                if bundle.get('result') and not bundle['result'].get('incomplete'):
                    break
                if len(executions)>=3:
                    raise RuntimeError('Three collection executions consumed; preserved incomplete state')
                executions.append({'status':'reserved'}); save(execution_path,executions)
                try:
                    bundle=run_research(request,folder/'acquisition')
                    executions[-1].update(status='returned',session_state=bundle.get('session_state'))
                except Exception as exc:
                    executions[-1].update(status='failed',error=str(exc)); raise
                finally:
                    save(execution_path,executions)
            stage='supplement'
            archive=folder/'parent.zip'
            with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as saved:
                saved.writestr('campaign.json',json.dumps({'input_sha256':digest(request),
                    'tasks':{ident:{'status':'acquired' if bundle.get('acquisition_complete') else 'closed_with_gaps'}}}))
                for path in sorted((folder/'acquisition').rglob('*')):
                    if path.is_file():saved.write(path,f'tasks/{ident}/'+str(path.relative_to(folder/'acquisition')).replace('\\','/'))
            if os.environ.get('FORECAST_CHECKED_HANDOFF')=='1':
                from ForecastAgent.evidence.collection_handoff import prepare
                overlay=prepare(bundle,folder/'checked-handoff',network=True)
                summary=load(folder/'checked-handoff/repair/summary.json')['tasks']
            else:
                summary=supplement(archive,folder/'supplement',[ident],network=True)
                overlay=analysis_overlay(bundle,folder/'supplement',ident)
                overlay['gaps']=bundle.get('result',{}).get('gaps',[])
            save(folder/'analysis-input.json',overlay)
            stage='mercury'
            result=mercury.analyze(row,overlay,folder/'analysis')
            result.update(collection_session_state=bundle.get('session_state'),
                          collection_executions=len(executions),supplement=summary,
                          tavily_lifetime_attempts=len(bundle['searches']),exa_lifetime_attempts=len(bundle['exa_searches']))
            save(folder/'pipeline-result.json',result)
        except Exception as exc:
            result={'post_id':row['post_id'],'type':row['type'],'status':'failed','stage':stage,'error':str(exc)}
            save(folder/'failure.json',result)
        results.append(result);save(output/'report.json',{'rows':results,'no_evaluation_labels_loaded':True,'no_forecasts_submitted':True})
    if any(r['status']=='failed' for r in results):raise RuntimeError('Pipeline failures preserved; no automatic quota reset')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    parser.add_argument('--seed',required=True);parser.add_argument('--batch',type=int,choices=[1,2,3,4],required=True)
    args=parser.parse_args();run(args.output,args.seed,args.batch)
