"""Freeze saved acquisition projections before developing the packing candidate."""

import gzip
import hashlib
import json
from pathlib import Path

from ForecastAgent.acquisition.pipeline import reject_outcomes, verify_baseline
from ForecastAgent.analysis.pilot import digest

ROOT = Path(__file__).resolve().parents[2]
REGRESSION = ['36871', '43494', '43501', '43991', '44801']
EXPANSION = ['44925', '40938', '43703', '43709', '44247', '43844', '43992', '41090', '43615', '44448']


def main():
    verify_baseline()
    prior = ROOT.parent / 'dev_acquisition_v2'
    regression_protocol = json.loads((prior / 'ForecastAgent/experiments/materials_handoff_score_protocol.json').read_text(encoding='utf-8'))
    source_root = Path(r'E:\metaculus_data\reports\full-chain-ten-37393064392')
    bundles, cases = {}, []
    for phase, ids in [('regression', REGRESSION), ('expansion', EXPANSION)]:
        for position, qid in enumerate(ids):
            source = (prior / f'snapshots/intelligent-frontier-review-37316638264/{qid}/candidate/bundle.json'
                      if phase == 'regression' else source_root / qid / 'acquisition/package.json')
            raw = source.read_bytes()
            source_hash = hashlib.sha256(raw).hexdigest()
            if phase == 'regression' and source_hash != regression_protocol['bundles'][qid]:
                raise ValueError('Original regression snapshot changed')
            original = json.loads(raw)
            reject_outcomes(original['request'])
            if str(original['request']['id']) != qid:
                raise ValueError('Wrong source question')
            projected = {key: original[key] for key in ('request', 'pages', 'excerpts', 'gaps', 'supplement_lineage') if key in original}
            projected.setdefault('gaps', original.get('result', {}).get('gaps', []))
            projected.setdefault('excerpts', [])
            bundles[qid] = projected
            formats = sorted({str(doc.get('metadata', {}).get('format', 'unknown'))
                              for page in projected['pages'].values() for doc in page.get('documents', [])})
            cases.append({'question_id': qid, 'phase': phase, 'question': original['request']['question'],
                          'original_run_id': '37316638264' if phase == 'regression' else '37393064392',
                          'original_file_sha256': source_hash, 'projected_bundle_sha256': digest(projected),
                          'source_count': len(projected['pages']), 'document_formats': formats,
                          'route_order': ['release', 'context'] if position % 2 == 0 else ['context', 'release']})
    payload = json.dumps({'schema': 'v103-saved-handoff-input-v1', 'bundles': bundles}, ensure_ascii=False, sort_keys=True).encode()
    fixture = ROOT / 'ForecastAgent/fixtures/v103_handoff_saved.json.gz'
    fixture.write_bytes(gzip.compress(payload, mtime=0))
    manifest = {'schema': 'v103-handoff-cohort-v1', 'base_release': 'v1.0.3',
                'base_commit': 'a2a3dba9ac28fc6d768536de705dcbfec5cda4fc',
                'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
                'cases': cases, 'selection_policy': 'Fixed five development cases and ten different material families outside this repair set. The expansion cases were used in earlier experiments and are not pristine holdouts.',
                'source_policy': 'Exact saved-body projection; original files unchanged. No search, capture, supplement, labels or prior forecasts are supplied to the model.',
                'evaluation_warning': 'Retrospective engineering diagnostic. Current web material and model knowledge may contain outcomes.',
                'promotion_allowed': False}
    target = ROOT / 'ForecastAgent/experiments/v103_handoff_cohort.json'
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'questions': len(cases), 'compressed_bytes': fixture.stat().st_size, 'fixture_sha256': manifest['fixture_sha256']}))


if __name__ == '__main__':
    main()
