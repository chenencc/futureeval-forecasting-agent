"""Review saved acquisition inventories offline, without provider requests."""
import argparse
import hashlib
from pathlib import Path
from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.supplement import enhanced, need_ledger, material_review


def run(root, output):
    root, output = Path(root), Path(output)
    implementation = digest({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
        (Path(enhanced.__file__), Path(need_ledger.__file__), Path(material_review.__file__))})
    report = {'schema':'material_fit_offline_regression_v1', 'source_directory':str(root),
        'implementation_sha256':implementation, 'network_requests':0, 'model_requests':0,
        'forecast_submissions':0, 'cases':[],
        'scope':'Saved-body routing and honest handoff only; no new semantic model review or online quality claim.'}
    for case in sorted(p for p in root.glob('solid-collection-case-*') if p.is_dir()):
        original = load(case/'acquisition/bundle.json')
        saved = load(case/'intelligence-bundle.json')
        before = digest(saved)
        ledger = need_ledger.build(saved)
        folder = output/(case.name+'-'+implementation[:12])
        replay = enhanced.run(original, folder, network=False, max_link_depth=3,
            caps={'tavily':6,'exa':2,'http':64,'browser':12})
        attempts = load(folder/'state.json')['attempts']
        if attempts or before != digest(load(case/'intelligence-bundle.json')):
            raise ValueError('Offline replay changed input or attempted a provider request')
        report['cases'].append({'question_id':original['request']['id'],
            'saved_body_count':len(saved.get('pages',{})), 'original_bundle_sha256':before,
            'original_bundle_unchanged':True, 'offline_engine_attempts':len(attempts),
            'lexical_candidate_need_ids':[n['id'] for n in ledger['needs'] if n['candidates']],
            'target_material_need_ids':[n['id'] for n in ledger['needs'] if n['target_material_captured']],
            'termination':need_ledger.termination(ledger,reason='offline_review',search_available=False),
            'offline_engine_termination':replay['material_termination'],
            'needs':[{'id':n['id'],'family':n['family'],'acquisition_state':n['acquisition_state'],
                'candidate_urls':[c['url'] for c in n['candidates']]} for n in ledger['needs']]})
    if not report['cases']:
        raise ValueError('No complete saved acquisition cases found')
    save(output/'report.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=run(args.input,args.output)
    print('Reviewed',len(result['cases']),'saved cases; zero provider requests.')
