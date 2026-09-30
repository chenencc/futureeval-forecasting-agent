"""Read-only live adapter checks with one durable ledger and no model/search calls."""
import json
from pathlib import Path
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.evidence.acceptance import collection_acceptance

ROOT = Path('snapshots/acquisition-verification')
REQUEST = {'question': 'Collect live government dataset examples and Anthropic IPO market candidates for acquisition QA.',
           'resolution_criteria': 'Capture source data and contract rules without evaluating any future outcome.', 'mode': 'live'}


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    lock = ROOT / '.running.lock'
    import os
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(descriptor)
    try:
        task = RetrievalTask(ROOT, REQUEST)
        if task.bundle.get('result'):
            report = {'checks': task.bundle.get('verification_checks', []), 'updates': task.bundle.get('updates', []),
                      'acceptance': collection_acceptance(task.bundle), 'budget_remaining': task.budget(),
                      'tavily_calls': len(task.bundle['searches']), 'model_calls': 0}
            (ROOT / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print('Cached verification report:', ROOT / 'report.json')
            return
        if task.bundle['plan'] is None:
            task.execute('plan_evidence', {'needs': [{'id': 'data', 'condition': 'Captured source material',
                         'priority': 'critical', 'expected_source': 'Public provider APIs', 'query': 'Provider QA'}]}, '')
        checks = task.bundle.setdefault('verification_checks', [])
        for dataset in ['bls_cpi', 'bls_unemployment', 'bls_payrolls', 'treasury_debt', 'federal_register']:
            if any(c['name'] == dataset for c in checks):
                continue
            try:
                result = task.execute('collect_official', {'dataset': dataset, 'query': 'artificial intelligence', 'need_ids': ['data']}, '')
                row = {'name': dataset, 'status': 'passed', 'url': result['url'], 'documents': result.get('document_count'),
                       'pagination': result.get('pagination')}
            except Exception as exc:
                row = {'name': dataset, 'status': 'failed', 'error': str(exc)[:200]}
            checks.append(row); task.save(); print(json.dumps(row), flush=True)
        if not any(c['name'] == 'polymarket' for c in checks):
            try:
                result = task.execute('collect_polymarket', {'query': 'Anthropic IPO', 'need_ids': ['data']}, '')
                row = {'name': 'polymarket', 'status': 'passed', 'snapshot_id': result['snapshot_id'],
                       'candidates': len(result['candidates']), 'pagination': result.get('pagination')}
            except Exception as exc:
                row = {'name': 'polymarket', 'status': 'failed', 'error': str(exc)[:200]}
            checks.append(row); task.save(); print(json.dumps(row), flush=True)
        if task.bundle['pages'] and not any(c['name'] == 'incremental' for c in checks):
            url = next(iter(task.bundle['pages']))
            result = task.execute('refresh_sources', {'urls': [url]}, '')
            row = {'name': 'incremental', 'status': 'passed' if result['items'][0]['ok'] else 'failed', 'result': result['items']}
            checks.append(row); task.save(); print(json.dumps(row), flush=True)
        task.execute('finish_collection', {'gaps': [c['name'] + ': ' + c.get('error', 'Read failed') for c in checks if c['status'] == 'failed']}, '')
        report = {'checks': checks, 'updates': task.bundle.get('updates', []), 'acceptance': collection_acceptance(task.bundle), 'budget_remaining': task.budget(),
                  'tavily_calls': len(task.bundle['searches']), 'model_calls': 0}
        (ROOT / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('Saved report:', ROOT / 'report.json')
    finally:
        lock.unlink()


if __name__ == '__main__':
    main()
