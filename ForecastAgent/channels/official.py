"""Official-source adaptation through native reservations and immutable captures."""
import base64
import copy
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from ForecastAgent.tavily_research import canonical_url
from ForecastAgent.runtime.budget import reserve
from ForecastAgent.tools.intelligence_box import core
from ForecastAgent.channels.contracts import NETWORK_NAMES

def _box(task, need_ids):
    """The toolbox journal is a mirror, never an independent production quota."""
    from ForecastAgent.runtime import retrieval
    def fetcher(url, byte_cap):
        # This guard is per physical request, including manual pagination or redirects.
        attempt = {'url': url, 'channel': 'native_official_channels', 'need_ids': need_ids,
                   'status': 'reserved', 'at': core.now()}
        row = box.db.execute("SELECT id FROM attempts WHERE url=? AND status='reserved' ORDER BY created DESC LIMIT 1", (url,)).fetchone()
        attempt['channel_journal_id'] = row[0] if row else None
        attempt['operation_sha256'] = getattr(task, '_channel_operation', None)
        reserve(task.bundle, 'fetch_attempts', attempt, task.fetch_limit, task.save)
        host = urlsplit(url).hostname
        if host == 'api.gdeltproject.org':
            task.bundle['channel_tools'].setdefault('provider_ready_at', {})['gdelt_news'] = (
                datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
        try:
            response = core.transport(url, byte_cap,
                allowed_domains=box.allowed_domains,
                user_agent=os.environ.get('SEC_USER_AGENT') if host in {'data.sec.gov', 'www.sec.gov'}
                    else 'ForecastAgent native official channels',
                api_key=os.environ.get('CONGRESS_API_KEY') if host == 'api.congress.gov' else None)
            attempt.update(status='completed' if response['status'] == 200 else 'failed', http_status=response['status'])
            if host == 'api.gdeltproject.org':
                # Return control to the agent; never sleep or retry inside a tool.
                delay = 5
                retry_after = response.get('retry_after')
                if response['status'] == 429 and retry_after:
                    try:
                        delay = max(delay, int(retry_after))
                    except (ValueError, TypeError):
                        try:
                            delay = max(delay, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError, OverflowError):
                            pass
                task.bundle['channel_tools'].setdefault('provider_ready_at', {})['gdelt_news'] = (
                    datetime.now(timezone.utc) + timedelta(seconds=min(delay, 86400))).isoformat()
            return response
        except Exception as exc:
            attempt.update(status='failed', error=type(exc).__name__)
            raise
        finally:
            task.save()
    box = core.Toolbox(task.directory / 'channel-tools', max_requests=1000, fetcher=fetcher)
    return box

def _known_url(task, url):
    key = canonical_url(url)
    if key not in task.catalog() and key not in task.bundle['pages']:
        raise ValueError('Read only exact URLs already discovered in this task')

def _capture_page(task, result, box):
    """Project native records, not a model summary; retain failed/empty originals."""
    if not result.get('id'):
        return
    ident = result['id']
    task.bundle['channel_tools']['captures'][ident] = copy.deepcopy(result)
    if not result.get('raw_path'):
        return
    capture, raw = box._saved(ident)
    task.bundle.setdefault('channel_raw_captures', {})[ident] = {
        'capture': capture, 'raw_response_base64': base64.b64encode(raw).decode()}
    from ForecastAgent.channels.discovery import is_index, preserve_leads
    if result['status'] == 'usable' and is_index(result):
        preserve_leads(task, result)
        return  # Search/index metadata is not a readable original body.
    url = canonical_url(result['request_url'])
    records = result['records']
    documents = [r for r in records if isinstance(r, dict) and isinstance(r.get('page_content'), str)]
    text = '\n\n'.join(d['page_content'] for d in documents) if documents else json.dumps(
        {'records': records, 'source_binding': result.get('source_binding'), 'coverage': result.get('coverage')}, ensure_ascii=False)
    page = {'url': result['request_url'], 'final_url': result['http']['final_url'],
            'content': text, 'documents': documents, 'content_type': result['http']['content_type'],
            'sha256': result['raw_sha256'], 'raw_response_base64': base64.b64encode(raw).decode(),
            'retrieved_at_utc': result['captured_at_utc'], 'capture_method': 'native_official_channels',
            'temporal_status': 'live_capture', 'http_status': result['http']['status'],
            'response_headers': result['http'].get('response_headers', {}),
            'raw_truncated': result['http'].get('truncated', False),
            'channel_capture_id': ident, 'channel_coverage': result.get('coverage'),
            'source_id': result['source_id'], 'source_role': result['role'],
            'material_kind': 'readable_original' if documents else 'structured_observations',
            'source_binding': result.get('source_binding'), 'limitations': result['limitations'],
            'links': []}
    if not documents:
        page['rows'] = records
    if result['status'] != 'usable':
        task.bundle.setdefault('failed_captures', []).append({'url': url, 'page': page,
            'channel_capture_id': ident, 'channel_status': result['status'], 'error': result.get('error')})
        return
    task.bundle['source_leads'].setdefault(url, {'url': url, 'origin': 'official_contract', 'source_id': result['source_id']})
    task.store_page(url, page)

def preflight(task, name, args, need_ids):
    for field in ('capture_id', 'detail_capture_id'):
        if args.get(field) and args[field] not in task.bundle['channel_tools']['captures']:
            raise ValueError('Choose a saved capture already recorded by this task')
    if name in NETWORK_NAMES:
        if task.bundle.get('result'):
            raise ValueError('Retrieval already finished; no new channel capture allowed')
        task.needs({'need_ids': need_ids})
        if task.bundle['mode'] != 'live' or task.cutoff:
            raise ValueError('Current official channel captures require live mode')
        if name == 'intelligence_read':
            _known_url(task, args['url'])
        if name == 'intelligence_profile' and args.get('url'):
            _known_url(task, args['url'])
        if name == 'intelligence_fetch' and args['source_id'].startswith('sec_'):
            cik = str(args.get('parameters', {}).get('cik', ''))
            import re
            if not re.fullmatch(r'[0-9]{10}', cik):
                raise ValueError('SEC CIK must have exactly ten digits')
            known = json.dumps({'request': task.bundle['request'], 'pages': task.bundle['pages']}, ensure_ascii=False)
            numeric = str(int(cik))
            if not (re.search(r'(?i)CIK[\s:/"=]*0*' + re.escape(numeric) + r'\b', known) or
                    re.search(r'(?i)"cik"\s*:\s*"?0*' + re.escape(numeric) + r'\b', known)):
                raise ValueError('SEC requires a CIK already observed in task materials or rules')
        if name == 'intelligence_fetch' and not replay_available(task, name, args):
            from ForecastAgent.channels.selection import availability
            status = availability(task, args['source_id'])
            if status['status'] == 'cooldown':
                raise ValueError('Provider cooldown until ' + status['next_request_at_utc'] + '; no request reserved')

def acquire(task, name, args, need_ids, box):
    signature = hashlib.sha256(json.dumps({'tool': name, 'args': args}, sort_keys=True).encode()).hexdigest()
    operations = task.bundle['channel_tools']['operations']
    prior = next((o for o in operations if o['signature'] == signature), None)
    if prior:
        if not prior.get('capture_id'):
            attempt = next((a for a in reversed(task.bundle['fetch_attempts']) if
                a.get('operation_sha256') == signature and a.get('channel_journal_id')), None)
            if attempt:
                try:
                    capture, _ = box._saved(attempt['channel_journal_id'])
                except FileNotFoundError:
                    pass
                else:
                    if capture['request_url'] != attempt['url']:
                        raise ValueError('Recovered channel capture identity mismatch')
                    _capture_page(task, capture, box)
                    prior.update(capture_id=capture['id'], status=capture['status'], recovered_without_http=True)
                    task.save()
        if prior.get('capture_id'):
            capture = task.bundle['channel_tools']['captures'][prior['capture_id']]
            box._saved(capture['id']) if capture.get('raw_path') else None
            return {**copy.deepcopy(capture), 'cached': True, 'budget': task.budget()}
        raise ValueError('Channel operation interrupted or failed; preserved reservation requires review')
    operation = {'signature': signature, 'status': 'reserved', 'at': core.now()}
    operations.append(operation)
    task.save()
    task._channel_operation = signature


    result = box.call(name, args)
    _capture_page(task, result, box)
    operation.update(status=result.get('status'), capture_id=result.get('id'))
    if result.get('status') == 'configuration_required':
        operations.remove(operation)  # No physical attempt was reserved; configuration can be supplied later.
    result['budget'] = task.budget()
    result['budget_authority'] = 'native_fetch_attempts'
    task.save()
    return result


def replay_available(task, name, args):
    """A recorded channel operation can only replay or recover saved state."""
    if name not in NETWORK_NAMES:
        return False
    clean = {k:v for k,v in args.items() if k != 'need_ids'}
    signature = hashlib.sha256(json.dumps({'tool': name, 'args': clean}, sort_keys=True).encode()).hexdigest()
    return any(o['signature'] == signature for o in task.bundle.get('channel_tools', {}).get('operations', []))
