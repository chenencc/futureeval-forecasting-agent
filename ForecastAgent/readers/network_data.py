"""Archive bounded JSON/CSV responses already requested by a public browser page."""
import asyncio
from datetime import datetime, timezone
import time

from ForecastAgent.readers.loader import load_response
from ForecastAgent.readers.material_structure import structured_rows


class DataResponseObserver:
    def __init__(self, parent_url, guard, *, max_responses=3, max_bytes=2_000_000):
        if not 1 <= max_responses <= 3 or not 1 <= max_bytes <= 2_000_000:
            raise ValueError('Data response archive bounds exceed supported limits')
        self.parent_url, self.guard = parent_url, guard
        self.max_responses, self.max_bytes = max_responses, max_bytes
        self.records, self.tasks = [], []
        self.saved_bytes = 0
        self.over_limit_responses = 0
        self.closed = False

    def observe(self, response):
        if self.closed:
            return
        request = response.request
        kind = response.headers.get('content-type', '').split(';')[0].strip().lower()
        if request.method not in {'GET', 'HEAD'} or kind not in {'application/json', 'text/csv', 'application/csv'}:
            return
        try:
            if self.guard.origin(response.url) not in self.guard.validated_origins:
                return
        except ValueError:
            return
        if len(self.records) >= self.max_responses:
            self.over_limit_responses += 1
            return
        record = {'url': response.url, 'content_type': kind, 'http_status': response.status,
                  'parent_page_url': self.parent_url, 'status': 'reserved',
                  'observed_at_utc': datetime.now(timezone.utc).isoformat(),
                  'new_http_dispatch': False, 'raw_kind': 'browser_decoded_response_body'}
        self.records.append(record)
        self.tasks.append(asyncio.create_task(self.capture(response, record)))

    async def capture(self, response, record):
        try:
            length = response.headers.get('content-length', '')
            if length.isdecimal() and int(length) > self.max_bytes:
                raise ValueError('Declared data response exceeds byte limit')
            remaining_time = self.guard.deadline - time.monotonic()
            if remaining_time <= 0:
                raise TimeoutError('Data response deadline reached')
            raw = await asyncio.wait_for(response.body(), timeout=min(2, remaining_time))
            # This counts archived decoded bytes, not all browser wire bytes.
            # Missing content length means size can only be checked after body().
            if len(raw) > self.max_bytes - self.saved_bytes:
                raise ValueError('Total archived data bytes exceed limit')
            self.saved_bytes += len(raw)
            page = load_response({'url': response.url, 'final_url': response.url, 'raw': raw,
                'content_type': record['content_type'], 'charset': 'utf-8', 'response_headers': {}},
                retrieved_at=record['observed_at_utc'], preserve_raw_on_failure=True)
            page.update(http_status=response.status, raw_kind='browser_decoded_response_body',
                        capture_method='observed_browser_data_response', parent_page_url=self.parent_url)
            try:
                rows = structured_rows(page)
                if response.status >= 400:
                    rows['state'] = 'http_error'
                page['structured_data'] = rows
                usable = response.status < 400 and rows['state'] == 'rows_available'
            except (ValueError, TypeError, RecursionError) as exc:
                page['structured_data'] = {'state': 'parse_gap', 'error': type(exc).__name__}
                usable = False
            page['capture_status'] = {'usable_text': usable, 'category': 'structured_rows' if usable else 'data_gap',
                                      'truth_verified': False, 'relevance_verified': False}
            record.update(status='captured', snapshot=page, decoded_bytes=len(raw))
        except asyncio.CancelledError:
            record.update(status='cancelled', gap='Browser closed before data response archival completed')
        except Exception as exc:
            record.update(status='failed', error=type(exc).__name__, detail=str(exc)[:400])

    async def finish(self):
        self.closed = True
        if not self.tasks:
            return
        budget = max(0, min(1, self.guard.deadline-time.monotonic()))
        _, pending = await asyncio.wait(self.tasks, timeout=budget)
        for task in pending:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def export(self):
        return {'schema': 'observed_browser_data_v1', 'records': self.records,
                'max_responses': self.max_responses, 'max_archived_decoded_bytes': self.max_bytes,
                'archived_decoded_bytes': self.saved_bytes, 'overflow_responses': self.over_limit_responses,
                'network_calls_added': 0,
                'scope': 'Decoded browser responses already dispatched under the parent request guard; not wire-byte accounting.'}
