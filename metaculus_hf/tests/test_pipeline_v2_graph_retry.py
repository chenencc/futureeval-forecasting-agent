"""Graph transient retries are bounded, opt-in, charged and preserve the request."""
import json,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from pipeline_v2.providers import Router,Transport,Budget,retry_delay,provider_error
from pipeline_v2.contracts import PipelineError

class GraphRetryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.requests=[];self.sleeps=[];self.wall=1000.;self.responses=[]
  self.addCleanup(patch.stopall);patch.dict(os.environ,{'OPENROUTER':'fixture-key-a','OPENROUTER2':'fixture-key-b','FORECAST_V2_SHARED_BUDGET_ROOT':'','FORECAST_V2_ONCE_NAMESPACE':''},clear=False).start()
  patch('requests.Session.send',new=lambda session,request,**kw:self.send(request)).start();patch('pipeline_v2.providers.time.sleep',side_effect=self.sleeps.append).start();patch('pipeline_v2.providers.time.time',side_effect=self.wallclock).start()
 def wallclock(self):self.wall+=5;return self.wall
 def send(self,request):
  import requests
  self.requests.append(request);status,value,headers=self.responses.pop(0);r=requests.Response();r.status_code=status;r.url=request.url;r._content=json.dumps(value).encode();r._content_consumed=True;r.headers.update(headers);return r
 def response(self,status=200,error=None,headers=None):
  value=dict(choices=[dict(finish_reason='stop',message=dict(content='{"ok":true}'))],usage=dict(prompt_tokens=13,completion_tokens=4,cost=0)) if status==200 and error is None else dict(error=error or dict(code=status,message='NEVER_SAVE_PROVIDER_RAW_TEXT',metadata=dict(error_type='server',raw='SECRET_RAW_PAYLOAD')))
  self.responses.append((status,value,headers or {}))
 def router(self,*,daily=1500,deadline=None,keys=('OPENROUTER',)):
  t=Transport(Budget(self.root,model_daily=daily),'same-run/official_graph',deadline=deadline);self.progress=[];t.on_progress=lambda usage:self.progress.append(dict(usage));return Router(t,keys)
 def call(self,r,**kwargs):return r.chat('existing graph prompt',dict(snapshot='same evidence'),**kwargs)
