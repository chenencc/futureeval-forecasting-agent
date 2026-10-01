"""Validate model tool calls before any resource reservation or network request."""
import re
from datetime import datetime
from urllib.parse import urlsplit

from ForecastAgent.tavily_research import canonical_url
from ForecastAgent.readers.saved import select


class ContractError(ValueError):
    def __init__(self, code, field, instruction, allowed=None):
        super().__init__(instruction)
        self.details = {'code': code, 'field': field, 'instruction': instruction}
        if allowed is not None:
            self.details['allowed_values'] = allowed[:20]


def validate_plan_cutoff(task, needs):
    """Reject explicit post-cutoff observed-data demands before a plan is frozen."""
    if not task.cutoff or not task.optimized:
        return
    from ForecastAgent.runtime.collection_v2 import late_dates
    for need in needs:
        text = need.get('condition', '')
        if not re.search(r'price data|price reached|daily .*values|measured values|observed (?:prices|results)', text, re.I):
            continue
        if re.search(r'forecast|projected|expected|scenario|prediction', text, re.I):
            continue
        for stamp in late_dates(text, task.cutoff):
            for pattern in ('%Y-%m-%d','%B %d, %Y','%B %d %Y','%d %B %Y'):
                try:
                    day = datetime.strptime(stamp, pattern).date()
                except ValueError:
                    continue
                if day > task.cutoff.date():
                    raise ContractError('future_observation_need', 'needs',
                        'As-of '+task.cutoff.isoformat()+' is the simulated present. Plan pre-cutoff observations/history and available forward-looking drivers, not realized future prices/results: '+need.get('id',''))
                break


def check_schema(value, schema, path='arguments', required=True):
    types = {'object': dict, 'array': list, 'string': str, 'integer': int,
             'boolean': bool, 'number': (int, float)}
    expected = schema.get('type')
    if expected in types and (not isinstance(value, types[expected]) or
                             expected in {'integer', 'number'} and isinstance(value, bool)):
        raise ContractError('invalid_type', path, f'{path} must be {expected}.')
    if 'enum' in schema and value not in schema['enum']:
        raise ContractError('invalid_choice', path, f'Choose a listed value for {path}.', schema['enum'])
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        if required:
            for key in schema.get('required', []):
                if key not in value:
                    raise ContractError('missing_argument', path+'.'+key, f'Supply {path}.{key}.')
        if schema.get('additionalProperties') is False:
            unknown = set(value)-set(properties)
            if unknown:
                raise ContractError('unknown_argument', path, 'Use only declared parameters.', sorted(properties))
        for key, item in value.items():
            if key in properties:
                check_schema(item, properties[key], path+'.'+key, required)
    elif isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', float('inf')):
            raise ContractError('invalid_length', path, f'{path} violates the declared item limits.')
        for index, item in enumerate(value):
            check_schema(item, schema.get('items', {}), f'{path}[{index}]', required)
    elif isinstance(value, str):
        if len(value) > schema.get('maxLength', float('inf')):
            raise ContractError('invalid_length', path, f'{path} exceeds the declared character limit.')
    elif isinstance(value, (int, float)):
        if value < schema.get('minimum', -float('inf')) or value > schema.get('maximum', float('inf')):
            raise ContractError('invalid_range', path, f'{path} must be within the declared range.')


SAVED = {'read_document', 'record_quote', 'record_excerpt', 'read_dataset_rows',
         'list_documents', 'search_saved_text', 'find_passages'}
DISCOVERED = {'fetch_page', 'fetch_pages', 'read_sources', 'collect_archive',
              'extract_failed_pages', 'select_sources'}


def validate(task, name, args, tools=None):
    if not isinstance(args, dict):
        raise ContractError('invalid_type', 'arguments', 'Tool arguments must be an object.')
    if name == 'extract_failed_pages' and task.bundle['mode'] == 'historical_strict':
        raise ContractError('temporal_block', 'tool', 'Historical strict forbids current Extract captures.')
    if tools is not None:
        schema = next((t['function']['parameters'] for t in tools if t['function']['name'] == name), None)
        if schema is None:
            raise ContractError('unavailable_tool', 'tool', 'Choose an available tool or finish with gaps.',
                                [t['function']['name'] for t in tools])
        # Old profiles accepted omitted optional planning/search metadata.
        check_schema(args, schema, required=task.optimized)
    known = sorted(n['id'] for n in task.bundle.get('plan') or [])
    def needs(value, path='arguments'):
        if isinstance(value, dict):
            if 'need_ids' in value:
                ids = value['need_ids']
                if not isinstance(ids, list) or not ids or any(not isinstance(n, str) or n not in known for n in ids):
                    raise ContractError('unknown_need_id', path+'.need_ids',
                                        'Copy existing evidence need IDs; channel IDs are invalid.', known)
            for key, child in value.items():
                needs(child, path+'.'+key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                needs(child, f'{path}[{index}]')
    needs(args)
    if name in SAVED | DISCOVERED:
        urls = args.get('urls', []) if name in {'fetch_pages', 'read_sources', 'extract_failed_pages', 'select_sources'} else [args['url']] if args.get('url') else []
        if not isinstance(urls, list):
            raise ContractError('invalid_type', 'urls', 'Use a list of discovered URLs.')
        available = task.bundle['pages'] if name in SAVED else {**task.catalog(), **task.bundle['pages']}
        for url in urls:
            if not isinstance(url, str) or urlsplit(url).scheme not in {'http', 'https'} or canonical_url(url) not in available:
                raise ContractError('unknown_source', 'url', 'Copy an exact saved/discovered URL; archive replay URLs are not source keys.', list(available))
        if args.get('url') and name in SAVED and name != 'list_documents':
            target = task.bundle['pages'][canonical_url(args['url'])]
            from ForecastAgent.runtime.collection_v2 import eligible
            if tools is not None and task.verified_only and not eligible(target,task.cutoff):
                raise ContractError('unreadable_historical_source', 'url',
                    'This saved body is audit-only or corrupt. Read an eligible original source key; obtain a pre-cutoff archive only when two HTTP attempts remain, otherwise finish with the missing snapshot gap.',
                    [u for u,p in task.bundle['pages'].items() if eligible(p,task.cutoff)])
            count = len(target.get('documents') or [None])
            index = args.get('document_index')
            if index is not None and (type(index) is not int or not 1 <= index <= count):
                raise ContractError('unknown_document', 'document_index', 'Use a ONE based saved document index or omit the index to read saved content.', list(range(1, min(count, 20)+1)))
            page, text, _ = select(task.bundle['pages'], args['url'], args.get('document_index'))
            if name == 'read_document' and args.get('start_char', 0) >= len(text):
                raise ContractError('empty_read', 'start_char', 'The selected text is empty or its end was reached. Choose another document or finish with gaps.')
            if tools is not None and name == 'read_document':
                from ForecastAgent.runtime.collection_actions import duplicate_read
                if duplicate_read(task,args):
                    raise ContractError('already_delivered_range','start_char',
                        'This complete range was already delivered. Review pending passages, locate different material, read an unseen continuation or rescue a failed primary source. Do not repeat this read.')
            if name == 'read_dataset_rows' and args.get('offset', 0) >= len(page.get('rows', [])):
                raise ContractError('empty_read', 'offset', 'No unread rows at this offset. Choose another dataset or finish with gaps.')
    if tools is not None and name in {'search_tavily', 'search_exa'}:
        # A conservative grounding check is acquisition guidance, not relevance verification.
        query = args.get('query', '')
        corpus = ' '.join(str(task.bundle['request'].get(k, '')) for k in ('question', 'title', 'resolution_criteria'))
        corpus += ' '+str((task.bundle.get('entity_card') or {}).get('subject', ''))
        corpus += ' '+' '.join(n.get('query', '')+' '+n.get('condition', '') for n in task.bundle.get('plan') or [] if n['id'] in args.get('need_ids', []))
        stop = {'the', 'and', 'for', 'before', 'after', 'will', 'what', 'when', 'with', 'from', 'search', 'official', 'source', 'data', 'information'}
        tokens = lambda text: set(re.findall(r'[\w]{3,}', text.casefold()))-stop
        if not tokens(query) & tokens(corpus):
            raise ContractError('ungrounded_query', 'query', 'Describe the actual entity, event, form or series from the task/selected need. Do not search for internal tool names.')
