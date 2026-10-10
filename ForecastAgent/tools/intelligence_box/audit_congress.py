"""Offline integrity and credential-exclusion audit of Congress captures."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from .core import now


def audit(root):
    root=Path(root)
    chain=json.loads((root/'congress-chain-report.json').read_text(encoding='utf-8'))
    follow=json.loads((root/'congress-followup-report.json').read_text(encoding='utf-8'))
    rows=[]
    key=os.environ.get('CONGRESS_API_KEY','')
    for path in sorted(root.glob('captures/*/capture.json')):
        capture=json.loads(path.read_text(encoding='utf-8'))
        if not capture['source_id'].startswith('congress_'): continue
        raw=(root/capture['raw_path']).read_bytes()
        rows.append({'id':capture['id'],'source':capture['source_id'],'status':capture['status'],
                     'captured_at_utc':capture['captured_at_utc'],'http_status':capture['http']['status'],
                     'request_url':capture['request_url'],'raw_bytes':len(raw),'raw_sha256':capture['raw_sha256'],
                     'hash_matches':hashlib.sha256(raw).hexdigest()==capture['raw_sha256'],
                     'credential_absent':all(key.encode() not in body for body in (raw,path.read_bytes())) if key else None,
                     'source_binding':capture.get('source_binding'),'record_count':len(capture['records']),
                     'projected_body_chars':sum(len(r.get('page_content','')) for r in capture['records']),
                     'more_available':capture['coverage']['more_available'],
                     'reported_total':capture['coverage']['total_reported']})
    actions=[r for r in rows if r['source']=='congress_actions']
    return {'schema':'congress_chain_audit_v1','audited_at_utc':now(),'http_requests_during_audit':0,
            'new_physical_http_attempts':chain['new_physical_attempts']+follow['new_physical_attempts'],
            'budget':follow['budget'],'model_requests':0,'search_requests':0,
            'all_hashes_match':all(r['hash_matches'] for r in rows),
            'credential_exclusion_checked':bool(key),
            'credential_absent_from_captures':all(r['credential_absent'] for r in rows) if key else None,
            'credential_absent_from_ledger':key.encode() not in (root/'journal.sqlite').read_bytes() if key else None,
            'native_stage_fields':chain['native_stage_fields'],'text_version_count':len(chain['text_versions']),
            'action_page_rows':[r['record_count'] for r in actions],
            'action_records_returned':sum(r['record_count'] for r in actions),
            'action_total_count_consistent':len({r['reported_total'] for r in actions})==1,
            'snapshot_consistency_across_pages_verified':False,'captures':rows,
            'production_integrated':False,'submitted':False,
            'limitations':['Official stage fields preserved; no enactment inference from draft/enrolled text',
                           'Action pages have separate capture times; no atomic cross-page snapshot guarantee',
                           'Full original responses retained; agent excerpts truncate at 150000 characters',
                           'Predictive quality and historical leakage not tested']}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    report=audit(args.root)
    Path(args.output).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('new_physical_http_attempts','all_hashes_match','credential_absent_from_captures','credential_absent_from_ledger','text_version_count','action_records_returned','budget')}))


if __name__=='__main__': main()
