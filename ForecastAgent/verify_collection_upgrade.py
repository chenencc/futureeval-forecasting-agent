"""Free reader/data smoke checks; no Ultra, Tavily, forecast or budget reset."""
import json
from datetime import datetime,timezone
from pathlib import Path
from ForecastAgent.providers.ultra import fetch_public_page
from ForecastAgent.providers.official import fetch_official
from ForecastAgent.readers.loader import load_response


def verify(root=Path('snapshots/collection-upgrade-smoke')):
    root.mkdir(parents=True,exist_ok=True)
    report={'started_at_utc':datetime.now(timezone.utc).isoformat(),'checks':[],
            'model_requests':0,'tavily_requests':0,'forecast_submissions':0}
    checks=[('html',lambda:fetch_public_page('https://www.federalregister.gov/developers/documentation/api/v1')),
            ('noaa_html',lambda:fetch_public_page('https://www.ncei.noaa.gov/products/optimum-interpolation-sst')),
            ('treasury_range',lambda:fetch_official('treasury_debt','',1,fetch_public_page,
                  start_date='2026-08-19',end_date='2026-08-25'))]
    for name,operation in checks:
        try:
            page=operation()
            (root/(name+'.json')).write_text(json.dumps(page,ensure_ascii=False,indent=2),encoding='utf-8')
            report['checks'].append({'name':name,'status':'captured' if page.get('body_diagnostics',{}).get('usable_text',True) else 'blocked_capture', 'chars':len(page['content']),
                'diagnostics':page.get('body_diagnostics'),'row_count':len(page.get('rows',[])),
                'metadata':{k:v for k,v in page.get('page_date_metadata',{}).items() if k!='tables'},
                'requested_range':page.get('requested_range'),'raw_sha256':page['sha256']})
        except Exception as exc:
            report['checks'].append({'name':name,'status':'unavailable','error':str(exc)[:300]})
    try:
        from io import BytesIO
        from reportlab.platypus import SimpleDocTemplate,Table,TableStyle
        buffer=BytesIO()
        table=Table([['Date','Value'],['2026-08-19','123.4'],['2026-08-20','125.6']])
        table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),1,'black')]))
        SimpleDocTemplate(buffer).build([table])
        page=load_response({'url':'https://example.org/local-fixture.pdf','final_url':'https://example.org/local-fixture.pdf',
            'raw':buffer.getvalue(),'content_type':'application/pdf'},retrieved_at=datetime.now(timezone.utc).isoformat())
        (root/'pdf_fixture.json').write_text(json.dumps(page,indent=2),encoding='utf-8')
        assert '123.4' in page['content'] and page['documents'][0]['metadata']['table_count']>=1
        report['checks'].append({'name':'pdf_fixture','status':'captured','table_count':page['documents'][0]['metadata']['table_count']})
    except Exception as exc:
        report['checks'].append({'name':'pdf_fixture','status':'unavailable','error':str(exc)[:300]})
    report['finished_at_utc']=datetime.now(timezone.utc).isoformat()
    (root/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    print(json.dumps(verify(),ensure_ascii=True))
