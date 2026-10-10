"""Bounded live SEC validation; contact headers are never emitted."""
import argparse
import json
from pathlib import Path
from .core import Toolbox, now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    root = Path(args.root)
    box = Toolbox(root, max_requests=25, max_document_bytes=16_000_000)
    report = {'checked_at_utc': now(), 'configuration_name': 'SEC_USER_AGENT',
              'contact_value_recorded': False, 'cases': [], 'model_requests': 0}
    try:
        for source, params in (
            ('sec_submissions', {'cik': '0001318605'}),
            ('sec_concept', {'cik': '0001318605', 'taxonomy': 'us-gaap',
                             'tag': 'RevenueFromContractWithCustomerExcludingAssessedTax'}),
        ):
            result = box.fetch(source, params)
            report['cases'].append({'source': source, 'status': result['status'],
                                    'capture_id': result.get('id'),
                                    'http_status': result.get('http', {}).get('status'),
                                    'record_count': len(result['records']),
                                    'raw_bytes': result.get('raw_bytes'),
                                    'error_type': result.get('error', {}).get('type') if result.get('error') else None,
                                    'sample_field_names': sorted(result['records'][0]) if result['records'] else [],
                                    'quality': result.get('quality')})
        report['budget'] = box.budget()
        (root / 'sec-live-validation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))
    finally:
        box.close()


if __name__ == '__main__':
    main()
