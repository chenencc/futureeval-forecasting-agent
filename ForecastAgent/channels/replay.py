"""Replay an operator-provided saved-original cohort with network forbidden."""
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.channels import native
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.research_loop import POLICY, fusion, state


def replay(validation, originals, output):
    validation, originals, output = Path(validation), Path(originals), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    inputs = json.loads(validation.read_text(encoding='utf-8'))
    paths = [originals / 'journal.sqlite']
    for case in inputs['cases']:
        folder = originals / 'captures' / case['capture_id']
        paths.extend([folder / 'capture.json', folder / 'response.raw'])
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    rows = []
    with patch('socket.create_connection', side_effect=AssertionError('Network forbidden during replay')):
        for index, case in enumerate(inputs['cases']):
            folder = originals / 'captures' / case['capture_id']
            capture = json.loads((folder / 'capture.json').read_text(encoding='utf-8'))
            raw = (folder / 'response.raw').read_bytes()
            if hashlib.sha256(raw).hexdigest() != capture['raw_sha256']:
                raise ValueError('Original checksum mismatch')
            url = capture['request_url']
            request = {'id': str(900000 + index), 'question': 'Offline navigation of ' + case['label'],
                'resolution_criteria': 'Read the specified saved original unit: ' + url,
                'mode': 'live', 'pipeline': 'collection', native.FIELD: native.POLICY,
                'research_state_policy': POLICY, 'research_acquisition_policy': fusion.POLICY_NAME}
            task = RetrievalTask(output / ('case-' + str(index + 1)), request)
            task.bundle['plan'] = [{'id': 'source', 'priority': 'critical', 'condition': 'Read selected original unit',
                'expected_source': 'Saved official document', 'query': case['label']}]
            if url not in task.bundle['pages']:
                docs = [r for r in capture.get('records', []) if isinstance(r.get('page_content'), str)]
                text = '\n\n'.join(d['page_content'] for d in docs)
                task.store_page(url, {'content': text, 'documents': docs, 'sha256': capture['raw_sha256'],
                    'raw_response_base64': base64.b64encode(raw).decode(),
                    'retrieved_at_utc': capture['captured_at_utc'], 'content_type': capture['http']['content_type'],
                    'response_headers': capture['http'].get('response_headers', {}),
                    'http_status': capture['http']['status'], 'raw_truncated': capture['http'].get('truncated', False),
                    'capture_method': 'imported_saved_original', 'temporal_status': 'live_capture'})
            fusion.initialize(task); task.save()
            part = task.execute('intelligence_part', {'url': url, 'unit_id': case['selected_part']['unit_id']}, '')
            coords = part.get('native_coordinates')
            matched = bool(coords and task.bundle['pages'][url]['content'][coords['native_start']:coords['native_end']] == part['text'])
            raw_preserved = hashlib.sha256(base64.b64decode(task.bundle['pages'][url]['raw_response_base64'])).hexdigest() == capture['raw_sha256']
            material = state.catalog(task.bundle)
            valid = bool(part.get('evidence')) and all(material['spans'].get(r['evidence_id']) == r for r in part['evidence'])
            rows.append({'label': case['label'], 'source_url': url, 'unit_id': part.get('unit_id'),
                'status': part['status'], 'delivered_chars': len(part.get('text', '')),
                'native_coordinate_match': matched, 'reference_handles_verified': valid,
                'raw_preserved': raw_preserved, 'capture_time_preserved': task.bundle['pages'][url]['retrieved_at_utc'] == capture['captured_at_utc'],
                'material_arrivals': len(task.bundle['channel_tools']['material_events']['events']),
                'pending_map_update': task.bundle['research_acquisition']['pending_map_update'],
                'http_attempts': len(task.bundle['fetch_attempts']), 'relevance_verified': False, 'truth_verified': False})
            resumed = RetrievalTask(task.directory, copy.deepcopy(request))
            second = resumed.execute('intelligence_part', {'url': url, 'unit_id': part['unit_id']}, '')
            rows[-1]['repeat_read_same_text'] = second['text'] == part['text']
            rows[-1]['repeat_read_new_bodies'] = resumed.bundle['research_acquisition']['events'][-1]['new_bodies']
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    report = {'schema': 'native_channel_replay_v1', 'donor_commit': native.DONOR if hasattr(native, 'DONOR') else 'dde8b02',
        'cases': rows, 'originals_and_donor_ledger_unchanged': before == after,
        'network_calls': 0, 'model_calls': 0, 'search_calls': 0, 'forecast_submissions': 0,
        'native_budget_resets': 0, 'forecast_quality_verified': False,
        'passed': before == after and all(r['native_coordinate_match'] and r['reference_handles_verified'] and
            r['raw_preserved'] and r['capture_time_preserved'] and r['repeat_read_same_text'] and
            not r['repeat_read_new_bodies'] and r['http_attempts'] == 0 for r in rows)}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description='Zero-network saved-original integration replay')
    parser.add_argument('--validation', type=Path, required=True)
    parser.add_argument('--originals', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = replay(args.validation, args.originals, args.output)
    print(json.dumps({'passed': report['passed'], 'cases': len(report['cases']),
                      'network_calls': report['network_calls'], 'output': str(args.output)}))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
