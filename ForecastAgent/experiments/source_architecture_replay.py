"""Audit saved pilot bodies and routing without network or model requests."""
import argparse
from pathlib import Path
from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.supplement import enhanced, frontier
from ForecastAgent.evidence.source_identity import inventory


def run(inputs, output):
    inputs, output = Path(inputs), Path(output)
    rows = []
    for folder in sorted(inputs.glob('solid-collection-case-*')):
        original = load(folder/'acquisition/bundle.json')
        final = load(folder/'intelligence-bundle.json')
        prior = load(folder/'supplement/state.json')
        old_quality = load(folder/'quality.json')
        before = digest(final)
        old_counts = enhanced.usage(final, [prior], {'attempts':[]})
        plan = enhanced.plan(final)
        initial_plan = enhanced.plan(original)
        routing = frontier.admit(initial_plan['sources'], original['request'], pages=original['pages'])
        root = output/'tasks'/str(original['request']['id'])
        overlay = enhanced.run(final, root/'offline', prior=[prior], network=False,
            caps={'tavily':6,'exa':2,'http':64,'browser':12}, max_link_depth=2)
        state = load(root/'offline/state.json') if (root/'offline/state.json').exists() else {'attempts':[]}
        if state['attempts'] or digest(final) != before:
            raise ValueError('Offline replay changed source inputs or made reservations')
        after_counts = enhanced.usage(final, [prior], state)
        if after_counts != old_counts:
            raise ValueError('Cumulative provider accounting changed')
        relevant_data = [{'url':url,'coverage':d['coverage_axes']} for url,d in plan['body_assessments'].items()
                         if d['coverage_status']=='candidate_data']
        primary = [s['url'] for s in plan['sources'] if s['rule_primary']]
        readable_primary = [url for url in primary if url in final['pages'] and
                            plan['body_assessments'][url]['eligible_for_evidence']]
        row = {'id':str(original['request']['id']), 'source_input_sha256':before,
            'original_inputs_unchanged':True,'new_http_requests':0,'new_model_requests':0,
            'cumulative_usage_unchanged':True,'cumulative_usage':after_counts,
            'identity_inventory':inventory(final['pages']),
            'previous_unread_rule_urls':old_quality['rule_primary_unreadable_or_missing'],
            'normalized_rule_urls':primary,'readable_rule_urls':readable_primary,
            'data_candidates':relevant_data,'initial_frontier':routing,
            'all_captures_retained':len(final['pages'])==inventory(final['pages'])['capture_count'],
            'semantic_recall_verified':False}
        save(root/'review.json',row); rows.append(row)
    if len(rows)!=5:raise ValueError('All five saved cases required')
    report={'protocol':'source-architecture-offline-replay-v1','parent_run':inputs.name.rsplit('-',1)[-1],
        'rows':rows,'new_provider_requests':0,'new_model_requests':0,
        'all_originals_preserved':True,'labels_used':False,'forecast_submissions':0,
        'limitations':'Offline routing diagnostic; does not prove how future live retrieval will perform.'}
    save(output/'report.json',report)
    for row in rows:
        print(row['id'],'captures',row['identity_inventory']['capture_count'],
              'unique bodies',row['identity_inventory']['unique_body_count'],
              'accepted routes',len(row['initial_frontier']['accepted']),
              'deferred',len(row['initial_frontier']['deferred']),
              'data candidates',len(row['data_candidates']),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--inputs',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();run(args.inputs,args.output)
