"""Host-local authenticated browser pool shared by capture processes."""
import asyncio
import hmac
import json
import multiprocessing as mp
import os
from pathlib import Path
import secrets
import signal
import time

from .contracts import PipelineError,now
from .repository import atomic_bytes,ensure,file_lock

POLICY='bounded-crawl4ai-pool-v1'


def owner_token(pid=None):
    import psutil
    try:return psutil.Process(pid or os.getpid()).create_time()
    except psutil.Error:return None


class Slot:
    def __init__(self,worker,fixture_origin):
        self.worker,self.fixture_origin=worker,fixture_origin;self.process=None;self.conn=None;self.ready=False

    def start(self):
        ctx=mp.get_context('spawn');parent,child=ctx.Pipe()
        self.process=ctx.Process(target=self.worker,args=(child,self.fixture_origin),name='v2-crawl4ai-browser')
        self.process.start();child.close();self.conn=parent

    async def stop(self):
        self.ready=False
        p=self.process
        if p is not None:
            if p.is_alive():
                if os.name=='posix' and os.getpgid(p.pid)==p.pid:os.killpg(p.pid,signal.SIGTERM)
                else:p.terminate()
                await asyncio.to_thread(p.join,2)
            if p.is_alive():
                if os.name=='posix' and os.getpgid(p.pid)==p.pid:os.killpg(p.pid,signal.SIGKILL)
                else:p.kill()
                await asyncio.to_thread(p.join,2)
            ensure(not p.is_alive(),'BROWSER_WORKER_STOP_FAILED');p.close();self.process=None
        if self.conn is not None:self.conn.close();self.conn=None

    async def warm(self):
        if self.process is None:self.start()
        try:
            value=await asyncio.wait_for(asyncio.to_thread(self.conn.recv),60)
            ensure(value.get('ready') is True,'BROWSER_WARMUP_FAILED');self.ready=True
        except BaseException:
            await self.stop();raise

    async def execute(self,job):
        ensure(self.ready and self.process is not None and self.process.is_alive(),'BROWSER_NOT_READY')
        self.conn.send(job)
        return await asyncio.wait_for(asyncio.to_thread(self.conn.recv),job['seconds']+8)


class BrowserPool:
    def __init__(self,root,slots=1,*,worker=None,fixture_origin=None,wait_seconds=20):
        from .browser_render import process_worker
        ensure(type(slots)is int and 1<=slots<=4,'BROWSER_SLOT_BOUNDS')
        ensure(0<wait_seconds<=20,'BROWSER_WAIT_BOUNDS')
        self.root=Path(root);self.token=secrets.token_urlsafe(32);self.capacity=slots;self.wait=wait_seconds
        self.available=asyncio.Queue();self.slots=[Slot(worker or process_worker,dict(fixture_origin=fixture_origin,
            cache_root=str(Path(root)/'workers'/str(i)))) for i in range(slots)]
        self.active=0;self.pending=0;self.closed=False;self.counts={};self.handlers=set()
        self.warmers=set()

    async def warmup(self):
        for slot in self.slots:
            await slot.warm();self.available.put_nowait(slot);self.bump('warmed');self.save()

    async def recover(self,slot):
        try:
            await slot.warm()
            if not self.closed:self.available.put_nowait(slot);self.bump('rewarmed')
        except Exception:self.bump('warmup_failed')
        finally:self.save()

    def bump(self,key):self.counts[key]=self.counts.get(key,0)+1

    def health(self):return dict(policy=POLICY,pid=os.getpid(),slots=self.capacity,ready_slots=sum(s.ready for s in self.slots),active=self.active,
        pending=self.pending,counts=self.counts,queue_policy='bounded-evidence-wait-v2',queue_wait_limit_seconds=self.wait,
        worker_pids=[s.process.pid for s in self.slots if s.process and s.process.is_alive()],at=now())

    def save(self):atomic_bytes(self.root/'health.json',(json.dumps(self.health())+'\n').encode())

    async def render(self,job,disconnected=lambda:False):
        ensure(not self.closed,'BROWSER_POOL_STOPPING')
        ensure(isinstance(job.get('url'),str) and len(job['url'])<=4096,'BROWSER_URL_FORMAT')
        ensure(type(job.get('seconds'))in (int,float) and 1<=job['seconds']<=20,'BROWSER_TIMEOUT_BOUNDS')
        ensure(type(job.get('request_limit'))is int and 1<=job['request_limit']<=25,'BROWSER_REQUEST_BOUNDS')
        queue_seconds=job.get('queue_seconds',self.wait)
        ensure(type(queue_seconds) in (int,float) and 0<=queue_seconds<=20,'BROWSER_QUEUE_BOUNDS')
        ensure(type(job.get('owner_pid'))is int and job['owner_pid']>0 and isinstance(job.get('owner_birth'),(int,float)),
               'BROWSER_OWNER_FORMAT')
        ensure(self.pending<4,'BROWSER_POOL_BUSY')
        ensure(owner_token(job.get('owner_pid'))==job.get('owner_birth'),'BROWSER_OWNER_GONE')
        self.pending+=1;self.save();slot=None;render=None;monitor=None;acquire=None;active=False;clean_rejection=False
        queued=time.monotonic()
        try:
            async def watch():
                while not disconnected() and owner_token(job['owner_pid'])==job['owner_birth']:await asyncio.sleep(.1)
                raise PipelineError('BROWSER_OWNER_GONE')
            monitor=asyncio.create_task(watch())
            if queue_seconds==0:
                try:slot=self.available.get_nowait()
                except asyncio.QueueEmpty:raise PipelineError('BROWSER_POOL_BUSY') from None
            else:
                acquire=asyncio.create_task(self.available.get())
                done,_=await asyncio.wait([acquire,monitor],timeout=min(self.wait,queue_seconds),return_when=asyncio.FIRST_COMPLETED)
                if monitor in done:await monitor
                if acquire not in done:raise PipelineError('BROWSER_POOL_BUSY')
                slot=await acquire
            ensure(not disconnected() and owner_token(job['owner_pid'])==job['owner_birth'],'BROWSER_OWNER_GONE')
            self.pending-=1;self.active+=1;active=True;self.bump('started');self.save()
            queue_wait=time.monotonic()-queued
            render=asyncio.create_task(slot.execute(job))
            done,_=await asyncio.wait([render,monitor],return_when=asyncio.FIRST_COMPLETED)
            if monitor in done:await monitor
            result=await render
            if 'error' in result:
                code='BROWSER_RENDER_TIMEOUT' if result['error']=='TimeoutError' else result['error'] if result['error'] in {
                    'BROWSER_HTTP_FAILURE','BROWSER_ACCESS_BLOCKED','BROWSER_DOM_SIZE','BROWSER_TEXT_SIZE'} else 'BROWSER_RENDER_FAILED'
                # A completed worker response means its session finally-block ran.
                # HTTP/access/size rejection must not discard a healthy warm worker.
                clean_rejection=code in {'BROWSER_HTTP_FAILURE','BROWSER_ACCESS_BLOCKED','BROWSER_DOM_SIZE','BROWSER_TEXT_SIZE'}
                error=PipelineError(code);error.metadata={'phase':result.get('phase'),'queue_wait_seconds':queue_wait}
                raise error
            self.bump('completed');return {**result,'queue_wait_seconds':queue_wait}
        except BaseException as exc:
            self.bump(getattr(exc,'code',type(exc).__name__))
            if slot is not None and active and not clean_rejection:await slot.stop()
            raise
        finally:
            for task in (render,monitor,acquire):
                if task is not None and not task.done():task.cancel()
            await asyncio.gather(*(t for t in (render,monitor,acquire) if t is not None),return_exceptions=True)
            # Acquisition can finish in the same loop turn as a disconnect/timeout.
            # Return that unstarted slot rather than leak capacity or kill its worker.
            if slot is None and acquire is not None and acquire.done() and not acquire.cancelled() and acquire.exception() is None:
                slot=acquire.result()
            if active:self.active-=1
            else:self.pending-=1
            if slot is not None:
                if slot.ready:self.available.put_nowait(slot)
                elif not self.closed:
                    task=asyncio.create_task(self.recover(slot));self.warmers.add(task);task.add_done_callback(self.warmers.discard)
            self.save()

    async def close(self):
        self.closed=True
        for task in list(self.handlers):task.cancel()
        await asyncio.gather(*list(self.handlers),return_exceptions=True)
        for task in list(self.warmers):task.cancel()
        await asyncio.gather(*list(self.warmers),return_exceptions=True)
        for slot in self.slots:await slot.stop()
        self.save()


async def serve(root,slots=1,*,fixture_origin=None,stop_event=None):
    from aiohttp import web
    pool=BrowserPool(root,slots,fixture_origin=fixture_origin);pool.root.mkdir(parents=True,exist_ok=True)
    endpoint=pool.root/'endpoint.json'
    async def handle(request):
        if not hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+pool.token):
            raise web.HTTPUnauthorized()
        if request.method=='GET':return web.json_response(pool.health())
        task=asyncio.current_task();pool.handlers.add(task)
        try:
            job=await request.json()
            result=await pool.render(job,lambda:request.transport is None or request.transport.is_closing())
            return web.json_response(result)
        except PipelineError as exc:return web.json_response({'error':exc.code,'metadata':getattr(exc,'metadata',None)},status=503)
        except asyncio.TimeoutError:return web.json_response({'error':'BROWSER_RENDER_TIMEOUT'},status=503)
        except Exception:return web.json_response({'error':'BROWSER_SERVICE_ERROR'},status=503)
        finally:pool.handlers.discard(task)
    app=web.Application(client_max_size=8192);app.router.add_get('/health',handle);app.router.add_post('/render',handle)
    runner=web.AppRunner(app,access_log=None);await runner.setup()
    with file_lock(pool.root/'owner.lock',timeout=.1):
        try:await pool.warmup()
        except BaseException:await pool.close();await runner.cleanup();raise
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start();port=site._server.sockets[0].getsockname()[1]
        atomic_bytes(endpoint,json.dumps(dict(policy=POLICY,pid=os.getpid(),owner_birth=owner_token(),port=port,token=pool.token)).encode())
        if os.name=='posix':endpoint.chmod(0o600)
        pool.save();event=stop_event or asyncio.Event()
        if stop_event is None:
            for sig in (signal.SIGTERM,signal.SIGINT):asyncio.get_running_loop().add_signal_handler(sig,event.set)
        try:await event.wait()
        finally:
            await pool.close();await runner.cleanup();endpoint.unlink(missing_ok=True)


class BrowserClient:
    def __init__(self,endpoint):
        self.endpoint=Path(endpoint);ensure(self.endpoint.is_absolute(),'BROWSER_ENDPOINT_ABSOLUTE')

    def health(self):
        import requests
        try:
            d=json.loads(self.endpoint.read_text())
            ensure(d['policy']==POLICY and owner_token(d['pid'])==d['owner_birth'],'BROWSER_POOL_STALE')
            with requests.Session() as s:
                s.trust_env=False
                r=s.get('http://127.0.0.1:'+str(d['port'])+'/health',headers={'Authorization':'Bearer '+d['token']},timeout=1)
                ensure(r.status_code==200,'BROWSER_POOL_UNAVAILABLE');return r.json()
        except PipelineError:raise
        except Exception:raise PipelineError('BROWSER_POOL_UNAVAILABLE') from None

    def render(self,url,*,seconds=20,request_limit=25,queue_seconds=20):
        import requests
        try:
            data=json.loads(self.endpoint.read_text())
            ensure(data['policy']==POLICY and owner_token(data['pid'])==data['owner_birth'],'BROWSER_POOL_STALE')
            ensure(type(data['port'])is int and 0<data['port']<65536,'BROWSER_ENDPOINT_FORMAT')
            with requests.Session() as session:
                session.trust_env=False
                r=session.post('http://127.0.0.1:'+str(data['port'])+'/render',
                    headers={'Authorization':'Bearer '+data['token']},json=dict(url=url,seconds=seconds,request_limit=request_limit,
                    queue_seconds=queue_seconds,owner_pid=os.getpid(),owner_birth=owner_token()),timeout=queue_seconds+seconds+12)
                result=r.json()
            if r.status_code!=200:
                error=PipelineError(result.get('error','BROWSER_SERVICE_ERROR'));error.metadata=result.get('metadata');raise error
            ensure(isinstance(result.get('text'),str) and len(result['text'])<=512_000,'BROWSER_RESULT_FORMAT')
            return result
        except PipelineError:raise
        except Exception:raise PipelineError('BROWSER_POOL_UNAVAILABLE') from None


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--slots',type=int,default=1)
    p.add_argument('--fixture-origin');a=p.parse_args()
    if a.fixture_origin:
        from urllib.parse import urlsplit
        u=urlsplit(a.fixture_origin)
        ensure(u.scheme=='http' and u.hostname=='127.0.0.1' and u.port and u.path=='','BROWSER_FIXTURE_ORIGIN')
    asyncio.run(serve(a.root,a.slots,fixture_origin=a.fixture_origin))


if __name__=='__main__':main()
