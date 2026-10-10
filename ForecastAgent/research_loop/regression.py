"""Replay actual saved map outputs under strict and isolated acceptance, offline."""
import argparse
import copy
import json
from pathlib import Path

from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.research_loop import POLICY, decision, live_trial
from ForecastAgent.research_loop.state import initialize, update
from ForecastAgent.research_loop.acceptance import accept, MapAcceptanceError


def run(parents, trials, root):
    rows = []
    for path in parents:
        original = load(path); ident = str(original['request']['id'])
        child = copy.deepcopy(original); child['request']['research_state_policy'] = POLICY
        child['pipeline'] = 'collection'; initialize(child)
        try:
            baseline = decision.prepare(child)['baseline']
            _, refs = live_trial.visible_catalog(child, baseline)
        except ValueError as exc:
            rows.append({'id': ident, 'status': 'metadata_blocked', 'error': str(exc)})
            continue
        allowed = [r['evidence_id'] for r in refs]
        for trial in trials:
            for file in sorted((Path(trial)/'cases'/ident/'map').glob('message-*.json')):
                message = load(file); row = {'id': ident, 'trial': str(trial),
                    'message': str(file), 'provider_attempts': 0}
                try:
                    calls = message.get('tool_calls', [])
                    if len(calls) != 1:
                        raise ValueError('Missing single structured tool')
                    p = json.loads(calls[0]['function']['arguments'])
                except (ValueError, KeyError, TypeError) as exc:
                    row.update(strict='rejected', isolated='rejected', error=str(exc))
                else:
                    old = copy.deepcopy(child)
                    try:
                        used = {ref for n in p.get('nodes', []) for ref in n.get('evidence_ids', [])}
                        if not used <= set(allowed): raise ValueError('Outside common coverage')
                        update(old, p)
                        notes = decision.prepare(old)['enriched']['research_map']
                        if notes['status'] != 'unverified_interpretations' or notes.get('hidden_node_ids'):
                            raise ValueError('Whole map not delivered')
                        row['strict'] = 'accepted'
                    except (ValueError, KeyError, TypeError) as exc:
                        row.update(strict='rejected', strict_error=str(exc))
                    new = copy.deepcopy(child)
                    try:
                        result = accept(new, p, allowed=allowed)
                        after = decision.prepare(new)
                        if after['baseline'] != baseline:
                            raise AssertionError('Original coverage changed')
                        notes = after['enriched']['research_map']
                        if notes['status'] != 'unverified_interpretations':
                            raise ValueError('No grounded map delivered')
                        delivered = {n['id'] for n in notes['nodes']}
                        quarantined = {r['node_id'] for r in result['acceptance']['rejected'] if r['section'] == 'nodes'}
                        if delivered & quarantined:
                            raise AssertionError('Rejected node was delivered')
                        row.update(isolated='accepted', acceptance=result['acceptance'],
                            delivered_node_ids=sorted(delivered), equal_original_coverage=True)
                        if new.get('pages') != original.get('pages'):
                            raise AssertionError('Archived raw pages changed')
                    except (ValueError, KeyError, TypeError) as exc:
                        row.update(isolated='rejected', isolated_error=str(exc),
                            acceptance=exc.report if isinstance(exc, MapAcceptanceError) else None)
                rows.append(row)
    result = {'protocol': 'actual-map-node-acceptance-offline-v1', 'rows': rows,
        'provider_attempts': 0, 'prediction_quality_tested': False,
        'strict_accepted': sum(r.get('strict') == 'accepted' for r in rows),
        'isolated_accepted': sum(r.get('isolated') == 'accepted' for r in rows),
        'new_false_rejections': sum(r.get('strict') == 'accepted' and r.get('isolated') != 'accepted' for r in rows),
        'blocked': sum(r.get('status') == 'metadata_blocked' for r in rows)}
    save(Path(root)/'report.json', result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parents', type=Path, required=True)
    p.add_argument('--trials', nargs='+', type=Path, required=True)
    p.add_argument('--root', type=Path, required=True)
    a = p.parse_args(); result = run(load(a.parents), a.trials, a.root)
    print(json.dumps({k:v for k,v in result.items() if k != 'rows'}))


if __name__ == '__main__':
    main()
