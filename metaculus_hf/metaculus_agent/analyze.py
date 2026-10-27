"""Pinned existing official Mercury analysis and authorized reasoning fallback."""
import json,os
from pathlib import Path
from unittest.mock import patch
from pipeline_v2.contracts import digest,now
from .products import bundle,verify_context

def analyze(value,root,checkpoint):
    from ForecastAgent.competition.live import analyze as existing
    from ForecastAgent.analysis.pilot import Journal,save
    from ForecastAgent.providers import model
    from ForecastAgent.analysis import referenced
    from .analysis_transport import installed
    root=Path(root);root.mkdir(parents=True,exist_ok=True);source=bundle(value)
    path=root/'input.json'
    if path.exists() and json.loads(path.read_text())!=source:raise RuntimeError('FROZEN_ANALYSIS_INPUT_CHANGED')
    save(path,source);checkpoint()
    original=Journal.__call__
    def persist(self,*a,**kw):
        result=original(self,*a,**kw);checkpoint();return result
    native_packet=referenced.evidence_packet
    def evidence_packet(bundle):
        packet=native_packet(bundle)
        # The existing fallback also sees the shared independent-reading warning.
        packet.setdefault('collection_inspection',{})['original_reading']=bundle['original_reading']
        return packet
    started=now()
    budget_root=Path(os.environ.get('FORECAST_V2_SHARED_BUDGET_ROOT',root/'budgets'))
    with patch.object(Journal,'__call__',persist),patch.object(referenced,'evidence_packet',evidence_packet),installed(budget_root,'analysis-'+value['run_id'],checkpoint):
        candidate=existing(path,root/'official',value['question_id'].split(':')[1])
    records={p.relative_to(root).as_posix():json.loads(p.read_text()) for p in root.rglob('*.json') if p.name!='input.json'}
    result=dict(schema='metaculus-analysis/1',question_id=value['question_id'],run_id=value['run_id'],snapshot_id=value['snapshot_id'],
        context_sha256=digest(value),analysis_input_sha256=digest(source),analysis_release='751145e604406278d6d39d75135668bf30a93b78',
        research_model=model.configured_model(),decision_model='inception/mercury-decide:free',adapter='shared-snapshot-to-official/2',original_reading=source['original_reading'],
        started_at=started,finished_at=now(),candidate=candidate,records=records)
    checkpoint();return result
