"""Real browser proof on deterministic HTTP fixtures, suitable for Linux CI."""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from contextlib import ExitStack
from unittest.mock import patch

from ForecastAgent.readers.browser import render_page as legacy
from ForecastAgent.readers.crawl4ai import render_page as candidate
from ForecastAgent.acquisition.nextgen import metrics
from ForecastAgent.supplement.stage import save


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        code = 200
        kind = 'text/html; charset=utf-8'
        if self.path == '/dynamic':
            html = '<html><body><nav>'+('Navigation menu content. '*25)+'</nav><main id="result">Loading...</main><script>setTimeout(() => {document.querySelector("main").innerHTML = "<article data-loaded=true>Official October release: the policy rate is 4.5%. The rate applies from October 8, 2026.</article>";}, 1200);</script></body></html>'
        elif self.path == '/challenge':
            html = '<html><body>Just a moment. Verify you are human. Enable JavaScript and cookies.</body></html>'
        elif self.path == '/denied':
            code = 403
            html = '<html><body>'+('The endpoint denied this request. '*20)+'</body></html>'
        elif self.path == '/table':
            rows = ''.join(f'<tr><td>MONTH-{i}</td><td>{i}.25</td></tr>' for i in range(200))
            html = '<html><body><h1>Statistical release</h1><table><thead><tr><th>Month</th><th>Value</th></tr></thead><tbody>'+rows+'</tbody></table></body></html>'
        elif self.path == '/slow':
            html = '<html><body><article><h1>Official notice</h1><p>The board retained the 4.5% rate.</p></article><script src="/slow-asset"></script></body></html>'
        elif self.path == '/slow-asset':
            threading.Event().wait(6)
            html = ''
        elif self.path == '/embedded':
            html = '<html><body><h1>Official surveillance</h1><p>Data is presented in the embedded dashboard.</p><iframe title="Observed data" src="/response-data"></iframe></body></html>'
        elif self.path in {'/response-data', '/data-error'}:
            endpoint = '/observations.json' if self.path == '/response-data' else '/error.json'
            html = '<html><body><h1>Official observations chart</h1><p>Values are provided by the page data service.</p><canvas></canvas><script>fetch("'+endpoint+'").then(r=>r.json()).then(d=>{window.chartData=d;});</script></body></html>'
        elif self.path in {'/observations.json', '/error.json'}:
            kind = 'application/json; charset=utf-8'
            html = json.dumps({'error': {'message': 'No observations for this date'}} if self.path == '/error.json' else {
                'metadata': {'station': 'DEMO-17', 'datum': 'MLLW', 'units': 'feet', 'time_zone': 'UTC'},
                'data': [{'time': '2026-10-08T00:00:00Z', 'value': '6.12'}, {'time': '2026-10-08T00:06:00Z', 'value': '5.90'}]})
        else:
            code = 404
            html = '<html><body>Missing fixture.</body></html>'
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.end_headers()
        try:
            self.wfile.write(html.encode())
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('snapshots/crawl4ai-ci-proof'))
    parser.add_argument('--browser-channel', choices=['chromium', 'chrome', 'msedge'], default='chromium')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    prefix = f'http://127.0.0.1:{server.server_port}'
    # This private-address exception is local to the fixture harness, never CLI.
    allowed = lambda url: url.startswith(prefix+'/')
    captured = []
    patches = ExitStack()
    if args.browser_channel != 'chromium':
        # Select the same installed browser for both local test arms. Production
        # and the Linux acceptance workflow retain bundled Chromium by default.
        from playwright.sync_api import BrowserType
        original_launch = BrowserType.launch
        def launch(browser_type, **kwargs):
            return original_launch(browser_type, **dict(kwargs, channel=args.browser_channel))
        patches.enter_context(patch.object(BrowserType, 'launch', launch))
    try:
        for path in ('/dynamic', '/table', '/challenge', '/denied', '/slow', '/response-data', '/embedded', '/data-error'):
            stamp = datetime.now(timezone.utc).isoformat()
            snapshot = candidate(prefix+path, retrieved_at=stamp, _public_check=allowed,
                wait_for_css='[data-loaded]' if path == '/dynamic' else None,
                timeout_ms=3000 if path == '/slow' else 15000,
                browser_channel=args.browser_channel)
            save(args.output/(path[1:]+'-candidate.json'), snapshot)
            row = {'fixture': path, 'candidate': metrics(snapshot)}
            if path in {'/dynamic', '/table'}:
                parent = legacy(prefix+path, retrieved_at=stamp, _public_check=allowed, timeout_ms=15000)
                save(args.output/(path[1:]+'-release.json'), parent)
                row['release'] = metrics(parent)
            if path == '/dynamic':
                assert '4.5%' in snapshot['content'], 'Dynamic content was missed'
                row['candidate_target_found'] = '4.5%' in snapshot['content']
                row['release_target_found'] = '4.5%' in parent['content']
            elif path == '/table':
                assert snapshot['crawl4ai']['structured_tables'][0]['rows'][-1][0] == 'MONTH-199'
                assert len(snapshot['crawl4ai']['structured_tables'][0]['rows']) == 200
                row['legacy_table_rows'] = len(parent['page_date_metadata']['tables'][0]['text'].splitlines())
            elif path == '/slow':
                assert snapshot['render_state'] == 'deadline_partial_dom'
                assert '4.5%' in snapshot['content']
                assert not snapshot['capture_status']['render_complete']
                assert snapshot['render_gaps']
            elif path in {'/response-data', '/embedded', '/data-error'}:
                records = snapshot['data_response_capture']['records']
                payloads = [r['snapshot'] for r in records if r.get('snapshot')]
                assert len(payloads) == 1, 'The browser data response was not archived'
                payload = payloads[0]
                if path == '/data-error':
                    assert payload['structured_data']['state'] == 'remote_error'
                    assert not payload['capture_status']['usable_text']
                else:
                    assert payload['structured_data']['row_count'] == 2
                    assert payload['structured_data']['reported_metadata']['$.metadata']['station'] == 'DEMO-17'
                    assert payload['sha256'] != snapshot['sha256'], 'DOM and data bodies need separate hashes'
                row['data_response_state'] = payload['structured_data']['state']
                row['data_rows'] = payload['structured_data'].get('row_count', 0)
                assert snapshot['data_response_capture']['network_calls_added'] == 0
            else:
                assert not snapshot['body_diagnostics']['usable_text'], 'A failure became readable evidence'
            assert snapshot['browser_audit']['allowed_requests'] <= 25
            captured.append(row)
        report = {'schema': 'crawl4ai_browser_proof_v1', 'cases': captured,
                  'browser_channel': args.browser_channel,
                  'provider_calls': {'models': 0, 'tavily': 0, 'exa': 0},
                  'scope': 'Deterministic browser fixture proof, not live-site or forecast-quality validation.'}
        # Exercise the registered release executor with a real browser, not a
        # mocked capture. Only this local harness permits its loopback fixture.
        from ForecastAgent.runtime.retrieval import RetrievalTask
        from ForecastAgent.runtime.source_reading import POLICY
        from ForecastAgent.runtime.contracts import validate
        from ForecastAgent.runtime.tool_selection import active_tools
        from ForecastAgent.runtime.source_reading import TOOLS
        request={'id':'900000','question':'Read the fixture observations',
            'resolution_criteria':prefix+'/response-data','mode':'live',
            'pipeline':'collection','acquisition_profile':'collection_v3',
            'source_reading_policy':POLICY}
        task=RetrievalTask(args.output/'release-tool-task',request)
        task.bundle['plan']=[{'id':'n','priority':'critical','condition':'Observation rows',
                             'query':'Observation rows','expected_source':'Fixture'}]
        task.bundle['pages'][prefix+'/response-data']=json.loads((args.output/'response-data-candidate.json').read_text())
        task.save()
        def fixture_reader(url, **kwargs):
            return candidate(url,**{**kwargs,'_public_check':allowed,'browser_channel':args.browser_channel})
        tool_args={'url':prefix+'/response-data','need_ids':['n'],'reason':'Read chart response rows missing from the thin wrapper'}
        validate(task,'render_source',tool_args,active_tools(task,TOOLS))
        with patch('ForecastAgent.readers.crawl4ai.render_page',side_effect=fixture_reader):
            task.execute('render_source',tool_args,'')
            resumed=RetrievalTask(task.directory,request)
            cached=resumed.execute('render_source',tool_args,'')
        rows=resumed.execute('inspect_source_structure',{'url':prefix+'/observations.json','view':'data'},'')
        assert rows['total']==2
        assert cached['no_network']
        assert len(resumed.bundle['fetch_attempts'])==1
        assert resumed.budget()['page_fetch_remaining']==7
        assert resumed.bundle['fetch_attempts'][0]['backend']=='crawl4ai'
        report['registered_release_tools']={'real_browser':True,'structured_rows':rows['total'],
            'shared_fetch_attempts':1,'duplicate_after_resume_http_calls':0,'source_bound':True}
        save(args.output/'report.json', report)
        print(json.dumps(report, indent=2))
    finally:
        patches.close()
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
