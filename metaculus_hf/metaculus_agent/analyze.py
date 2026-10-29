"""Saved original evidence into validated Mercury typed decisions."""
import json,os
from pathlib import Path
from pipeline_v2.contracts import digest,now
from .products import bundle,verify_context
from .analysis_policy import decision_route,AnalysisProviderError
from .storage import Conflict,PersistenceError
from . import fallback

def analyze(value,root,checkpoint):
    from ForecastAgent.analysis.pilot import save
    from . import decision_analysis,decision_model
    from .analysis_transport import installed
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    snapshot=verify_context(value)  # Integrity is never a reason to invent a forecast.
    if value['question_id']!='metaculus:'+str(value['request']['id']):raise ValueError('ANALYSIS_QUESTION_IDENTITY')
    fallback.neutral(value['request'])  # Validate native options/scales before HTTP.
    try:source=bundle(value)
    except Exception as exc:
        if fallback.code(exc)!='NO_USABLE_EVIDENCE':raise
        source=fallback.prior_source(value,snapshot)
    path=root/'input.json'
    if path.exists() and json.loads(path.read_text())!=source:raise RuntimeError('FROZEN_ANALYSIS_INPUT_CHANGED')
    save(path,source);checkpoint()
    started=now();route=None
    budget_root=Path(os.environ.get('FORECAST_V2_SHARED_BUDGET_ROOT',root/'budgets'))
    transport=None
    try:
        route=decision_route();save(root/'route.json',route);checkpoint()
        with installed(budget_root,'analysis-'+value['run_id'],checkpoint) as transport:
            candidate=decision_analysis.run(source,root/'decision',route,checkpoint)
    except (Conflict,PersistenceError):raise
    except Exception as exc:
        events=transport.analysis_events if transport else []
        last=events[-1] if events else {}
        fault=exc if isinstance(exc,AnalysisProviderError) else AnalysisProviderError(last['code'],category=last.get('category','provider'),retry_after=last.get('retry_after',300),status=last.get('http_status')) if last.get('code') and last.get('http_status')!=200 else None
        if not fallback.allowed(exc):raise
        failure=fault or exc
        save(root/'forecast-fallback.json',dict(policy=fallback.POLICY,code=str(fallback.code(failure)),category=getattr(failure,'category',None),
            retry_after=getattr(failure,'retry_after',None),http_status=getattr(failure,'http_status',None),events=events));checkpoint()
        candidate=fallback.candidate(value['request'],failure)
        candidate['fallback'].update(error_category=getattr(failure,'category',None),http_status=getattr(failure,'http_status',None),
                                     operator_action_required=getattr(failure,'category',None)=='configuration')
    records={p.relative_to(root).as_posix():json.loads(p.read_text()) for p in root.rglob('*.json') if p.name!='input.json'}
    result=dict(schema='metaculus-analysis/1',question_id=value['question_id'],run_id=value['run_id'],snapshot_id=value['snapshot_id'],
        context_sha256=digest(value),analysis_input_sha256=digest(source),analysis_release=fallback.POLICY if candidate.get('forecast_origin')=='operational_default' else decision_analysis.PROTOCOL,algorithm_source_release='751145e604406278d6d39d75135668bf30a93b78',
        research_model=None,collection_model='nvidia/nemotron-3.5-super-vl:free',decision_model=decision_model.MODEL,decision_model_attempted=bool(transport.analysis_events) if transport else False,analysis_route=route,provider_events=transport.analysis_events if transport else [],adapter='shared-snapshot-to-mercury/1',original_reading=source['original_reading'],
        forecast_origin=candidate.get('forecast_origin','original_evidence'),fallback=candidate.get('fallback'),collection_failure=value.get('collection_failure'),
        started_at=started,finished_at=now(),candidate=candidate,records=records)
    checkpoint();return result
