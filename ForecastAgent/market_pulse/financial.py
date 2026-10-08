"""Quote-derived issuer routing and available-information research objectives.

Routing is not factual verification. Original group rules remain untouched.
Unknown independent sources stay eligible; explicit sibling issuer links do not.
"""
import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit

from ForecastAgent.providers.financial import source_urls
from ForecastAgent.tavily_research import canonical_url


VERSION = 'financial-acquisition-p0-v1'


def tokens(text):
    return set(re.findall(r'[a-z0-9]+', text.lower()))


def symbol(url):
    parts = urlsplit(url)
    value = parse_qs(parts.query).get('symbol', [''])[0]
    match = re.search(r'/(?:symbol|stock|quote)/([A-Za-z0-9.-]+)(?:/|$)', parts.path)
    return (value or (match.group(1) if match else '')).upper()


def issuer_profile(request):
    """Bind a selected group label and tickers only from observed rule links."""
    question = request.get('question', '')
    label = request.get('issuer_label')
    if not label:
        match = re.search(r'\(([^()]+)\)\s*$', question)
        label = match.group(1).strip() if match else None
    rows = []
    for field in ('resolution_criteria', 'fine_print', 'background'):
        for line in request.get(field, '').splitlines():
            # Only explicit list rows establish sibling identity. Prose and
            # generic SEC links are retained without an invented issuer label.
            match = re.match(r'\s*[*-]\s+(?:\[([^\]]+)\]|([A-Za-z0-9&. -]+?)(?=\s+Q[1-4]\b|\s*\())', line)
            name = (match.group(1) or match.group(2)).strip() if match else None
            for url in source_urls(line):
                rows.append({'url': url, 'issuer': name, 'field': field,
                             'quoted_line': line, 'observed_symbol': symbol(url) or None})
    target = tokens(label or '')
    target_rows = [row for row in rows if target and tokens(row['issuer'] or '') == target]
    siblings = {row['issuer'] for row in rows if row['issuer'] and tokens(row['issuer']) != target}
    return {'schema': VERSION, 'issuer_label': label,
            'identity_status': 'selected_label' if label else 'unresolved',
            'observed_symbols': sorted({r['observed_symbol'] for r in target_rows if r['observed_symbol']}),
            'sibling_labels': sorted(siblings), 'rule_link_rows': rows,
            'ticker_identity_verified': False, 'cik': None,
            'metric': 'gaap_diluted_eps' if 'earnings per share' in question.lower() else
                      'quarterly_revenue' if 'revenue' in question.lower() else 'other',
            'original_fields_sha256': {k: hashlib.sha256(request.get(k, '').encode()).hexdigest()
                for k in ('question', 'resolution_criteria', 'fine_print', 'background')}}


def url_scope(url, profile):
    """Exclude explicit cross-issuer routes without domain allowlisting."""
    target = tokens(profile.get('issuer_label') or '')
    if not target:
        return 'unknown'
    exact = {r['issuer'] for r in profile['rule_link_rows']
             if r['issuer'] and canonical_url(r['url']) == canonical_url(url)}
    if exact:
        # An ambiguous shared URL is retained, not assigned to one issuer.
        scopes = {tokens(name) == target for name in exact}
        return 'target' if scopes == {True} else 'other_issuer' if scopes == {False} else 'unknown'
    observed = symbol(url)
    if observed:
        if observed in profile['observed_symbols']:
            return 'target'
        other = {r['observed_symbol'] for r in profile['rule_link_rows']
                 if r['issuer'] and tokens(r['issuer']) != target}
        if observed in other:
            return 'other_issuer'
    host_words = tokens(urlsplit(url).hostname or '')
    if target <= host_words:
        return 'target'
    if any(tokens(name) <= host_words for name in profile['sibling_labels']):
        return 'other_issuer'
    return 'unknown'


def page_scope(url, page, profile):
    route = url_scope(url, profile)
    if route == 'other_issuer':
        return {'state': route, 'eligible_for_target': False,
                'reason': 'Observed group link or sibling symbol belongs to another issuer.'}
    # Calendar filters can be ignored by the publisher. A target URL is not
    # sufficient: distinguish an actual target row from a generic daily list.
    parts = urlsplit(url)
    if (profile.get('issuer_label') and parts.hostname == 'finance.yahoo.com'
            and parts.path.rstrip('/') == '/calendar/earnings'):
        body = tokens(page.get('content', ''))
        identifiers = [tokens(profile.get('issuer_label') or '')] + [tokens(s) for s in profile['observed_symbols']]
        found = any(s and s <= body for s in identifiers)
        if not found:
            return {'state': 'calendar_target_not_observed', 'eligible_for_target': False,
                    'reason': 'Saved calendar body has no target issuer or observed symbol; URL filter is unconfirmed.'}
        return {'state': 'target_calendar_context', 'eligible_for_target': True,
                'reason': 'Issuer identifier observed; row/date and metric still require review.'}
    return {'state': route, 'eligible_for_target': True,
            'reason': 'Routing hint only; body identity, period, metric and units are not verified.'}


def research_contract(profile):
    """Separate prediction inputs from the eventual settlement artifact."""
    return {'schema': VERSION, 'issuer_label': profile['issuer_label'], 'metric': profile['metric'],
        'objectives': [
            {'role': 'published_financial_history', 'instruction': 'Find the latest already published issuer quarterly report and comparable prior-year quarter. Preserve full tables, fiscal period, quarter versus cumulative columns, units and GAAP basis.'},
            {'role': 'current_operating_drivers', 'instruction': 'Find available issuer operating releases and dated independent drivers relevant to the upcoming quarter. Follow observed official detail links from a press index.'},
            {'role': 'management_guidance', 'instruction': 'Find existing management guidance for the target period, or record specifically that it is unavailable.'},
            {'role': 'dated_estimates', 'instruction': 'Find dated issuer-specific estimates if available; preserve period, currency, adjusted versus GAAP basis, and publication time. An adjusted EPS estimate cannot replace GAAP EPS.'},
            {'role': 'settlement_artifact', 'instruction': 'Locate the original target-quarter release if already published. If unpublished, record pending_publication separately; its absence is expected and is not a missing currently available predictor.'}],
        'planning_instruction': 'Use separate needs for available predictors and the eventual settlement artifact. When the target release is unpublished, prioritize published financial history as a critical need and the pending settlement artifact as supporting. Keep unavailable guidance or estimates as explicit gaps. Bind immutable original rule fields; do not invent target dates, quarters, values, source URLs or missing-event conclusions. A pending release is not a reason to keep retrying or to declare other research complete.',
        'routing_instruction': 'Group rules contain sibling issuers. Use target-specific links; retain SEC and independent unknown-domain leads. Check the saved body, not just the URL symbol filter. A press index is a discovery source, not a financial table.',
        'closure_instruction': 'Stop within the original budget and export explicit predictor gaps. Do not mark settlement pending as semantic completion, event absence or a forecast.',
        'effective_clock_instruction': 'Use the runtime operating clock and historical cutoff policy. These objectives do not establish publication availability or waive historical restrictions.',
        'search_limits_unchanged': {'tavily_basic_lifetime_max': 3, 'exa_lifetime_max': 1}}


def instruction(profile):
    return '\nFinancial acquisition objectives (research routing, not amended resolution rules):\n' + json.dumps(research_contract(profile))
