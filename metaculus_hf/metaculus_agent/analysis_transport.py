"""Preserve public production failover; persist each physical model attempt."""
from contextlib import contextmanager
import os
from pathlib import Path
from unittest.mock import patch
from pipeline_v2.providers import Budget
from .vendor import provider_failover as native

@contextmanager
def installed(root,run_id,checkpoint,*,opener=None):
    from ForecastAgent.providers import decisions,ultra
    primary=os.environ.get('OPENROUTER_API_KEY','');backup=os.environ.get('OPENROUTER_API_KEY2','')
    if not primary:raise RuntimeError('MISSING_ANALYSIS_MODEL_KEY')
    budget=Budget(Path(root));original=opener or decisions.urlopen
    def physical(req,*a,**kw):
        budget.reserve('model',run_id,provider='openrouter',credential='analysis-transport')
        checkpoint()  # A quota reservation must be remote-durable BEFORE HTTP.
        return original(req,*a,**kw)
    transport=native.ProviderTransport(physical,{'openrouter':(primary,backup)},Path(root)/'analysis-provider-transport')
    write=native._write
    def durable(path,value):write(path,value);checkpoint()
    with patch.object(native,'_write',durable),patch.object(decisions,'urlopen',transport),patch.object(ultra,'urlopen',transport):yield transport
