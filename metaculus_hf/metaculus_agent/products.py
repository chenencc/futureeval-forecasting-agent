"""Same Parquet envelope and intelligence payload as shared acquisition."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from pipeline_v2.contracts import digest,PipelineError
from pipeline_v2.delivery import parquet_payload
from pipeline_v2.intelligence_package import snapshot_from_payload,load
from pipeline_v2.admission import decision_view
from .storage import sha

def prefix(task):
    date=datetime.fromisoformat(task['started_at'].replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    if not str(task['id']).isdecimal() or not task['run_id'].isalnum():raise ValueError('PRODUCT_IDENTITY')
    return f"metaculus/start_date={date}/question_id={task['id']}/run_id={task['run_id']}"
def context(task,package,snapshot_id):
    snap=snapshot_from_payload(package,snapshot_id)
    if package['question_id']!='metaculus:'+str(task['id']):raise ValueError('CONTEXT_QUESTION_MISMATCH')
    return dict(schema='metaculus-context/1',question_id=package['question_id'],post_id=task['post_id'],run_id=task['run_id'],
        started_at=task['started_at'],partition_timezone='Asia/Shanghai',snapshot_id=snapshot_id,
        evidence_cutoff_at=snap['evidence_cutoff_at'],rule_identity=task['rule_identity'],
        request=task['request'],execution_profile=task['execution_profile'],intelligence=package,
        run_purpose=task.get('run_purpose','live'),test_only=bool(task.get('test_only',False)))
def verify_context(value):
    if value.get('schema')!='metaculus-context/1':raise ValueError('CONTEXT_SCHEMA')
    snap=snapshot_from_payload(value['intelligence'],value['snapshot_id'])
    if value['question_id']!=value['intelligence']['question_id'] or value['evidence_cutoff_at']!=snap['evidence_cutoff_at']:raise ValueError('CONTEXT_BINDING')
    return snap
def bundle(value):
    snap=verify_context(value);view=decision_view(snap,'original');pages={};bindings={}
    for material in view['materials']:
        binding=material['binding'];source=material['source'];url=source['source_url'];text=material.get('text')
        if not text:continue
        if url in pages and pages[url]['content']!=text:raise ValueError('AMBIGUOUS_SOURCE_BODY')
        pages[url]=dict(content=text,retrieved_at_utc=source.get('captured_at'),content_sha256=source['text_sha256'],
            body_diagnostics=dict(usable_text=True,truth_verified=False,
                review_status='unverified' if material.get('decision_reading') else 'reviewed'),capture_metadata=source)
        bindings[url]=binding
    if not pages:raise PipelineError('NO_USABLE_EVIDENCE')
    reading=view['original_reading'];request={**value['request'],'original_evidence_reading':reading}
    return dict(request=request,pages=pages,gaps=snap['gaps'],original_reading=reading,
        result=dict(incomplete=False),collection_handoff=dict(protocol='shared-snapshot-to-official/2'),
        source_aliases={},source_bindings=bindings,source_snapshot_id=value['snapshot_id'],
        context_sha256=digest(value),evidence_cutoff_at=value['evidence_cutoff_at'])
def product(kind,value):
    raw=parquet_payload(value)
    import pyarrow as pa,pyarrow.parquet as pq
    with pq.ParquetFile(pa.BufferReader(raw),page_checksum_verification=True) as f:
        restored=json.loads(f.read().to_pylist()[0]['payload'])
    if restored!=value:raise ValueError('PARQUET_ROUNDTRIP')
    if kind=='context':verify_context(restored)
    return raw
def manifest(task,files):return dict(schema='metaculus-manifest/1',question_id=task['id'],post_id=task['post_id'],run_id=task['run_id'],
    snapshot_id=task.get('snapshot_id'),files={name:dict(sha256=sha(body),bytes=len(body)) for name,body in files.items()})
