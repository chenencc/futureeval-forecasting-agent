"""Offline verification of saved pilots, without rewriting captures or ledgers."""
import argparse
import hashlib
import json
import sqlite3
import socket
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch
from .core import parse
from .catalog import SOURCES
from .profiles import PROFILES

PILOTS=['toolbox-public-pilot-20261010','toolbox-priority-pilot-20261010','discovery-acquisition-pilot-20261010','public-channels-pilot-20261010','public-channels-followup-20261010','govinfo-original-pilot-20261010']

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def audit(worktree):
    output={'version':'toolbox_saved_acceptance_v1','network_requests':0,'pilots':[]}
    with patch.object(socket,'create_connection',side_effect=AssertionError('Offline replay cannot open network sockets')), patch.object(socket,'getaddrinfo',side_effect=AssertionError('Offline replay cannot resolve hosts')):
        for name in PILOTS:
            root=worktree/'.tmp'/name
            if not root.is_dir(): raise FileNotFoundError('Required saved pilot missing: '+name)
            journal=root/'journal.sqlite'; before=sha(journal)
            db=sqlite3.connect(journal.resolve().as_uri()+'?mode=ro',uri=True)
            try:
                cap=db.execute('SELECT cap FROM config').fetchone()[0]
                attempts=db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
            finally: db.close()
            rows=[]
            for path in (root/'captures').glob('*/capture.json'):
                original_hash=sha(path); c=json.loads(path.read_text(encoding='utf-8'))
                row={'id':c['id'],'source_id':c['source_id'],'original_status':c['status'],'capture_unchanged':None}
                if c.get('raw_path'):
                    raw_path=(root/c['raw_path']).resolve()
                    if not raw_path.is_relative_to(root.resolve()): raise ValueError('Saved raw path escaped root')
                    row['raw_hash_verified']=sha(raw_path)==c['raw_sha256']
                    if not row['raw_hash_verified']: raise ValueError('Saved raw hash mismatch')
                else: row['raw_hash_verified']=None
                kind=c.get('http',{}).get('content_type','')
                if c['status'] not in {'usable','empty'}:
                    row['replay']='preserved_original_nonusable'
                elif c.get('http',{}).get('status')!=200 or c['http'].get('truncated'):
                    raise ValueError('Successful capture has incomplete HTTP response')
                elif 'officedocument' in kind or kind in {'application/msword','application/vnd.ms-excel'}:
                    row['replay']='original_downloaded_parser_unavailable'
                else:
                    source=dict(SOURCES.get(c['source_id'],{'format':'document','caveat':'Saved original replay'}))
                    if c['source_id'].startswith('discovery:'):
                        params=c['parameters']; profile=PROFILES.get(params.get('profile_id'))
                        source.update(format='discovery',discovery_parameters=params,discovery_hosts=profile['hosts'] if profile else [urlsplit(c['request_url']).hostname])
                    source.update(url=c['request_url'],final_url=c['http']['final_url'],content_type=kind,response_headers=c['http'].get('response_headers',{}),captured_at=c['captured_at_utc'],request_parameters=c.get('parameters',{}),decoded_byte_cap=c.get('byte_cap',8_000_000))
                    try:
                        records,coverage=parse(source,raw_path.read_bytes())
                        row.update(replay='parsed',current_records=len(records),more_available=coverage['more_available'])
                    except Exception as exc:
                        row.update(replay='parser_failure',error_type=type(exc).__name__)
                row['capture_unchanged']=sha(path)==original_hash
                if not row['capture_unchanged']: raise ValueError('Replay changed original capture')
                rows.append(row)
            unchanged=sha(journal)==before
            if not unchanged or attempts>cap: raise ValueError('Ledger changed or quota exceeded')
            output['pilots'].append({'name':name,'attempts':attempts,'cap':cap,'ledger_sha256':before,'ledger_unchanged':unchanged,'captures':rows})
    rows=[r for pilot in output['pilots'] for r in pilot['captures']]
    output['summary']={'capture_count':len(rows),'verified_raw_count':sum(r['raw_hash_verified'] is True for r in rows),'parsed_count':sum(r['replay']=='parsed' for r in rows),'parser_failures':sum(r['replay']=='parser_failure' for r in rows),'originals_unchanged':all(r['capture_unchanged'] for r in rows),'ledgers_unchanged':all(p['ledger_unchanged'] for p in output['pilots'])}
    return output

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--worktree',type=Path,default=Path('.')); parser.add_argument('--output',type=Path,required=True); args=parser.parse_args()
    report=audit(args.worktree.resolve()); args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report['summary']))
    if report['summary']['parser_failures']: raise SystemExit(1)
if __name__=='__main__': main()
