"""Opt-in cross-task live body cache; immutable capture times and hash checks."""
import base64
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
import uuid


def cache_path(url):
    root=os.environ.get('FORECAST_SHARED_CACHE_ROOT')
    if not root: return None
    return Path(root)/ (hashlib.sha256(url.encode()).hexdigest()+'.json')


def cached_page(url, max_age_seconds=900):
    path=cache_path(url)
    if not path or not path.exists(): return None
    try:
        page=json.loads(path.read_text(encoding='utf-8'))
        stamp=datetime.fromisoformat(page['retrieved_at_utc'].replace('Z','+00:00'))
        age=(datetime.now(timezone.utc)-stamp).total_seconds()
        raw=base64.b64decode(page['raw_response_base64'],validate=True)
        from ForecastAgent.readers.saved import version_digest
        if page['url']!=url or not stamp.tzinfo or not 0<=age<=max_age_seconds or hashlib.sha256(raw).hexdigest()!=page['sha256']:
            return None
        if page.get('cache_parsed_sha256')!=version_digest(page): return None
        page['cache_provenance']={'kind':'cross_task_live_cache','age_seconds':round(age,3),
                                  'original_capture_unchanged':True}
        return page
    except (KeyError,ValueError,TypeError,OSError):
        return None


def save_cached_page(url,page):
    path=cache_path(url)
    if not path or not page.get('raw_response_base64') or page.get('temporal_status')!='live_capture': return
    try:
        payload=dict(page,url=url)
        from ForecastAgent.readers.saved import version_digest
        payload['cache_parsed_sha256']=version_digest(page)
        payload.pop('cache_provenance',None)
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        temporary.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
        temporary.replace(path)
    except OSError:
        page['cache_warning']='Shared cache could not be written; task-local capture retained.'
