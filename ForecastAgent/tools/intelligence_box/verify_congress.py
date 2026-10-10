"""One authenticated public Congress API probe; never persist request headers."""
import hashlib
import json
import os
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from .core import now


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main():
    key = os.environ.get('CONGRESS_API_KEY', '')
    if not key or any(c.isspace() for c in key):
        raise SystemExit('Missing or invalid local CONGRESS_API_KEY; no request sent.')
    root = Path('.tmp/congress-configuration-probe-20261010')
    root.mkdir(parents=True, exist_ok=True)
    marker = root / 'attempt-reserved.json'
    with marker.open('x', encoding='utf-8') as stream:
        json.dump({'started_at_utc': now(), 'maximum_requests': 1}, stream)
    url = 'https://api.congress.gov/v3/bill?format=json&limit=1'
    request = Request(url, headers={'X-Api-Key': key, 'Accept': 'application/json',
                                    'User-Agent': 'ForecastAgent public API validation'})
    report = {'checked_at_utc': now(), 'url': url, 'configuration_name': 'CONGRESS_API_KEY',
              'physical_http_attempts': 1, 'maximum_requests': 1, 'key_recorded': False}
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(1_000_001)
            report['http_status'] = response.status
        if len(raw) > 1_000_000:
            raise ValueError('Response exceeds bounded probe cap')
        data = json.loads(raw)
        # Ignore reflected request metadata; retain only public bill fields.
        bills = data.get('bills', [])
        public = [{k: bill.get(k) for k in ('congress', 'type', 'number', 'title', 'latestAction', 'updateDate')} for bill in bills]
        safe = json.dumps(public, indent=2).encode('utf-8')
        (root / 'public-bills.json').write_bytes(safe)
        report.update(status='usable' if public else 'empty', record_count=len(public),
                      public_record_sha256=hashlib.sha256(safe).hexdigest())
    except HTTPError as exc:
        report.update(status='failed', http_status=exc.code, error_type='HTTPError')
    except Exception as exc:
        report.update(status='failed', error_type=type(exc).__name__)
    (root / 'validation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
