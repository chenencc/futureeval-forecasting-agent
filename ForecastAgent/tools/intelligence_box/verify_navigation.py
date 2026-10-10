"""Read-only replay of existing originals; writes a separate validation report."""
import argparse
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from .navigation import navigate


def verify(root):
    root=Path(root).resolve()
    ledger=root/'journal.sqlite'
    ledger_hash=hashlib.sha256(ledger.read_bytes()).hexdigest()
    with sqlite3.connect(ledger.as_uri()+'?mode=ro',uri=True) as db:
        reserved=db.execute('SELECT count(*) FROM attempts').fetchone()[0]
    cases=[]
    chosen={
        '0b71dccc-7361-46b1-9ae4-fa93c889e704':('Microsoft financial HTML','Revenue'),
        '370528f2-5636-4948-842f-f91df2a7c438':('Congress enrolled text','SEC.'),
        'cbe65c3e-72a0-41d4-be7c-679b85cc5dc0':('Congress public law','SEC.'),
        '60a72aa5-d32f-4383-a82d-8289d34b7094':('UK statute','commencement'),
        'fe03d879-ac8d-405a-a2fe-5c40edea76fa':('Tesla late PDF page','Revenue'),
        '4c788e7a-fa38-4126-8bac-f7632cbca9e0':('Election calendar PDF','2026'),
    }
    with patch('socket.create_connection',side_effect=AssertionError('Network forbidden during replay')):
        for capture_id,(label,query) in chosen.items():
            c=json.loads((root/'captures'/capture_id/'capture.json').read_text(encoding='utf-8'))
            raw_path=(root/c['raw_path']).resolve()
            if not raw_path.is_relative_to(root): raise ValueError('Escaped root')
            raw=raw_path.read_bytes()
            assert hashlib.sha256(raw).hexdigest()==c['raw_sha256']
            started=time.monotonic()
            outline=navigate(c,raw,'outline',limit=100)
            search=navigate(c,raw,'search',query=query,limit=5)
            target='page:27' if label.startswith('Tesla') else search['hits'][0]['unit_id'] if search['hits'] else None
            part=navigate(c,raw,'part',unit_id=target,max_rows=3) if target else None
            tables=[]
            if 'HTML' in label:
                # Paginate the full outline instead of silently retaining its first page.
                offset=outline['next_offset']
                entries=outline['units'][:]
                while offset is not None:
                    page=navigate(c,raw,'outline',offset=offset,limit=100)
                    entries+=page['units']; offset=page['next_offset']
                for unit in entries:
                    if unit['kind']=='table':
                        tables.append(navigate(c,raw,'part',unit_id=unit['unit_id'],max_rows=3))
                        if len(tables)==2: break
            cases.append({'label':label,'capture_id':capture_id,'outline':outline,'search':search,
                          'selected_part':part,'selected_tables':tables,'elapsed_seconds':round(time.monotonic()-started,3),
                          'raw_unchanged':hashlib.sha256(raw_path.read_bytes()).hexdigest()==c['raw_sha256']})
    return {'version':'saved_navigation_validation_v1','checked_at_utc':datetime.now(timezone.utc).isoformat(),
            'network_requests':0,'model_calls':0,'search_provider_calls':0,'reserved_attempts_before':reserved,
            'ledger_unchanged':hashlib.sha256(ledger.read_bytes()).hexdigest()==ledger_hash,
            'cases':cases,'limitations':['Text/table recall is not target relevance or truth verification.',
            'Linux resource/time limits and scanned-PDF OCR are not validated. PDF columns may require layout fallback.']}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    result=verify(args.root)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'cases':len(result['cases']),'ledger_unchanged':result['ledger_unchanged'],
                      'readable_parts':sum(bool(c['selected_part'] and c['selected_part']['status']=='readable') for c in result['cases'])}))
