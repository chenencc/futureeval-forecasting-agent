"""Crash-safe book Parquet checkpoints backed by a private fsynced temporary journal.

Only question.parquet and orderbook.parquet are final question artifacts. The
journal is private in .inflight and removed only after exact logical validation.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import time
from .contracts import canonical, PipelineError
from .repository import ensure
from .parquet_store import VERSION, book_delta_schema, read_book_rows
from .book_delta import FRAME,LAYOUT,TickEncoder,TickDecoder


def raw_frames(path,limit=None,*,tolerate_tail=False):
    with Path(path).open('rb') as f:
        end=Path(path).stat().st_size if limit is None else limit
        while f.tell()<end:
            prefix=f.read(40)
            if len(prefix)<40:
                if tolerate_tail:return
                raise PipelineError('BOOK_JOURNAL_TRUNCATED')
            size=struct.unpack('>Q',prefix[:8])[0]
            ensure(size<=16*1024*1024,'BOOK_JOURNAL_FRAME_TOO_LARGE')
            raw=f.read(size)
            if len(raw)!=size or f.tell()>end:
                if tolerate_tail:return
                raise PipelineError('BOOK_JOURNAL_TRUNCATED')
            ensure(hashlib.sha256(raw).digest()==prefix[8:],'BOOK_JOURNAL_HASH_MISMATCH')
            yield json.loads(raw),f.tell()


def frames(path,limit=None,*,tolerate_tail=False):
    decoder=TickDecoder()
    for row,end in raw_frames(path,limit,tolerate_tail=tolerate_tail):
        if set(row)=={FRAME}:row=decoder.decode(row[FRAME])
        yield row,end


def publish(path,journal,cut,header,footer,logical_hash,count):
    import pyarrow as pa
    import pyarrow.parquet as pq
    metadata={b'format':VERSION,b'kind':b'book',b'header':canonical(header).encode(),b'footer':canonical(footer).encode(),
              b'logical_sha256':logical_hash.encode(),b'logical_records':str(count).encode(),b'book_layout':LAYOUT.encode(),b'book_checkpoint_ticks':b'60'}
    schema=book_delta_schema(metadata)
    fd,temp=tempfile.mkstemp(prefix='.uncommitted-book-',dir=Path(path).parent);done=False
    try:
        with os.fdopen(fd,'wb') as stream:
            with pq.ParquetWriter(stream,schema,compression='zstd',compression_level=5,use_dictionary=True,write_page_checksum=True) as writer:
                batch=[];size=0;encoder=TickEncoder()
                for frame,_ in raw_frames(journal,cut):
                    if set(frame)=={FRAME}:row=frame[FRAME]
                    elif frame['kind']=='tick':row=encoder.encode(frame)
                    else:continue
                    batch.append(row);size+=len(canonical(row))
                    if len(batch)>=64 or size>=4*1024*1024:
                        writer.write_table(pa.Table.from_pylist(batch,schema=schema));batch=[];size=0
                if batch:writer.write_table(pa.Table.from_pylist(batch,schema=schema))
            stream.flush();os.fsync(stream.fileno())
        # A readable footer, page checksums and logical rows must all agree before publication.
        ensure(sum(1 for _ in read_book_rows(temp))==count,'BOOK_PARQUET_ROW_COUNT')
        for attempt in range(20):
            try:os.replace(temp,path);done=True;break
            except PermissionError:
                if attempt==19:raise
                time.sleep(.025)
        if os.name=='posix':
            directory=os.open(Path(path).parent,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
    finally:
        if not done and os.path.exists(temp):os.unlink(temp)


class BookParquetStream:
    def __init__(self,path,journal,*,checkpoint_ticks=60):
        self.path,self.journal=Path(path),Path(journal)
        ensure(not self.path.exists() and not self.journal.exists(),'BOOK_ALREADY_STARTED')
        self.journal.parent.mkdir(parents=True,exist_ok=True)
        self.file=self.journal.open('xb');self.lock=threading.Lock();self.wake=threading.Event()
        self.header=self.footer=None;self.cut=self.count=self.published=0
        self.encoder=TickEncoder();self.digest=hashlib.sha256();self.closing=False;self.error=None;self.checkpoint_ticks=checkpoint_ticks
        self.thread=threading.Thread(target=self.worker,name='book-parquet-checkpoint',daemon=True);self.thread.start()
    def __enter__(self):return self
    def tell(self):return self.cut
    def write_record(self,row):
        logical=(canonical(row)+'\n').encode()
        with self.lock:
            if self.error:raise PipelineError('BOOK_CHECKPOINT_FAILED',type(self.error).__name__)
            stored={FRAME:self.encoder.encode(row)} if row['kind']=='tick' else row
            raw=(canonical(stored)+'\n').encode()
            self.file.write(struct.pack('>Q',len(raw))+hashlib.sha256(raw).digest()+raw);self.file.flush();os.fsync(self.file.fileno())
            self.digest.update(logical);self.count+=1;self.cut=self.file.tell()
            if row['kind']=='start':self.header=row
            elif row['kind']=='end':self.footer=row
            if row['kind'] in {'start','end'} or (row.get('slot',-1)+1)%self.checkpoint_ticks==0:self.wake.set()
    def worker(self):
        try:
            while True:
                self.wake.wait();self.wake.clear()
                with self.lock:cut,header,footer,digest,count,closing=self.cut,self.header,self.footer,self.digest.hexdigest(),self.count,self.closing
                if header is not None and cut!=self.published:
                    publish(self.path,self.journal,cut,header,footer,digest,count);self.published=cut
                if closing:
                    with self.lock:
                        if self.cut==self.published:return
                    self.wake.set()
        except BaseException as exc:self.error=exc
    def __exit__(self,kind,value,tb):
        with self.lock:self.closing=True
        self.wake.set();self.thread.join(60);self.file.close()
        ensure(not self.thread.is_alive(),'BOOK_CHECKPOINT_TIMEOUT')
        if self.error:raise PipelineError('BOOK_CHECKPOINT_FAILED',type(self.error).__name__) from self.error
        ensure(self.published==self.cut,'BOOK_CHECKPOINT_NOT_CURRENT')
        # These are this writer's temporary durability bytes, never historical originals.
        if self.footer is not None and kind is None:self.journal.unlink()


def recover(journal,path):
    """Recover durable complete frames without a new request or a new recording window.

    A torn final frame is reported, not repaired in place. Keep the journal.
    A partial recording remains visibly unsealed; it is never a complete hour.
    """
    import pyarrow.parquet as pq
    existing=None
    if Path(path).exists():
        with pq.ParquetFile(path) as old:
            meta=old.schema_arrow.metadata
            ensure(meta.get(b'kind')==b'book','BOOK_RECOVERY_WRONG_TARGET')
            existing=(int(meta[b'logical_records']),meta[b'logical_sha256'].decode(),json.loads(meta[b'header']))
    header=footer=None;digest=hashlib.sha256();count=cut=0;prefix_matches=existing is None
    for row,end in frames(journal,tolerate_tail=True):
        if row['kind']=='start':header=row
        if row['kind']=='end':footer=row
        digest.update((canonical(row)+'\n').encode());count+=1;cut=end
        if existing and count==existing[0]:prefix_matches=digest.hexdigest()==existing[1]
    ensure(header is not None,'BOOK_JOURNAL_HEADER_MISSING')
    ensure(existing is None or (existing[0]<=count and prefix_matches and existing[2]==header),'BOOK_RECOVERY_WOULD_REPLACE_DIFFERENT_DATA')
    publish(path,journal,cut,header,footer,digest.hexdigest(),count)
    return dict(logical_records=count,torn_tail_bytes=Path(journal).stat().st_size-cut,sealed=footer is not None,journal_preserved=True)
