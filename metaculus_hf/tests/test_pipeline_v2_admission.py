"""Evidence admission, no rejected-body leakage, and usable positive controls."""
from copy import deepcopy
import json,tempfile,unittest
from pathlib import Path
from pipeline_v2.admission import assess,rejection,admitted_view,POLICY
from pipeline_v2.engine import Engine,profile,find_run
from pipeline_v2.repository import Repository
from pipeline_v2.contracts import canonical,PipelineError,now
from pipeline_v2.context import delivery

TEXT='Official Target issuer revenue for Q3 FY2027 was USD 2600 million. Information as of 2026-10-20.\n| Issuer | Revenue USD millions |\n| Target | 2600 |\n| Competitor | 2500 |'
QUOTE='Target issuer revenue for Q3 FY2027 was USD 2600 million.'
CUT='2026-10-21T14:00:00Z'

def source(sid='s1',url='https://authority.example/primary'):
 return dict(source_id=sid,source_url=url,final_url=None,status='extracted')
def proposal(**values):
 out=dict(content_kind='factual_report',source_id='s1',source_role='current_official',inclusion='core',applicable_period='Q3 FY2027; observed baseline as of 2026-10-20',coverage_note='Exact target issuer, revenue metric and declared period.',relevance=dict(entity='match',metric='match',period='match',quote=QUOTE,as_of_date='2026-10-20'));out.update(values);return out

