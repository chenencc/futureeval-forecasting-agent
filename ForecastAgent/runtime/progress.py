"""Measure durable acquisition changes and newly delivered reading ranges."""
import hashlib
import json
from ForecastAgent.runtime.collection_v2 import eligible
from ForecastAgent.readers.saved import version_digest
from ForecastAgent.readers.quality import body_diagnostics


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def snapshot(task):
    b = task.bundle
    usable = {u:p for u,p in b['pages'].items() if p.get('content') and body_diagnostics(p['content'])['usable_text'] and
              (not task.verified_only or eligible(p, task.cutoff))}
    return {'leads': set(task.catalog()),
            'bodies': {(u, version_digest(p)) for u,p in usable.items()},
            'excerpts': {fingerprint(e) for e in b['excerpts'] if e['url'] in usable},
            'passages': set(b.get('passages', {})),
            'reads': set(b.get('progress', {}).get('reads', {})),
            'markets': set(b.get('market_snapshots', {})),
            'preparation': {fingerprint(v) for v in [b.get('plan'), b.get('channel_plan')] if v}
                           | {'passage_disposition:'+pid for pid in b.get('passage_dispositions',{})}
                           | {'need_status:'+fingerprint(v) for v in b.get('need_status', {}).values()}
                           | {'skill:'+s for s in b.get('loaded_skills', [])}}


def delivered(task, name, args, result):
    """Reading an overlapping range cannot repeatedly buy apparent progress."""
    if result.get('error') or result.get('blocked') or name not in {'read_document', 'read_dataset_rows'}:
        return
    from ForecastAgent.runtime.delivery import ensure_delivery_state
    ensure_delivery_state(task)
    from ForecastAgent.tavily_research import canonical_url
    url = canonical_url(args['url'])
    page = task.bundle['pages'].get(url)
    if not page:
        from ForecastAgent.tavily_research import canonical_url
        page = task.bundle['pages'].get(canonical_url(url))
    if not page or task.verified_only and not eligible(page, task.cutoff):
        return
    rows = name == 'read_dataset_rows'
    if rows:
        start, end = args.get('offset', 0), args.get('offset', 0)+len(result.get('rows', []))
    else:
        start, end = result.get('start_char', 0), result.get('end_char', 0)
    if end <= start:
        return
    scope = fingerprint([url, version_digest(page), name, args.get('document_index')])
    reads = task.bundle.setdefault('progress', {}).setdefault('reads', {})
    old = [v for v in reads.values() if v['scope'] == scope]
    # Merge intervals so shifted requests wholly inside delivered text are neutral.
    uncovered = [(start, end)]
    for interval in old:
        next_ranges = []
        for left, right in uncovered:
            if interval['end'] <= left or interval['start'] >= right:
                next_ranges.append((left, right))
            else:
                if left < interval['start']: next_ranges.append((left, interval['start']))
                if interval['end'] < right: next_ranges.append((interval['end'], right))
        uncovered = next_ranges
    for left, right in uncovered:
        key = fingerprint([scope, left, right])
        reads[key] = {'scope':scope, 'start':left, 'end':right, 'url':url}


def delta(before, after):
    counts = {key: len(after[key]-before[key]) for key in before}
    material = any(counts[k] for k in ('bodies', 'excerpts', 'passages', 'reads', 'markets'))
    return {'new':counts, 'material_progress':material,
            'discovery_progress':bool(counts['leads']),
            'advanced':material or bool(counts['leads']) or bool(counts['preparation']),
            'truth_verified':False}
