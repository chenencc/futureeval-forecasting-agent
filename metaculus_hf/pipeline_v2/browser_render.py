"""Pinned, read-only Crawl4AI worker. DOM exists only in worker memory."""
import asyncio
import importlib.metadata
import time
import uuid
from urllib.parse import urlsplit

VERSION='0.9.4'


class Renderer:
    def __init__(self,fixture_origin=None,stage=lambda _:None):
        self.crawler=None;self.fixture_origin=fixture_origin;self.stage=stage;self.last_phase=None

    def mark(self,phase):
        self.last_phase=phase;self.stage(phase)

    def public(self,url):
        from .acquisition import public_url
        parts=urlsplit(url)
        if self.fixture_origin and parts.scheme+'://'+parts.netloc==self.fixture_origin:return
        public_url(url)

    async def close(self):
        if self.crawler is not None:
            await self.crawler.close();self.crawler=None

    async def start(self):
        if self.crawler is not None:return
        self.mark('importing_crawl4ai')
        if importlib.metadata.version('crawl4ai')!=VERSION:raise ValueError('BROWSER_VERSION_MISMATCH')
        from crawl4ai import AsyncWebCrawler,BrowserConfig
        self.mark('launching_chromium')
        self.crawler=AsyncWebCrawler(config=BrowserConfig(headless=True,verbose=False,
            accept_downloads=False,enable_stealth=False,use_persistent_context=False,
            extra_args=['--disable-dev-shm-usage','--disable-background-networking']),verbose=False)
        await self.crawler.start()
        self.mark('browser_ready')

    async def render(self,url,seconds=20,request_limit=25):
        from .contracts import now
        from .acquisition import extract_text,outgoing_links
        if importlib.metadata.version('crawl4ai')!=VERSION:raise ValueError('BROWSER_VERSION_MISMATCH')
        from crawl4ai import CrawlerRunConfig,CacheMode
        begin=time.monotonic();self.mark('validating_url')
        await asyncio.wait_for(asyncio.to_thread(self.public,url),min(3,seconds))
        await self.start()
        counts={'allowed':0,'blocked':0};pages=[];status={};origins=set();timings=[]
        def phase(name):
            self.mark(name);timings.append(dict(phase=name,seconds=time.monotonic()-begin))
        async def route(r):
            req=r.request;parts=urlsplit(req.url);origin=parts.scheme+'://'+parts.netloc
            allow=req.method in ('GET','HEAD') and req.resource_type not in ('image','media','font')
            allow=allow and counts['allowed']<request_limit and time.monotonic()<begin+seconds
            if allow and origin not in origins:
                try:await asyncio.wait_for(asyncio.to_thread(self.public,req.url),3);origins.add(origin)
                except Exception:allow=False
            # DNS checks can yield to other routes; recheck before reserving.
            allow=allow and counts['allowed']<request_limit and time.monotonic()<begin+seconds
            if allow:counts['allowed']+=1;await r.continue_()
            else:counts['blocked']+=1;await r.abort()
        async def created(page,context,**kw):
            phase('navigating')
            pages.append(page);await context.route('**/*',route)
            await context.route_web_socket('**/*',lambda ws:ws.close())
            await page.add_init_script("if('serviceWorker' in navigator){navigator.serviceWorker.register=()=>Promise.reject(new Error('disabled'));}")
            return page
        async def navigated(page,response=None,**kw):
            status.update(http_status=response.status if response else None,final_url=page.url)
            phase('response_received')
            if status['http_status'] is not None and status['http_status']>=400:raise ValueError('BROWSER_HTTP_FAILURE')
            return page
        async def retrieving(page,**kw):phase('reading_dom');return page
        strategy=self.crawler.crawler_strategy
        strategy.set_hook('on_page_context_created',created);strategy.set_hook('after_goto',navigated)
        strategy.set_hook('before_retrieve_html',retrieving)
        sid=uuid.uuid4().hex
        config=CrawlerRunConfig(session_id=sid,cache_mode=CacheMode.DISABLED,verbose=False,
            max_retries=0,page_timeout=int(seconds*1000),wait_until='domcontentloaded',
            # Crawl4AI otherwise waits 30 seconds even when hidden-body visibility
            # will be ignored. This exceeded the entire bounded render budget.
            body_visibility_timeout=1000,ignore_body_visibility=True,wait_for_images=False,
            delay_before_return_html=1.5,word_count_threshold=0,table_score_threshold=0,
            process_iframes=False,capture_network_requests=False,exclude_all_images=True)
        try:
            # The V2 body extractor already owns cleaning/table preservation.
            # Use Crawl4AI's browser strategy directly; arun() would additionally
            # scrape tables and generate Markdown that this adapter discards.
            remaining=seconds-(time.monotonic()-begin)
            if remaining<=0:raise asyncio.TimeoutError()
            result=await asyncio.wait_for(strategy.crawl(url=url,config=config),remaining)
            html=(result.html or '').encode('utf-8')
            if not html or len(html)>4*1024**2:raise ValueError('BROWSER_DOM_SIZE')
            final=status.get('final_url') or result.redirected_url or url
            phase('validating_final_url')
            remaining=seconds-(time.monotonic()-begin)
            if remaining<=0:raise asyncio.TimeoutError()
            await asyncio.wait_for(asyncio.to_thread(self.public,final),min(3,remaining))
            if not status.get('http_status') or status['http_status']>=400:raise ValueError('BROWSER_HTTP_FAILURE')
            from crawl4ai.antibot_detector import is_blocked
            if is_blocked(status['http_status'],html.decode('utf-8'))[0]:raise ValueError('BROWSER_ACCESS_BLOCKED')
            text=extract_text(html,'text/html')
            if len(text)>512_000:raise ValueError('BROWSER_TEXT_SIZE')
            return dict(text=text,final_url=final,http_status=status['http_status'],captured_at=now(),
                outgoing_links=outgoing_links(html,'text/html',final),request_counts=counts,
                request_limit=request_limit,backend='crawl4ai',backend_version=VERSION,
                session_isolation='unique-session-context-closed',raw_saved=False,
                rendered_only=True,timings=timings,elapsed_seconds=time.monotonic()-begin)
        finally:
            try:await asyncio.wait_for(strategy.kill_session(sid),2)
            except Exception:raise ValueError('BROWSER_CLEANUP_FAILED') from None


def process_worker(conn,fixture_origin=None):
    """One owned process/session per warm browser; supervisor can kill its tree."""
    import os,signal
    from pathlib import Path
    config=fixture_origin if isinstance(fixture_origin,dict) else {'fixture_origin':fixture_origin}
    fixture_origin=config.get('fixture_origin');cache=config.get('cache_root')
    for key in list(os.environ):
        if key.startswith(('OPENROUTER','TAVILY','EXA_API','PREDICT_API','HF_TOKEN','HUGGING_FACE')):os.environ.pop(key,None)
    if cache:
        Path(cache).mkdir(parents=True,exist_ok=True);os.environ['CRAWL4_AI_BASE_DIRECTORY']=cache
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'
    os.environ['TOKENIZERS_PARALLELISM']='false'
    if os.name=='posix':
        os.setsid()
        try:
            import ctypes
            ctypes.CDLL(None).prctl(1,signal.SIGTERM)
        except (AttributeError,OSError):pass
    async def run():
        from .contracts import now
        def stage(value):
            if cache:Path(cache,'bootstrap.json').write_text(__import__('json').dumps({'phase':value,'at':now()}))
        worker=Renderer(fixture_origin,stage)
        try:
            if cache:Path(cache,'bootstrap.json').write_text(__import__('json').dumps({'phase':'starting','at':now()}))
            await worker.start()
            if cache:Path(cache,'bootstrap.json').write_text(__import__('json').dumps({'phase':'ready','at':now()}))
            conn.send({'ready':True})
            while True:
                job=await asyncio.to_thread(conn.recv)
                if job is None:break
                try:out=await worker.render(job['url'],job['seconds'],job['request_limit'])
                except Exception as exc:out={'error':str(exc) if type(exc) is ValueError else type(exc).__name__,'phase':worker.last_phase}
                conn.send(out)
        finally:await worker.close();conn.close()
    try:asyncio.run(run())
    except (EOFError,BrokenPipeError):pass
