"""Bounded acquisition of issuer indexes, official feeds and sitemaps."""
import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl
from .profiles import inventory, PROFILES
from ForecastAgent.readers.encoding import decode_response

KINDS={'ir','rss','sitemap'}
ROUTES=[
    {'id':'govinfo_laws_feed','domain':'politics','kind':'rss',
     'url':'https://www.govinfo.gov/rss/plaw.xml','role':'official_document_discovery'},
    {'id':'govinfo_hearings_feed','domain':'politics','kind':'rss',
     'url':'https://www.govinfo.gov/rss/chrg.xml','role':'official_document_discovery'},
    {'id':'microsoft_earnings_index','domain':'finance','kind':'ir','profile_id':'microsoft_ir',
     'url':PROFILES['microsoft_ir']['url'],'role':'issuer_discovery'},
    {'id':'fed_announcement_feed','domain':'finance','kind':'rss',
     'url':'https://www.federalreserve.gov/feeds/press_all.xml','role':'official_announcement_discovery'},
    {'id':'fec_official_sitemap','domain':'politics','kind':'sitemap',
     'url':'https://www.fec.gov/sitemap-wagtail.xml/','role':'authority_page_discovery'},
]


def safe_target(value,base,hosts):
    url=urljoin(base,value.strip())
    p=urlsplit(url)
    safe=(p.scheme=='https' and p.hostname in hosts and not p.username and not p.password
          and p.port in (None,443) and len(url)<=2048
          and not any(any(t in k.casefold() for t in ('key','token','secret','signature','password')) for k,v in parse_qsl(p.query)))
    return urlunsplit((p.scheme,p.netloc,p.path,p.query,'')),bool(safe)


def parse_discovery(source,raw):
    params=source['discovery_parameters']
    kind=params['kind']; offset=params['offset']; limit=params['limit']
    base=source['final_url']; hosts=set(source['discovery_hosts'])
    raw,encoding=decode_response(raw,source.get('response_headers'),maximum=2_000_000)
    candidates=[]
    if kind=='ir':
        # Reuse label/context discovery; fetch full originals separately.
        from lxml import html
        root=html.fromstring(raw)
        base_tags=root.xpath('//base/@href')
        if base_tags:
            candidate_base,allowed=safe_target(base_tags[0],base,hosts)
            if allowed: base=candidate_base
        visible=' '.join(root.text_content().split())
        if re.search(r'checking your browser|verify you are human|access denied',visible,re.I) and len(visible)<2000:
            raise ValueError('Blocked issuer index; no document discovery')
        profile=PROFILES[params['profile_id']]
        result=inventory(raw,base,profile,limit=100000)
        for link in result['links']:
            url,eligible=safe_target(link['url'],base,hosts)
            if url==base or link['label'].strip().casefold().startswith('skip to '): continue
            # Issuer ownership is necessary but does not make unrelated product
            # download pages an IR source. Retain rejected leads transparently.
            eligible=eligible and any(urlsplit(url).path.casefold().startswith(p.casefold()) for p in profile['paths'])
            candidates.append(dict(link,url=url,eligible=eligible,target_kind='document',
                                   publisher_entity=profile['entity'],cik=profile.get('cik')))
        envelope='issuer_html_index'
    else:
        if re.search(br'<!\s*(?:DOCTYPE|ENTITY)\b',raw,re.I): raise ValueError('DTD and entity declarations forbidden')
        root=ET.fromstring(raw)
        local=lambda x:x.split('}')[-1]
        envelope=local(root.tag)
        if kind=='sitemap':
            if envelope not in {'urlset','sitemapindex'}: raise ValueError('Expected sitemap XML')
            tag='url' if envelope=='urlset' else 'sitemap'
            for entry in root:
                if local(entry.tag)!=tag: continue
                fields={local(x.tag):''.join(x.itertext()).strip() for x in entry}
                if not fields.get('loc'): raise ValueError('Sitemap entry missing loc')
                url,eligible=safe_target(fields['loc'],base,hosts)
                candidates.append({'url':url,'eligible':eligible,'target_kind':'sitemap' if tag=='sitemap' else 'document',
                                   'lastmod':fields.get('lastmod'),'label':fields['loc']})
        else:
            if envelope not in {'rss','feed','RDF'}: raise ValueError('Expected RSS or Atom envelope')
            for entry in root.iter():
                if local(entry.tag) not in {'item','entry'}: continue
                fields={}; links=[]
                for node in entry:
                    name=local(node.tag)
                    if name=='link':
                        if node.get('rel','alternate')=='alternate': links.append(node.get('href') or ''.join(node.itertext()).strip())
                    elif name in {'title','pubDate','published','updated','guid','id','description','summary'}:
                        fields[name]=''.join(node.itertext()).strip()
                value=next((x for x in links if x),None)
                url,eligible=safe_target(value,base,hosts) if value else (None,False)
                candidates.append(dict(fields,url=url,eligible=eligible,target_kind='document',label=fields.get('title'),
                                       missing_link=value is None))
                # GovInfo's alternate link is a detail page. Its RSS description
                # carries explicit full-text/PDF/XML originals. Retain those as
                # separate candidates instead of counting detail metadata as law text.
                if urlsplit(base).hostname=='www.govinfo.gov':
                    from lxml import html
                    markup=fields.get('description') or fields.get('summary') or ''
                    if markup.strip():
                        fragment=html.fragment_fromstring(markup,create_parent=True)
                        for anchor in fragment.xpath('.//a[@href]'):
                            original,allowed=safe_target(anchor.get('href'),base,hosts)
                            path=urlsplit(original).path
                            if not path.startswith('/content/pkg/') or not path.endswith(('.htm','.pdf','.xml')): continue
                            candidates.append(dict(url=original,eligible=allowed,target_kind='document',
                                label=' '.join(anchor.text_content().split()),guid=fields.get('guid'),
                                pubDate=fields.get('pubDate'),parent_entry_url=url,
                                publisher_entity='US Government Publishing Office',link_role='original_rendition'))
    # Exact URL deduplication only; query strings retain distinct documents.
    seen=set(); unique=[]
    for item in candidates:
        key=item['url']
        if key and key in seen: continue
        if key: seen.add(key)
        unique.append(item)
    records=unique[offset:offset+limit]
    metadata={'discovery_kind':kind,'envelope':envelope,'offset':offset,'limit':limit,
              'total_candidates':len(unique),'duplicates_removed':len(candidates)-len(unique),
              'next_offset':offset+limit if offset+limit<len(unique) else None,'content_encoding':encoding,
              'allowed_target_hosts':sorted(hosts),'full_archive_complete':False,
              'parser_version':'discovery_v1',
              'publication_or_lastmod_is_historical_body_proof':False}
    return records,{'total_reported':len(unique),'more_available':offset+limit<len(unique),
                    'scope':'one index response; linked originals and child sitemaps are separate requests',
                    'native_metadata':metadata}


def definitions():
    specs=[('intelligence_discover','Fetch one official RSS/Atom, sitemap, or curated issuer IR index. Saves raw response and candidate URLs. No automatic recursion, original-page download, redirects or retry.',
            {'url':{'type':'string'},'kind':{'type':'string','enum':sorted(KINDS)},'profile_id':{'type':'string'},
             'offset':{'type':'integer','minimum':0,'maximum':100000},'limit':{'type':'integer','minimum':1,'maximum':100}},['url','kind']),
           ('intelligence_acquire_link','Download one exact candidate from a hash-verified discovery capture using zero-based index. Child sitemap requires an explicit separate discover call. Preserves parent hash, source identity, timestamps and original bytes; consumes the existing shared request budget.',
            {'capture_id':{'type':'string'},'index':{'type':'integer','minimum':0,'maximum':99}},['capture_id','index'])]
    return [{'type':'function','function':{'name':name,'description':description,'parameters':{'type':'object','properties':props,'required':required,'additionalProperties':False}}} for name,description,props,required in specs]
