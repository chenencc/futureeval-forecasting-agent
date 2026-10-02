"""Actual Chromium and XLSX fixture checks on a GitHub-hosted runner."""
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from ForecastAgent.readers.browser import render_page
from ForecastAgent.supplement.stage import parse_spreadsheet, now


def main():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path=='/data':
                content=json.dumps({'value':'Official measurement: 20.31 Celsius. '*12}).encode();kind='application/json'
            else:
                content=b'<html><body><div id="report"></div><script>fetch("/data").then(r=>r.json()).then(d=>document.getElementById("report").textContent=d.value);fetch("/write",{method:"POST"}).catch(()=>{});</script></body></html>';kind='text/html'
            self.send_response(200);self.send_header('Content-Type',kind);self.end_headers();self.wfile.write(content)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);Thread(target=server.serve_forever,daemon=True).start()
    url=f'http://127.0.0.1:{server.server_port}/'
    try:
        page=render_page(url,retrieved_at=now(),_public_check=lambda u:u.startswith(url))
    finally:
        server.shutdown();server.server_close()
    assert '20.31 Celsius' in page['content']
    assert any(r['reason']=='non_read_method' for r in page['browser_audit']['requests'])
    assert sum(r['allowed'] for r in page['browser_audit']['requests'])<=25
    import openpyxl
    book=openpyxl.Workbook();book.active.append(['date','value','unit']);book.active.append(['2026-02-18',20.31,'Celsius'])
    raw=io.BytesIO();book.save(raw)
    spreadsheet=parse_spreadsheet(raw.getvalue(),'https://example.org/data.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    assert '20.31' in spreadsheet['content'] and spreadsheet['documents'][1]['metadata']['row']==2
    from pathlib import Path
    output=Path('snapshots/supplement-ci');output.mkdir(parents=True,exist_ok=True)
    (output/'fixture-report.json').write_text(json.dumps({'chromium_dynamic_fixture':'passed','xlsx_fixture':'passed',
        'browser_request_audit':page['browser_audit'],'scope':'Controlled fixtures, not a claim of live website reliability.'},indent=2))
    print('Chromium dynamic fixture and XLSX reader passed')


if __name__=='__main__':main()
