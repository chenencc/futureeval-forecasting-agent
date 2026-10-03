"""Observable source contracts, without event judgments or outcome labels."""
import re
from urllib.parse import urlsplit, unquote
from ForecastAgent.supplement.discovery import tokens, safe_url
from ForecastAgent.evidence.source_identity import observed_urls

MONTHS = 'January February March April May June July August September October November December'.split()
GENERIC = set('what when how much many company companies report reported reporting earnings release releases financial quarterly annual total adjusted seasonally thousands units million billion percent percentage preliminary initial value amount highest lowest publicly available the for a an and or of to in on at by is are was were all any its their this that it has have been with from than as before after during according'.split())


def host_root(url):
    parts = (urlsplit(url).hostname or '').lower().split('.')
    length = 3 if len(parts) >= 3 and '.'.join(parts[-2:]) in {'co.uk', 'com.au', 'co.jp'} else 2
    return '.'.join(parts[-length:])


def normalized(text):
    return ' '.join(re.findall(r'[a-z0-9]+', text.lower()))


def contract(question):
    title = str(question.get('question', '')); rules = str(question.get('resolution_criteria', ''))
    issuer = question.get('target_issuer') or {}
    name = issuer.get('name') if isinstance(issuer, dict) else str(issuer)
    if not name:
        match = re.search(r'\bwill\s+(?:the\s+)?(.{3,100}?)\s+(?:report|file|submit)\b', title, re.I)
        # Only infer named organizations, avoiding generic event/action phrases.
        if match and re.search(r'\b[A-Z][a-z]+\b', match[1]): name = match[1]
    topics = tokens(title) - GENERIC - {m.lower() for m in MONTHS}
    sources = {}
    for field in ('resolution_criteria', 'background'):
        for raw in observed_urls(question.get(field, '')):
            url = safe_url(raw)
            if url:
                sources[url] = field
    periods = re.findall(r'\bQ([1-4])\s*(20\d\d)?', title, re.I)
    metrics = []
    for phrase in re.findall(r'\b(?:in|of)\s+([A-Za-z -]{3,90}?)\s+(?:will|be|for)\b', title, re.I):
        words = normalized(phrase).split()
        if len(words) >= 2 and not set(words[-2:]) <= GENERIC:
            metrics.append(' '.join(words[-2:]))
    return {'issuer_name': name, 'issuer_aliases': issuer.get('aliases', []) if isinstance(issuer, dict) else [],
            'topic_terms': sorted(topics), 'preferred_domains': sorted({host_root(u) for u in sources}),
            'rule_urls': [u for u, origin in sources.items() if origin == 'resolution_criteria'],
            'quarter': periods[0][0] if periods else None, 'metric_phrases': metrics,
            'target_months': [m for m in MONTHS if re.search(r'\b'+m+r'\b', title, re.I)],
            'initial_release_required': bool(re.search(r'initially published|first release|preliminary', rules, re.I))}


def match_source(expected, url, text):
    body = normalized(text); issuer = expected['issuer_name']
    preferred = host_root(url) in expected['preferred_domains']
    names = [issuer, *expected['issuer_aliases']] if issuer else []
    observed = any(normalized(n) in body for n in names)
    topic_matches = sorted(set(expected['topic_terms']) & tokens(text+' '+urlsplit(url).path))
    entity = 'not_required' if not issuer else 'observed_name' if observed else 'preferred_domain' if preferred else 'not_observed'
    period = expected['quarter']
    signal = text+' '+unquote(urlsplit(url).path)
    observed_quarters = set(re.findall(r'\bQ([1-4])\b',signal,re.I))
    body_quarters = set(re.findall(r'\bQ([1-4])\b',text,re.I))
    for number,name in {'1':'first','2':'second','3':'third','4':'fourth'}.items():
        if re.search(r'\b'+name+r' quarter\b',signal,re.I): observed_quarters.add(number)
        if re.search(r'\b'+name+r' quarter\b',text,re.I): body_quarters.add(number)
    quarter_matches = not period or period in body_quarters
    quarter_status = 'not_required' if not period else 'observed' if period in observed_quarters else 'mismatch' if observed_quarters else 'unknown'
    metrics = expected['metric_phrases']
    metric_observed = not metrics or any(m in body for m in metrics)
    return {'entity_status': entity, 'preferred_domain': preferred, 'topic_matches': topic_matches,
            'quarter_observed': quarter_matches, 'quarter_status':quarter_status,
            'observed_quarters':sorted(observed_quarters), 'entity_acceptable': entity != 'not_observed',
            'body_quarters':sorted(body_quarters),
            'topic_acceptable': bool(expected['topic_terms']) and len(topic_matches) >= min(2, len(expected['topic_terms'])),
            'metric_phrase_observed': metric_observed, 'source_identity_verified': False,
            'metric_verified': False, 'initial_release_verified': False}
