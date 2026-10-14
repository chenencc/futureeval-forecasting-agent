"""Bind shared capabilities to native competition reservations and frozen requests."""
import copy
import hashlib
import json
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch
from ForecastAgent.acquisition_shared import identity
from ForecastAgent.acquisition_shared.contract import (body_contract,action_link,saved_source,sheet_tab_leads,sheet_export,
                       materialize_csv,materialize_sheet,export_access_denied,failure_details)
from ForecastAgent.acquisition_shared.tool_output import compact_view


def project(page,url):
    return materialize_sheet(materialize_csv(page,url),url)


@contextmanager
def adapter():
    from ForecastAgent.acquisition import eager_capture as eager
    from ForecastAgent.runtime.retrieval import RetrievalTask
    from ForecastAgent.supplement import stage
    from ForecastAgent.tavily_research import canonical_url
    from ForecastAgent.retrieval_sources import allowed_source
    old_identity,old_diagnostic,old_candidates=eager.identity,eager.diagnostic,eager.candidates
    execute_method='_execute' if hasattr(RetrievalTask,'_execute') else 'execute'
    old_execute,old_view=getattr(RetrievalTask,execute_method),RetrievalTask.page_view
    old_inventory,old_fetch=stage.inventory,stage.fetch_document

    def capture_identity():
        return {**old_identity(),'shared_acquisition':identity(),
            'shared_host_adapter_sha256_lf':hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n',b'\n')).hexdigest()}
    def diagnostic(page,url):
        return body_contract(page,url,old_diagnostic(page,url))
    def enabled(task):
        return task.bundle.get('request',{}).get(eager.FIELD,{}).get('shared_acquisition')==identity()
    def candidates(task):
        for url,page in list(task.bundle.get('pages',{}).items()):
            parent=task.bundle.get('source_leads',{}).get(canonical_url(url),{})
            if parent.get('depth',0)>=1 or parent.get('origin')=='observed_sheet_tab':continue
            for lead in sheet_tab_leads(page,page.get('url') or url):
                lead['depth']=1
                if allowed_source(lead['url']):task.bundle.setdefault('source_leads',{}).setdefault(canonical_url(lead['url']),lead)
        return [r for r in old_candidates(task) if not action_link(r['url']) and
            saved_source(task.bundle,r['url'])[0] is None]
    def execute(task,name,args,key):
        if not enabled(task):return old_execute(task,name,args,key)
        if name!='fetch_page':return old_execute(task,name,args,key)
        url=args.get('url','')
        if action_link(url):raise ValueError('Sharing control is not an original source; select an observed source page')
        original,binding=saved_source(task.bundle,url)
        if original and binding['source_url']!=url and not task.cutoff:
            task.bundle.setdefault('shared_source_reuse',[]).append({'requested_url':url,**binding,'new_http_requests':0})
            task.save()
            return compact_view(task.page_view(original,args.get('start_char',0)))
        before=len(task.bundle.get('fetch_attempts',[]))
        try:
            value=old_execute(task,name,args,key)
        except Exception as exc:
            if len(task.bundle.get('fetch_attempts',[]))>before:
                task.bundle['fetch_attempts'][-1].update(failure_details(exc));task.save()
            raise
        canonical=canonical_url(url)
        if canonical in task.bundle.get('pages',{}):
            page=task.bundle['pages'][canonical];derived=project(page,url)
            if derived!=page:task.store_page(url,derived)
            task.bundle['pages'][canonical]['body_diagnostics']=diagnostic(task.bundle['pages'][canonical],url)
            task.save()
            value=task.page_view(task.bundle['pages'][canonical],args.get('start_char',0))
        return compact_view(value)
    def view(task,page,*args,**kwargs):
        value=old_view(task,page,*args,**kwargs)
        return compact_view(value) if enabled(task) else value
    def inventory(archive):
        result=old_inventory(archive)
        from zipfile import ZipFile
        with ZipFile(archive) as source:
            for name in source.namelist():
                if not name.startswith('tasks/') or not name.endswith('/bundle.json'):continue
                bundle=json.loads(source.read(name));ident=name.split('/')[1]
                policy=bundle.get('request',{}).get(eager.FIELD,{})
                if policy.get('shared_acquisition')!=identity():continue
                rows=result['failed_fetches'];known={r['url'] for r in rows if r['task_id']==ident}
                for row in rows:
                    if row['task_id']!=ident:continue
                    page,binding=saved_source(bundle,row['url'])
                    if page and diagnostic(page,row['url'])['usable_text']:
                        row.update(category='already_recovered',shared_source_binding=binding)
                    elif action_link(row['url']):row['category']='sharing_action'
                    elif export_access_denied(row['url'],bundle.get('fetch_attempts',[])):
                        row['category']='authentication_required'
                for url,page in bundle.get('pages',{}).items():
                    if bundle.get('source_leads',{}).get(canonical_url(url),{}).get('origin')=='observed_sheet_tab':continue
                    for lead in sheet_tab_leads(page,page.get('url') or url,limit=2):
                        if lead['url'] in known:continue
                        known.add(lead['url']);rows.append({'task_id':ident,'url':lead['url'],
                            'category':'unattempted_observed_source','kind':'unattempted_lead','origin':'observed_sheet_tab',
                            'priority':65,'domain':__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(lead['url']).hostname,
                            'shared_sheet_binding':lead})
        return result
    def fetch(url,*args,**kwargs):
        return project(old_fetch(url,*args,**kwargs),url)
    with patch.object(eager,'identity',capture_identity),patch.object(eager,'diagnostic',diagnostic),\
         patch.object(eager,'candidates',candidates),patch.object(RetrievalTask,execute_method,execute),\
         patch.object(RetrievalTask,'page_view',view),patch.object(stage,'inventory',inventory),patch.object(stage,'fetch_document',fetch):
        yield


def acquisition_call(fn):
    from functools import wraps
    @wraps(fn)
    def wrapped(*args,**kwargs):
        with adapter():return fn(*args,**kwargs)
    return wrapped
