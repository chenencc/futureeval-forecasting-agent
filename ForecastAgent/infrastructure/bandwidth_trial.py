"""Cloud-only source repacking and bounded actual GitHub-to-host experiments."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import zipfile
from urllib.request import Request, build_opener

from . import evidence_packs as packs

REPO='chenencc/futureeval-forecasting-agent'
FIXTURE=Path(__file__).with_name('fixtures')/'bandwidth_pack_five.json'


def produce(output):
    """The large original archive never travels to the user's computer."""
    from ForecastAgent.market_repricing.github_state import api,ArtifactRedirect
    fixed=json.loads(FIXTURE.read_text());run=api('/actions/runs/'+str(fixed['source_run_id']))
    if run['conclusion']!='success' or run['name']!='Market repricing research worker' or run['head_branch']!='main':
        raise ValueError('Untrusted source run')
    artifact=api('/actions/artifacts/'+str(fixed['source_artifact_id']))
    if artifact['expired'] or artifact['name']!=fixed['source_artifact_name'] or artifact['size_in_bytes']!=fixed['source_compressed_bytes']:
        raise ValueError('Frozen source artifact differs')
    if artifact['workflow_run']['id']!=fixed['source_run_id']:raise ValueError('Wrong source run')
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    source=output/'source.zip'
    request=Request('https://api.github.com/repos/'+REPO+'/actions/artifacts/'+str(artifact['id'])+'/zip',
        headers={'Authorization':'Bearer '+os.environ['GH_STATE_TOKEN'],'Accept':'application/vnd.github+json'})
    digest=hashlib.sha256();total=0
    with build_opener(ArtifactRedirect()).open(request,timeout=120) as response,source.open('wb') as saved:
        while data:=response.read(1024*1024):
            total+=len(data)
            if total>1024**3:raise ValueError('Cloud source compressed cap exceeded')
            saved.write(data);digest.update(data)
    if total!=artifact['size_in_bytes']:raise ValueError('Cloud download size differs')
    if artifact.get('digest') and artifact['digest']!='sha256:'+digest.hexdigest():raise ValueError('Cloud archive hash differs')
    cases=[]
    with zipfile.ZipFile(source) as archive:
        infos=archive.infolist()
        if len(infos)>20000 or sum(i.file_size for i in infos)>5*1024**3:raise ValueError('Cloud source expansion bound exceeded')
        members={}
        for info in infos:
            if info.is_dir():continue
            packs.safe_path(info.filename)
            if info.filename in members or stat.S_ISLNK(info.external_attr>>16):raise ValueError('Unsafe cloud member')
            members[info.filename]=info
        for case in fixed['cases']:
            files={}
            for row in case['files']:
                info=members[row['path']]
                if info.file_size!=row['size_bytes'] or info.file_size>packs.MAX_FILE:raise ValueError('Frozen original size differs')
                content=archive.read(info)
                if packs.sha(content)!=row['sha256']:raise ValueError('Frozen original hash differs')
                files[row['path']]=content
            cases.append({'key':case['key'],'files':files})
    provenance={'repo':REPO,'run_id':fixed['source_run_id'],'artifact_id':artifact['id'],
        'cloud_source_bytes':total,'local_source_archive_download_bytes':0,
        'producer_run_id':os.environ['GITHUB_RUN_ID'],'producer_commit':os.environ['GITHUB_SHA']}
    manifest=packs.build(cases,output,provenance)
    # One fresh baseline, one fresh packed transfer and its partial/resume pass
    # share an explicit finite host allowance. No provider quota is involved.
    estimate=manifest['baseline_zip_bytes']+manifest['packed_bytes']+1024**2
    if estimate>fixed['maximum_local_download_bytes']:
        raise ValueError('Estimated trial transfer exceeds predeclared host cap')
    for case in manifest['cases']:
        objects=packs.verify_pack((output/'packed'/case['pack']['path']).read_bytes(),case)
        for row in case['files']:assert packs.sha(objects[row['sha256']])==row['sha256']
    report={'schema':'bandwidth-cloud-producer-v1','raw_bytes':manifest['expanded_file_bytes'],
        'baseline_zip_bytes':manifest['baseline_zip_bytes'],'packed_bytes':manifest['packed_bytes'],
        'estimated_host_allowance_bytes':estimate,'host_cap_bytes':fixed['maximum_local_download_bytes'],
        'five_cases':True,'ten_original_files_sha_verified':True,'all_packs_roundtrip_verified':True,
        'no_model_search_calls':True,'producer':provenance}
    packs.write(output/'heads/producer-report.json',report)
    print(json.dumps(report))


def client(gh):
    from ForecastAgent.local_sync import GitHub
    return GitHub(gh)


def open_artifact(gh,metadata,root,budget):
    from ForecastAgent.local_transfer import RangeFile,SignedRanges,ArtifactZip
    reader=RangeFile(Path(root)/'ranges'/str(metadata['id']),metadata,
                    SignedRanges(gh,REPO,metadata['id'],metadata,budget),budget)
    return ArtifactZip(reader)


def validate_meta(cli,run_id,name):
    run=cli.api('repos/'+REPO+'/actions/runs/'+str(run_id))
    if run['name']!='Bandwidth evidence pack trial' or run['head_branch']!='codex/bandwidth-pack-validation' or run['conclusion']!='success':
        raise ValueError('Untrusted trial producer')
    rows=cli.api('repos/'+REPO+'/actions/runs/'+str(run_id)+'/artifacts')['artifacts']
    matching=[a for a in rows if a['name']==name and not a['expired']]
    if len(matching)!=1:raise ValueError('Exact trial artifact unavailable')
    meta=cli.api('repos/'+REPO+'/actions/artifacts/'+str(matching[0]['id']))
    if meta['workflow_run']['id']!=int(run_id):raise ValueError('Artifact run differs')
    return meta


def measure(gh,run_id,root,phase):
    from ForecastAgent.local_transfer import TrafficBudget
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    cap=json.loads(FIXTURE.read_text())['maximum_local_download_bytes']
    budget=TrafficBudget(root,cap,cap);cli=client(gh)
    manifest_path=root/'manifest.json'
    if not manifest_path.exists():
        head=validate_meta(cli,run_id,'bandwidth-trial-heads')
        with open_artifact(gh,head,root/'heads',budget) as archive:
            if set(archive.members)!={'manifest.json','head.json','producer-report.json'}:raise ValueError('Unexpected head members')
            for name in archive.members:
                data=archive.read(name,maximum=128*1024);(root/name).write_bytes(data)
        packs.write(root/'head-integrity.json',{'producer_run_id':str(run_id),
            'files':{name:packs.sha((root/name).read_bytes()) for name in ('manifest.json','head.json','producer-report.json')}})
    integrity=json.loads((root/'head-integrity.json').read_text())
    if integrity['producer_run_id']!=str(run_id) or any(packs.sha((root/name).read_bytes())!=digest for name,digest in integrity['files'].items()):
        raise ValueError('Cached trial head changed')
    manifest=json.loads(manifest_path.read_text())
    if manifest['schema']!=packs.SCHEMA or manifest['source']['producer_run_id']!=str(run_id):raise ValueError('Frozen trial identity changed')
    fixed=json.loads(FIXTURE.read_text());expected={r['path']:(r['sha256'],r['size_bytes']) for c in fixed['cases'] for r in c['files']}
    if {r['path']:(r['sha256'],r['size_bytes']) for r in manifest['files']}!=expected:
        raise ValueError('Experiment source cohort changed')
    before=budget.snapshot()['reserved_bytes_today'];details={}
    if phase=='head':
        details={'body_packs_downloaded':0,'head_only':True}
    elif phase=='baseline':
        meta=validate_meta(cli,run_id,'bandwidth-trial-baseline')
        with open_artifact(gh,meta,root/'baseline-cache',budget) as archive:
            data=archive.read('legacy-corpus.zip',maximum=cap)
        if packs.sha(data)!=manifest['baseline_zip_sha256']:raise ValueError('Baseline corpus changed')
        with zipfile.ZipFile(io.BytesIO(data)) as corpus:
            expected={r['path']:r for r in manifest['files']}
            if set(corpus.namelist())!=set(expected):raise ValueError('Baseline original set differs')
            for name,row in expected.items():
                if packs.sha(corpus.read(name))!=row['sha256']:raise ValueError('Baseline original changed')
        (root/'baseline-corpus.zip').write_bytes(data);details={'verified_original_files':len(manifest['files'])}
    elif phase in {'partial','resume','repeat'}:
        missing=[c for c in manifest['cases'] if not packs.available(root/'packed-cache',c)]
        if missing:
            meta=validate_meta(cli,run_id,'bandwidth-trial-packed')
            with open_artifact(gh,meta,root/'packed-ranges',budget) as archive:
                if set(archive.members)!={c['pack']['path'] for c in manifest['cases']}:raise ValueError('Unexpected packed members')
                if phase=='partial':
                    case=max(missing,key=lambda c:c['pack']['size_bytes'])
                    size=max(1,min(256*1024,case['pack']['size_bytes']//2))
                    with archive.archive.open(case['pack']['path']) as stream:stream.read(size)
                    details={'controlled_stop_after_payload_bytes':size,'partial_pack':case['key'],
                        'no_original_marked_complete':True,'range_cache_retained':True}
                else:
                    for case in missing:
                        data=archive.read(case['pack']['path'],maximum=cap)
                        packs.store(root/'packed-cache',case,data)
                    details={'verified_original_files':len(manifest['files']),'verified_cases':len(manifest['cases'])}
        else:details={'verified_original_files':len(manifest['files']),'no_payload_request_needed':True}
    else:raise ValueError('Unknown phase')
    snap=budget.snapshot();report={'phase':phase,'run_id':str(run_id),'details':details,'traffic':snap,
        'reserved_delta_excluding_initial_head':snap['reserved_bytes_today']-before,
        'measured_scope':'Actual artifact response-body bytes; API metadata and transport headers excluded.',
        'production_sync_policy_changed':False,'production_provider_calls':0}
    packs.write(root/(phase+'-report.json'),report)
    print(json.dumps(report))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['produce','measure'])
    p.add_argument('--root',type=Path,required=True);p.add_argument('--gh');p.add_argument('--run-id');p.add_argument('--phase')
    a=p.parse_args()
    if a.mode=='produce':produce(a.root)
    else:measure(a.gh,a.run_id,a.root,a.phase)
