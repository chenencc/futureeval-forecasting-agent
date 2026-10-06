"""Archive one fixed Metaculus round using GET requests only."""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

HOST = 'https://www.metaculus.com'


def utc():
    return datetime.now(timezone.utc).isoformat()


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def validate_url(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.netloc != 'www.metaculus.com' or not parts.path.startswith(
            ('/api/posts/', '/api/projects/tournaments/')):
        raise ValueError('Unapproved archive GET URL')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Reader:
    def __init__(self, token):
        if not token:
            raise ValueError('METACULUS_TOKEN is required')
        self.token = token.removeprefix('Token ').strip()
        self.records = []

    def read(self, url, target):
        validate_url(url)
        request = Request(url, method='GET', headers={
            'Authorization': 'Token ' + self.token, 'Accept': 'application/json',
            'User-Agent': 'ForecastAgent-readonly-archive/1.0'})
        for attempt in range(3):
            record = {'method': 'GET', 'url': url, 'at_utc': utc(), 'attempt': attempt + 1}
            try:
                with build_opener(NoRedirect()).open(request, timeout=45) as response:
                    raw = response.read()
                    record['http_status'] = response.status
                target = Path(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                record['response_sha256'] = hashlib.sha256(raw).hexdigest()
                self.records.append(record)
                return json.loads(raw)
            except (HTTPError, URLError, TimeoutError) as error:
                record.update(error_type=type(error).__name__, http_status=getattr(error, 'code', None))
                self.records.append(record)
                retryable = not isinstance(error, HTTPError) or error.code in {429, 500, 502, 503, 504}
                if not retryable or attempt == 2:
                    raise
                try:
                    delay = min(60, max(1, int(error.headers.get('Retry-After', '10'))))
                except (AttributeError, ValueError):
                    delay = 10
                time.sleep(delay)


def leaves(post):
    rows = [post['question']] if isinstance(post.get('question'), dict) else []
    rows += (post.get('group_of_questions') or {}).get('questions', [])
    conditional = post.get('conditional') or {}
    rows += [conditional[k] for k in ('question_yes', 'question_no') if isinstance(conditional.get(k), dict)]
    if any(not isinstance(q.get('id'), int) for q in rows):
        raise ValueError('Invalid question identity')
    return rows


def project_ids(post):
    projects = post.get('projects') or {}
    return {p['id'] for value in projects.values()
            for p in (value if isinstance(value, list) else [value])
            if isinstance(p, dict) and isinstance(p.get('id'), int)}


def download(reader, root, project_id, expected_start, advertised_count=60, pause=0.5):
    root = Path(root)
    if root.exists():
        raise ValueError('Use a fresh archive directory')
    root.mkdir(parents=True)
    started = utc()
    project = reader.read(f'{HOST}/api/projects/tournaments/{project_id}/', root / 'raw/project.json')
    if project.get('id') != project_id or not str(project.get('start_date', '')).startswith(expected_start):
        raise ValueError('Fixed tournament identity mismatch')
    posts, scans, probe_errors = {}, [], []
    # The default feed includes approved posts only. Explicit curation scopes
    # discover other records only if the authenticated account may read them.
    for scope in ('approved', 'pending', 'draft', 'rejected'):
        params = {'tournaments': project_id, 'limit': 100, 'include_descriptions': 'true',
                  'with_cp': 'false', 'order_by': '-created_at'}
        if scope != 'approved':
            params['statuses'] = scope
        url, visited, count = f'{HOST}/api/posts/?{urlencode(params)}', set(), 0
        try:
            for number in range(1, 31):
                validate_url(url)
                if parse_qs(urlsplit(url).query).get('tournaments') != [str(project_id)] or url in visited:
                    raise ValueError('Archive pagination identity mismatch')
                visited.add(url)
                data = reader.read(url, root / f'raw/lists/{scope}-{number:02d}.json')
                rows = data.get('results')
                if not isinstance(rows, list):
                    raise ValueError('Question list missing')
                for post in rows:
                    if not isinstance(post.get('id'), int) or project_id not in project_ids(post):
                        raise ValueError('List post belongs to another tournament')
                    posts[post['id']] = post
                count += len(rows)
                next_url = data.get('next') if rows else None
                if not next_url:
                    scans.append({'scope': scope, 'pages': number, 'rows': count, 'complete': True})
                    break
                url = urljoin(HOST, next_url)
                if pause:
                    time.sleep(pause)
            else:
                raise ValueError('Archive exceeds bounded pagination window')
        except HTTPError as error:
            if scope == 'approved':
                raise
            probe_errors.append({'scope': scope, 'http_status': error.code, 'complete': False})
    entries, detail_errors = [], []
    for ident, listed in sorted(posts.items()):
        try:
            post = reader.read(f'{HOST}/api/posts/{ident}/?with_cp=false', root / f'raw/posts/{ident}.json')
            if post.get('id') != ident or project_id not in project_ids(post):
                raise ValueError('Detail post identity mismatch')
            detail_complete = True
        except (HTTPError, URLError, TimeoutError) as error:
            post, detail_complete = listed, False
            save(root / f'raw/list-fallbacks/{ident}.json', listed)
            detail_errors.append({'post_id': ident, 'http_status': getattr(error, 'code', None),
                                  'error_type': type(error).__name__})
        for question in leaves(post):
            resolution = question.get('resolution')
            entry = {'question_id': question['id'], 'post_id': ident, 'project_id': project_id,
                     'url': f'{HOST}/questions/{ident}/', 'title': question.get('title') or post.get('title'),
                     'type': question.get('type'), 'status': question.get('status'),
                     'curation_status': post.get('curation_status'), 'detail_complete': detail_complete,
                     'description': question.get('description') or post.get('description'),
                     'resolution_criteria': question.get('resolution_criteria') or post.get('resolution_criteria'),
                     'fine_print': question.get('fine_print') or post.get('fine_print'),
                     'options': question.get('options'), 'scaling': question.get('scaling'),
                     'open_time': question.get('open_time'), 'spot_scoring_time': question.get('spot_scoring_time'),
                     'scheduled_close_time': question.get('scheduled_close_time'),
                     'actual_close_time': question.get('actual_close_time'),
                     'scheduled_resolve_time': question.get('scheduled_resolve_time'),
                     'actual_resolve_time': question.get('actual_resolve_time'), 'resolution': resolution,
                     'resolution_available': resolution is not None, 'question': question,
                     'retrieved_at_utc': started}
            entries.append(entry)
        if pause:
            time.sleep(pause)
    ids = [row['question_id'] for row in entries]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate question identities in archive')
    save(root / 'questions.json', entries)
    (root / 'questions.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in entries), encoding='utf-8')
    fields = ['question_id', 'post_id', 'type', 'status', 'curation_status', 'title', 'url',
              'open_time', 'spot_scoring_time', 'scheduled_close_time', 'scheduled_resolve_time',
              'resolution', 'resolution_available', 'detail_complete', 'resolution_criteria']
    with (root / 'questions.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(entries)
    reported = project.get('questions_count_including_subquestions', project.get('questions_count'))
    summary = {'schema': 'readonly-tournament-archive-v1', 'project_id': project_id,
               'project_slug_at_download': project.get('slug'), 'start_date': project.get('start_date'),
               'retrieved_at_utc': started, 'finished_at_utc': utc(), 'post_count': len(posts),
               'question_count': len(entries), 'project_reported_question_count': reported,
               'webpage_advertised_question_count': advertised_count,
               'matches_project_reported_count': len(entries) == reported,
               'matches_webpage_advertised_count': len(entries) == advertised_count,
               'question_types': dict(Counter(row['type'] for row in entries)),
               'question_statuses': dict(Counter(row['status'] for row in entries)),
               'curation_statuses': dict(Counter(row['curation_status'] for row in entries)),
               'resolution_available_count': sum(row['resolution_available'] for row in entries),
               'detail_failure_count': len(detail_errors), 'detail_errors': detail_errors,
               'scans': scans, 'curation_probe_errors': probe_errors,
               'approved_api_scan_complete': any(row['scope'] == 'approved' and row['complete'] for row in scans),
               'actual_http_attempts': len(reader.records), 'model_calls': 0, 'search_calls': 0,
               'forecasts_submitted': 0,
               'coverage_note': 'Complete pagination describes account-visible records, not inaccessible or deleted questions.'}
    save(root / 'summary.json', summary)
    save(root / 'http-requests.json', reader.records)
    (root / 'README.md').write_text(
        '# MiniBench round archive\n\n'
        f'Fixed project: {project_id}. Round start: {expected_start}. Downloaded: {started}.\n\n'
        'This is a one-time, read-only dataset archive. MiniBench competition is disabled.\n'
        'No models, search providers, forecasts or comments are called.\n\n'
        '- `questions.csv`: question index, deadlines, rules and available results.\n'
        '- `questions.json` and `questions.jsonl`: typed records with the complete raw question object.\n'
        '- `raw/project.json`, `raw/lists/`, `raw/posts/`: original API response bytes.\n'
        '- `summary.json`: counts, curation scopes, permission gaps and completeness.\n'
        '- `http-requests.json`: GET-only transport audit without credentials.\n'
        '- `manifest.json`: SHA-256 hashes and byte sizes of every archive file.\n\n'
        'Null resolutions are unavailable at this snapshot time. They are not NO outcomes.\n'
        'Resolved or cancelled labels remain separate from forecasting eligibility.\n'
        'Historical forecasting evaluation must handle information and outcome leakage separately.\n', encoding='utf-8')
    save(root / 'manifest.json', {'schema': 'tournament-archive-files-v1', 'files': {
        path.relative_to(root).as_posix(): {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                          'size_bytes': path.stat().st_size}
        for path in sorted(root.rglob('*')) if path.is_file()}})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-id', type=int, required=True)
    parser.add_argument('--expected-start', required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--advertised-count', type=int, default=60)
    args = parser.parse_args()
    reader = Reader(os.environ['METACULUS_TOKEN'])
    existed = args.root.exists()
    try:
        result = download(reader, args.root, args.project_id, args.expected_start, args.advertised_count)
    finally:
        if not existed and args.root.exists():
            save(args.root / 'http-requests.json', reader.records)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
