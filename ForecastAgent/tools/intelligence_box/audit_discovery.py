"""Audit acquisition pilot originals without changing captures or request caps."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile


def audit(root):
    root=Path(root).resolve()
    ledger=root/'journal.sqlite'; before=hashlib.sha256(ledger.read_bytes()).hexdigest()
    with sqlite3.connect(ledger.as_uri()+'?mode=ro',uri=True) as db:
        cap=db.execute('SELECT cap FROM config').fetchone()[0]
        reservations=db.execute('SELECT count(*) FROM attempts').fetchone()[0]
    rows=[]
    for path in sorted((root/'captures').glob('*/capture.json')):
        c=json.loads(path.read_text(encoding='utf-8'))
        raw_path=(root/c['raw_path']).resolve() if c.get('raw_path') else None
        if raw_path and not raw_path.is_relative_to(root): raise ValueError('Raw path escaped root')
        verified=bool(raw_path and hashlib.sha256(raw_path.read_bytes()).hexdigest()==c['raw_sha256'])
        complete=c.get('http',{}).get('status')==200 and not c.get('http',{}).get('truncated') and verified
        row={'capture_id':c['id'],'url':c['request_url'],'source_id':c['source_id'],
             'original_status':c['status'],'raw_bytes':c.get('raw_bytes'),
             'raw_hash_verified':verified,'wire_download_complete':complete,
             'record_count':len(c['records']),'readable_original':bool(c['quality'].get('readable_document')),
             'parent_capture_id':c.get('source_binding',{}).get('parent_capture_id'),
             'captured_at_utc':c['captured_at_utc'],'error':c.get('error')}
        if c.get('coverage'): row['coverage']=c['coverage']
        if complete and 'openxmlformats' in c['http']['content_type']:
            row['current_file_state']='captured_unparsed'
            row['office_zip_valid']=zipfile.is_zipfile(raw_path)
            row['note']='Original failed parse status preserved. New acquisition code stores this MIME as captured_unparsed; parser not implemented.'
        rows.append(row)
    return {'schema':'discovery_acquisition_validation_v1','maximum_http_attempts':cap,
            'reserved_http_attempts':reservations,'remaining':cap-reservations,
            'ledger_unchanged_by_audit':before==hashlib.sha256(ledger.read_bytes()).hexdigest(),
            'original_status_distribution':dict(Counter(r['original_status'] for r in rows)),
            'all_available_raw_hashes_verified':all(r['raw_hash_verified'] for r in rows),
            'complete_downloads':sum(r['wire_download_complete'] for r in rows),
            'readable_original_downloads':sum(r['readable_original'] for r in rows),
            'model_calls':0,'paid_search_calls':0,'records':rows,
            'limits':['Discovery windows are not archive completeness or target relevance.',
                      'One same-host redirect was explicitly followed in a separate request.',
                      'Office original downloaded but no Office reader implemented.',
                      'Native production integration and Linux runtime remain unverified.']}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--output',required=True)
    args=p.parse_args(); result=audit(args.root)
    Path(args.output).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in {'records','limits'}}))
