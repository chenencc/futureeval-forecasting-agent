"""Freeze a score-independent saved-body cohort and separate archive-bound labels."""
import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.research_loop import decision, labels


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(row):
    q = {k: copy.deepcopy(row.get(k)) for k in labels.FIELDS}
    q.update(id=str(row.get('id', row.get('post_id'))),
        question=row.get('question', row.get('title')),
        question_type=row.get('question_type', row.get('type')),
        resolution_criteria=row.get('resolution_criteria', row.get('criteria')))
    return q


def freeze(manifest_path, ids, root, binary_source, nonbinary_inputs, nonbinary_records, conflicts=None):
    root = Path(root); manifest = load(manifest_path)
    if len(set(ids)) != len(ids): raise ValueError('Cohort IDs must be unique')
    lookup = {r['id']: r for r in manifest['rows']}
    if any(i not in lookup for i in ids): raise ValueError('Unknown cohort ID')
    provenance_files = [manifest_path, binary_source, nonbinary_inputs, nonbinary_records]
    if conflicts: provenance_files.append(conflicts)
    inventory = {str(Path(p).resolve()): sha(p) for p in provenance_files}
    frozen = {'schema': 'saved-body-cohort-v1', 'ids': ids,
        'selection': 'Explicit domain and question-type coverage; no forecast scores used',
        'source_inventory': inventory, 'implementation': decision.implementation_hashes(),
        'super_http_cap': 2, 'mercury_http_cap_per_arm': 1, 'fresh_search': 0, 'fresh_fetch': 0,
        'submission': False, 'labels_sent_to_models': False}
    if (root/'cohort-identity.json').exists() and load(root/'cohort-identity.json') != frozen:
        raise ValueError('Frozen cohort identity changed')
    save(root/'cohort-identity.json', frozen)
    binary = {str(x['id']): x for x in (json.loads(line) for line in Path(binary_source).read_text(encoding='utf-8').splitlines())}
    questions = {str(x['post_id']): x for x in load(nonbinary_inputs)}
    records = {str(x['post_id']): x for x in load(nonbinary_records)}
    conflict_registry = load(conflicts) if conflicts else {}
    parents, bindings, audits = [], {}, []
    for ident in ids:
        entry = lookup[ident]; source = Path(entry['input'])
        if sha(source) != entry['input_sha256']:
            raise ValueError('Manifest input checksum mismatch: '+ident)
        raw = source.read_bytes(); parent = root/'inputs'/ident/'bundle.json'
        parent.parent.mkdir(parents=True, exist_ok=True)
        if parent.exists() and parent.read_bytes() != raw:
            raise ValueError('Frozen saved body changed')
        parent.write_bytes(raw); parents.append(str(parent.resolve()))
        request = load(parent)['request']
        if request['question_type'] == 'binary':
            source_record = binary[ident]; source_q = source_record['question']
            reference_q = canonical(source_q)
            reference_q.update(question_type='binary',
                resolution_criteria=source_q.get('market_info_resolution_criteria'),
                fine_print=source_q.get('market_info_fine_print'))
            resolution = source_record['resolution']
            if not resolution.get('resolved'):
                raise ValueError('Archived binary result is unresolved')
            outcome = {'id': ident, 'type': 'binary', 'value': resolution['resolved_to']}
            prov = {'source_path': str(binary_source), 'source_sha256': sha(binary_source),
                'source_url': source_q['url'], 'observed_at': source_record['forecast_due_date'],
                'method': 'ForecastBench archived question and resolution',
                'time_note': 'Observed_at is archive question-set date; resolution_date retained separately',
                'resolution_date': resolution.get('resolution_date'),
                'source_record_sha256': digest(source_record)}
        else:
            source_record = records[ident]; reference_q = canonical(questions[ident])
            outcome = {k: copy.deepcopy(source_record[k]) for k in
                ('post_id', 'question_id', 'type', 'resolution_display', 'result_kind',
                 'value', 'option_index_zero_based') if k in source_record}
            prov = {'source_path': str(nonbinary_records), 'source_sha256': sha(nonbinary_records),
                'source_url': source_record['source_url'], 'observed_at': source_record['observed_at_utc'],
                'method': source_record['provenance'], 'source_record_sha256': digest(source_record),
                'additional_sources': [{'path': str(nonbinary_inputs), 'sha256': sha(nonbinary_inputs)}],
                'source_gaps': source_record.get('gaps', []),
                'rules_capture': source_record.get('rules_capture')}
        binding = labels.bind(request, outcome, {'question': reference_q, 'outcome': outcome}, prov,
                              conflict_registry.get(ident, []))
        bindings[ident] = binding
        audits.append({'id': ident, 'question_type': request['question_type'],
            'parent_sha256': sha(parent), 'label_binding': labels.validate(request, binding)})
    save(root/'parents.json', parents); save(root/'isolated-label-bindings.json', bindings)
    save(root/'preflight-label-audit.json', {'rows': audits, 'labels_sent_to_models': False})
    return audits


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--ids', nargs='+', required=True)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--binary-source', type=Path, required=True)
    p.add_argument('--nonbinary-inputs', type=Path, required=True)
    p.add_argument('--nonbinary-records', type=Path, required=True)
    p.add_argument('--conflicts', type=Path)
    a = p.parse_args()
    rows = freeze(a.manifest, a.ids, a.root, a.binary_source, a.nonbinary_inputs, a.nonbinary_records, a.conflicts)
    print(json.dumps(rows))


if __name__ == '__main__':
    main()
