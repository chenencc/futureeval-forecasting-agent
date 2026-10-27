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
    root=Path(root);root.mkdir(parents=True,exist_ok=True);source=bundle(value)
    path=root/'input.json';save(path,source);checkpoint()
    original=Journal.__call__
    def persist(self,*a,**kw):
        result=original(self,*a,**kw);checkpoint();return result
    started=now()
    with patch.object(Journal,'__call__',persist):candidate=existing(path,root/'official',value['question_id'].split(':')[1])
    records={p.relative_to(root).as_posix():json.loads(p.read_text()) for p in root.rglob('*.json') if p.name!='input.json'}
    result=dict(schema='metaculus-analysis/1',question_id=value['question_id'],run_id=value['run_id'],snapshot_id=value['snapshot_id'],
        context_sha256=digest(value),analysis_input_sha256=digest(source),analysis_release='751145e604406278d6d39d75135668bf30a93b78',
        research_model=model.configured_model(),decision_model='inception/mercury-decide:free',adapter='shared-snapshot-to-official/1',
        started_at=started,finished_at=now(),candidate=candidate,records=records)
    checkpoint();return result
