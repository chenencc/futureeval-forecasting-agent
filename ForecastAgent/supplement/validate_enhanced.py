"""Offline paired acquisition/reading audit. No provider requests or labels."""
import argparse
from pathlib import Path
from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.analysis.priority_reading import select
from ForecastAgent.supplement.enhanced import plan, hints, assess


def validate(parent, output):
    rows = []
    for path in sorted(Path(parent).glob('tasks/*/analysis-input.json')):
        bundle = load(path); original_hash = digest(bundle); p = plan(bundle)
        screened = dict(bundle); screened['pages'] = {u: v for u, v in bundle['pages'].items()
            if assess(bundle['request'], u, v.get('content', ''))['eligible_for_evidence']}
        if not screened['pages']:
            rows.append({'id': path.parent.name, 'status': 'no_readable_sources', 'gaps': p['gaps']}); continue
        packet = chain.full_packet(screened); reading = hints(screened)
        old, old_audit = chain.select(packet)
        new, audit = select(packet, reading)
        priority = {r['evidence_id'] for r in reading['spans'][:len(packet['sources'])]}
        anchors = []
        # Publication/date clues are an observable coverage diagnostic, not an answer.
        for span in packet['evidence']:
            if 'publish' in span['text'].lower() and ('July 28' in span['text'] or 'August 1' in span['text']):
                anchors.append({'id': span['evidence_id'], 'old_selected': span['evidence_id'] in old_audit['selected_ids'],
                                'new_selected': span['evidence_id'] in audit['selected_ids']})
        assert digest(bundle) == original_hash
        rows.append({'id': path.parent.name, 'sources': len(packet['sources']), 'excluded_shells': len(bundle['pages'])-len(screened['pages']),
                     'coverage_gaps': p['gaps'], 'old_request_bytes': old_audit['request_bytes'], 'new_request_bytes': audit['request_bytes'],
                     'priority_source_spans_old': len(priority & set(old_audit['selected_ids'])),
                     'priority_source_spans_new': len(priority & set(audit['selected_ids'])), 'publication_anchors': anchors,
                     'raw_input_unchanged': True})
        save(Path(output)/path.parent.name/'reading-hints.json', reading)
        save(Path(output)/path.parent.name/'new-input-audit.json', audit)
    report = {'protocol': 'enhanced-supplement-offline-acceptance-v1', 'rows': rows, 'provider_calls': 0,
              'forecast_submissions': 0, 'labels_used': False, 'quality_claim': 'Observable body/window coverage only; not probability accuracy.'}
    save(Path(output)/'report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--parent', required=True); parser.add_argument('--output', required=True)
    args = parser.parse_args(); print(__import__('json').dumps(validate(args.parent, args.output), ensure_ascii=False))
