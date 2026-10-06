"""Offline production capture replay. No provider, forecast or delivery calls."""
import argparse
import copy
import hashlib
import json
import runpy
import tempfile
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.competition.mercury import packet_for
from ForecastAgent.providers.financial import source_urls
from ForecastAgent.readers.quality import body_diagnostics
from ForecastAgent.runtime.contracts import validate
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.runtime.source_frontier import unread_candidates
from ForecastAgent.runtime.tool_selection import active_tools
from ForecastAgent.tools.registry import COLLECTION_TOOLS


def replay(bundle_path, baseline_root):
    raw=Path(bundle_path).read_bytes();original=json.loads(raw)
    baseline=Path(baseline_root)
    old_quality=runpy.run_path(str(baseline/'ForecastAgent/readers/quality.py'))
    old_financial=runpy.run_path(str(baseline/'ForecastAgent/providers/financial.py'))
    old_contracts=runpy.run_path(str(baseline/'ForecastAgent/runtime/contracts.py'))
    pages=[];visible=copy.deepcopy(original)
    for url,page in original['pages'].items():
        before=old_quality['body_diagnostics'](page['content'])
        after=body_diagnostics(page['content'],documents=page.get('documents',[]))
        visible['pages'][url]['body_diagnostics']=after
        pages.append({'url':url,'chars':len(page['content']),
            'body_sha256':hashlib.sha256(page['content'].encode()).hexdigest(),
            'baseline_state':before['state'],'candidate_state':after['state'],
            'baseline_usable':before['usable_text'],'candidate_usable':after['usable_text']})
    url_changes=[]
    for field in ('resolution_criteria','fine_print','background'):
        text=original['request'].get(field,'')
        old=old_financial['source_urls'](text);new=source_urls(text)
        if old!=new:url_changes.append({'field':field,'baseline':old,'candidate':new})
    rule=next(u for u in source_urls(original['request']['resolution_criteria']) if 'sectionNum=' in u)
    article=next(hit['url'] for s in original['exa_searches'] for hit in s['results'] if '/2026/09/18/' in hit['url'] and not hit['url'].endswith('.pdf'))
    args={'urls':['https://www.gov.ca.gov/','https://www.gov.ca.gov/category/proclamations/',
                  article.rstrip('/'),rule.replace('%20','+')],
          'queries':[{'query':'Newsom AI legislature','need_ids':[original['plan'][0]['id']]}],
          'rescue_failed':False}
    with tempfile.TemporaryDirectory() as folder:
        task=RetrievalTask(Path(folder),original['request']);task.bundle=copy.deepcopy(original)
        task.bundle['result']=None  # Disposable simulation only; no parent task is resumed.
        # Reload immutable rule links through the candidate parser.
        from ForecastAgent.tavily_research import canonical_url
        for field in ('resolution_criteria','fine_print','background'):
            for url in source_urls(original['request'].get(field,'')):
                task.bundle['source_leads'][canonical_url(url)]={'url':url,'origin':'question_'+field}
        tools=active_tools(task,COLLECTION_TOOLS)
        try:
            old_contracts['validate'](task,'read_sources',args,tools)
            old_error=None
        except ValueError as exc:
            old_error=getattr(exc,'details',{'message':str(exc)})
        validate(task,'read_sources',args,tools)
        # Test routing with a synthetic readable body. It is not new evidence.
        fake={'content':'Synthetic contract-routing witness. '*10,
              'documents':[],'sha256':'test','temporal_status':'live_capture'}
        with patch('ForecastAgent.runtime.retrieval.cached_page',return_value=None), \
             patch('ForecastAgent.runtime.retrieval.save_cached_page'), \
             patch('ForecastAgent.runtime.retrieval.fetch_public_page',return_value=fake) as http:
            response=task.execute('read_sources',args,'')
        batch={'baseline_error':old_error,'candidate_reads':[
            {'url':r['url'],'ok':r['ok'],'contract_error':r.get('contract_error')}
            for r in response['reads']], 'simulated_http_calls':http.call_count,
            'new_real_captures':0,'synthetic_body_used_for_routing_only':True}
    before_packet=packet_for(original);after_packet=packet_for(visible)
    return {'schema':'release-production-collection-replay-v1','question_id':original['request']['id'],
        'baseline_tag':'v1.0.2','original_production_release':'v1.0.1',
        'input_sha256':hashlib.sha256(raw).hexdigest(),
        'original_file_unchanged':Path(bundle_path).read_bytes()==raw,
        'real_provider_calls':0,'forecast_submissions':0,'quota_resets':0,
        'url_decoding_changes':url_changes,'per_source_body_checks':pages,'mixed_batch':batch,
        'analysis_handoff_source_counts':{'baseline':len(before_packet['sources']),
                                        'candidate':len(after_packet['sources'])},
        'prioritized_unread_candidates':unread_candidates(original),
        'limitations':['Offline replay establishes software repair, not forecast accuracy.',
                      'Synthetic HTTP bodies are not substantive capture improvements.',
                      'Routing scores and date hints do not verify source relevance or publication.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--baseline-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();report=replay(args.bundle,args.baseline_root)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'question_id':report['question_id'],'pages':len(report['per_source_body_checks']),
        'analysis_sources':report['analysis_handoff_source_counts'],
        'simulated_http_calls':report['mixed_batch']['simulated_http_calls'],
        'real_provider_calls':0,'original_file_unchanged':report['original_file_unchanged']}))


if __name__=='__main__':main()
