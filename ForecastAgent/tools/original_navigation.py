"""Saved-original navigation and exact versioned native reference projection."""
import base64
import copy
import hashlib
import json
from ForecastAgent.tavily_research import canonical_url
MAX_VIEW_CHARS = 2_000_000
MAX_VIEWS = 128

def _original(task, args, box):
    if bool(args.get('url')) == bool(args.get('capture_id')):
        raise ValueError('Choose exactly one saved url or capture_id')
    if args.get('capture_id'):
        ident = args['capture_id']
        if ident not in task.bundle['channel_tools']['captures']:
            raise ValueError('Choose a capture already recorded by this task')
        capture, raw = box._saved(ident)
        key = canonical_url(capture['request_url'])
    else:
        key = canonical_url(args['url'])
        page = task.bundle['pages'].get(key)
        if not page or not page.get('raw_response_base64'):
            raise ValueError('Choose a saved native page with original bytes')
        raw = base64.b64decode(page['raw_response_base64'], validate=True)
        if hashlib.sha256(raw).hexdigest() != page.get('sha256'):
            raise ValueError('Saved original checksum mismatch')
        capture = {'id': 'native:' + page['sha256'], 'raw_sha256': page['sha256'], 'request_url': key,
            'captured_at_utc': page.get('retrieved_at_utc'), 'status': 'usable',
            'http': {'status': page.get('http_status', 200), 'content_type': page.get('content_type', ''),
                     'response_headers': page.get('response_headers', {}), 'truncated': page.get('raw_truncated', False)}}
    page = task.bundle['pages'].get(key)
    if page is None and args.get('capture_id'):
        page = next((p['page'] for p in reversed(task.bundle.get('failed_captures', []))
                     if p.get('channel_capture_id') == args['capture_id']), None)
    from ForecastAgent.runtime.collection_v2 import eligible
    if not page or task.cutoff and not eligible(page, task.cutoff):
        raise ValueError('Original is unavailable or temporally ineligible for this task')
    if page.get('sha256') != capture['raw_sha256']:
        raise ValueError('Original capture is not the current saved source version')
    return key, capture, raw

def _materialize(task, url, result, original=None):
    """Map unit offsets into a separately versioned native parsed-text view.

Raw bytes and capture time never change. Old parsed views go to page_history.
Normalized unit offsets are never presented as raw or native body offsets.
"""
    text = result.get('text', '')
    if result.get('status') != 'readable' or not text.strip():
        return
    page = task.bundle['pages'].get(url) or original
    if page is None:
        raise ValueError('Original source is missing')
    if task.bundle.get('result'):
        result['materialization_status'] = 'read_only_closed_task'
        return
    view_key = hashlib.sha256(json.dumps({k: result.get(k) for k in
        ('raw_sha256', 'parser_version', 'unit_id', 'start', 'end')}, sort_keys=True).encode()).hexdigest()
    views = task.bundle['channel_tools']['views']
    text_hash = hashlib.sha256(text.encode()).hexdigest()
    if view_key in views and views[view_key]['text_sha256'] != text_hash:
        raise ValueError('Frozen navigation view changed')
    if view_key not in views:
        if len(views) >= MAX_VIEWS or sum(v['chars'] for v in views.values()) + len(text) > MAX_VIEW_CHARS:
            raise ValueError('Saved navigation view limit exhausted; originals retained')
        old = page['content']
        position = old.find(text)
        if position < 0 or old.find(text, position + 1) >= 0:
            derivative = copy.deepcopy(page)
            prefix = '\n\n[Saved-original view: ' + result['unit_id'] + ']\n'
            position = len(old) + len(prefix)
            derivative['content'] = old + prefix + text
            derivative.setdefault('documents', []).append({'page_content': text, 'metadata': {
                'format': 'saved_original_unit', 'parser': result['parser_version'],
                'raw_sha256': result['raw_sha256'], 'unit_id': result['unit_id'],
                'unit_start': result['start'], 'unit_end': result['end'],
                'native_start': position, 'native_end': position + len(text)}})
            derivative['capture_method'] = page.get('capture_method', 'saved_original')
            derivative['parsed_view_method'] = 'saved_original_navigation_v1'
            derivative.pop('body_diagnostics', None)
            task.store_page(url, derivative)
        views[view_key] = {'url': url, 'raw_sha256': result['raw_sha256'], 'parser': result['parser_version'],
            'unit_id': result['unit_id'], 'unit_start': result['start'], 'unit_end': result['end'],
            'native_start': position, 'native_end': position + len(text), 'text_sha256': text_hash,
            'chars': len(text), 'capture_time_preserved': True}
    view = views[view_key]
    if task.bundle['pages'][url]['content'][view['native_start']:view['native_end']] != text:
        raise ValueError('Navigation view no longer matches native saved text')
    result['native_coordinates'] = copy.deepcopy(view)
    result['native_coordinates']['body_sha256'] = hashlib.sha256(task.bundle['pages'][url]['content'].encode()).hexdigest()
    from ForecastAgent.research_loop import state
    if state.enabled(task.bundle):
        catalog = state.catalog(task.bundle, task.cutoff)
        result['evidence'] = [s for s in catalog['spans'].values() if s['url'] == url and
            s['start'] < view['native_end'] and s['end'] > view['native_start']][:4]
        result['material_sha256'] = catalog['material_sha256']
    task.save()

def _table_window(capture, raw, options, result):
    """HTML row selection must deliver the selected rows, not the first text window."""
    if 'rows' not in result or 'row_start' not in options:
        return None
    from ForecastAgent.tools.intelligence_box.navigation import units
    with units(capture, raw, options.get('max_pages', 100)) as (items, _):
        unit = next((u for u in items if u['unit_id'] == result['unit_id']), None)
        if not unit or unit.get('kind') != 'table':
            return None
        lines = [' | '.join(c['text'] for c in row) for row in unit['rows']]
        row_start = options['row_start']
        selected = lines[row_start:row_start + options.get('max_rows', 40)]
        start = sum(len(line) + 1 for line in lines[:row_start])
        text = '\n'.join(selected)
        cap = options.get('max_chars', 12000)
        result.update(start=start, end=start + min(len(text), cap), text=text[:cap],
            row_text_truncated=len(text) > cap,
            next_start=start + cap if len(text) > cap else None,
            next_row=row_start + len(selected) if row_start + len(selected) < len(lines) else None)
        if not selected:
            result['status'] = 'unreadable'
        # Preserve a contiguous literal header prefix as a separately bound view.
        count = 0
        for row in unit['rows']:
            if not any(c['tag'] == 'th' for c in row):
                break
            count += 1
        header = '\n'.join(lines[:count])[:cap]
        return {**result, 'start': 0, 'end': len(header), 'text': header} if header and row_start >= count else None
