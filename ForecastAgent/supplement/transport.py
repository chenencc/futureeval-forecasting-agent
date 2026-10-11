"""One bounded supplemental transport; persist attempts and never retry silently."""
import argparse
import json
import sys
from pathlib import Path

from ForecastAgent.runtime.filesystem import io_path


class CaptureFailure(RuntimeError):
    def __init__(self, message, audit):
        super().__init__(message)
        self.audit = audit


def capture(method, url, directory, *, timeout_seconds=60, browser_profile=None, _command=None):
    from ForecastAgent.providers.ultra import public_url
    from ForecastAgent.releases.guard import execute
    from ForecastAgent.supplement.stage import save, now
    if method not in {'http', 'browser'} or not public_url(url):
        raise ValueError('One declared public supplemental transport is required')
    if not 0 < timeout_seconds <= 120:
        raise ValueError('Supplement wall deadline must be between 0 and 120 seconds')
    profile = browser_profile or {'backend': 'playwright', 'query': ''}
    if (set(profile) != {'backend', 'query'} or profile['backend'] not in {'playwright', 'crawl4ai'}
            or not isinstance(profile['query'], str)):
        raise ValueError('Unsupported supplemental browser profile')
    directory = io_path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    request_path = directory / 'request.json'
    if request_path.exists():
        raise CaptureFailure('Supplement operation was already attempted; preserve its receipt',
                             {'status': 'already_attempted', 'retry_issued': False})
    request = {'method': method, 'url': url, 'started_at_utc': now(),
               'timeout_seconds': timeout_seconds, 'retrieved_at_utc': now()}
    if method == 'browser':
        request['browser_profile'] = profile
    save(request_path, request)
    command = _command or [sys.executable, '-X', 'utf8', '-m', __name__,
                           '--request', str(request_path), '--output', str(directory / 'result.json')]
    try:
        receipt = execute(command, directory / 'process.log', timeout_seconds,
                          cwd=Path(__file__).resolve().parents[2])
    except Exception as exc:
        audit = {'status': 'process_boundary_error', 'error': type(exc).__name__,
                 'remote_attempt_unknown': True, 'retry_issued': False}
        save(directory / 'boundary-error.json', audit)
        raise CaptureFailure('Supplement process boundary failed', audit) from exc
    save(directory / 'process-receipt.json', receipt)
    result_path = directory / 'result.json'
    if receipt['status'] != 'completed' or not result_path.exists():
        raise CaptureFailure('Supplement operation ' + receipt['status'],
            {**receipt, 'remote_attempt_unknown': True, 'retry_issued': False})
    result = json.loads(result_path.read_text(encoding='utf-8'))
    if result.get('request') != request:
        raise CaptureFailure('Supplement response identity mismatch',
                             {**receipt, 'response_identity_verified': False})
    if not result.get('ok'):
        raise CaptureFailure('Supplement transport failed: ' + result.get('error', 'unknown'),
                             {**receipt, 'transport_error': result, 'retry_issued': False})
    page = result['page']
    page['supplement_transport_boundary'] = receipt
    return page


def worker(request_path, output):
    from ForecastAgent.supplement.stage import fetch_document, save
    from ForecastAgent.readers.browser import render_page
    request = json.loads(io_path(request_path).read_text(encoding='utf-8'))
    try:
        if request['method'] == 'http':
            page = fetch_document(request['url'])
        elif request['browser_profile']['backend'] == 'crawl4ai':
            from ForecastAgent.acquisition.nextgen import supplement_backend
            from ForecastAgent.supplement import stage
            with supplement_backend(request['browser_profile']['query']):
                page = stage.render_page(request['url'], retrieved_at=request['retrieved_at_utc'])
        else:
            page = render_page(request['url'], retrieved_at=request['retrieved_at_utc'])
        result = {'request': request, 'ok': True, 'page': page}
    except Exception as exc:
        result = {'request': request, 'ok': False, 'error': type(exc).__name__,
                  'detail': str(exc)[:500], 'transport_audit': getattr(exc, 'audit', None)}
    save(io_path(output), result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    worker(args.request, args.output)
