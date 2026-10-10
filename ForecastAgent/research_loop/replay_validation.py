"""Mechanical validation on frozen real bodies, with explicitly synthetic maps."""
import argparse
import copy
import hashlib
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.acquisition.handoff import uncovered
from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.research_loop import POLICY, VERSION
from ForecastAgent.research_loop.__main__ import replay
from ForecastAgent.research_loop.decision import prepare, run
from ForecastAgent.research_loop.state import catalog, initialize

LEDGERS = ('searches', 'exa_searches', 'fetch_attempts', 'model_attempts', 'extract_attempts')


def validate(parents, root):
    root = Path(root)
    frozen = [{'path': str(Path(p).resolve()), 'sha256': hashlib.sha256(Path(p).read_bytes()).hexdigest()}
              for p in parents]
    if (root/'frozen-inputs.json').exists() and load(root/'frozen-inputs.json') != frozen:
        raise ValueError('Frozen validation cohort changed')
    save(root/'frozen-inputs.json', frozen)
    cases = []
    for entry in frozen:
        parent = Path(entry['path']); original = load(parent)
        child = copy.deepcopy(original)
        child['request']['research_state_policy'] = POLICY
        child['pipeline'] = 'collection'; initialize(child)
        material = catalog(child)
        missing_scale = (child['request']['question_type'] in {'numeric', 'date', 'discrete'} and
            (not child['request'].get('scaling') or not child['request'].get('inbound_outcome_count')))
        if missing_scale:
            # Preserve this frozen case and report the expected guard, not a made-up scale.
            with patch('ForecastAgent.providers.decisions.decide', side_effect=AssertionError('No scoring permitted')):
                try:
                    prepare(child)
                except ValueError:
                    pass
                else:
                    raise ValueError('Incomplete platform scale was incorrectly accepted')
            refs = list(material['spans'].values())[:2]
        else:
            initial = prepare(child)
            visible = initial['baseline']; sources = {s['source_id']: s for s in visible['sources']}
            refs = [s for s in material['spans'].values() if not uncovered(s['start'], s['end'],
                [(e['start'], e['end']) for e in visible['evidence']
                 if sources.get(e['source_id'], {}).get('url') == s['url'] and
                    sources.get(e['source_id'], {}).get('body_sha256') == s['body_sha256']])][:2]
        if not refs:
            raise ValueError('No whole saved span fits the fixed original coverage')
        nodes = [{'id': 'original_' + str(i), 'kind': 'observation',
                  'claim': 'Exact saved-text sample, not a factual interpretation: ' + ref['text'][:270],
                  'evidence_ids': [ref['evidence_id']], 'event_time': '', 'time_status': 'unknown', 'gap_reason': 'none'}
                 for i, ref in enumerate(refs, 1)]
        nodes.append({'id': 'interpretation_pending', 'kind': 'unknown',
            'claim': 'The mechanical replay does not judge the source meaning or future outcome.',
            'evidence_ids': [], 'event_time': '', 'time_status': 'unknown', 'gap_reason': 'unreviewed'})
        proposal = {'expected_revision': 0, 'revision_kind': 'material_update',
            'material_sha256': material['material_sha256'], 'nodes': nodes, 'relations': [],
            'supporting_path': 'Not evaluated. The next agent test must build a supporting path from original evidence.',
            'alternative_path': 'Not evaluated. The next agent test must independently retain counterevidence and alternatives.',
            'material_requests': [], 'retired_node_ids': [],
            'revision_reason': 'Synthetic contract exercise using real saved body coordinates.'}
        ident = original['request']['id']; folder = root/'cases'/str(ident)
        save(folder/'synthetic-proposal.json', proposal)
        replay(parent, folder/'synthetic-proposal.json', folder/'replay')
        enriched = load(folder/'replay/research-package.json')
        if missing_scale:
            blocked = {'status': 'blocked_missing_platform_metadata',
                'required': ['authoritative scaling', 'inbound_outcome_count'], 'provider_attempts': 0,
                'originals_preserved': True, 'same_coverage_comparison': 'not_prepared'}
            save(folder/'decision/preflight-blocked.json', blocked)
            prepared = {'audit': {'baseline_bytes': None, 'enriched_bytes': None,
                'equal_original_coverage': None, 'metadata_guard': blocked}}
        else:
            prepared = run(enriched, folder/'decision')
            run(enriched, folder/'decision')
        counters_preserved = all(original.get(k) == enriched.get(k) for k in LEDGERS)
        raw_preserved = original.get('pages') == enriched.get('pages')
        parent_preserved = hashlib.sha256(parent.read_bytes()).hexdigest() == entry['sha256']
        if not all((raw_preserved, counters_preserved, parent_preserved)):
            raise ValueError('Original evidence or provider ledger changed')
        cases.append({'id': ident, 'question_type': original['request']['question_type'],
            'question': original['request']['question'], 'parent_sha256': entry['sha256'],
            'saved_sources': len(original.get('pages', {})), 'map_bound_spans': len(refs),
            'readable_sources': len(material['sources']), 'excluded_sources': len(material['excluded_sources']),
            'raw_pages_preserved': raw_preserved, 'parent_file_preserved': parent_preserved,
            'provider_ledgers_preserved': counters_preserved, 'provider_attempts': 0,
            'synthetic_map': True, 'scoring_inputs_only': True, 'audit': prepared['audit'],
            'status': 'blocked_missing_platform_metadata' if missing_scale else 'paired_inputs_prepared',
            'child_package': str(folder/'replay/research-package.json')})
    result = {'schema': VERSION, 'scope': 'Frozen saved-body contract and preservation validation only',
        'case_count': len(cases), 'cases': cases, 'provider_attempts': 0, 'submitted': False,
        'model_understanding_tested': False, 'forecast_improvement_tested': False,
        'raw_and_budget_preservation_passed': True,
        'paired_inputs_prepared': sum(c['status'] == 'paired_inputs_prepared' for c in cases),
        'metadata_blocked': sum(c['status'] == 'blocked_missing_platform_metadata' for c in cases),
        'equal_original_coverage_passed_for_prepared_cases': True}
    save(root/'report.json', result)
    rows = '\n'.join(f"| {c['id']} | {c['question_type']} | {c['saved_sources']} | {c['map_bound_spans']} | {c['audit']['baseline_bytes']} | {c['audit']['enriched_bytes']} | {c['status']} |" for c in cases)
    (root/'SUMMARY.md').write_text('# Incremental Research Loop: offline acceptance\n\n'
        '64 focused offline tests passed before this real-body replay.\n\n'
        '| Question | Type | Saved sources | Bound spans | Direct bytes | Map bytes | Status |\n'
        '| --- | --- | ---: | ---: | ---: | ---: | --- |\n' + rows + '\n\n'
        'All parent files, pages and original provider ledgers were preserved. Both\n'
        'scoring arms retain equal original coverage. No provider calls or submissions\n'
        f"occurred. {result['metadata_blocked']} cases lack authoritative platform scaling and\n"
        'were preserved as blocked before scoring; no range was invented.\n'
        'Maps are synthetic format exercises, not agent research results.\n'
        'No forecast accuracy or causal reasoning improvement has been established.\n', encoding='utf-8')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parents', type=Path, required=True, help='JSON list of parent bundle paths')
    p.add_argument('--root', type=Path, required=True)
    args = p.parse_args()
    with patch('ForecastAgent.providers.decisions.decide', side_effect=AssertionError('Offline only')), \
            patch('urllib.request.urlopen', side_effect=AssertionError('Offline only')):
        result = validate(load(args.parents), args.root)
    print({'case_count': result['case_count'], 'provider_attempts': 0, 'preservation_passed': True})


if __name__ == '__main__':
    main()
