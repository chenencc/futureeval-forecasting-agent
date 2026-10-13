"""Publish small SHA-256 catalogs independently of worker recovery artifacts."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re


SCHEMA = 'forecast-local-catalog-v1'
PREFIX = 'forecast-local-catalog-v1-'
STREAMS = ('market-worker', 'market-monitor', 'official-worker', 'official-monitor')


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def summary(root):
    """Keep forecast heads and missing states; omit raw prompts and explanations."""
    result = {}
    for filename in ('report.json', 'evaluation.json', 'monitor-health.json'):
        path = root / filename
        if path.is_file() and path.stat().st_size < 256 * 1024:
            result[filename] = json.loads(path.read_text(encoding='utf-8'))
    for filename in ('state.json', 'campaign.json'):
        path = root / filename
        if not path.is_file():
            continue
        state = json.loads(path.read_text(encoding='utf-8'))
        rows = []
        for ident, task in state.get('tasks', {}).items():
            row = {key: task[key] for key in ('status', 'stage', 'executions', 'enrolled_at',
                'last_attempt_at', 'retry_at', 'error_type', 'evidence_status', 'analysis_stage') if key in task}
            row['id'] = ident
            market = task.get('market', {})
            if market:
                row['market'] = {key: market[key] for key in ('key', 'venue', 'venue_id', 'question', 'url',
                    'rules', 'rules_sha256', 'created_at', 'close_at', 'resolution_hint', 'terminal_result') if key in market}
            forecast = task.get('forecast') or {}
            if forecast:
                row['forecast'] = {key: forecast[key] for key in ('anchor_at', 'sealed_at', 'status',
                    'timely', 'baseline', 'pair_audit') if key in forecast}
                row['forecast']['anchor'] = {key: forecast.get('anchor', {})[key] for key in (
                    'received_at', 'status', 'yes_bid', 'yes_ask', 'midpoint', 'spread', 'raw_book_sha256') if key in forecast.get('anchor', {})}
                row['forecast']['arms'] = {arm: {key: value[key] for key in ('status', 'price_means',
                    'eventual_probability_yes', 'map_delivered', 'map_status', 'response_sha256') if key in value}
                    for arm, value in forecast.get('arms', {}).items()}
            rows.append(row)
        result[filename] = {'identity': state.get('identity'), 'schema': state.get('schema'),
                            'tasks': rows, 'task_count': len(rows)}
    return result


def export(sources, output, stream, *, run_id=None, repo=None):
    """Catalog exact artifact-relative paths, including hidden original records."""
    if stream not in STREAMS:
        raise ValueError('Unknown local export stream')
    run_id = str(run_id or os.environ.get('GITHUB_RUN_ID', ''))
    repo = repo or os.environ.get('GITHUB_REPOSITORY', '')
    if not run_id.isdecimal() or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Exact Actions run and repository identity required')
    output = Path(output).resolve()
    records, summaries = [], {}
    for artifact_name, folder, patterns in sources:
        root = Path(folder).resolve()
        if output == root or output.is_relative_to(root) or root.is_relative_to(output):
            raise ValueError('Export and source directories must be disjoint')
        if not root.is_dir() or not artifact_name or '/' in artifact_name or '\\' in artifact_name:
            raise ValueError('Invalid catalog source')
        selected = set()
        for pattern in patterns or ('**/*',):
            selected.update(root.glob(pattern))
        files = []
        for path in sorted(selected):
            if path.is_symlink():
                raise ValueError('Symlinks cannot be published as evidence')
            if path.is_file():
                files.append({'path': path.relative_to(root).as_posix(), 'sha256': sha(path),
                              'size_bytes': path.stat().st_size})
        if not files:
            continue
        records.append({'artifact_name': artifact_name, 'run_id': run_id, 'files': files})
        summaries[artifact_name] = summary(root)
    if not records:
        raise ValueError('No local synchronization material to publish')
    output.mkdir(parents=True, exist_ok=True)
    catalog = {'schema': SCHEMA, 'repo': repo, 'run_id': run_id, 'stream': stream,
               'sources': records, 'all_files_required_for_complete_import': True}
    # Formatting and generation time do not affect catalog content identity.
    (output / 'sync-manifest.json').write_text(json.dumps(catalog, sort_keys=True, separators=(',', ':'),
                                                         ensure_ascii=False), encoding='utf-8')
    (output / 'summary.json').write_text(json.dumps({'schema': 'forecast-local-summary-v1',
        'run_id': run_id, 'stream': stream, 'generated_at': datetime.now(timezone.utc).isoformat(),
        'sources': summaries}, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    return {'artifact_name': PREFIX + stream, 'files': sum(len(source['files']) for source in records),
            'catalog_bytes': (output / 'sync-manifest.json').stat().st_size,
            'summary_bytes': (output / 'summary.json').stat().st_size,
            'provider_calls': 0, 'budget_reset': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', action='append', required=True, help='ARTIFACT_NAME=DIRECTORY')
    parser.add_argument('--pattern', action='append', help='Optional glob applied to each source root')
    parser.add_argument('--stream', choices=STREAMS, required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    sources = []
    for value in args.source:
        name, separator, folder = value.partition('=')
        if not separator:
            parser.error('--source requires ARTIFACT_NAME=DIRECTORY')
        sources.append((name, folder, args.pattern))
    print(json.dumps(export(sources, args.output, args.stream)))


if __name__ == '__main__':
    main()
