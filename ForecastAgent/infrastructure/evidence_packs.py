"""Lossless per-question transport packs; collection and analysis stay untouched."""
import hashlib
import io
import json
import lzma
from pathlib import Path, PurePosixPath
import re
import tarfile
import zipfile

SCHEMA='forecast-evidence-packs-v1'
MAX_FILE=64*1024**2
MAX_TOTAL=256*1024**2


def sha(data):return hashlib.sha256(data).hexdigest()
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False),encoding='utf-8')
def safe_path(name):
    p=PurePosixPath(name)
    if not name or '\\' in name or ':' in name or p.is_absolute() or '..' in p.parts or str(p)!=name:
        raise ValueError('Unsafe original path')
    return name


def build(cases,output,source):
    """cases contain exact original bytes, not reconstructed or stripped JSON."""
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    manifest={'schema':SCHEMA,'source':source,'cases':[],'files':[],'codec':'tar.xz-preset8',
              'expanded_file_bytes':0,'lossless_original_bytes':True}
    legacy=io.BytesIO();seen=set()
    with zipfile.ZipFile(legacy,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for case in cases:
            objects={};files=[]
            for name,data in sorted(case['files'].items()):
                safe_path(name)
                if name in seen or not isinstance(data,bytes) or len(data)>MAX_FILE:raise ValueError('Invalid or duplicate original')
                seen.add(name);digest=sha(data);objects[digest]=data
                row={'path':name,'sha256':digest,'size_bytes':len(data),'question_key':case['key']}
                files.append(row);manifest['files'].append(row);manifest['expanded_file_bytes']+=len(data)
                if manifest['expanded_file_bytes']>MAX_TOTAL:raise ValueError('Trial source cap exceeded')
                info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(info,data,compresslevel=6)
            raw=io.BytesIO()
            with tarfile.open(fileobj=raw,mode='w') as tar:
                for digest,data in sorted(objects.items()):
                    info=tarfile.TarInfo('objects/'+digest);info.size=len(data);info.mode=0o444;info.mtime=0
                    tar.addfile(info,io.BytesIO(data))
            compressed=lzma.compress(raw.getvalue(),preset=8);digest=sha(compressed)
            name='packs/'+digest+'.tar.xz';path=output/'packed'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(compressed)
            manifest['cases'].append({'key':case['key'],'files':files,
                'pack':{'path':name,'sha256':digest,'size_bytes':len(compressed),
                        'objects':{h:len(data) for h,data in objects.items()},'expanded_object_bytes':sum(map(len,objects.values()))}})
    (output/'baseline').mkdir(exist_ok=True);(output/'baseline/legacy-corpus.zip').write_bytes(legacy.getvalue())
    manifest['baseline_zip_sha256']=sha(legacy.getvalue());manifest['baseline_zip_bytes']=len(legacy.getvalue())
    manifest['packed_bytes']=sum(c['pack']['size_bytes'] for c in manifest['cases'])
    write(output/'heads/manifest.json',manifest)
    write(output/'heads/head.json',{'schema':'forecast-transport-head-v1','source':source,
        'cases':[{'key':c['key'],'file_count':len(c['files']),'original_bytes':sum(f['size_bytes'] for f in c['files']),
                  'pack_sha256':c['pack']['sha256'],'pack_bytes':c['pack']['size_bytes']} for c in manifest['cases']],
        'baseline_zip_bytes':manifest['baseline_zip_bytes'],'packed_bytes':manifest['packed_bytes'],
        'forecasting_or_search_calls':0})
    return manifest


def verify_pack(data,case):
    pack=case['pack']
    if len(data)!=pack['size_bytes'] or sha(data)!=pack['sha256']:raise ValueError('Compressed pack changed')
    expected=pack['objects'];result={}
    if not expected or sum(expected.values())>MAX_TOTAL:raise ValueError('Invalid expansion allowance')
    with lzma.LZMAFile(io.BytesIO(data)) as decoded,tarfile.open(fileobj=decoded,mode='r|') as tar:
        for info in tar:
            if not info.isreg() or not re.fullmatch('objects/[0-9a-f]{64}',info.name):raise ValueError('Unexpected pack member')
            key=info.name.split('/')[1]
            if key not in expected or key in result or info.size!=expected[key] or not 0<=info.size<=MAX_FILE:
                raise ValueError('Unexpected object or size')
            content=tar.extractfile(info).read(info.size+1)
            if len(content)!=info.size or sha(content)!=key:raise ValueError('Original object changed')
            result[key]=content
    if set(result)!=set(expected):raise ValueError('Incomplete pack')
    for row in case['files']:
        safe_path(row['path'])
        if row['sha256'] not in result or len(result[row['sha256']])!=row['size_bytes']:raise ValueError('Original path index changed')
    return result


def available(root,case):
    """A local flag is insufficient; verify every stored original before reuse."""
    for row in case['files']:
        path=Path(root)/'objects'/row['sha256']
        if not path.exists():return False
        if path.stat().st_size!=row['size_bytes'] or sha(path.read_bytes())!=row['sha256']:
            raise ValueError('Local original changed')
    return True


def store(root,case,data):
    root=Path(root);objects=verify_pack(data,case)
    for digest,content in objects.items():
        path=root/'objects'/digest;path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and path.read_bytes()!=content:raise ValueError('Existing original changed')
        if not path.exists():path.write_bytes(content)
    for row in case['files']:
        target=root/'restored'/safe_path(row['path']);target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and target.read_bytes()!=objects[row['sha256']]:raise ValueError('Restored original changed')
        if not target.exists():target.write_bytes(objects[row['sha256']])
    assert available(root,case)
