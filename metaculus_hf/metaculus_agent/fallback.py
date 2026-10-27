"""Explicit model-prior and operational defaults; never invent evidence."""
from copy import deepcopy
from pathlib import Path
import re
from pipeline_v2.contracts import digest,now,snapshot_payload
from .storage import Conflict,PersistenceError
from ForecastAgent.analysis.distributions import payload,range_metadata

POLICY='metaculus-forecast-fallback/1'

def code(error):
    return getattr(error,'code',None) or (str(error) if re.fullmatch('[A-Z][A-Z0-9_]{1,99}',str(error)) else type(error).__name__)

def integrity_failure(error):
    value=str(code(error)).upper()
    return any(term in value for term in ('HASH','INTEGRITY','IDENTITY','MISMATCH','FROZEN','UNSAFE','TRAVERSAL','INVALID_CAPTURE_PATH','QUESTION_RULES_OR_SCALE_CHANGED'))

def allowed(error):
    if isinstance(error,(Conflict,PersistenceError,AssertionError,MemoryError)):return False
    if isinstance(error,OSError) and getattr(error,'errno',None) is not None:return False
    return not integrity_failure(error)

def neutral(question):
    """Use native payload validation, exact options/grid and explicit open tails."""
    kind=question['question_type']
    if kind=='binary':raw={'probability_yes':.5}
    elif kind=='multiple_choice':
        options=question['options'];raw={'probability_yes_per_category':{x:1/len(options) for x in options}}
    else:
        meta=range_metadata(question);count=meta['inbound_outcome_count']
        below=.02 if meta['open_lower_bound'] else 0.;above=.02 if meta['open_upper_bound'] else 0.
        raw={'continuous_cdf':[below+(1-below-above)*i/count for i in range(count+1)]}
    return payload(question,raw)

def candidate(question,error):
    detail=dict(policy=POLICY,level='operational_default',trigger_code=str(code(error)),
                model_generated=False,evidence_based=False,calibrated=False,
                method='binary-0.5; equal-category-mass; uniform-platform-grid-with-2pct-open-tails')
    value=neutral(question)
    return dict(payload=value,selection='operational_default',forecast_origin='operational_default',fallback=detail,
                release_version=POLICY,model_result_sha256=digest(dict(payload=value,fallback=detail)),automatic=True,
                comment='# ForecastAgent operational fallback\n\nNo validated model forecast was available. '
                        'This is an explicitly uninformative operational default, not an evidence-backed or calibrated prediction. '
                        'Trigger: '+detail['trigger_code']+'\n\n## Resolution rules\n'+question['resolution_criteria']+'\n\n'+question.get('fine_print',''))

def prior_source(value,snapshot):
    return dict(request=deepcopy(value['request']),pages={},gaps=[*snapshot['gaps'],dict(code='NO_USABLE_EVIDENCE',severity='warning')],
                original_reading=dict(mode='question_prior',no_usable_original_evidence=True,
                     excluded_source_ids=[m['source']['source_id'] for m in snapshot['materials']],unverified_source_ids=[]),
                source_snapshot_id=value['snapshot_id'],context_sha256=digest(value),evidence_cutoff_at=value['evidence_cutoff_at'])

def capture_package(task,post,root,error):
    """Keep partial evidence; explicitly assemble an unavailable-capture snapshot."""
    if not allowed(error):raise error
    from pipeline_v2.repository import Repository
    from pipeline_v2.platforms import metaculus
    from pipeline_v2.engine import collection_profile
    data=Path(root)/'capture';repo=Repository(data);qid='metaculus:'+task['id'];path,_=repo.paths(qid)
    if path.exists():record=repo.get_question(qid)
    else:
        adapted=deepcopy(post)
        if adapted['question']['type']=='discrete':adapted['question']['type']='numeric'
        item=next(x for x in metaculus(adapted) if x['question_id']==qid);version=digest(dict(question_id=qid,definition=item['definition']))
        record=dict(question=dict(id=qid,current_version_id=version,versions={version:dict(observed_at=now(),definition=item['definition'])}),
                    searches=[],evidence=dict(sources=[],texts={},snapshots=[]),runs=[])
    package=dict(schema='intelligence-package/1',question_id=qid,definitions=deepcopy(record['question']['versions']),
                 searches=deepcopy(record['searches']),evidence=deepcopy(record['evidence']))
    run=next((r for r in record['runs'] if r['run_id']==task.get('capture_run_id')),None)
    sid=run.get('snapshot_id') if run else None
    if not sid:
        at=now();snap=dict(snapshot_id='',question_version_id=run['question_version_id'] if run else record['question']['current_version_id'],
            acquired_by_run_id=task.get('capture_run_id') or task['run_id'],sealed_at=at,evidence_cutoff_at=at,temporal_policy='prospective',
            materials=[dict(source_id=s['source_id'],inclusion='gap_only',source_role='unresolved',coverage_note='CAPTURE_FAILED_NO_ADMISSION') for s in package['evidence']['sources']],
            context=dict(capture_status='failed',operational_fallback=True),gaps=[dict(code=str(code(error)),severity='warning')])
        sid=digest(snapshot_payload(record,snap));snap['snapshot_id']=sid;package['evidence']['snapshots'].append(snap)
    task['snapshot_id']=sid;task['collection_failure']=dict(code=str(code(error)),policy=POLICY,capture_succeeded=False,at=now())
    task.setdefault('execution_profile',collection_profile())
    task['execution_profile']['parameters'].update(orderbook=False,submit=False,trade=False)
    return package
