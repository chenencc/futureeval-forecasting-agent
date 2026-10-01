"""Expose collection capabilities only when their prerequisites and budgets permit."""
from ForecastAgent.runtime.collection_v2 import eligible


def active_tools(task, tools, forced=None):
    if forced:
        return [tool for tool in tools if tool['function']['name']==forced]
    b=task.bundle
    budget=task.budget()
    excluded=set()
    if b['plan'] is not None:
        excluded.add('plan_evidence')
    if b.get('channel_plan'):
        excluded.add('plan_channels')
    if budget['tavily_basic_remaining']<=0:
        excluded.add('search_tavily')
    if budget.get('exa_search_remaining',0)<=0:
        excluded.add('search_exa')
    if budget['basic_extract_batches_remaining']<=0 or task.verified_only or not task.rescue_candidates():
        excluded.add('extract_failed_pages')
    if b['mode']!='live':
        excluded.update({'list_official_datasets','collect_official','collect_polymarket','read_market_snapshot','record_channel_decision'})
    if not task.cutoff or budget['page_fetch_remaining']<2:
        excluded.add('collect_archive')
    readable={u:p for u,p in b['pages'].items() if not task.verified_only or eligible(p,task.cutoff)}
    if not readable:
        excluded.update({'list_documents','read_document','record_quote','record_excerpts','read_dataset_rows'})
    elif not any(p.get('rows') for p in readable.values()):
        excluded.add('read_dataset_rows')
    if not b.get('passages'):
        excluded.add('record_excerpts')
    if budget['page_fetch_remaining']<=0:
        excluded.update({'collect_dataset','collect_official','collect_polymarket'})
    return [tool for tool in tools if tool['function']['name'] not in excluded]
