"""Local acquisition checkpoints, source selection and bounded recovery hints."""
from ForecastAgent.tavily_research import canonical_url


def reading_targets(bundle):
    """Question links, search hits and explicitly selected links are reading leads."""
    targets = {url for url, row in bundle.get('source_leads', {}).items() if row.get('origin') != 'page_link'}
    targets.update(canonical_url(hit['url']) for search in bundle.get('searches', []) + bundle.get('exa_searches', []) for hit in search.get('results', []))
    targets.update(bundle.get('selected_sources', {}))
    return targets


def checkpoint(task):
    b = task.bundle
    if getattr(task,'raw_recall',False):
        from ForecastAgent.evidence.raw_capture import capture_report
        from ForecastAgent.runtime.search_policy import requirement
        from ForecastAgent.runtime.collection_actions import raw_stop_reason, discovery_read_action
        report=capture_report(b)
        action=discovery_read_action(task)
        return {'schema':'raw_acquisition_checkpoint_v1','budget_remaining':task.budget(),
            'exa_requirement':requirement(task),'program_stop_reason':raw_stop_reason(task),
            'saved_body_count':report['capture_count'],'usable_saved_body_count':report['readable_body_count'],
            'possible_index_shell_count':report['possible_index_shell_count'],
            'parse_gap_urls':report['parse_gap_urls'],'unmatched_candidate_urls':report['unmatched_candidate_urls'],
            'sources':[{k:r[k] for k in ('url','page_form','identity','observed_start','observed_end','source_row_count')} for r in report['sources']],
            'suggested_next_steps':[action] if action else [],
            'limits':'Raw acquisition only. No missing excerpts or interpretation obligations. Match and form diagnostics do not verify truth or full recall.'}
    from ForecastAgent.runtime.needs import inventory
    from ForecastAgent.runtime.collection_v2 import eligible
    from ForecastAgent.readers.quality import body_diagnostics
    usable={url for url,page in b['pages'].items() if body_diagnostics(page.get('content',''))['usable_text'] and (not task.verified_only or eligible(page,task.cutoff))}
    covered = {need for e in b['excerpts'] if e['url'] in usable for need in e.get('need_ids', [])}
    covered.update(need for a in b['fetch_attempts'] if a.get('status') == 'completed' and a.get('url') in usable for need in a.get('need_ids', []))
    covered.update(row['need_id'] for row in inventory(b)['needs'] if row['usable_associated_sources'])
    from ForecastAgent.runtime.needs import active_needs
    missing = [n['id'] for n in active_needs(b) if n['id'] not in covered]
    decisions = b.get('channel_decisions', {})
    rescue = task.rescue_candidates() if b['mode'] != 'historical_strict' and not b['extract_attempts'] else []
    todo = []
    if usable and missing:
        todo.append({'tool': 'search_saved_text', 'reason': 'Find passages addressing needs without excerpts; use excerpt_args or record_quote.'})
    if task.verified_only and set(b['pages'])-usable:
        if task.budget()['page_fetch_remaining']>=2:
            todo.append({'tool':'collect_archive','reason':'Audit-only pages cannot be read. Choose one important accepted URL not previously attempted; an archive costs two HTTP requests and may be unavailable.'})
        else:
            todo.append({'tool':'finish_collection','reason':'Audit-only historical pages have no usable body and fewer than two archive requests remain. Record the missing snapshot as a gap.'})
    if missing and task.budget().get('exa_search_remaining',0)>0:
        todo.append({'tool':'search_exa','reason':'One authorized supplemental discovery attempt remains; use it only for a critical gap or independent source. It does not permit reading current historical bodies.'})
    from ForecastAgent.runtime.search_policy import requirement
    exa = requirement(task)
    if exa['required'] and exa['status'] == 'pending':
        todo = [item for item in todo if item['tool'] != 'search_exa']
        todo.insert(0, {'tool':'search_exa', 'required':True,
                       'reason':'One task-grounded independent discovery attempt is required before normal finish; provider failures consume the attempt. No extra quota.'})
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
            'acquisition_inventory':inventory(b),
            'needs_without_located_material': missing, 'saved_body_count': len(b['pages']),
            'usable_saved_body_count':len(usable),'audit_only_urls':sorted(set(b['pages'])-usable),
            'association_warning':'Located material does not establish resolution-condition coverage or known future outcomes.',
            'excerpt_count': len(b['excerpts']), 'extract_eligible_urls': rescue[:5],
            'exa_requirement':exa,
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
    if name in {'search_tavily','search_exa'}:
        return {'tool': 'collection_checkpoint', 'instruction': 'Failures consume search quota. Inspect saved material and remaining budget before another search.'}
    return {'tool': 'collection_checkpoint', 'instruction': 'Inspect saved progress and choose another available tool within existing budgets.'}
