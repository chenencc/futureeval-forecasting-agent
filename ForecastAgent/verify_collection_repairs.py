"""Read-only smoke checks without model or paid search calls."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from ForecastAgent.runtime.retrieval import RetrievalTask


def main():
 report=[]
 with TemporaryDirectory() as directory:
  task=RetrievalTask(Path(directory),{'question':'Read-only adapter smoke check',
   'resolution_criteria':'https://www.sec.gov/Archives/edgar/data/320193/ and https://example.org/',
   'pipeline':'collection','acquisition_profile':'collection_v2','as_of_utc':'2026-03-06T00:00:00Z'})
  task.bundle['plan']=[{'id':'data','priority':'critical'}]
  for args in [
   {'dataset':'binance_daily','start_date':'2026-02-01','end_date':'2026-02-03','need_ids':['data']},
   {'dataset':'north_atlantic_sst','start_date':'2026-02-01','end_date':'2026-03-05','need_ids':['data']},
   {'dataset':'sec_submissions','cik':'320193','forms':['10-K'],'start_date':'2025-10-01','end_date':'2026-03-05','need_ids':['data']}]:
   try:
    result=task.execute('collect_dataset',args,'')
    cached=task.execute('collect_dataset',args,'')
    report.append({'dataset':args['dataset'],'status':'captured','rows':result['total_rows'],
      'sample':result['rows'][:2],'cache_reused':cached['cached'],'warning':result['warning']})
   except Exception as exc:report.append({'dataset':args['dataset'],'status':'unavailable','reason':str(exc)[:250]})
  try:
   result=task.execute('collect_archive',{'url':'https://example.org/'},'')
   report.append({'archive':'https://example.org/','status':'captured','temporal_status':result.get('temporal_status')})
  except Exception as exc:report.append({'archive':'https://example.org/','status':'unavailable','reason':str(exc)[:250]})
  output={'checks':report,'free_http_attempts':len(task.bundle['fetch_attempts']),
    'basic_searches':len(task.bundle['searches']),'model_calls':len(task.bundle.get('model_attempts',[]))}
  Path('snapshots/collection-repairs-smoke.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
  print(json.dumps(output))

if __name__=='__main__':main()
