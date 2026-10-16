"""Trusted checkpoint selection with one explicitly audited discovery gap.

Unknown lost mutations remain blocking. The exception below is limited to one
monitor run that logged zero admissions and zero paid-provider transmissions.
Its unsaved public observations are a permanent gap, not recovered originals.
"""
import copy
import os

RECOVERY = {
    'schema': 'audited-monitor-public-observation-gap-v1',
    'repository': 'chenencc/futureeval-forecasting-agent',
    'branch': 'main', 'lost_run_id': 38811619270,
    'lost_run_head_sha': '8f75974165abdd6bc00d1e9de70e62e8d62c01e7',
    'source_run_id': 38810374416, 'source_artifact_id': 12008308014,
    'source_artifact_digest': 'sha256:a638789b74176152bab9848d80b650ddbe495b726e6f30f52d6e1da1ffd6810e',
    'source_state_sha256': 'e4a99aa62fb544882e1fc147316e1d464b67edb01318edd02e1de3a7d62d6e4d',
    'lost_report_sha256': '7a2b56d7b79bd7373b79fb896a6d2ac5f6df563bfb2973776dd4df6137212108',
    'lost_report_new_admissions': 0, 'lost_report_intake_http_attempts': 0,
    'lost_report_public_http_attempts': 96,
    'lost_report_new_sightings': 142, 'lost_report_metadata_changes': 449,
    'gap_started_at': '2026-10-15T21:40:13Z',
    'gap_last_scan_at': '2026-10-15T21:42:39.474025+00:00',
    'scope': 'Public discovery metadata, sightings and quote observations from the failed publication are unavailable. Do not recreate their original capture clocks.',
    'budgets_reset': False, 'lost_raw_observations_recovered': False,
}


def audited_skip(run, jobs, role, branch):
    if role != 'monitor' or branch != RECOVERY['branch'] or run['id'] != RECOVERY['lost_run_id']:
        return False
    if (os.environ.get('GITHUB_REPOSITORY') != RECOVERY['repository'] or
            run.get('head_sha') != RECOVERY['lost_run_head_sha'] or run.get('conclusion') != 'failure'):
        raise ValueError('Audited recovery source identity changed')
    expected = {'Run research monitor': 'success', 'Seal interrupted monitor state': 'success',
        'Build bounded lossless monitor checkpoint': 'failure', 'Publish independent monitor checkpoint': 'skipped'}
    steps = [s for j in jobs for s in j.get('steps', [])]
    if len(jobs) != 1 or any(len([s for s in steps if s['name'] == name and s.get('conclusion') == status]) != 1
                             for name, status in expected.items()):
        raise ValueError('Audited lost-run step receipt differs')
    return True


def find_checkpoint(role, branch, exact_run=None):
    from urllib.parse import urlencode
    from ForecastAgent.market_repricing.github_state import api, WORKFLOWS
    filename, expected_name, mutation_step = WORKFLOWS[role]
    if exact_run and (not str(exact_run).isdigit() or int(exact_run) <= 0):
        raise ValueError('Invalid source run')
    gap = None
    seen = 0
    for page in range(1, 6):
        runs = ([api('/actions/runs/' + str(exact_run))] if exact_run else
            api('/actions/workflows/' + filename + '/runs?' + urlencode({
                'branch': branch, 'status': 'completed', 'per_page': 100, 'page': page}))['workflow_runs'])
        for run in runs:
            if str(run['id']) == os.environ.get('GITHUB_RUN_ID') and not exact_run:
                continue
            seen += 1
            if (run['name'] != expected_name or run['head_branch'] != branch or
                    run.get('head_repository', {}).get('full_name') != os.environ['GITHUB_REPOSITORY']):
                raise ValueError('Untrusted workflow source')
            if exact_run and run.get('conclusion') != 'success':
                raise ValueError('Explicit discovery source must have completed successfully')
            listing = api('/actions/runs/' + str(run['id']) + '/artifacts?per_page=100')
            artifacts = listing['artifacts']
            if listing.get('total_count', len(artifacts)) > len(artifacts):
                raise ValueError('Checkpoint artifact enumeration incomplete')
            names = {'market-repricing-v2-' + role + '-state', 'market-repricing-v2-' + role + '-emergency-state'}
            candidates = [a for a in artifacts if a['name'] in names and not a['expired']]
            if len(candidates) > 1:
                raise ValueError('Ambiguous checkpoint publications')
            if len(candidates) == 1:
                artifact = copy.deepcopy(candidates[0])
                if gap:
                    if (run['id'] != RECOVERY['source_run_id'] or artifact['id'] != RECOVERY['source_artifact_id'] or
                            artifact.get('digest') != RECOVERY['source_artifact_digest']):
                        raise ValueError('Audited recovery checkpoint differs')
                    artifact['_audited_recovery'] = copy.deepcopy(gap)
                return artifact
            if any(a['name'] == 'market-repricing-' + role + '-state' for a in artifacts):
                raise ValueError('Legacy campaign requires explicit migration')
            jobs = api('/actions/runs/' + str(run['id']) + '/jobs?per_page=100')['jobs']
            attempted = any(s['name'] == mutation_step and s.get('conclusion') != 'skipped'
                            for j in jobs for s in j.get('steps', []))
            if attempted or exact_run:
                if not exact_run and attempted and audited_skip(run, jobs, role, branch):
                    gap = copy.deepcopy(RECOVERY)
                    continue
                raise ValueError('Latest mutated research checkpoint is missing; refuse budget reset: ' + str(run['id']))
        if exact_run or len(runs) < 100:
            if gap:
                raise ValueError('Audited recovery checkpoint unavailable')
            return None
    raise ValueError('Recovery history limit reached; do not bootstrap')
