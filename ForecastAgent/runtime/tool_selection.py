"""Expose collection capabilities only when their prerequisites and budgets permit."""
from copy import deepcopy
from ForecastAgent.runtime.collection_v2 import eligible
from ForecastAgent.runtime.search_policy import ready


def active_tools(task, tools, forced=None):
    tools=deepcopy(tools)
    need_ids=[need['id'] for need in task.bundle.get('plan') or []]
    def bind_ids(value):
        if isinstance(value,dict):
            single=value.get('properties',{}).get('need_id')
            if single and need_ids:
                value['properties']['need_id']={**single,'enum':need_ids}
            prop=value.get('properties',{}).get('need_ids')
            if prop and need_ids:
                prop['items']={'type':'string','enum':need_ids}
                prop['description']='Existing evidence-plan IDs only; channel IDs and tool names are invalid.'
            for child in value.values():
                bind_ids(child)
        elif isinstance(value,list):
            for child in value:
                bind_ids(child)
    bind_ids(tools)
    for entry in tools:
        if entry['function']['name'] == 'load_research_skill':
            names = [name for name in task.bundle.get('skill_bank', {}) if name != 'evidence-review']
            if names:
                entry['function']['parameters']['properties']['name'] = {'type':'string', 'enum':names}
    if forced:
        if forced == 'read_sources':
            for entry in tools:
                if entry['function']['name'] == 'read_sources':
                    entry['function']['parameters']['properties']['urls']['items'] = {'type': 'string', 'enum': list(task.catalog())}
        return [tool for tool in tools if tool['function']['name']==forced]
    b=task.bundle
    budget=task.budget()
    excluded=set()
    if ready(task) and not b['control'].get('forced_close'):
        excluded.add('finish_collection')
    if b['plan'] is not None:
        excluded.add('plan_evidence')
    if b.get('channel_plan'):
        excluded.add('plan_channels')
    if budget['tavily_basic_remaining']<=0:
        excluded.add('search_tavily')
    from ForecastAgent.runtime.collection_actions import discovery_read_action
    if discovery_read_action(task):
        excluded.update({'search_tavily', 'search_exa'})
    if budget.get('exa_search_remaining',0)<=0:
        excluded.add('search_exa')
    if budget['basic_extract_batches_remaining']<=0 or task.verified_only or not task.rescue_candidates():
        excluded.add('extract_failed_pages')
    if b['mode']!='live':
        excluded.update({'list_official_datasets','collect_official','collect_polymarket','read_market_snapshot','record_channel_decision'})
    if not task.cutoff or budget['page_fetch_remaining']<2:
        excluded.add('collect_archive')
    from ForecastAgent.readers.quality import body_diagnostics
    readable={u:p for u,p in b['pages'].items() if body_diagnostics(p.get('content',''))['usable_text'] and (not task.verified_only or eligible(p,task.cutoff))}
    from ForecastAgent.runtime.collection_actions import duplicate_read
    unread={u:p for u,p in readable.items() if not duplicate_read(task,{'url':u,'max_chars':len(p['content'])})
        or any(len(doc['page_content'].strip())>=80 and not duplicate_read(task,{'url':u,'document_index':i,'max_chars':len(doc['page_content'])})
               for i,doc in enumerate(p.get('documents') or [{'page_content':p['content']}],1))}
    if not unread:
        excluded.add('read_document')
    # Saved-document tools cannot select quarantined bodies. Binding only need
    # IDs left every audit-only URL looking selectable to the model.
    for entry in tools:
        if entry['function']['name'] in {'read_document','record_quote','read_dataset_rows','list_documents','search_saved_text','find_passages'}:
            prop=entry['function']['parameters'].get('properties',{}).get('url')
            if prop and readable:
                entry['function']['parameters']['properties']['url']={**prop,'enum':list(unread if entry['function']['name']=='read_document' else readable),
                    'description':'Choose a readable saved source key only. Audit-only current bodies and archive replay URLs cannot be read.'}
    if not readable:
        excluded.update({'list_documents','read_document','record_quote','record_excerpts','read_dataset_rows'})
    elif not any(p.get('rows') for p in readable.values()):
        excluded.add('read_dataset_rows')
    if not b.get('passages'):
        excluded.add('record_excerpts')
    from ForecastAgent.runtime.collection_actions import pending_passages
    if not pending_passages(task):
        excluded.add('review_passages')
    if budget['page_fetch_remaining']<=0:
        excluded.update({'collect_dataset','collect_official','collect_polymarket','parameterize_source'})
    return [tool for tool in tools if tool['function']['name'] not in excluded]
