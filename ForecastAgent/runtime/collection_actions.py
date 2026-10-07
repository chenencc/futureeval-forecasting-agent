"""Bounded next actions for located material, failed primary reads and repeats."""
import re
from urllib.parse import urlsplit
from ForecastAgent.readers.saved import select, version_digest
from ForecastAgent.tavily_research import canonical_url
from ForecastAgent.runtime.collection_v2 import eligible

STATUS_WORDS = r'\b(?:deprecated|discontinued|retired|sunset|shutdown|no longer)\b'


def discovery_read_action(task):
    """Require one real reading batch per discovery advance, before more search."""
    b = task.bundle
    if task.cutoff:
        return None  # Historical replay requires its archive/vintage routing.
    count = len(b.get('searches', [])) + len(b.get('exa_searches', []))
    if not count or b.get('control', {}).get('read_after_discovery', 0) >= count or task.budget()['page_fetch_remaining'] <= 0:
        return None
    attempted = {canonical_url(a['url']) for a in b.get('fetch_attempts', []) if a.get('url')}
    leads = {}; dates = {}
    for search in b.get('searches', []) + b.get('exa_searches', []):
        for hit in search.get('results', []):
            leads[canonical_url(hit['url'])] = hit.get('title', '')
            dates[canonical_url(hit['url'])] = hit.get('published_date')
    leads.update({u: '' for u, row in b.get('source_leads', {}).items() if row.get('origin', '').startswith('question_')})
    terms = set(re.findall(r'[a-z0-9]{4,}', b['request']['question'].lower())) - {'will', 'before', 'after', '2026'}
    from ForecastAgent.evidence.acquisition_quality import discovery_score
    ranked = sorted((u for u in leads if u not in attempted and u not in b['pages']),
        key=lambda u: (-discovery_score(b['request'],u,leads[u],named_primary(task,u),dates.get(u)) if getattr(task,'raw_recall',False)
                      else -10 * bool(named_primary(task, u)) - len(terms & set(re.findall(r'[a-z0-9]{4,}', (u + ' ' + leads[u]).lower()))), u))
    if not ranked:
        return None
    return {'tool': 'read_sources', 'urls': ranked[:4 if getattr(task,'raw_recall',False) else 2],
        'instruction': ('Capture this discovered batch as raw material. Identifier ranking is a routing hint, not a relevance verdict; no paragraph selection is required.'
            if getattr(task,'raw_recall',False) else
            'Read a relevant exact discovered source before another search. Batch free fetch, permitted primary rescue and paragraph location in this action. Use concrete queries for critical needs; this route is not a relevance verdict.')}


def raw_stop_reason(task):
    """Close mechanically without another model call; never assert full recall."""
    if not getattr(task,'raw_recall',False) or task.bundle.get('plan') is None:
        return None
    from ForecastAgent.runtime.search_policy import requirement
    obligation=requirement(task)
    if obligation.get('required') and not obligation.get('attempt_requirement_met'):
        return None
    budget=task.budget()
    if primary_rescue(task):
        return None
    if task.bundle['control'].get('no_progress_turns',0)>=2:
        return 'raw_no_progress_limit'
    if budget['page_fetch_remaining']<=0:
        return 'raw_source_budget_exhausted'
    if budget['tavily_basic_remaining']<=0 and budget['exa_search_remaining']<=0 and not discovery_read_action(task):
        return 'raw_discovery_frontier_exhausted'
    return None


def named_primary(task, url):
    from ForecastAgent.runtime.needs import active_needs
    labels=set(re.findall(r'[a-z0-9]{4,}',(urlsplit(url).hostname or '').lower()))
    return [n['id'] for n in active_needs(task.bundle) if n['priority']=='critical'
        and labels & set(re.findall(r'[a-z0-9]{4,}',n.get('expected_source','').lower()))]


def pending_passages(task, limit=8, *, include_deferred=False):
    """Expose only surfaced, version-valid candidates, never auto-accept relevance."""
    b=task.bundle
    surfaced={}
    for pid, row in b.get('progress', {}).get('delivery_passages', {}).items():
        surfaced[pid] = {'need_ids': list(row.get('need_ids', []))}
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
    from ForecastAgent.runtime.needs import active_needs
    active_ids={n['id'] for n in active_needs(b)}
    critical_ids={n['id'] for n in active_needs(b) if n['priority']=='critical'}
    banked={n for e in b['excerpts'] for n in e.get('need_ids',[])}
    result=[]
    for pid, row in surfaced.items():
        passage=b.get('passages',{}).get(pid)
        disposition = dispositions.get(pid)
        if not passage or (disposition and (disposition.get('action') != 'defer' or not include_deferred)):
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
        ids=[n for n in row['need_ids'] if n not in covered and n in active_ids]
        if ids and quoted.strip():
            result.append({'passage_id':pid,'url':passage['url'],'need_ids':ids,'text':quoted[:4000]})
    result.sort(key=lambda row: (-len(set(row['need_ids']) & (critical_ids-banked)),
                                -len(set(row['need_ids']) & critical_ids)))
    return result[:limit]


def primary_rescue(task):
    """Prioritize discovered failed hosts named in critical source requirements."""
    if task.verified_only or task.budget()['basic_extract_batches_remaining']<=0:
        return []
    if 'tavily_extract_basic' in task.bundle.get('channel_decisions',{}):
        return []
    # Another document on the same host does not supply this failed source.
    from ForecastAgent.runtime.needs import active_needs
    needs=[n for n in active_needs(task.bundle) if n['priority']=='critical']
    rows=[]
    for url in task.rescue_candidates():
        host=(urlsplit(url).hostname or '').lower()
        # Short issuer names such as IMF, WHO and AAA are meaningful host tokens.
        # Common domain/navigation words are not issuer identity evidence.
        generic={'www','com','org','net','gov','edu','int','co','uk','us','en',
                 'data','api','news','media','official','website','source'}
        labels=set(re.findall(r'[a-z0-9]{2,}',host))-generic
        matches=[n['id'] for n in needs if labels &
                 (set(re.findall(r'[a-z0-9]{2,}',n.get('expected_source','').lower()))-generic)]
        if matches:
            rows.append({'url':url,'need_ids':matches,'reason':'Failed discovered host matches a named critical expected source; a routing hint, not authority verification.'})
    return rows[:5]


def next_action(task):
    from ForecastAgent.runtime.intelligent_acquisition import enabled, repaired
    if enabled(task):
        if repaired(task):
            focus = review_focus(task)
            passages = focus['passages'] if focus else []
            if passages and focus.get('mandatory', True):
                return {'tool': 'review_passages', 'candidates': passages,
                    'instruction': 'Keep or explicitly reject these exact delivered spans before more navigation. Choose actual associated need IDs; availability is not relevance. Text remains pinned until disposition or forced closure, with remaining work exported as gaps.'}
        # Preserve release repair and discovery-to-read gates. Remaining local
        # navigation and material selection are agent decisions, not word rules.
        rescue = primary_rescue(task)
        if rescue:
            return {'tool': 'extract_failed_pages', 'candidates': rescue,
                    'instruction': 'Rescue a failed named source once within the existing allowance.'}
        return discovery_read_action(task)
    if getattr(task,'raw_recall',False):
        rescue=primary_rescue(task)
        if rescue:
            return {'tool':'extract_failed_pages','candidates':rescue,
                'instruction':'Rescue the failed named source once within the existing Extract budget; preserve vendor provenance and failures.'}
        return discovery_read_action(task)
    passages=pending_passages(task)
    from ForecastAgent.runtime.needs import active_needs
    critical={n['id'] for n in active_needs(task.bundle) if n['priority']=='critical'}
    banked={n for e in task.bundle['excerpts'] for n in e.get('need_ids',[])}
    core_associated = bool(critical and critical <= banked)
    passages=[] if core_associated else [p for p in passages if set(p['need_ids']) & critical]
    if passages:
        return {'tool':'review_passages','candidates':passages,
            'instruction':'Review this batch for critical needs first. Keep substantive exact material with its date/heading context; reject irrelevant, isolated header or duplicate candidates with a reason. Association is not a relevance verdict. Do not reread bodies before disposing of this batch.'}
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
    if core_associated:
        # Primary-source rescue remains available; optional background passage
        # bookkeeping does not force a new loop once core material is associated.
        return None
    if not task.cutoff and task.budget()['page_fetch_remaining']>0:
        words=set(re.findall(r'[a-z0-9]{4,}',task.bundle['request']['question'].lower()))-{'openai','before','after','release','released','windows','browser','question','official'}
        attempted={canonical_url(a['url']) for a in task.bundle.get('fetch_attempts',[]) if a.get('url')}
        for url in task.catalog():
            path=urlsplit(url).path.lower()
            if url not in task.bundle['pages'] and url not in attempted and named_primary(task,url) and re.search(r'release[-_]notes|version[-_]history',path) and any(w in path for w in words):
                return {'tool':'read_sources','urls':[url],
                    'instruction':'Follow this already-discovered named primary product update page for current platform/status material, within the existing HTTP allowance. Never infer event absence from a failed read.'}
    return discovery_read_action(task)


def review_focus(task):
    """Bounded exact-source refresh, independent of evicted inventory messages."""
    from ForecastAgent.runtime.intelligent_acquisition import repaired
    if not repaired(task) or task.bundle.get('control', {}).get('forced_close'):
        return None
    from ForecastAgent.runtime.material_protocol import enabled as v3, REVIEW_ROUNDS
    modern = v3(task)
    candidates = pending_passages(task, limit=8 if modern else 2)
    rows = []
    for candidate in candidates:
        passage = task.bundle['passages'][candidate['passage_id']]
        # pending_passages already verifies version, eligibility and coordinates.
        _, text, _ = select(task.bundle['pages'], passage['url'], passage.get('document_index'))
        exact = text[passage['start_char']:passage['end_char']]
        if sum(len(r['text']) for r in rows) + len(exact) > 6000:
            continue
        rows.append({**candidate, **passage, 'text': exact})
        if len(rows) >= (4 if modern else 2):
            break
    if not rows:
        return None
    mandatory = not modern or len(task.bundle.get('material_review_batches', [])) < REVIEW_ROUNDS
    return {'kind': 'pending_material_review', 'passages': rows,
            **({'mandatory':mandatory, 'review_rounds_remaining':max(0, REVIEW_ROUNDS-len(task.bundle.get('material_review_batches', [])))} if modern else {}),
            'instruction': 'These are exact saved spans previously surfaced for review, with immutable versions and coordinates. Use review_passages keep/reject; do not retype text. Need IDs are choices, not verified associations. Remaining spans stay on disk.',
            'semantic_verified': False}


def duplicate_read(task, args, projected_only=True):
    """Reject only a projected slice still visible in current model memory."""
    from ForecastAgent.runtime.progress import fingerprint
    from ForecastAgent.runtime.delivery import ensure_delivery_state
    ensure_delivery_state(task)
    url=canonical_url(args['url'])
    page,text,_=select(task.bundle['pages'],url,args.get('document_index'))
    start=args.get('start_char',0)
    count=args.get('max_chars',6000)
    end=min(len(text),start+(min(count,6000) if projected_only else count))
    indices={args.get('document_index')}
    docs=page.get('documents') or [{'page_content':page['content']}]
    if text==page['content']:
        indices.add(None)
        indices.update(i for i,d in enumerate(docs,1) if d['page_content']==text)
    scopes={fingerprint([url,version_digest(page),'read_document',index]) for index in indices}
    visible=getattr(task,'_projected_visible_reads',None)
    if visible is None:
        visible=task.bundle.get('progress',{}).get('visible_reads',{})
    intervals=sorted((r['start'],r['end']) for r in visible.values() if r['scope'] in scopes)
    cursor=start
    for left,right in intervals:
        if left>cursor:
            break
        cursor=max(cursor,right)
    return end>start and cursor>=end
