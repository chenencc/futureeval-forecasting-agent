"""Local acquisition checkpoints, source selection and bounded recovery hints."""
from ForecastAgent.tavily_research import canonical_url


def reading_targets(bundle):
    """Question links, search hits and explicitly selected links are reading leads."""
    targets = {url for url, row in bundle.get('source_leads', {}).items() if row.get('origin') != 'page_link'}
    targets.update(canonical_url(hit['url']) for search in bundle.get('searches', []) for hit in search.get('results', []))
    targets.update(bundle.get('selected_sources', {}))
    return targets


def checkpoint(task):
    b = task.bundle
    covered = {need for e in b['excerpts'] for need in e.get('need_ids', [])}
    covered.update(need for a in b['fetch_attempts'] if a.get('status') == 'completed' for need in a.get('need_ids', []))
    missing = [n['id'] for n in b.get('plan') or [] if n['id'] not in covered]
    decisions = b.get('channel_decisions', {})
    rescue = task.rescue_candidates() if b['mode'] != 'historical_strict' and not b['extract_attempts'] else []
    todo = []
    if b['pages'] and missing:
        todo.append({'tool': 'search_saved_text', 'reason': 'Find passages addressing needs without excerpts; use excerpt_args or record_quote.'})
    if rescue and 'tavily_extract_basic' not in decisions:
        todo.append({'tool': 'extract_failed_pages', 'reason': 'Important failed pages may be rescued in one basic batch; choose relevant URLs or record a deferral.'})
    if b['mode'] == 'live' and not b['market_snapshots'] and 'polymarket_gamma' not in decisions:
        todo.append({'tool': 'collect_polymarket', 'reason': 'Consider one concise entity/event query before spending all eight HTTP attempts; otherwise record why deferred.'})
    if b['mode'] == 'live' and not any(a.get('channel') == 'official' for a in b['fetch_attempts']) and 'official_government' not in decisions:
        todo.append({'tool': 'list_official_datasets', 'reason': 'Check whether a supported series/document API fits the acquisition needs; otherwise record not applicable.'})
    targets = reading_targets(b)
    if getattr(task,'optimized',False):
        for step in todo:
            if step['tool']=='search_saved_text':
                step.update(tool='read_sources',reason='Locate complete saved paragraphs for several acquisition queries in one batch; no additional HTTP call for cached sources.')
    unread = sorted(targets - set(b['pages']))
    planned = sum(row.get('expected_http_attempts',0) for row in b.get('channel_plan',[]))
    if not b.get('channel_plan'):
        todo.insert(0,{'tool':'plan_channels','reason':'Allocate shared HTTP attempts to important sources, structured data and any two-request archive lookup.'})
    return {'schema': 'acquisition_checkpoint_v1', 'budget_remaining': task.budget(),
            'needs_without_located_material': missing, 'saved_body_count': len(b['pages']),
            'association_warning':'Located material does not establish resolution-condition coverage or known future outcomes.',
            'excerpt_count': len(b['excerpts']), 'extract_eligible_urls': rescue[:5],
            'selected_unread_count': len(unread), 'selected_unread_urls': unread[:12],
            'captured_link_count': sum(row.get('origin') == 'page_link' for row in b['source_leads'].values()),
            'channel_decisions': decisions, 'suggested_next_steps': todo,
            'http_plan':{'estimated_attempts':planned,'initial_attempt_cap':8,
                         'over_initial_cap':planned>8,'archive_requires_two_remaining':True},
            'limits': 'Suggestions grant no extra calls. Empty search results do not establish absence. No truth verification.'}


def recovery_hint(name):
    if name in {'record_excerpt', 'record_quote'}:
        return {'tool': 'search_saved_text', 'instruction': 'Copy returned excerpt_args and add need_ids, or copy a unique exact passage into record_quote. Never guess coordinates; maximum excerpt length is 4000.'}
    if name in {'fetch_page', 'fetch_pages'}:
        return {'tool': 'list_sources', 'instruction': 'Choose an exact accepted URL. Do not invent or repeatedly fetch the same failed URL. Inspect collection_checkpoint for basic Extract rescue.'}
    if name == 'search_tavily':
        return {'tool': 'collection_checkpoint', 'instruction': 'Failures consume search quota. Inspect saved material and remaining budget before another search.'}
    return {'tool': 'collection_checkpoint', 'instruction': 'Inspect saved progress and choose another available tool within existing budgets.'}
