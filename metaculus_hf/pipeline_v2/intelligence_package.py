"""One compact replay package: original immutable evidence plus final review results."""
from copy import deepcopy
import json
from .contracts import digest
from .repository import ensure
from .delivery import parquet_payload,sha


def load(path):
    import pyarrow.parquet as pq
    with pq.ParquetFile(path,page_checksum_verification=True) as f:rows=f.read(use_threads=False).to_pylist()
    ensure(len(rows)==1,'SPLIT_ROW_COUNT')
    return json.loads(rows[0]['payload'])


def original(payload):
    return dict(schema='intelligence-package/1',**{k:payload[k] for k in ('question_id','definitions','searches','evidence')})


def package_for_question(payload,qid):
    if payload.get('schema')=='recording-group-context/1':
        from .collection_intelligence import validate_context
        payload=validate_context(payload)
    ensure(payload.get('question_id')==qid,'SPLIT_IDENTITY_MISMATCH')
    return payload


def export(repo,record,body):
    from .review_lifecycle import export_reviews
    payload=load(body);reviews=export_reviews(repo,record)
    if not reviews:return body.read_bytes(),{}
    base=original(payload)
    ensure(payload==base,'NATIVE_PACKAGE_SCHEMA')
    payload.update(schema='intelligence-package/2',source_package_sha256=sha(body),source_payload_hash=digest(base),final_reviews=reviews)
    return parquet_payload(payload),dict(source_sha256=sha(body),source_payload_hash=digest(base),review_results_hash=digest(reviews))


def validate_export(payload,ref,remote):
    if payload.get('schema')=='recording-group-context/1':
        payload=package_for_question(payload,remote['question_id'])
        ensure(remote['source_sha256']==ref['sha256'] and
               remote['source_payload_hash']==digest(original(payload)),'GROUP_SOURCE_BODY_MISMATCH')
        if payload.get('schema')=='intelligence-package/2':
            ensure(payload['source_package_sha256']==ref['sha256'] and
                   remote['review_results_hash']==digest(payload['final_reviews']),'GROUP_SOURCE_REVIEW_MISMATCH')
        return
    if payload.get('schema')!='intelligence-package/2':
        ensure(remote['sha256']==ref['sha256'],'SPLIT_HASH_MISMATCH');return
    ensure(remote.get('source_sha256')==ref['sha256']==payload['source_package_sha256'],'SPLIT_SOURCE_HASH_MISMATCH')
    ensure(remote.get('source_payload_hash')==payload['source_payload_hash']==digest(original(payload)),'SPLIT_SOURCE_PAYLOAD_MISMATCH')
    reviews=payload['final_reviews']
    ensure(remote.get('review_results_hash')==digest(reviews),'SPLIT_REVIEW_HASH_MISMATCH')
    for row in reviews:ensure(row['result_hash']==digest({k:v for k,v in row.items() if k!='result_hash'}),'SPLIT_REVIEW_RESULT_HASH')


def read_final_snapshot(repo,qid,sid):
    """No inference: original body snapshot and an explicit final review overlay."""
    from .split_repository import index_read,body_file
    from .review_recovery import compose_reviewed_snapshot
    from .review_lifecycle import export_reviews
    path,_=repo.paths(qid);index=index_read(path)
    with body_file(repo,path,index) as body:payload=package_for_question(load(body),qid)
    snap=repo.read_snapshot(qid,sid)
    reviews=payload.get('final_reviews')
    if reviews is None:reviews=export_reviews(repo,repo.get_runtime(qid))
    review=next((r for r in reviews if r['source_snapshot_id']==sid),None)
    if review and review['status']=='completed':return compose_reviewed_snapshot(snap,review,qid,sid)
    # An honest failure/uncertainty is replayable as such, never called approved.
    result=deepcopy(snap)
    result['final_review_status']=review or snap['context'].get('final_review',{'status':'legacy_untracked'})
    return result


def snapshot_from_package(path,sid,*,reviewed=True):
    """Standalone backtest input: needs only this Parquet, no server control/DB."""
    data=load(path)
    if data.get('schema')=='recording-group-context/1':
        from .collection_intelligence import validate_context
        validate_context(data)
        candidates=[p for p in data.get('intelligence',[]) if any(
            s.get('snapshot_id')==sid for s in p.get('evidence',{}).get('snapshots',[]))]
        ensure(data.get('intelligence_status')=='sealed' and len(candidates)==1,'GROUP_SNAPSHOT_NOT_FOUND')
        data=candidates[0]
    return snapshot_from_payload(data,sid,reviewed=reviewed)


def snapshot_from_payload(data,sid,*,reviewed=True):
    """Same immutable evidence validation for standalone and group contexts."""
    import hashlib
    from .contracts import snapshot_payload
    from .review_recovery import compose_reviewed_snapshot
    ensure(data.get('schema') in {'intelligence-package/1','intelligence-package/2'},'INTELLIGENCE_PACKAGE_SCHEMA')
    if data.get('schema')=='intelligence-package/2':
        ensure(data['source_payload_hash']==digest(original(data)),'SPLIT_SOURCE_PAYLOAD_MISMATCH')
    record=dict(question=dict(id=data['question_id'],versions=data['definitions']),evidence=data['evidence'])
    saved=[s for s in data['evidence']['snapshots'] if s['snapshot_id']==sid]
    ensure(len(saved)==1,'SNAPSHOT_NOT_FOUND')
    value=snapshot_payload(record,saved[0]);ensure(digest(value)==sid,'SNAPSHOT_HASH_MISMATCH')
    for m in value['materials']:
        hashed=m['source']['text_sha256'];m['text']=data['evidence']['texts'].get(hashed) if hashed else None
        if hashed:ensure(m['text'] is not None and hashlib.sha256(m['text'].encode()).hexdigest()==hashed,'REVIEW_BODY_HASH_MISMATCH')
    snap=dict(snapshot_id=sid,**value)
    if not reviewed:return snap
    row=next((r for r in data.get('final_reviews',[]) if r['source_snapshot_id']==sid),None)
    if row:
        ensure(row['result_hash']==digest({k:v for k,v in row.items() if k!='result_hash'}),'SPLIT_REVIEW_RESULT_HASH')
        ensure(row['input_body']['sha256']==data['source_package_sha256'],'SPLIT_REVIEW_SOURCE_MISMATCH')
        if row['status']=='completed':return compose_reviewed_snapshot(snap,row,data['question_id'],sid)
    snap['final_review_status']=row or snap['context'].get('final_review',{'status':'legacy_untracked'})
    return snap
