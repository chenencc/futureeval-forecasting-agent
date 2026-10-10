"""Deterministic reading views with preserved originals and explicit coordinates."""
import hashlib
import json
import re

FIELD = 'research_reading_policy'
POLICY = 'preserved_views_v1'
INLINE_IMAGE = re.compile(r'data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=\r\n]+')


def enabled(bundle):
    return bundle.get('request', {}).get(FIELD) == POLICY


def reading(body, url):
    """Decode only a recognized post-stream schema; other JSON stays original."""
    if body.lstrip().startswith('{'):
        try:
            data = json.loads(body)
        except ValueError:
            data = None
        posts = data.get('post_stream', {}).get('posts') if isinstance(data, dict) and isinstance(data.get('post_stream'), dict) else None
        if isinstance(posts, list) and posts and all(isinstance(p, dict) and isinstance(p.get('cooked'), str) for p in posts):
            from ForecastAgent.readers.html import parse_html
            text, provenance, links = '', [], []
            for index, post in enumerate(posts):
                if not post['cooked'].strip():
                    continue
                decoded, _, observed = parse_html(post['cooked'], url)
                if not decoded.strip():
                    continue
                header = '\n'.join(f'{label}: {post[key]}' for key, label in
                    [('created_at','Post created'),('updated_at','Post updated'),('username','Author')]
                    if isinstance(post.get(key), str))
                start = len(text)
                text += (header+'\n' if header else '') + decoded + '\n\n'
                provenance.append({'start':start,'end':len(text),'json_pointer':f'/post_stream/posts/{index}',
                    'html_field':'cooked','post_id':post.get('id'), 'post_number':post.get('post_number'),
                    'metadata_is_copied_not_inferred':True})
                links.extend(observed)
            if text.strip():
                return {'text':text, 'coordinate_space':'decoded_post_view',
                    'view_sha256':hashlib.sha256(text.encode()).hexdigest(),
                    'parser':POLICY, 'provenance':provenance, 'links':links,
                    'topic_completeness_verified':False}
    return {'text':body, 'coordinate_space':'saved_body', 'parser':POLICY,
        'omitted_inline_image_ranges':[(m.start(),m.end()) for m in INLINE_IMAGE.finditer(body)]}


def partitions(view):
    """Never change a literal span or claim that an omitted image was read."""
    from ForecastAgent.research_loop.forecast_brief_refs import blocks
    text = view['text']; cursor = 0
    ranges = []
    for start, end in view.get('omitted_inline_image_ranges', []):
        if cursor < start:ranges.append((cursor,start))
        cursor=end
    if cursor < len(text):ranges.append((cursor,len(text)))
    for start, end in ranges:
        for a,b in blocks(text[start:end]):
            if text[start+a:start+b].strip():
                yield start+a,start+b


def metadata(view):
    return {k:view[k] for k in ('coordinate_space','view_sha256','parser','topic_completeness_verified') if k in view}


def substantive(spans):
    """Move title/date/author navigation into context; never alter quoted text."""
    body=[s for s in spans if not re.fullmatch(r'\s*#{1,6}\s+[^\n]+\s*',s['text'])
        and not re.fullmatch(r'\s*(?:Post created|Post updated|Author):[^\n]+\s*',s['text'])
        and not re.fullmatch(r'\s*\[?!\[.*?\]\(.*?\)(?:\]\([^)]*\))?\s*',s['text'])
        and not re.fullmatch(r'\s*\d{1,2}:\d{2}\s*(?:AM|PM)\s+[A-Z]{2,5}\s*',s['text'])
        and not re.fullmatch(r'\s*(?:[\w-]+\.)+[A-Za-z]{2,}\s*',s['text'])]
    return body or spans
