"""Document acquisition needs and observable candidates, never event verdicts."""
import hashlib
import re
from ForecastAgent.supplement.discovery import tokens
from ForecastAgent.supplement.source_contract import contract, match_source
from ForecastAgent.readers.quality import body_diagnostics
from ForecastAgent.evidence.source_coverage import observe
from ForecastAgent.supplement import binding_guard

PROTOCOL = 'material-gap-v1'
STOP = set('verify confirm determine find documented which whether publicly available before after must model models source sources official report reports query browser public'.split())
FAMILIES = (
    ('availability', r'public.*(?:query|access)|(?:browser|api).*access|release notice|availability|verify which models',
     r'now available|available (?:today|now|to everyone)|(?:launch|releas)(?:ed|ing).{0,60}(?:today|public)|roll(?:ing|ed) out.{0,60}(?:today|now)|access.{0,60}(?:api|browser)|available.{0,60}(?:api|browser|subscribers|users)',
     'public launch announcement API browser access availability'),
    ('evaluation', r'evaluat|score|performance|benchmark|gold|methodolog|internet search',
     r'evaluat|benchmark|score|gold.medal|methodolog|experiment|internet search',
     'evaluation technical report scores methodology'),
    ('metadata', r'time.?zone|timestamp|metadata|units|station.*identity|gauge.*identity|category.*definition|revision|access.*(?:historical|data)',
     r'time.?zone|timestamp|units?|station|gauge|messstellen|revision|definition|download|webservice|api',
     'official metadata timestamp units definition'),
    ('data', r'measurement|observation|time.series|water level|hourly earnings|price observation',
     None, 'official historical measurement dataset download'),
)


def enabled(bundle):
    q = bundle.get('request', {})
    return (q.get('collection_workflow') == PROTOCOL and q.get('experiment_id')
            and q.get('pipeline') == 'collection')


def family(text):
    for name, pattern, _, _ in FAMILIES:
        if re.search(pattern, text, re.I): return name
    return 'document'


def build(bundle, journal=None, usage=None, caps=None):
    """A candidate means literal document coverage; analysis must bind conditions."""
    q = bundle['request']; expected = contract(q)
    attempts = (journal or {}).get('attempts', [])
    needs = [dict(n) for n in bundle.get('plan') or []]
    # Preserve an explicit qualifying condition even if the planner omitted it.
    rules = q.get('resolution_criteria', '')
    if re.search(r'public.*(?:available|queryable)', rules, re.I) and not any(
            family(n.get('condition', '')) == 'availability' for n in needs):
        needs.append({'id':'rule_public_access', 'priority':'critical',
            'condition':'Public release or browser/API access notice for the subject system',
            'query':q['question']+' public launch API browser access announcement'})
    rows = []
    for n in needs:
        text = n.get('condition', '')
        kind = family(text)
        pattern = next((p for name,_,p,_ in FAMILIES if name == kind), None)
        suffix = next((s for name,_,_,s in FAMILIES if name == kind), 'primary source document')
        terms = tokens(text) - STOP
        candidates = []
        for url, page in bundle.get('pages', {}).items():
            body = page.get('content', '')
            if not body_diagnostics(body)['usable_text']: continue
            match = match_source(expected, url, body)
            coverage=observe(q,url,body,page)
            identifier_anchor=bool(set(coverage['identity']['expected_ids']) & set(coverage['identity']['observed_ids']))
            lead = bundle.get('source_leads',{}).get(url,{})
            discovery_association=n['id'] in lead.get('need_ids',[]) and lead.get('origin')=='missing_family_search'
            anchored=(match['topic_acceptable'] or identifier_anchor or discovery_association)
            if not anchored or not match['entity_acceptable']: continue
            signals = sorted(terms & tokens(body))
            present = bool(pattern and re.search(pattern, body, re.I))
            if kind == 'availability':
                # Announced future rollout is a discovery lead, not a captured
                # current-access notice. This still does not verify event truth.
                sentences=re.split(r'[.!?\n]+',body)
                present=any(re.search(pattern,s,re.I) and not re.search(
                    r'will be|plan(?:s|ned)? to|coming soon|before rolling|trusted testers',s,re.I)
                    for s in sentences)
            if kind == 'data':
                present = coverage['data_capture_candidate']
            elif kind == 'document':
                present = len(signals) >= min(3, max(1, len(terms)))
            if present:
                candidates.append({'url':url, 'body_sha256':hashlib.sha256(body.encode()).hexdigest(),
                                   'literal_terms':signals,'discovery_need_association':discovery_association,
                                   'subject_binding_verified':False,'truth_verified':False})
        bindings = []; blocked_bindings = []
        for review in (journal or {}).get('material_reviews', []):
            for binding in review.get('result', {}).get('bindings', []):
                if binding.get('need_id') != n['id']:
                    continue
                guard = binding_guard.assess(n, binding,
                    {r['url'] for r in review.get('result', {}).get('deferred_urls',[])})
                if not guard['eligible_for_material_closure']:
                    blocked_bindings.append({'url':binding.get('url'), 'guard':guard})
                    continue
                page = bundle.get('pages', {}).get(binding.get('url'), {})
                body = page.get('content', '')
                if (binding.get('need_id') == n['id'] and binding.get('quote_bound') and
                        binding.get('body_sha256') == hashlib.sha256(body.encode()).hexdigest() and
                        body[binding.get('start', 0):binding.get('end', 0)] == binding.get('quote') and
                        body_diagnostics(body)['usable_text'] and
                        all(binding.get('axes', {}).get(axis) is True for axis in binding_guard.required_axes(n))):
                    bindings.append(binding)
        # Structured measurement records can be checked without model reasoning.
        exact_data = []
        if kind == 'data':
            for c in candidates:
                page = bundle['pages'][c['url']]
                axes = observe(q, c['url'], page.get('content', ''), page)
                if (binding_guard.assess(n,c)['eligible_for_material_closure'] and
                    axes['data_capture_candidate'] and (not axes['clock']['expected'] or axes['clock']['requested_clock_observed'])):
                    exact_data.append(c)
        target_captured = bool(bindings or exact_data)
        leads = bundle.get('source_leads', {})
        discovered = [u for u, lead in leads.items() if n['id'] in lead.get('need_ids', [])]
        readable = [u for u in discovered if body_diagnostics(bundle.get('pages', {}).get(u, {}).get('content', ''))['usable_text']]
        searches = [a for a in attempts if a.get('need_id') == n['id'] and a.get('tool') in {'tavily','exa'}]
        failed_reads = [a for a in attempts if a.get('status') in {'failed','rejected'} and
                        a.get('need_id') == n['id'] and a.get('tool') in {'http','browser'}]
        status = 'candidate_captured' if candidates else 'unlocated'
        reason = 'Candidate document retained; qualifying conditions require later analysis.' if candidates else 'No body matching this document family is saved.'
        if not candidates and searches:
            failed = any(a['status']=='failed' for a in searches)
            status = 'discovery_failed' if failed else 'search_attempted'
            reason = 'Discovery request failed; attempt remains consumed.' if failed else 'Discovery attempted; no matching readable body captured.'
            if all(a.get('result_count')==0 for a in searches):
                status='no_source_returned';reason='Completed searches returned no discovery candidates.'
        if not candidates and failed_reads:
            status = 'read_failed'; reason = 'Located candidates failed capture or parsing.'
        if not candidates and usage and caps and all(usage[k] >= caps[k] for k in ('tavily','exa')):
            status = 'budget_exhausted'; reason = 'Shared discovery lifetime capacity exhausted.'
        query_base=(q['question']+' '+text[:100]) if kind=='availability' else n.get('query') or q['question']
        rows.append({'id':n['id'], 'priority':n.get('priority','useful'), 'family':kind,
                     'required_source_domains':n.get('required_source_domains',[]),
                     'condition':text, 'status':status, 'reason':reason,
                     'query':(' '.join(query_base.split())[:240]+' '+suffix)[:350],
                     'candidates':candidates, 'search_attempts':len(searches),
                     'discovered_urls':discovered, 'readable_urls':readable,
                     'acquisition_state':'target_material_captured' if target_captured else
                         'readable_body_saved' if candidates or readable else 'candidate_discovered' if discovered else 'unlocated',
                     'target_material_captured':target_captured, 'material_bindings':bindings,
                     'blocked_material_bindings':blocked_bindings,
                     'source_requirement':binding_guard.source_requirement(n),
                     'required_axes':binding_guard.required_axes(n),
                     'deterministic_data_bindings':exact_data,
                     'next_action':None if target_captured else 'review_candidate_fit' if candidates else 'discover_missing_document_family',
                     'semantic_verified':False})
    return {'schema':PROTOCOL, 'needs':rows, 'usage':usage, 'caps':caps,
            'review_errors':[r.get('error_code',r.get('error','unknown_review_failure'))
                for r in (journal or {}).get('material_reviews',[]) if r.get('status') in {'failed','invalid_or_failed','reserved'}],
            'candidate_coverage_only':True, 'semantic_completeness_verified':False}


def next_search(ledger, journal, usage, caps, available_tools=('tavily','exa')):
    """One request per need/provider; failures and reservations remain consumed."""
    pending = [n for n in ledger['needs'] if n['priority']=='critical' and not n['target_material_captured']]
    pending.sort(key=lambda n:(n['priority']!='critical', n['family']!='availability', n['id']))
    attempted = {(a.get('need_id'), a.get('tool')) for a in journal['attempts']}
    queries = {(a.get('tool'),a.get('query')) for a in journal['attempts']}
    for need in pending:
        for tool in ('tavily','exa'):
            if tool in available_tools and usage[tool] < caps[tool] and (need['id'],tool) not in attempted and (tool,need['query']) not in queries:
                return {'tool':tool, 'need_id':need['id'], 'query':need['query']}
    return None


def termination(ledger, *, reason, search_available):
    pending = [n['id'] for n in ledger['needs'] if n['priority']=='critical' and not n['target_material_captured']]
    complete = bool(ledger['needs']) and not pending
    return {'reason':reason, 'critical_unlocated_need_ids':pending,
            'critical_unverified_need_ids':pending,
            'review_errors':ledger.get('review_errors',[]),
            'legacy_unlocated_field_means_unverified':True,
            'acquisition_outcome':'materials_ready' if complete else
                'review_incomplete' if ledger.get('review_errors') or reason=='material_review_failed' else
                'budget_exhausted' if 'capacity_exhausted' in reason else
                'source_unreadable' if any(n['status']=='read_failed' for n in ledger['needs'] if n['id'] in pending) else 'material_unlocated',
            'target_material_need_ids':[n['id'] for n in ledger['needs'] if n['target_material_captured']],
            'candidate_need_ids':[n['id'] for n in ledger['needs'] if n['candidates']],
            'search_available':search_available, 'semantic_completeness_verified':False,
            'interpretation_pending':True}
