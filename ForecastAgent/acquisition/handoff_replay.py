"""No-provider, same-bundle paired first-request delivery acceptance audit."""
import argparse
import hashlib
import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.acquisition import handoff
from ForecastAgent.acquisition.pipeline import verify_baseline
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.competition.mercury import packet_for
from ForecastAgent.readers.saved import select as saved_select

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT/'ForecastAgent/experiments/materials_handoff_paired_protocol.json'
RUBRIC = ROOT/'ForecastAgent/experiments/intelligent_frontier_rubric.json'
POOL = ROOT/'ForecastAgent/fixtures/intelligent_frontier_five.json.gz'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def checks(state, targets):
    sources = {s['source_id']: s for s in state['sources']}
    texts = {}
    for span in state['evidence']:
        texts.setdefault(sources[span['source_id']]['url'], []).append(span['text'])
    return [{'target_id': t['id'], 'visible': any(
        all(anchor in '\n'.join(texts.get(a['url'], [])) for anchor in a['anchors'])
        for a in t['alternatives'])} for t in targets]


def audit_spans(bundle, state):
    sources = {s['source_id']: s for s in state['sources']}
    errors = []
    for span in state['evidence']:
        source = sources[span['source_id']]
        page, original, _ = saved_select(bundle['pages'], source['url'], span.get('document_index'))
        if original[span['start']:span['end']] != span['text']:
            errors.append({'evidence_id': span['evidence_id'], 'reason': 'Original coordinate mismatch'})
        if source['body_sha256'] != sha(page['content'].encode()):
            errors.append({'evidence_id': span['evidence_id'], 'reason': 'Whole body hash mismatch'})
        if span.get('document_index') is not None:
            view = next((v for v in source.get('document_views',[])
                         if v['document_index']==span['document_index']),{})
            if span.get('coordinate_space') != 'saved_document' or view.get('document_sha256') != sha(original.encode()):
                errors.append({'evidence_id': span['evidence_id'], 'reason': 'Document view is not version bound'})
    return errors


def forwarded_chars(state, bank_delivery):
    groups = {}
    for bank in bank_delivery:
        key = (bank['source_id'], bank['document_index'])
        groups.setdefault(key, []).append((bank['start'], bank['end']))
    total = 0
    for key, spans in groups.items():
        covered = [(s['start'],s['end']) for s in state['evidence'] if handoff.space(s)==key]
        for start,end in handoff.ranges(spans):
            total += end-start-sum(b-a for a,b in handoff.uncovered(start,end,covered))
    return total


def review(root):
    baseline = verify_baseline()
    protocol_raw = PROTOCOL.read_bytes().replace(b'\r\n',b'\n')
    protocol = json.loads(protocol_raw)
    if sha(POOL.read_bytes()) != protocol['pool_sha256']:
        raise ValueError('Frozen source pool changed')
    if sha(RUBRIC.read_bytes().replace(b'\r\n',b'\n')) != protocol['rubric_sha256_lf']:
        raise ValueError('Frozen rubric changed')
    rubric = json.loads(RUBRIC.read_text(encoding='utf-8'))
    rows = []
    states = {}
    limit = protocol['limit_bytes']
    with ExitStack() as stack:
        # Any accidental provider use fails rather than silently spending quota.
        for name in ('ForecastAgent.providers.decisions.decide', 'ForecastAgent.providers.http.download',
                     'urllib.request.urlopen'):
            stack.enter_context(patch(name, side_effect=AssertionError('Providers are forbidden in offline handoff replay')))
        for qid in protocol['question_ids']:
            row = {'question_id': qid, 'arms': {}}
            for arm in ('baseline','candidate'):
                path = Path(root)/qid/arm/'bundle.json'
                original = path.read_bytes()
                if sha(original) != protocol['bundles'][qid][arm]:
                    raise ValueError('Frozen bundle changed: '+qid+'/'+arm)
                bundle = json.loads(original)
                old, old_selection = chain.select(packet_for(bundle), limit=limit)
                candidate, manifest = handoff.pack(bundle, limit=limit)
                if path.read_bytes() != original or handoff.digest(bundle) != manifest['bundle_sha256']:
                    raise ValueError('Original bundle was mutated')
                old_checks = checks(old, rubric['targets'][qid])
                new_checks = checks(candidate, rubric['targets'][qid])
                lost = [a['target_id'] for a,b in zip(old_checks,new_checks) if a['visible'] and not b['visible']]
                gained = [a['target_id'] for a,b in zip(old_checks,new_checks) if b['visible'] and not a['visible']]
                errors = audit_spans(bundle, candidate)
                passed = (not errors and not lost and not manifest['old_visible_text_removed']
                          and manifest['request_bytes'] <= limit)
                record = {'bundle_file_sha256': sha(original),
                          'old': {'request_bytes': old_selection['request_bytes'], 'checks': old_checks,
                                  'forwarded_bank_chars': forwarded_chars(old,manifest['bank_delivery']),
                                  'visible_body_chars': sum(len(s['text']) for s in old['evidence'])},
                          'new': {'request_bytes': manifest['request_bytes'], 'checks': new_checks,
                                  'forwarded_bank_chars': manifest['unique_forwarded_bank_chars'],
                                  'visible_original_chars': sum(len(s['text']) for s in candidate['evidence']),
                                  'manifest': manifest},
                          'gained_checks': gained, 'lost_checks': lost, 'integrity_errors': errors,
                          'protected_old_text_preserved': not manifest['old_visible_text_removed'],
                          'acceptance_gate_passed': passed}
                row['arms'][arm] = record
                states[qid+'/'+arm] = {'old_state': old, 'new_state': candidate, 'manifest': manifest}
            rows.append(row)
    totals = {}
    for arm in ('baseline','candidate'):
        records = [r['arms'][arm] for r in rows]
        totals[arm] = {'paired_bundles': len(records), 'frozen_checks': sum(len(r['old']['checks']) for r in records),
                       'old_visible_checks': sum(c['visible'] for r in records for c in r['old']['checks']),
                       'new_visible_checks': sum(c['visible'] for r in records for c in r['new']['checks']),
                       'gained_checks': sum(len(r['gained_checks']) for r in records),
                       'lost_checks': sum(len(r['lost_checks']) for r in records),
                       'old_forwarded_bank_chars': sum(r['old']['forwarded_bank_chars'] for r in records),
                       'new_forwarded_bank_chars': sum(r['new']['forwarded_bank_chars'] for r in records),
                       'unique_valid_bank_chars': sum(r['new']['manifest']['unique_valid_bank_chars'] for r in records),
                       'unique_omitted_bank_chars': sum(r['new']['manifest']['unique_omitted_bank_chars'] for r in records),
                       'invalid_bank_count': sum(len(r['new']['manifest']['invalid_banks']) for r in records),
                       'gate_passed_pairs': sum(r['acceptance_gate_passed'] for r in records),
                       'old_request_bytes_total': sum(r['old']['request_bytes'] for r in records),
                       'new_request_bytes_total': sum(r['new']['request_bytes'] for r in records)}
    return {'schema':'banked-material-handoff-paired-review-v1','parent_run_id':protocol['parent_run_id'],
            'protocol_sha256_lf':sha(protocol_raw),'implementation_sha256_lf':sha(Path(handoff.__file__).read_bytes().replace(b'\r\n',b'\n')),
            'frozen_release_dependencies_verified':len(baseline['initial_frozen_file_sha256']),
            'limit_bytes':limit,'rows':rows,'totals':totals,
            'all_acceptance_gates_passed':all(r['arms'][a]['acceptance_gate_passed'] for r in rows for a in ('baseline','candidate')),
            'provider_calls':0,'analysis_run':False,'submitted':False,'promotion_allowed':False,
            'scope':protocol['scope'],
            'limitations':['Five repaired development cases, not untouched holdouts.',
                'The before/after comparison is paired within each saved bundle; original collection arms remain stochastic.',
                'First-request original-text coverage only; Mercury has not been called and forecasting quality is unmeasured.',
                'Body identity context does not prove a table date, relevant event, or interval completeness.',
                'No historical cutoff enforcement. Unmapped extracted tables retain heuristic extraction warnings.']}, states


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--states-root',type=Path,required=True)
    args = parser.parse_args()
    report, states = review(args.root)
    from ForecastAgent.acquisition.frontier_compare import text_digest
    report['reviewer_sha256_lf'] = text_digest(Path(__file__))
    for key, value in states.items():
        path = args.states_root/key/'handoff.json'
        encoded = (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()
        if path.exists() and path.read_bytes() != encoded:
            raise ValueError('Paired state identity changed; use a separate output root')
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(encoded)
    raw = (json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode()
    if args.output.exists() and args.output.read_bytes() != raw:
        raise ValueError('Review identity changed; use a separate report path')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_bytes(raw)
    print(json.dumps({'output':str(args.output),'sha256':sha(raw),'totals':report['totals'],
                      'all_gates_passed':report['all_acceptance_gates_passed'],'provider_calls':report['provider_calls']}))


if __name__=='__main__':
    main()
