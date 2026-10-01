"""Bounded next actions for located material, failed primary reads and repeats."""
import re
from urllib.parse import urlsplit
from ForecastAgent.readers.saved import select, version_digest
from ForecastAgent.tavily_research import canonical_url
from ForecastAgent.runtime.collection_v2 import eligible

STATUS_WORDS = r'\b(?:deprecated|discontinued|retired|sunset|shutdown|no longer)\b'


def named_primary(task, url):
    labels=set(re.findall(r'[a-z0-9]{4,}',(urlsplit(url).hostname or '').lower()))
    return [n['id'] for n in task.bundle.get('plan') or [] if n['priority']=='critical'
        and labels & set(re.findall(r'[a-z0-9]{4,}',n.get('expected_source','').lower()))]


def pending_passages(task, limit=8):
    """Expose only surfaced, version-valid candidates, never auto-accept relevance."""
    b=task.bundle
    surfaced={}
    for step in b.get('transcript', []):
        if step.get('tool') != 'read_sources':
            continue
        data=(step.get('result') or {}).get('data',step.get('result') or {})
        for row in data.get('located_material', []):
            for item in row.get('passages', []):
                pid=item.get('passage_id')
                if pid:
                    saved=surfaced.setdefault(pid,{'need_ids':[]})
                    saved['need_ids']=sorted(set(saved['need_ids']+row.get('need_ids',[])))
    dispositions=b.get('passage_dispositions',{})
    result=[]
    for pid, row in surfaced.items():
        passage=b.get('passages',{}).get(pid)
        if not passage or pid in dispositions:
            continue
        page=b['pages'].get(canonical_url(passage['url']))
        if not page or version_digest(page)!=passage['source_version'] or page.get('sha256')!=passage.get('source_sha256'):
            continue
        if task.verified_only and not eligible(page,task.cutoff):
            continue
        _,text,_=select(b['pages'],passage['url'],passage.get('document_index'))
        quoted=text[passage['start_char']:passage['end_char']]
        covered={need for e in b['excerpts'] if e['url']==passage['url'] and e['text']==quoted
            and e.get('source_parsed_sha256')==passage['source_version'] for need in e.get('need_ids',[])}
        ids=[n for n in row['need_ids'] if n not in covered]
        if ids and quoted.strip():
            result.append({'passage_id':pid,'url':passage['url'],'need_ids':ids,'text':quoted[:4000]})
    return result[:limit]


def primary_rescue(task):
    """Prioritize discovered failed hosts named in critical source requirements."""
    if task.verified_only or task.budget()['basic_extract_batches_remaining']<=0:
        return []
    if 'tavily_extract_basic' in task.bundle.get('channel_decisions',{}):
        return []
    # A secondary excerpt mentioning a need does not supply its named primary
    # source. Count readable captures of that discovered host separately.
    from ForecastAgent.readers.quality import body_diagnostics
    needs=[n for n in task.bundle.get('plan') or [] if n['priority']=='critical']
    rows=[]
    for url in task.rescue_candidates():
        host=(urlsplit(url).hostname or '').lower()
        if any((urlsplit(u).hostname or '').lower()==host and body_diagnostics(p.get('content',''))['usable_text']
               and (not task.verified_only or eligible(p,task.cutoff)) for u,p in task.bundle['pages'].items()):
            continue
        labels=set(re.findall(r'[a-z0-9]{4,}',(urlsplit(url).hostname or '').lower()))
        matches=[n['id'] for n in needs if labels & set(re.findall(r'[a-z0-9]{4,}',n.get('expected_source','').lower()))]
        if matches:
            rows.append({'url':url,'need_ids':matches,'reason':'Failed discovered host matches a named critical expected source; a routing hint, not authority verification.'})
    return rows[:5]


def next_action(task):
    passages=pending_passages(task)
    if passages:
        return {'tool':'review_passages','candidates':passages,
            'instruction':'Keep relevant exact material or reject irrelevant/header/duplicate candidates with a reason. Do not reread bodies before disposing of this batch.'}
    # Status language is a reading lead, not a truth or event-resolution verdict.
    scanned=task.bundle.get('control',{}).get('status_scanned_versions',{})
    status_urls=[u for u,p in task.bundle['pages'].items() if named_primary(task,u)
        and re.search(STATUS_WORDS,p.get('content',''),re.I)
        and scanned.get(u)!=version_digest(p) and (not task.verified_only or eligible(p,task.cutoff))]
    if status_urls:
        return {'tool':'read_sources','urls':status_urls[:4],
            'instruction':'Read the named primary source status banner and lifecycle statements. Deprecated/discontinued/retired language is relevant acquisition material, not an outcome verdict. The program also locates these terms.'}
    rescue=primary_rescue(task)
    if rescue:
        return {'tool':'extract_failed_pages','candidates':rescue,
            'instruction':'Rescue the failed named primary source within the existing basic Extract allowance before more secondary rereads.'}
    fresh=task.bundle.get('control',{}).get('priority_read_urls',[])
    if fresh:
        return {'tool':'read_sources','urls':fresh[:4],
            'instruction':'Locate task-specific passages in these newly rescued bodies, then review the surfaced candidates.'}
    if not task.cutoff and task.budget()['page_fetch_remaining']>0:
        words=set(re.findall(r'[a-z0-9]{4,}',task.bundle['request']['question'].lower()))-{'openai','before','after','release','released','windows','browser','question','official'}
        attempted={canonical_url(a['url']) for a in task.bundle.get('fetch_attempts',[]) if a.get('url')}
        for url in task.catalog():
            path=urlsplit(url).path.lower()
            if url not in task.bundle['pages'] and url not in attempted and named_primary(task,url) and re.search(r'release[-_]notes|version[-_]history',path) and any(w in path for w in words):
                return {'tool':'read_sources','urls':[url],
                    'instruction':'Follow this already-discovered named primary product update page for current platform/status material, within the existing HTTP allowance. Never infer event absence from a failed read.'}
    return None


def duplicate_read(task, args):
    """Detect completely delivered ranges in the same version/coordinate space."""
    from ForecastAgent.runtime.progress import fingerprint
    url=canonical_url(args['url'])
    page,text,_=select(task.bundle['pages'],url,args.get('document_index'))
    start=args.get('start_char',0)
    end=min(len(text),start+args.get('max_chars',6000))
    indices={args.get('document_index')}
    docs=page.get('documents') or [{'page_content':page['content']}]
    if text==page['content']:
        indices.add(None)
        indices.update(i for i,d in enumerate(docs,1) if d['page_content']==text)
    scopes={fingerprint([url,version_digest(page),'read_document',index]) for index in indices}
    intervals=sorted((r['start'],r['end']) for r in task.bundle.get('progress',{}).get('reads',{}).values() if r['scope'] in scopes)
    cursor=start
    for left,right in intervals:
        if left>cursor:
            break
        cursor=max(cursor,right)
    return end>start and cursor>=end
