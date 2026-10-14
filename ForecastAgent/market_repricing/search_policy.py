"""Market-only mixed discovery over the immutable native search ledger."""
import contextlib
import copy
import hashlib
import json
from pathlib import Path
from urllib.request import Request
from unittest.mock import patch

VERSION = 'market-tavily-mixed-v1'
CAPS = {'basic': 2, 'advanced': 1, 'total': 3}
DESCRIPTION = ('At most TWO basic searches and ONE optional advanced search, THREE total lifetime '
    'reservations. Choose search_depth explicitly. Use basic for primary discovery and cross-checks; '
    'advanced only for a critical missing predicate or exact official source, with search_role gap '
    'or official_gap, existing critical need_ids and a concrete reason. Read saved results first. '
    'No obligation to spend all searches. Failed/interrupted searches count; credentials never renew limits.')


def identity():
    source = Path(__file__).read_text(encoding='utf-8').replace('\r\n', '\n')
    return {'version': VERSION, 'caps': dict(CAPS), 'chunks_per_source': 3,
            'max_results': 10, 'auto_parameters': False, 'preserve_complete_search_excerpts': True,
            'adapter_sha256_lf': hashlib.sha256(source.encode()).hexdigest()}


def enabled(task):
    saved = task.bundle['request'].get('market_search_profile')
    if saved is None:
        return False
    if saved != identity():
        raise ValueError('Frozen market search profile changed; no quota migration allowed')
    return True


def usage(task):
    attempts = task.bundle['searches']
    # An interrupted legacy reservation is conservatively basic, never free.
    counts = {depth: sum(row.get('depth', 'basic') == depth for row in attempts) for depth in ('basic', 'advanced')}
    return {'tavily_basic_remaining': max(0, CAPS['basic'] - counts['basic']),
            'tavily_advanced_remaining': max(0, CAPS['advanced'] - counts['advanced']),
            'tavily_total_remaining': max(0, CAPS['total'] - len(attempts)),
            'tavily_searches_reserved': len(attempts), 'tavily_depth_reservations': counts}


def check(task, args):
    depth = args.get('search_depth', 'basic')
    if depth not in ('basic', 'advanced'):
        raise ValueError('Choose basic or advanced search_depth')
    remaining = usage(task)
    if not remaining['tavily_total_remaining'] or not remaining['tavily_' + depth + '_remaining']:
        raise ValueError('Frozen ' + depth + ' search allowance exhausted; read saved sources or finish with gaps')
    if depth == 'advanced':
        critical = {need['id'] for need in task.bundle.get('plan', []) if need.get('priority') == 'critical'}
        if (args.get('search_role') not in {'gap', 'official_gap'} or
                not critical.intersection(args.get('need_ids', [])) or not str(args.get('reason', '')).strip()):
            raise ValueError('Advanced requires gap/official_gap, an existing critical need and a concrete reason')
    return depth


def tools(entries):
    entries = copy.deepcopy(entries)
    for entry in entries:
        function = entry['function']
        if function['name'] == 'search_tavily':
            function['description'] = DESCRIPTION
            schema = function['parameters']
            schema['properties']['search_depth'] = {'type': 'string', 'enum': ['basic', 'advanced']}
            if 'search_depth' not in schema['required']:
                schema['required'].append('search_depth')
    return entries


def guide(task):
    if not enabled(task):
        return ''
    return ('\nMARKET SEARCH OVERRIDE: This replaces the native basic-only search allowance. ' +
            DESCRIPTION + '\nActual cumulative discovery budget: ' + json.dumps(usage(task), sort_keys=True) +
            '\nAdvanced excerpts remain search leads, not captured or verified original bodies. '
            'Use read_sources for the relevant discovered originals; longer text does not prove the target condition.')


@contextlib.contextmanager
def adapter():
    """Keep native validation, deduplication, temporal gates and physical routing."""
    from ForecastAgent.runtime import retrieval, tool_selection
    from ForecastAgent.providers import tavily_search as provider
    Task = retrieval.RetrievalTask
    execute, budget = Task._execute, Task.budget
    validate, active = retrieval.validate, tool_selection.active_tools

    def mixed_budget(task):
        result = budget(task)
        if enabled(task):
            result.update(usage(task))
        return result

    def mixed_validate(task, name, args, offered=None, **kwargs):
        offered = kwargs.pop('tools', offered)
        if kwargs:
            raise TypeError('Unknown validation arguments')
        if enabled(task) and name == 'search_tavily':
            # Internal recovery calls may omit depth and remain basic, never upgraded implicitly.
            args = dict(args, search_depth=args.get('search_depth', 'basic'))
            offered = tools(offered if offered is not None else retrieval.COLLECTION_TOOLS)
            check(task, args)
        return validate(task, name, args, offered)

    def selected(task, entries, *args, **kwargs):
        if not enabled(task):
            return active(task, entries, *args, **kwargs)
        actual_budget = task.budget
        def visibility_budget():
            result = actual_budget()
            # Native tool visibility only understands one search counter. Keep its other
            # prerequisites intact while allowing the advanced slot after two basic calls.
            result['tavily_basic_remaining'] = result['tavily_total_remaining']
            return result
        with patch.object(task, 'budget', visibility_budget):
            result = tools(active(task, entries, *args, **kwargs))
        remaining = usage(task)
        for entry in result:
            if entry['function']['name'] == 'search_tavily':
                entry['function']['parameters']['properties']['search_depth']['enum'] = [
                    depth for depth in ('basic', 'advanced') if remaining['tavily_' + depth + '_remaining']]
        return result

    def mixed_execute(task, name, args, key):
        if name != 'search_tavily' or not enabled(task):
            return execute(task, name, args, key)
        depth = check(task, args)
        initial_count = len(task.bundle['searches'])
        save, native_search, transport = task.save, retrieval.search_batch, provider.urlopen

        def save_reservation():
            if len(task.bundle['searches']) > initial_count:
                row = task.bundle['searches'][initial_count]
                row.update(depth=depth, market_search_policy=VERSION,
                           credits_estimate=1 if depth == 'basic' else 2)
            return save()

        def send(request, *values, **options):
            if request.full_url != provider.TAVILY_SEARCH_URL or request.get_method() != 'POST':
                raise ValueError('Mixed search transport only accepts the exact Tavily Search endpoint')
            payload = json.loads(request.data)
            payload.update(search_depth=depth, chunks_per_source=3, auto_parameters=False)
            rewritten = Request(request.full_url, data=json.dumps(payload).encode('utf-8'),
                                headers=dict(request.header_items()), method='POST')
            return transport(rewritten, *values, **options)

        def search(*values, **options):
            with patch.object(provider, 'urlopen', send):
                data = native_search(*values, **options)
            # Native filtering remains authoritative. Rebind only the exact accepted URL;
            # do not resurrect excluded hosts, duplicates or post-cutoff hits.
            raw = {}
            for row in data.get('raw_results', []):
                raw.setdefault(row.get('url'), row)
            for hit in data['results']:
                source = raw.get(hit['url'], {})
                if isinstance(source.get('content'), str):
                    hit.update(content=source['content'], snippet_truncated=False,
                               content_role='complete_returned_search_excerpt_not_original_body')
            data.update(search_depth=depth, chunks_per_source=3, market_search_policy=VERSION)
            return data

        # The native reserve callback writes the actual depth before transmission, including
        # interruptions and failures. Reusing a provider key cannot create another logical slot.
        with patch.object(task, 'save', save_reservation), patch.object(retrieval, 'search_batch', search):
            return execute(task, name, args, key)

    with patch.object(Task, '_execute', mixed_execute), patch.object(Task, 'budget', mixed_budget), \
            patch.object(retrieval, 'validate', mixed_validate), \
            patch.object(retrieval, 'COLLECTION_TOOLS', tools(retrieval.COLLECTION_TOOLS)), \
            patch.object(tool_selection, 'active_tools', selected):
        yield
