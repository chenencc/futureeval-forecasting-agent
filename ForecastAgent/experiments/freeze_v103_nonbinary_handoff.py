"""Freeze the existing twenty nonbinary cases and saved bodies before inference."""
import gzip
import hashlib
import json
from pathlib import Path

from ForecastAgent.acquisition import v103_nonbinary_handoff_trial as trial
from ForecastAgent.acquisition import nonbinary_handoff_inputs as shared
from ForecastAgent.acquisition.pipeline import reject_outcomes, verify_baseline
from ForecastAgent.analysis import mercury_nonbinary_trial as typed
from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.releases.v1_0_3 import verify_release


def main():
    verify_release()
    verify_baseline()
    if any(p.exists() for p in (trial.COHORT, trial.FIXTURE, trial.LABELS, trial.PROTOCOL)):
        raise ValueError('Frozen inputs already exist; never replace their identity')
    inventory = load(Path(r'D:\metaculus\.tmp\v103-nonbinary-inventory.json'))
    core = load(typed.INPUT)
    if len(core) != 20 or len(inventory['selected']) != 20 or inventory['missing_ids']:
        raise ValueError('All twenty preselected questions must have saved bodies')
    cases, bundles = [], {}
    for position, metadata in enumerate(core):
        qid = str(metadata['post_id'])
        source = inventory['selected'][qid]
        raw = Path(source['local_blob']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source['original_sha256']:
            raise ValueError('Content-addressed original capture changed')
        original = json.loads(raw)
        projected = {k: original[k] for k in ('request','pages','excerpts','gaps','supplement_lineage') if k in original}
        projected.setdefault('excerpts', [])
        projected.setdefault('gaps', original.get('result',{}).get('gaps',[]))
        projected['request'] = shared.request(original['request'], metadata)
        reject_outcomes(metadata)
        spec = shared.spec(projected['request'], metadata)
        bundles[qid] = projected
        cases.append({'question_id': qid, 'type': metadata['type'], 'phase': 'nonbinary',
            'question': metadata['title'], 'metadata': metadata,
            'original_run_id': source['run_id'], 'original_artifact_id': source['artifact_id'],
            'original_artifact_path': source['artifact_path'], 'original_file_sha256': source['original_sha256'],
            'original_request_sha256': digest(original['request']),
            'shared_normalized_request_sha256': digest(projected['request']),
            'projected_bundle_sha256': digest(projected), 'distribution_spec_sha256': digest(spec),
            'registry_sha256': digest(typed.questions(spec)), 'source_count': source['source_count'],
            'document_formats': source['document_formats'],
            'official_metadata_available': spec['official_metadata_available'],
            'route_order': ['release','context'] if position % 2 == 0 else ['context','release']})
    payload = json.dumps({'schema': 'v103-typed-saved-handoff-v1','bundles': bundles}, ensure_ascii=False,sort_keys=True).encode()
    trial.FIXTURE.write_bytes(gzip.compress(payload,mtime=0))
    screening = trial.ROOT / 'ForecastAgent/experiments/v103_nonbinary_handoff_screening.json'
    save(screening, {'schema': 'v103-typed-source-screen-v1', 'model_calls': 0,
        'rows': [{k:v for k,v in r.items() if k!='local_blob'} for r in inventory['screen']]})
    cohort = {'schema': 'v103-typed-handoff-cohort-v1','base_release':'v1.0.3',
        'base_commit':'a2a3dba9ac28fc6d768536de705dcbfec5cda4fc',
        'fixture_sha256':trial.sha(trial.FIXTURE),'core_metadata_sha256_lf':trial.source_sha(typed.INPUT),
        'screening_sha256_lf':trial.source_sha(screening),'cases':cases,
        'selection_policy':'The existing core twenty nonbinary questions, not selected by outcome or new predictions.',
        'snapshot_policy':'Most recent imported saved bundle that passes exact wording, scale consistency and both-arm coordinate preservation. Rejected versions retained in zero-provider screening. Different archived capture versions across cases; identical source projection within each pair.',
        'metadata_policy':'Preserve original background and exact rules. Fill only absent archived scale/grid fields using the frozen core input after field-level consistency checks. All shared metadata and registry hashes frozen before calls.',
        'date_policy':'Three dates lack authoritative API scale/question identity; reuse prior fixed research UTC bins. Research distributions only, never claim submission validity.',
        'evaluation_warning':'Retrospective evidence and model knowledge can expose outcomes. This is an evidence-delivery engineering comparison, not a historical predictive backtest. One stochastic replicate per arm.',
        'promotion_allowed':False,'submitted':False}
    save(trial.COHORT,cohort)
    # Labels are opened after selection and original material have been fixed.
    label_path = Path(r'E:\metaculus_data\datasets\nonbinary-resolved-20261002\core-20-labels.json')
    labels = load(label_path)
    by_id = {str(r['post_id']):r for r in labels}
    if len(by_id)!=20 or set(by_id)!=set(bundles):
        raise ValueError('Frozen input/label identities differ')
    save(trial.LABELS, {'schema':'v103-typed-frozen-labels-v1','source_file_sha256':trial.sha(label_path),'records':by_id})
    trial.freeze_protocol()
    print(json.dumps({'questions_frozen':len(cases),'fixture_bytes':trial.FIXTURE.stat().st_size,
                     'fixture_sha256':trial.sha(trial.FIXTURE),'model_calls':0}))


if __name__ == '__main__':
    main()
