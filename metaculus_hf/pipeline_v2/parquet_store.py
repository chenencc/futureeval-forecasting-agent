"""Lossless columnar encoding of the question contract, plus native L2 tick columns."""
from __future__ import annotations
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from .contracts import canonical, PipelineError

VERSION=b'forecast-parquet/1'


@lru_cache(maxsize=1)
def arrow():
    import pyarrow as pa
    pa.set_cpu_count(1);pa.set_io_thread_count(1)
    return pa



@lru_cache(maxsize=1)
def warm_storage():
    """Load native conversion dependencies before acquiring any question lock."""
    pa=arrow()
    # The first Python-list conversion may import pandas in environments that
    # have it installed. Keep that cold start outside the writer's lock.
    pa.array([0],type=pa.int8(),from_pandas=False)


def question_schema(metadata=None):
    pa=arrow()
    return pa.schema([('question_id',pa.string()),('kind',pa.string()),('key',pa.string()),
                      ('ordinal',pa.int32()),('value',pa.large_string())],metadata=metadata)


def components(record):
    """Queryable component rows, no duplicate bodies or full-record JSON column."""
    qid=record['question']['id']
    def row(kind,key,value,ordinal=0,*,text=False):
        return dict(question_id=qid,kind=kind,key=key,ordinal=ordinal,value=value if text else canonical(value))
    yield row('record',qid,{k:v for k,v in record.items() if k not in {'question','searches','evidence','runs','orderbook'}})
    yield row('question',qid,{k:v for k,v in record['question'].items() if k not in {'id','versions'}})
    for i,(key,value) in enumerate(record['question']['versions'].items()):yield row('definition',key,value,i)
    for i,value in enumerate(record['searches']):yield row('search',value['search_id'],{k:v for k,v in value.items() if k!='search_id'},i)
    for i,(key,value) in enumerate(record['evidence']['texts'].items()):yield row('text',key,value,i,text=True)
    for kind,name,field in [('source','sources','source_id'),('snapshot','snapshots','snapshot_id')]:
        for i,value in enumerate(record['evidence'][name]):yield row(kind,value[field],{k:v for k,v in value.items() if k!=field},i)
    for i,value in enumerate(record['runs']):yield row('run',value['run_id'],{k:v for k,v in value.items() if k!='run_id'},i)
    yield row('orderbook',qid,record['orderbook'])


def restore(rows):
    out=[]
    for qid in dict.fromkeys(r['question_id'] for r in rows):
        own=[r for r in rows if r['question_id']==qid];groups={}
        for row in own:groups.setdefault(row['kind'],[]).append(row)
        allowed={'record','question','definition','search','text','source','snapshot','run','orderbook'}
        if set(groups)-allowed:raise PipelineError('PARQUET_COMPONENT_KIND')
        for name in ['record','question','orderbook']:
            if len(groups.get(name,[]))!=1:raise PipelineError('PARQUET_COMPONENT_MISSING_OR_DUPLICATE')
        def ordered(name):
            values=sorted(groups.get(name,[]),key=lambda x:x['ordinal'])
            if [r['ordinal'] for r in values]!=list(range(len(values))) or len({r['key'] for r in values})!=len(values):
                raise PipelineError('PARQUET_COMPONENT_ORDER_OR_DUPLICATE')
            return values
        record=json.loads(groups['record'][0]['value']);question=json.loads(groups['question'][0]['value'])
        question.update(id=qid,versions={r['key']:json.loads(r['value']) for r in ordered('definition')})
        record.update(question=question,searches=[dict(json.loads(r['value']),search_id=r['key']) for r in ordered('search')],
            evidence=dict(texts={r['key']:r['value'] for r in ordered('text')},
                          sources=[dict(json.loads(r['value']),source_id=r['key']) for r in ordered('source')],
                          snapshots=[dict(json.loads(r['value']),snapshot_id=r['key']) for r in ordered('snapshot')]),
            runs=[dict(json.loads(r['value']),run_id=r['key']) for r in ordered('run')],orderbook=json.loads(groups['orderbook'][0]['value']))
        out.append(record)
    return out


def question_bytes(records,contract=None):
    pa=arrow()
    import pyarrow.parquet as pq
    logical=(''.join(canonical(r)+'\n' for r in records)).encode()
    metadata={b'format':VERSION,b'kind':b'question-components',b'logical_sha256':hashlib.sha256(logical).hexdigest().encode(),b'records':str(len(records)).encode()}
    table=pa.Table.from_pylist([row for record in records for row in components(record)],schema=question_schema(metadata))
    sink=pa.BufferOutputStream()
    pq.write_table(table,sink,compression='zstd',compression_level=5,use_dictionary=True,write_page_checksum=True)
    return sink.getvalue().to_pybytes()


def read_questions(path):
    arrow()
    import pyarrow.parquet as pq
    file=pq.ParquetFile(path,page_checksum_verification=True)
    try:
        meta=file.schema_arrow.metadata
        if not meta or meta.get(b'format')!=VERSION or meta.get(b'kind')!=b'question-components':raise PipelineError('PARQUET_FORMAT_MISMATCH')
        records=restore(file.read(use_threads=False).to_pylist())
        logical=(''.join(canonical(r)+'\n' for r in records)).encode()
        if len(records)!=int(meta[b'records']) or hashlib.sha256(logical).hexdigest().encode()!=meta[b'logical_sha256']:
            raise PipelineError('PARQUET_LOGICAL_HASH_MISMATCH')
        return records
    finally:file.close()


def book_schema(metadata=None):
    pa=arrow()
    level=pa.struct([('price',pa.string()),('size',pa.string())])
    book=pa.struct([(k,pa.string()) for k in ['asset_id','market','timestamp','hash','tick_size','min_order_size']]+
                   [('neg_risk',pa.bool_()),('bids',pa.list_(level)),('asks',pa.list_(level))])
    return pa.schema([('kind',pa.string()),('slot',pa.int32()),('scheduled_at',pa.string()),('requested_at',pa.string()),
        ('received_at',pa.string()),('request_start_lag_ms',pa.float64()),('status',pa.string()),('books',pa.list_(book)),
        ('missing_tokens',pa.list_(pa.string())),('error_code',pa.string())],metadata=metadata)


def book_delta_schema(metadata=None):
    pa=arrow()
    fields=[f for f in book_schema() if f.name!="books"]
    return pa.schema(fields+[pa.field("book_reset",pa.bool_()),pa.field("book_ops",pa.large_string()),pa.field("clock_receipt",pa.large_string())],metadata=metadata)


def read_book_rows(path):
    arrow()
    import pyarrow.parquet as pq
    file=pq.ParquetFile(path,page_checksum_verification=True)
    try:
        meta=file.schema_arrow.metadata
        if not meta or meta.get(b'format')!=VERSION or meta.get(b'kind')!=b'book':raise PipelineError('PARQUET_FORMAT_MISMATCH')
        header=json.loads(meta[b'header']);footer=json.loads(meta[b'footer'])
        layout=meta.get(b'book_layout')
        if layout not in (None,b'checkpoint-delta-v2'):raise PipelineError('BOOK_LAYOUT_UNSUPPORTED')
        if layout:
            from .book_delta import TickDecoder
            decoder=TickDecoder()
        digest=hashlib.sha256();count=0
        def emit(row):
            nonlocal count
            digest.update((canonical(row)+'\n').encode());count+=1;return row
        yield emit(header)
        # Writer row groups are bounded by 64 ticks / ~4 MiB. Read one group
        # at a time, without per-tick native thread-pool handoffs. Row order,
        # page checksums and the full logical hash are still verified.
        for group in range(file.num_row_groups):
            for batch in file.iter_batches(batch_size=64,row_groups=[group],use_threads=False):
                for row in batch.to_pylist():yield emit(decoder.decode(row) if layout else row)
        if footer is not None:yield emit(footer)
        if count!=int(meta[b'logical_records']) or digest.hexdigest().encode()!=meta[b'logical_sha256']:
            raise PipelineError('PARQUET_LOGICAL_HASH_MISMATCH')
    finally:file.close()
