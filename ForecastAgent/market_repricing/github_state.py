"""Fail-closed Actions checkpoint restore, with trusted workflow identity."""
import argparse
import io
import json
import os
from pathlib import Path
import stat
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import zipfile
from urllib.error import HTTPError

from .common import verify

WORKFLOWS = {'monitor': ('market_research_monitor.yaml', 'Market repricing research monitor', 'Run research monitor'),
             'worker': ('market_research_worker.yaml', 'Market repricing research worker', 'Run release-pinned research')}


def api(path):
    request = Request('https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY'] + path,
                      headers={'Authorization': 'Bearer ' + os.environ['GH_STATE_TOKEN'],
                               'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def find(role, branch, exact_run=None):
    filename, expected_name, mutation_step = WORKFLOWS[role]
    if exact_run:
        if not str(exact_run).isdigit():
            raise ValueError('Invalid source run')
        runs = [api('/actions/runs/' + str(exact_run))]
    else:
        runs = []
        for page in range(1, 6):
            try:
                page_runs = api('/actions/workflows/' + filename + '/runs?' + urlencode({
                    'branch': branch, 'status': 'completed', 'per_page': 100, 'page': page}))['workflow_runs']
            except HTTPError as exc:
                if exc.code == 404 and page == 1:
                    # First deployment only: prove repository access succeeds;
                    # a missing workflow then means no prior worker ledger.
                    api('')
                    return None
                raise
            runs.extend(page_runs)
            if len(page_runs) < 100:
                break
    for run in runs:
        if str(run['id']) == os.environ.get('GITHUB_RUN_ID') and not exact_run:
            continue
        if run['name'] != expected_name or run['head_branch'] != branch or run.get('head_repository', {}).get('full_name') != os.environ['GITHUB_REPOSITORY']:
            raise ValueError('Untrusted workflow source')
        if exact_run and run.get('conclusion') != 'success':
            raise ValueError('Explicit discovery source must have completed successfully')
        artifacts = api('/actions/runs/' + str(run['id']) + '/artifacts?per_page=100')['artifacts']
        matches = [a for a in artifacts if a['name'] == 'market-repricing-' + role + '-state' and not a['expired']]
        if len(matches) == 1:
            return matches[0]
        jobs = api('/actions/runs/' + str(run['id']) + '/jobs?per_page=100')['jobs']
        if exact_run or any(s['name'] == mutation_step and s.get('conclusion') != 'skipped' for j in jobs for s in j.get('steps', [])):
            raise ValueError('Latest mutated research checkpoint is missing; refuse budget reset')
    if len(runs) >= 500:
        raise ValueError('Recovery history limit reached; do not bootstrap')
    return None


def extract(data, root):
    root = Path(root).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError('Restore target must be empty')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(i.file_size for i in archive.infolist()) > 2 * 1024 ** 3:
            raise ValueError('Expanded state exceeds bound')
        for item in archive.infolist():
            # Windows drive names, traversal and symlinks are all rejected.
            if '\\' in item.filename or ':' in item.filename or '..' in Path(item.filename).parts or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('Unsafe artifact member')
            dest = (root / item.filename).resolve()
            if not dest.is_relative_to(root):
                raise ValueError('Unsafe artifact member')
        archive.extractall(root)
    verify(root)


def restore(role, root, branch, exact_run=None):
    artifact = find(role, branch, exact_run)
    if artifact is None:
        return {'bootstrap': True, 'role': role}
    request = Request('https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY'] +
                      '/actions/artifacts/' + str(artifact['id']) + '/zip',
                      headers={'Authorization': 'Bearer ' + os.environ['GH_STATE_TOKEN'], 'Accept': 'application/vnd.github+json'})
    with urlopen(request, timeout=120) as response:
        data = response.read(1024 ** 3 + 1)
    if len(data) > 1024 ** 3:
        raise ValueError('Compressed artifact exceeds bound')
    extract(data, root)
    return {'bootstrap': False, 'artifact_id': artifact['id'], 'role': role}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=WORKFLOWS, required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--branch', required=True)
    parser.add_argument('--run-id')
    args = parser.parse_args()
    print(json.dumps(restore(args.role, args.root, args.branch, args.run_id)))
