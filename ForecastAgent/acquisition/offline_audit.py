"""Read saved bundles and fixed-analysis projections without provider calls."""
import argparse
import base64
import hashlib
import json
from pathlib import Path

from ForecastAgent.acquisition.pipeline import verify_baseline, prepare
from ForecastAgent.readers.quality import body_diagnostics
from ForecastAgent.readers.saved import select
from ForecastAgent.supplement.stage import save


def audit(path):
    from ForecastAgent.competition.mercury import packet_for
    from ForecastAgent.analysis.mercury_evidence_chain import select as select_spans
    path = Path(path)
    bundle = json.loads(path.read_text(encoding='utf-8'))
    _, warnings = prepare(bundle['request'])
    issues = []
    raw_checked = 0
    readable = []
    for url, page in bundle.get('pages', {}).items():
        if body_diagnostics(page.get('content', ''))['usable_text']:
            readable.append(url)
        if page.get('raw_response_base64') and page.get('sha256'):
            raw_checked += 1
            try:
                if hashlib.sha256(base64.b64decode(page['raw_response_base64'], validate=True)).hexdigest() != page['sha256']:
                    issues.append({'url': url, 'issue': 'Raw hash mismatch'})
            except (ValueError, TypeError):
                issues.append({'url': url, 'issue': 'Invalid raw byte encoding'})
    for excerpt in bundle.get('excerpts', []):
        try:
            _, text, _ = select(bundle['pages'], excerpt['url'], excerpt.get('location', {}).get('document_index'))
            if text[excerpt['start_char']:excerpt['end_char']] != excerpt['text']:
                issues.append({'excerpt_id': excerpt['id'], 'issue': 'Excerpt differs from current source'})
        except (ValueError, KeyError, TypeError):
            issues.append({'excerpt_id': excerpt.get('id'), 'issue': 'Source coordinates unavailable'})
    packet = packet_for(bundle)
    state, selection = select_spans(packet)
    for span in state['evidence']:
        if bundle['pages'][span['url']]['content'][span['start']:span['end']] != span['text']:
            issues.append({'evidence_id': span['evidence_id'], 'issue': 'Fixed analyst projection is not an exact original span'})
    return {'question_id': bundle['request']['id'], 'bundle_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'input_warnings': warnings, 'captured_sources': len(bundle.get('pages', {})),
            'readable_sources': len(readable), 'raw_hashes_checked': raw_checked,
            'banked_excerpts': len(bundle.get('excerpts', [])),
            'readable_body_chars': sum(len(bundle['pages'][u]['content']) for u in readable),
            'fixed_analysis_first_request_bytes': selection['request_bytes'],
            'fixed_analysis_visible_chars': sum(len(s['text']) for s in state['evidence']),
            'fixed_analysis_visible_source_count': len(state['sources']),
            'fixed_analysis_context_omitted': state['context_omitted'], 'issues': issues}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundles', nargs='+', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    verify_baseline()
    rows = [audit(path) for path in args.bundles]
    report = {'schema': 'intelligent-acquisition-saved-body-audit-v1', 'questions': rows,
              'new_model_http_attempts': 0, 'new_search_attempts': 0, 'new_capture_attempts': 0,
              'forecasts_submitted': 0,
              'scope': 'Saved-body integrity and unchanged release analysis projection only. No agent inference or live recall improvement was evaluated.'}
    save(args.output, report)
    print(json.dumps(report, indent=2))
    if any(row['issues'] for row in rows):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
