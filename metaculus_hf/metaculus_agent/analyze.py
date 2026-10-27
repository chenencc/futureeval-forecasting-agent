"""Saved original evidence into validated Mammoth typed decisions."""
import json,os
from pathlib import Path
from pipeline_v2.contracts import digest,now
from .products import bundle,verify_context
from .analysis_policy import decision_route,AnalysisProviderError
from .storage import Conflict,PersistenceError

def analyze(value,root,checkpoint):
    from ForecastAgent.analysis.pilot import save
    from . import decision_analysis,decision_model
    from .analysis_transport import installed
    root=Path(root);root.mkdir(parents=True,exist_ok=True);source=bundle(value)
    path=root/'input.json'
    if path.exists() and json.loads(path.read_text())!=source:raise RuntimeError('FROZEN_ANALYSIS_INPUT_CHANGED')
    save(path,source);checkpoint()
    started=now();route=decision_route();save(root/'route.json',route);checkpoint()
    budget_root=Path(os.environ.get('FORECAST_V2_SHARED_BUDGET_ROOT',root/'budgets'))
    transport=None
    try:
        with installed(budget_root,'analysis-'+value['run_id'],checkpoint) as transport:
            candidate=decision_analysis.run(source,root/'mammoth',route,checkpoint)
    except (Conflict,PersistenceError):raise
    except Exception as exc:
        events=transport.analysis_events if transport else []
        last=events[-1] if events else {}
        fault=exc if isinstance(exc,AnalysisProviderError) else AnalysisProviderError(last['code'],category=last.get('category','provider'),retry_after=last.get('retry_after',300),status=last.get('http_status')) if last.get('code') and last.get('http_status')!=200 else None
        if fault:
            save(root/'provider-failure.json',dict(code=fault.code,category=fault.category,retry_after=fault.retry_after,http_status=fault.http_status,events=events));checkpoint();raise fault from None
        raise
    records={p.relative_to(root).as_posix():json.loads(p.read_text()) for p in root.rglob('*.json') if p.name!='input.json'}
    result=dict(schema='metaculus-analysis/1',question_id=value['question_id'],run_id=value['run_id'],snapshot_id=value['snapshot_id'],
        context_sha256=digest(value),analysis_input_sha256=digest(source),analysis_release=decision_analysis.PROTOCOL,algorithm_source_release='751145e604406278d6d39d75135668bf30a93b78',
        research_model=None,collection_model=route['collection_model'],decision_model=decision_model.MODEL,decision_model_attempted=bool(transport.analysis_events),analysis_route=route,provider_events=transport.analysis_events,adapter='shared-snapshot-to-mammoth/1',original_reading=source['original_reading'],
        started_at=started,finished_at=now(),candidate=candidate,records=records)
    checkpoint();return result
