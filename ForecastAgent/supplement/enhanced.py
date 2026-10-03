"""Candidate gap-driven repair with lifetime accounting and exact reading hints.

No labels, forecasts or community probabilities enter planning. Search callbacks
are optional; callers must use basic Tavily and one physical request per callback.
"""
import copy
import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit

from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.supplement.discovery import safe_url, tokens
from ForecastAgent.evidence.source_checks import inspect_body
from ForecastAgent.supplement.stage import fetch_document, now
from ForecastAgent.readers.browser import render_page

PROTOCOL = 'enhanced-supplement-candidate-v1'
CAPS = {'tavily': 3, 'exa': 1, 'http': 16, 'browser': 6}
MONTHS = 'January February March April May June July August September October November December'.split()


def assess(question, url, body):
    """Separate readable content from observable rule coverage; never verify truth."""
    check = inspect_body(question, url, body)
    # Short site promotion/privacy fragments can contain a long paragraph.
    shell = len(body) < 1800 and bool(re.search(
        r'we respect your privacy|grow (?:their|your) business|accept all cookies|manage consent', body, re.I))
    substantive = bool(re.search(r'\b20\d\d\b|\b\d+(?:\.\d+)?\s*%|\|.*\d.*\|', body))
    if shell and not substantive:
        check.update(eligible_for_evidence=False, reason='consent_or_promotion_shell')
        check['body'].update(state='consent_or_promotion_shell', usable_text=False)
    rule = str(question.get('question', '')) + ' ' + str(question.get('resolution_criteria', ''))
    months = [m for m in MONTHS if re.search(r'\b'+m+r'\b', rule, re.I)]
    required = tokens(rule)
    matched = sorted(required & tokens(body))
    dates = [m for m in months if re.search(r'\b'+m+r'\b', body, re.I)]
    return {**check, 'target_months': months, 'observed_target_months': dates,
            'matched_rule_terms': matched, 'coverage_status': 'candidate_text' if check['eligible_for_evidence'] and
            (not months or dates) and len(matched) >= 2 else 'gap_or_context',
            'event_timing_verified': False, 'metric_verified': False, 'truth_verified': False}


def plan(bundle):
    """Rank only observed URLs; retain exact rule URLs and expose missing coverage."""
    q = bundle['request']; rule = str(q.get('resolution_criteria', ''))
    context = str(q.get('question', '')) + ' ' + rule
    candidates = {}; primaries = set()
    def add(url, label, origin):
        url = safe_url(url)
        if not url or (urlsplit(url).hostname or '') == 'metaculus.com' or (urlsplit(url).hostname or '').endswith('.metaculus.com'):
            return
        if re.search(r'/(?:login|signin|privacy|terms)(?:/|$)', urlsplit(url).path, re.I):
            return
        overlap = tokens(context) & tokens(label+' '+url)
        row = {'url': url, 'origin': origin, 'matched_terms': sorted(overlap),
               'rule_primary': url in primaries, 'score': 100 if url in primaries else len(overlap)*3}
        if url not in candidates or row['score'] > candidates[url]['score']:
            candidates[url] = row
    for url in re.findall(r'https?://[^\s<>]+', rule):
        clean = safe_url(url.rstrip(').,'))
        if clean:
            primaries.add(clean); add(clean, context, 'resolution_rule')
    for field in ('background', 'fine_print'):
        for label, url in re.findall(r'\[([^]]+)\]\((https?://[^)]+)\)', str(q.get(field, ''))):
            add(url, label, field)
    for search in bundle.get('searches', []) + bundle.get('exa_searches', []):
        for hit in search.get('results', []):
            add(hit.get('url'), str(hit.get('title', ''))+' '+str(hit.get('content', '')), 'saved_search')
    assessments = {}
    for url, page in bundle.get('pages', {}).items():
        assessments[url] = assess(q, url, page.get('content', ''))
        add(url, page.get('title', ''), 'saved_page')
        for link in page.get('links', []):
            if isinstance(link, dict):
                from urllib.parse import urljoin
                add(urljoin(url, link.get('url') or link.get('href') or ''), str(link.get('text', '')), 'saved_link')
    leads = bundle.get('source_leads', {})
    for lead in (leads.values() if isinstance(leads, dict) else leads):
        if isinstance(lead, dict):
            add(lead.get('url'), str(lead.get('title', '')), 'saved_lead')
    gaps = [{'url': u, 'reason': a.get('reason') or 'target_coverage_unknown'} for u, a in assessments.items()
            if a['coverage_status'] != 'candidate_text']
    if not assessments:
        gaps.append({'url': None, 'reason': 'no_saved_bodies'})
    sources = [c for c in candidates.values() if c['rule_primary'] or c['score'] >= 6]
    sources.sort(key=lambda c: (-c['score'], c['url']))
    return {'protocol': PROTOCOL, 'sources': sources, 'body_assessments': assessments, 'gaps': gaps,
            'search_query': str(q.get('question', '')) + ' '+ ' '.join(a for a in MONTHS if a.lower() in context.lower()),
            'search_role': 'key_gap', 'labels_used': False, 'urls_synthesized': False,
            'condition_coverage_requires_analysis': True}


def usage(bundle, prior, state):
    """Failed/reserved attempts consume quota. Prior journals must be nonoverlapping."""
    counts = {'tavily': len(bundle.get('searches', [])), 'exa': len(bundle.get('exa_searches', [])),
              'http': len(bundle.get('fetch_attempts', [])), 'browser': 0}
    for journal in [*prior, state]:
        for entry in journal.get('attempts', []):
            method = entry.get('tool', entry.get('method'))
            if method in counts:
                counts[method] += 1
    return counts


def hints(bundle):
    """Produce exact local span identities, prioritizing rule coverage over headers."""
    from ForecastAgent.analysis import mercury_evidence_chain as chain
    if not bundle.get('pages'):
        return {'protocol': PROTOCOL, 'packet_sha256': None, 'spans': [], 'status': 'no_readable_sources', 'truth_verified': False}
    packet = chain.full_packet(bundle); terms = tokens(str(bundle['request'].get('question', ''))+' '+str(bundle['request'].get('resolution_criteria', '')))
    rows = []
    for span in packet['evidence']:
        score = len(terms & tokens(span['text']))
        date_measure = bool(re.search(r'\b20\d\d\b|\d+(?:\.\d+)?%|publish|chart dated', span['text'], re.I))
        score += 3*date_measure
        # Publication/effective/observation distinctions often have few entity
        # repetitions; preserve these temporal qualifiers ahead of boilerplate.
        temporal_qualifier = bool(re.search(r'publish|effective|issued|released', span['text'], re.I)) and bool(
            re.search(r'\b\d{1,4}\b', span['text'])) and score > 3
        score += 12*temporal_qualifier
        rows.append({'evidence_id': span['evidence_id'], 'url': span['url'], 'body_sha256': span['body_sha256'],
                     'start': span['start'], 'end': span['end'], 'score': score})
    # One strongest passage per source precedes additional passages.
    ranked = sorted(rows, key=lambda r: (-r['score'], r['url'], r['start']))
    seen = set(); first = []; rest = []
    for row in ranked:
        if row['url'] in seen: rest.append(row)
        else: first.append(row); seen.add(row['url'])
    return {'protocol': PROTOCOL, 'packet_sha256': digest(packet), 'spans': first+rest,
            'truth_verified': False}


def run(bundle, folder, *, prior=(), network=False, search=None):
    """Bounded supplemental acquisition; search(tool, query) performs one request.

    No search is required when saved leads suffice. Missing credentials do not
    create a pretend search result. Existing journals and bodies remain intact.
    """
    from ForecastAgent.runtime.task_lock import task_lock
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    with task_lock(folder):
        return _run(bundle, folder, prior=prior, network=network, search=search)


def _run(bundle, folder, *, prior, network, search):
    if (bundle.get('supplement_lineage') or bundle.get('enhanced_supplement') or
        any(p.get('supplement_provenance') for p in bundle.get('pages', {}).values())) and not prior:
        raise ValueError('Prior supplement journals required for shared lifetime accounting')
    if len({digest(j) for j in prior}) != len(prior):
        raise ValueError('Duplicate prior journals would double-count lifetime usage')
    identity = {'protocol': PROTOCOL, 'parent_sha256': digest(bundle), 'prior_sha256': digest(prior),
                'caps': CAPS, 'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if (folder/'identity.json').exists() and load(folder/'identity.json') != identity:
        raise ValueError('Frozen repair identity changed; budgets cannot restart')
    save(folder/'identity.json', identity)
    state = load(folder/'state.json') if (folder/'state.json').exists() else {'attempts': []}
    overlay = copy.deepcopy(bundle)
    def reserve(tool, url=None):
        if usage(bundle, prior, state)[tool] >= CAPS[tool]: return None
        row = {'tool': tool, 'url': url, 'status': 'reserved', 'started_at_utc': now()}
        state['attempts'].append(row); save(folder/'state.json', state)
        return row
    # Restore successful search outputs without altering parent provider ledgers.
    for row in state['attempts']:
        if row['tool'] in ('tavily', 'exa') and row.get('response_file'):
            overlay.setdefault('source_leads', {})
            for hit in load(folder/row['response_file']).get('results', []):
                overlay['source_leads'][hit['url']] = hit
        if row['status'] == 'captured':
            page = load(folder/row['response_file'])
            if digest(page) != row['response_sha256']:
                raise ValueError('Saved capture changed')
            key = row['url']
            if key in overlay['pages'] and overlay['pages'][key].get('content') != page.get('content'):
                key += '#repair='+digest(page)[:12]
            overlay['pages'][key] = page
    gap_plan = plan(overlay)
    if network and search and gap_plan['gaps'] and not gap_plan['sources']:
        for tool in ('tavily', 'exa'):
            if any(r['tool'] == tool for r in state['attempts']): continue
            row = reserve(tool)
            if not row: continue
            row.update(search_depth='basic' if tool == 'tavily' else None, query=gap_plan['search_query'])
            save(folder/'state.json', state)
            try:
                response = search(tool, row['query'])
                row['response_file'] = f'search-{len(state["attempts"])}.json'; save(folder/row['response_file'], response)
                row['status'] = 'completed'
                for hit in response.get('results', []): overlay.setdefault('source_leads', {})[hit['url']] = hit
            except Exception as exc: row.update(status='failed', error=str(exc)[:400])
            save(folder/'state.json', state); gap_plan = plan(overlay)
            if gap_plan['sources']: break
    save(folder/'plan.json', gap_plan)
    attempted = {(r.get('url'), r.get('tool', r.get('method'))) for j in [*prior, state] for r in j.get('attempts', [])}
    attempted.update((r.get('url'), 'http') for r in bundle.get('fetch_attempts', []))
    for source in gap_plan['sources']:
        url = source['url']
        if any(r.get('url') == url and r['status'] == 'captured' for r in state['attempts']) or (
            url in overlay['pages'] and assess(overlay['request'], url, overlay['pages'][url].get('content', ''))['coverage_status'] == 'candidate_text'):
            continue
        if not network: continue
        for tool in ('http', 'browser'):
            if (url, tool) in attempted: continue
            row = reserve(tool, url)
            if not row: continue
            attempted.add((url, tool))
            try:
                page = fetch_document(url) if tool == 'http' else render_page(url, retrieved_at=now())
                row['response_file'] = f'capture-{len(state["attempts"])}.json'; save(folder/row['response_file'], page)
                row['response_sha256'] = digest(page)
                row['assessment'] = assess(overlay['request'], url, page.get('content', ''))
                row['status'] = 'captured' if row['assessment']['eligible_for_evidence'] else 'rejected'
            except Exception as exc: row.update(status='failed', error=str(exc)[:400])
            save(folder/'state.json', state)
            if row['status'] == 'captured': break
    for row in state['attempts']:
        if row['status'] == 'captured':
            page = load(folder/row['response_file']); key = row['url']
            if digest(page) != row['response_sha256']:
                raise ValueError('Saved capture changed')
            if key in overlay['pages'] and overlay['pages'][key].get('content') != page.get('content'):
                key += '#repair='+digest(page)[:12]
            page['supplement_provenance'] = {'protocol': PROTOCOL, 'actual_requested_url': row['url']}
            overlay['pages'][key] = page
    # Preserve rejected raw text, but exclude it from the bounded analysis view.
    for url, page in list(overlay['pages'].items()):
        assessment = assess(overlay['request'], url, page.get('content', ''))
        if not assessment['eligible_for_evidence']:
            overlay.setdefault('supplement_excluded_pages', {})[url] = overlay['pages'].pop(url)
    overlay['enhanced_supplement'] = {'protocol': PROTOCOL, 'usage': usage(bundle, prior, state),
                                     'remaining_gaps': plan(overlay)['gaps'] + [g for g in gap_plan['gaps']
                                         if g['reason'] != 'target_coverage_unknown'], 'parent_sha256': digest(bundle)}
    save(folder/'analysis-input.json', overlay); save(folder/'reading-hints.json', hints(overlay))
    save(folder/'report.json', {'usage': usage(bundle, prior, state), 'caps': CAPS,
         'raw_preserved': True, 'search_callback_enabled': bool(search), 'no_forecasts_submitted': True})
    return overlay


def main():
    import argparse
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prior', type=Path, action='append', default=[])
    parser.add_argument('--network', action='store_true')
    parser.add_argument('--search', action='store_true')
    parser.add_argument('--analyze', action='store_true')
    args = parser.parse_args()
    # Prior journals are mandatory for already supplemented inputs; never infer
    # their request counts from the number of successful bodies.
    original = load(args.input)
    if (original.get('supplement_lineage') or any(p.get('supplement_provenance') for p in original.get('pages', {}).values())) and not args.prior:
        raise ValueError('Prior supplement journals required for shared lifetime accounting')
    def search(tool, query):
        if tool == 'tavily':
            from ForecastAgent.providers.tavily_search import search_batch
            return search_batch(query[:350], os.environ['TAVILY_API_KEY'])
        from ForecastAgent.providers.exa_search import search
        return search(query[:350], os.environ['EXA_API_KEY'])
    overlay = run(original, args.output, prior=[load(p) for p in args.prior], network=args.network,
                  search=search if args.search else None)
    if args.analyze:
        from ForecastAgent.competition.mercury import run as analyze
        if not overlay['pages']:
            save(args.output/'analysis-unavailable.json', {'reason': 'no_readable_sources', 'no_invented_score': True})
        else:
            analyze(overlay, args.output/'mercury', reading_hints=load(args.output/'reading-hints.json'))


if __name__ == '__main__':
    main()
