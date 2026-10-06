"""Freeze ten additional saved packages before any paired decision request."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path

from ForecastAgent.acquisition import v103_handoff_extension as extension
from ForecastAgent.acquisition.pipeline import reject_outcomes, verify_baseline
from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.releases.v1_0_3 import verify_release

SELECTED = [
    ('43688', 'monetary_policy'), ('43682', 'court_decision'),
    ('43171', 'sports_selection'), ('42540', 'space_observation'),
    ('43091', 'ai_benchmark'), ('41206', 'securities_filing'),
    ('43461', 'public_health'), ('43824', 'market_time_series'),
    ('43496', 'election_timing'), ('39992', 'entertainment_participation'),
]


def freeze_labels():
    if extension.LABELS.exists():
        raise ValueError('The evaluation labels are already frozen')
    cohort = load(extension.COHORT)
    ids = [r['question_id'] for r in cohort['cases']]
    if ids != [qid for qid, _ in SELECTED]:
        raise ValueError('Label projection does not match the frozen selection')
    source = Path(r'D:\metaculus\snapshots\forecastbench-history\metaculus_2026_test\evaluation_labels_135.json')
    rows = load(source)
    by_id = {str(r['id']): r for r in rows}
    if len(by_id) != len(rows):
        raise ValueError('Duplicate label identities')
    records = {}
    for qid in ids:
        row = by_id[qid]
        if type(row['resolved_to']) not in (int, float) or row['resolved_to'] not in (0, 1):
            raise ValueError('A binary outcome is required')
        records[qid] = {'resolution': row['resolved_to'], 'snapshot_date': row['snapshot_date'],
                        'source': 'Archived ForecastBench Metaculus 2026 evaluation dataset',
                        'source_row_sha256': digest(row),
                        'question_url': 'https://www.metaculus.com/questions/' + qid}
    save(extension.LABELS, {'schema': 'v103-extension-evaluation-labels-v1',
                            'source_file_sha256': extension.trial.sha(source), 'records': records})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--labels-only', action='store_true')
    args = parser.parse_args()
    if args.labels_only:
        freeze_labels()
        return
    verify_release()
    verify_baseline()
    if any(p.exists() for p in (extension.COHORT, extension.FIXTURE, extension.LABELS, extension.PROTOCOL)):
        raise ValueError('Extension inputs already exist; never replace a frozen cohort')
    inventory = load(Path(r'D:\metaculus\.tmp\v103-extension-inventory.json'))
    by_id = {r['question_id']: r for r in inventory}
    screen = load(Path(r'D:\metaculus\.tmp\v103-extension-compatible-screen.json'))
    replacement = next(r for r in screen['rows'] if r['passed'])
    by_id[replacement['question_id']] = replacement
    old = load(extension.ROOT / 'ForecastAgent/experiments/v103_handoff_cohort.json')
    old_ids = {r['question_id'] for r in old['cases']}
    bundles, cases = {}, []
    for position, (qid, family) in enumerate(SELECTED):
        if qid in old_ids:
            raise ValueError('Question was already in the fifteen-case pilot')
        source = by_id[qid]
        raw = Path(source['local_blob']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source['source_sha256']:
            raise ValueError('Original imported blob does not match its content address')
        original = json.loads(raw)
        reject_outcomes(original['request'])
        if str(original['request']['id']) != qid:
            raise ValueError('Wrong source question')
        projected = {k: original[k] for k in ('request', 'pages', 'excerpts', 'gaps', 'supplement_lineage') if k in original}
        projected.setdefault('gaps', original.get('result', {}).get('gaps', []))
        projected.setdefault('excerpts', [])
        bundles[qid] = projected
        cases.append({'question_id': qid, 'phase': 'extension', 'family': family,
                      'question': original['request']['question'],
                      'original_run_id': source['run_id'], 'original_artifact_id': source['artifact_id'],
                      'original_artifact_path': source['artifact_path'],
                      'original_file_sha256': source['source_sha256'],
                      'projected_bundle_sha256': digest(projected),
                      'source_count': source['source_count'], 'document_formats': source['document_formats'],
                      'route_order': ['release', 'context'] if position % 2 == 0 else ['context', 'release']})
    payload = json.dumps({'schema': 'v103-saved-handoff-input-v1', 'bundles': bundles}, ensure_ascii=False, sort_keys=True).encode()
    extension.FIXTURE.write_bytes(gzip.compress(payload, mtime=0))
    cohort = {'schema': 'v103-handoff-extension-cohort-v1', 'base_release': old['base_release'],
              'base_commit': old['base_commit'], 'fixture_sha256': extension.trial.sha(extension.FIXTURE),
              'cases': cases,
              'selection_policy': 'Ten purposively selected domains and saved document formats, outside the earlier fifteen-case delivery trial. Selection uses question metadata and material availability, before opening labels or new model results. Some questions appeared in older research; this is not a pristine holdout.',
              'snapshot_policy': 'Most recent imported saved raw package with original bodies and full criteria. The preselected sports case uses a same-run raw package because the expanded intelligence package failed the offline original URL/coordinate audit. Both arms receive the same compatible original projection; the rejected package and zero-provider screening are recorded separately. No new retrieval or supplement.',
              'pre_provider_screening': {'initial_cohort_sha256_lf': extension.trial.source_sha(extension.ROOT / 'ForecastAgent/experiments/v103_extension_screening/v103_handoff_extension_cohort.json'),
                                         'initial_model_calls': 0,
                                         'same_question_source_replacement': replacement['question_id'],
                                         'reason': 'Original URL index could not be resolved by the frozen saved reader for either arm.'},
              'source_policy': old['source_policy'], 'evaluation_warning': old['evaluation_warning'],
              'promotion_allowed': False}
    save(extension.COHORT, cohort)
    # Evaluation labels are opened only after the material selection is fixed.
    freeze_labels()
    print(json.dumps({'questions_frozen': len(cases), 'fixture_bytes': extension.FIXTURE.stat().st_size,
                      'fixture_sha256': cohort['fixture_sha256']}))


if __name__ == '__main__':
    main()
