"""One-question nonbinary diagnostic; never reopen the competition queue."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ForecastAgent.competition.queue import digest, load, save, questions
from ForecastAgent.runtime.task_lock import task_lock


def blind_input(post, ident):
    matches = [q for q in questions(post) if str(q['id']) == str(ident)]
    if len(matches) != 1:
        raise ValueError('Selected post/question identity mismatch')
    question = matches[0]
    if question.get('type') not in ('multiple_choice', 'numeric', 'discrete', 'date'):
        raise ValueError('A supported nonbinary question is required')
    criteria = question.get('resolution_criteria') or post.get('resolution_criteria')
    if not isinstance(criteria, str) or not criteria.strip():
        raise ValueError('Full resolution criteria missing; refuse title-only inference')
    options = question.get('options')
    from ForecastAgent.analysis.categorical import options_from
    request = {'id': str(ident), 'question': question.get('title') or post['title'],
        'question_type': question['type'], 'options': options, 'resolution_criteria': criteria,
        'fine_print': question.get('fine_print') or post.get('fine_print') or '',
        'background': question.get('description') or post.get('description') or '',
        'scheduled_resolve_time': question.get('scheduled_resolve_time'),
        'mode': 'live', 'pipeline': 'collection', 'acquisition_profile': 'collection_v3'}
    if question['type'] == 'multiple_choice':
        options_from(request)
    else:
        from ForecastAgent.analysis.distributions import range_metadata
        request.update({key: question.get(key) for key in
            ('scaling', 'inbound_outcome_count', 'open_lower_bound', 'open_upper_bound', 'unit')})
        range_metadata(request)
    return request


def run(root, post_id, ident, collector_root=None):
    from ForecastAgent.monitor_tournament import get_json, API_ROOT
    from ForecastAgent.agent import run_research
    def analyze(bundle, output):
        kind = load(bundle)['request']['question_type']
        if kind == 'multiple_choice':
            from ForecastAgent.analysis.categorical import run as execute
        else:
            from ForecastAgent.analysis.range_forecast import run as execute
        return execute(bundle, output)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if not str(post_id).isdecimal() or not str(ident).isdecimal():
        raise ValueError('Numeric post/question IDs required')
    with task_lock(root):
        try:
            manifest = root / 'manifest.json'
            if manifest.exists():
                prior = load(manifest)
                if prior['post_id'] != str(post_id) or prior['question_id'] != str(ident):
                    raise ValueError('Frozen diagnostic identity changed')
                request = load(root / 'question-input.json')
                if digest(request) != prior['input_sha256']:
                    raise ValueError('Frozen diagnostic request hash mismatch')
                if (root / 'analysis-input.json').exists():
                    result = analyze(root / 'analysis-input.json', root / 'analysis')
                    return result
            else:
                post = get_json(f'{API_ROOT}{post_id}/?include_descriptions=true&with_cp=false', os.environ['METACULUS_TOKEN'])
                matches = [q for q in questions(post) if str(q['id']) == str(ident)]
                save(root / 'input-inspection.json', {'post_id': post.get('id'),
                    'question_id': str(ident), 'post_fields': sorted(post),
                    'question_fields': sorted(matches[0]) if matches else [],
                    'question_content': {key: matches[0].get(key) for key in
                        ('title', 'type', 'status', 'description', 'resolution_criteria', 'fine_print', 'options')} if matches else {},
                    'post_content': {key: post.get(key) for key in ('description', 'resolution_criteria', 'fine_print')},
                    'no_outcome_or_community_input': True})
                if matches and not (matches[0].get('resolution_criteria') or post.get('resolution_criteria')):
                    from ForecastAgent.readers.metaculus_rules import read_rules
                    rules = read_rules(root, post_id, matches[0].get('title') or post['title'])
                    matches[0]['resolution_criteria'] = rules
                request = blind_input(post, ident)
                save(root / 'question-input.json', request)
                save(manifest, {'schema': 'nonbinary-debug-v1', 'post_id': str(post_id), 'question_id': str(ident),
                    'input_sha256': digest(request), 'created_at_utc': datetime.now(timezone.utc).isoformat(),
                    'platform_status_at_start': next(q['status'] for q in questions(post) if str(q['id']) == str(ident)),
                    'no_outcome_or_community_input': True, 'no_forecasts_submitted': True})
            existing = Path(collector_root) / 'retrieval' / str(ident) / 'bundle.json' if collector_root else None
            if existing and existing.exists():
                raise ValueError('Existing collector budget found; adopt it through a reviewed bridge instead of starting a duplicate task')
            folder = root / 'retrieval' / str(ident)
            bundle = run_research(request, folder)
            if not bundle.get('result') or bundle['result'].get('incomplete'):
                raise RuntimeError('Collection incomplete; preserve original search/model reservations')
            from ForecastAgent.supplement.stage import run as supplement, analysis_overlay
            import zipfile
            archive = root / 'supplement-input.zip'
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as saved:
                saved.writestr('campaign.json', json.dumps({'tasks': {str(ident): {'status': 'acquired'}}}))
                for path in sorted(folder.rglob('*')):
                    if path.is_file():
                        saved.write(path, f'tasks/{ident}/' + path.relative_to(folder).as_posix())
            supplement(archive, root / 'supplement', [str(ident)], network=True)
            overlay = analysis_overlay(bundle, root / 'supplement', str(ident))
            adopted = root / 'analysis-input.json'
            if adopted.exists() and digest(load(adopted)) != digest(overlay):
                raise ValueError('Frozen supplemental analysis input changed')
            save(adopted, overlay)
            result = analyze(adopted, root / 'analysis')
            save(root / 'summary.json', {'status': result['status'], 'question_id': str(ident),
                'result': result, 'readable_pages': sum(bool(p.get('content', '').strip()) for p in overlay.get('pages', {}).values()),
                'collection_gaps': bundle.get('gaps', []), 'no_forecasts_submitted': True})
            return result
        except Exception as exc:
            save(root / 'failure.json', {'status': 'failed', 'error': f'{type(exc).__name__}: {exc}',
                'question_id': str(ident), 'preserved_state': True, 'no_forecasts_submitted': True})
            raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--post-id', required=True)
    parser.add_argument('--question-id', required=True)
    parser.add_argument('--collector-root')
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.post_id, args.question_id, args.collector_root), indent=2))
