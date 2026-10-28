"""Distinguish platform rule references from independent event information."""
import re,unicodedata
from urllib.parse import urlsplit,unquote

POLICY='topic-bound-independent-candidates-v1'

def fold(text):return ''.join(c for c in unicodedata.normalize('NFKD',unquote(str(text))) if not unicodedata.combining(c)).lower()

def platform_exclusion(url):
    try:parts=urlsplit(url or '');host=(parts.hostname or '').lower()
    except ValueError:return None
    if host=='metaculus.com' or host.endswith('.metaculus.com'):
        if re.match(r'^/(?:faq|help|guides?|terms|privacy)(?:/|$)',parts.path,re.I):return 'PLATFORM_RULE_REFERENCE'
        if re.match(r'^/(?:questions?|posts?)(?:/|$)',parts.path,re.I):return 'PLATFORM_FORECAST_PAGE'
    if re.match(r'^/metaculus/\d+(?:/|$)',parts.path,re.I):return 'PREDICTION_QUESTION_MIRROR'
    return None

def topic_match(definition,row):
    title=fold(definition.get('title',''));rules=definition.get('resolution_criteria','')
    target=fold(' '.join(str(row.get(k) or '') for k in ('url','title','snippet')))
    identifiers=re.findall(r'\b(?:[A-Z]{1,6}-?\d{2,8}[A-Z]?|[A-Z]{2,8}-\d{1,6})\b',definition.get('title','')+' '+rules)
    if any(re.search(r'(?<![a-z0-9])'+re.escape(i.lower())+r'(?![a-z0-9])',target) for i in identifiers):return True
    stop=set('the how many will would what when where which this that with from before after been have does shall exceed count number official status report data'.split())
    def stem(word):return word[:-2] if word.endswith('es') and len(word)>5 else word[:-1] if word.endswith('s') and len(word)>4 else word
    terms={stem(w) for w in re.findall(r'[a-z]{3,}',title) if w not in stop}
    words={stem(w) for w in re.findall(r'[a-z]{3,}',target)}
    acronyms={fold(w) for w in re.findall(r'\b[A-Z][A-Z0-9]{2,}\b',definition.get('title',''))}
    return len(terms&words)>=2 or bool(acronyms&words)

def background_links(definition):
    from .platforms import rule_urls
    text=definition.get('description','') or '';result=[]
    for url in rule_urls(text):
        if platform_exclusion(url):continue
        match=re.search(r'\[([^]\n]+)\]\('+re.escape(url)+r'\)',text)
        result.append(dict(url=url,title=match.group(1) if match else '',question_context='description',truth_verified=False))
    return result
