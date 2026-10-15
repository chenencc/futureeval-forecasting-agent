"""Import separately timestamped local vintages without reopening native tasks."""
import hashlib
import json
from pathlib import Path

from .store import append,digest


def import_revisions(db,data_root):
    root=Path(data_root).resolve()
    path=root/'latest-material-revisions.json'
    if not path.exists(): return {'status':'not_present','imported':0}
    registry=json.loads(path.read_text(encoding='utf-8'))
    if registry.get('schema')!='market-material-forecast-revision-v1':
        raise ValueError('Unknown local revision registry')
    imported=0
    with db:
        for row in registry['cases']:
            key=row['key']
            if not db.execute('SELECT 1 FROM markets WHERE key=?',(key,)).fetchone(): continue
            original=Path(row['result_file']).resolve()
            if not original.is_relative_to(root) or original.stat().st_size>8*1024**2:
                raise ValueError('Local revision must be a bounded market data file')
            raw=original.read_bytes()
            value=json.loads(raw)
            # Native material-revision v1 hashes canonical JSON values, not the
            # pretty-printed file bytes. Preserve both schemes explicitly.
            if digest(value)!=row['result_sha256']:
                raise ValueError('Local revision content hash mismatch')
            if value['key']!=key or value['revision_id']!=row['revision_id']:
                raise ValueError('Local revision identity mismatch')
            observed=value.get('completed_at') or registry['archived_at']
            token=digest({'kind':'revision','key':key,'sha256':row['result_sha256']})
            if not db.execute('SELECT 1 FROM records WHERE id=?',(token,)).fetchone():
                imported+=append(db,'revision',key,{**row,'payload':value,
                    'result_hash_scheme':'native_canonical_json_utf8_v1',
                    'original_file_byte_sha256':hashlib.sha256(raw).hexdigest()},observed,ident=token)
            forecast=value.get('forecast') or {}
            for arm,arm_value in forecast.get('arms',{}).items():
                data={'arm':arm,'sealed_at':forecast.get('sealed_at'),'anchor_at':forecast.get('anchor_at'),
                    'value':arm_value,'revision_id':row['revision_id'],'material_sha256':row.get('material_sha256'),
                    'graph_sha256':row.get('graph_sha256'),'source_sha256':row['result_sha256']}
                token=digest({'kind':'forecast','key':key,'data':data})
                if not db.execute('SELECT 1 FROM records WHERE id=?',(token,)).fetchone():
                    append(db,'forecast',key,data,observed,ident=token)
    return {'status':'imported','imported':imported,'registry_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
