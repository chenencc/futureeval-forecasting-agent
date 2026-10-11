"""Runner/browser/process gate with synthetic data and no provider credentials."""
import argparse
import base64
import hashlib
import json
import os
import socket
import sys
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis.pilot import save
from ForecastAgent.releases.guard import execute


def regressions(root):
    modules=['ForecastAgent.tests.test_intelligence_worker','ForecastAgent.tests.test_official_competition',
        'ForecastAgent.tests.test_predictive_intelligence','ForecastAgent.tests.test_intelligence_validation',
        'ForecastAgent.tests.test_release_1_0_5']
    loader=unittest.TestLoader()
    suite=unittest.TestSuite([loader.loadTestsFromNames(modules),
        loader.discover('ForecastAgent/tests',pattern='test_research*.py')])
    with patch.object(socket.socket,'connect',side_effect=AssertionError('Network forbidden in regression gate')), \
         patch.object(socket,'create_connection',side_effect=AssertionError('Network forbidden in regression gate')):
        with (root/'offline-tests.log').open('w',encoding='utf-8') as stream:
            result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    report={'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
            'passed':result.wasSuccessful(),'external_sockets_forbidden':True}
    save(root/'offline-tests.json',report)
    if not result.wasSuccessful():raise ValueError('Offline regression gate failed')
    return report


def process_gate(root):
    child='import time; time.sleep(30)'
    code='import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c",'+repr(child)+']); print(p.pid,flush=True); time.sleep(30)'
    report=execute([sys.executable,'-u','-c',code],root/'process-timeout.log',1)
    if report['status']!='timed_out':raise ValueError('Process timeout did not occur')
    ident=int((root/'process-timeout.log').read_text().strip())
    if os.name!='nt':
        path=Path('/proc')/str(ident)/'stat'
        if path.exists() and path.read_text().split()[2]!='Z':raise ValueError('Child process still executing')
    next_task=execute([sys.executable,'-c','print("next task")'],root/'next-task.log',10)
    if next_task['status']!='completed':raise ValueError('Next task could not run')
    result={'timeout':report,'next_task':next_task,'child_not_executing':os.name!='nt'}
    save(root/'process-gate.json',result)
    return result


def browser_gate(root):
    # Only this fixture seam allows the exact owned loopback server. Runtime
    # public-destination checks are unchanged and remain enabled by default.
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path=='/metric.json':
                body=b'{"period":"2026-10-11","unit":"units","value":24}';kind='application/json'
            else:
                body=('''<html><body><h1>Official fixture dataset</h1><p>'''+
                    'The fixture reports a dated measurement, not a future resolved value. '*8+
                    '''</p><table><caption>Current dataset</caption><tr><th>Period</th><th>Unit</th><th>Value</th></tr>
                    <tr id="target"><td>Loading</td><td></td><td></td></tr></table>
                    <script>fetch('/metric.json').then(r=>r.json()).then(d=>{document.querySelector('#target').innerHTML=
                    '<td>'+d.period+'</td><td>'+d.unit+'</td><td>'+d.value+'</td>';});</script></body></html>''').encode()
                kind='text/html'
            self.send_response(200);self.send_header('Content-Type',kind);self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin='http://127.0.0.1:'+str(server.server_port)
    from ForecastAgent.readers.crawl4ai import render_page
    try:
        result=render_page(origin+'/',retrieved_at=datetime.now(timezone.utc).isoformat(),
            request_limit=8,timeout_ms=20000,wait_for_css='#target',
            _public_check=lambda url:url.startswith(origin+'/'))
    finally:server.shutdown();server.server_close();thread.join(timeout=5)
    save(root/'browser-capture.json',result)
    raw=base64.b64decode(result['raw_response_base64'])
    if hashlib.sha256(raw).hexdigest()!=result['sha256']:raise ValueError('DOM checksum mismatch')
    tables=result['crawl4ai']['structured_tables']
    if not any(any('2026-10-11' in str(row) and '24' in str(row) for row in t['rows']) for t in tables):
        raise ValueError('Dated table row was not rendered and parsed')
    audit=result['browser_audit']
    allowed=sum(r.get('allowed',False) for r in audit['requests'])
    if allowed>8:raise ValueError('Browser dependency cap exceeded')
    report={'dom_hash_verified':True,'dated_table_row_verified':True,'dependency_requests':allowed,
        'dependency_cap':8,'model_calls':0,'provider_searches':0,'submitted':False,
        'fixture_only':True,'public_transport_verified':False}
    save(root/'browser-gate.json',report)
    return report


def run(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    if any(os.environ.get(k) for k in ('OPENROUTER_API_KEY','OPENROUTER','OPENROUTER2',
        'TAVILY_KEY1','TAVILY_KEY2','TAVILY_API_KEY','EXA_API_KEY','EXA_API','EXA_API2','METACULUS_TOKEN')):
        raise ValueError('This offline/fixture runner gate requires an environment without provider credentials')
    report={'platform':sys.platform,'python':sys.version,'linux':sys.platform.startswith('linux'),
            'submitted':False,'production_promoted':False,'outcomes_used':False}
    try:
        report['regressions']=regressions(root)
        report['process']=process_gate(root)
        report['browser']=browser_gate(root)
        report['status']='passed'
    except Exception as exc:
        report.update(status='failed',error_type=type(exc).__name__,error=str(exc))
        raise
    finally:save(root/'runner-gate.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True,type=Path)
    args=parser.parse_args();print(json.dumps(run(args.root),indent=2))
