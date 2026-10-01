"""Observable extraction diagnostics, never source reliability or truth scores."""
import re


def navigation_shell(content):
    """Detect link-heavy navigation with no substantial body, regardless of length."""
    links = re.findall(r'\[[^\]\n]*\]\([^\n]*?\)', content)
    residue = re.sub(r'!?\[[^\]\n]*\]\([^\n]*?\)', '', content)
    paragraphs = [line.strip(' *+#\t') for line in residue.splitlines()]
    substantive = [line for line in paragraphs if len(line) >= 160 and len(line.split()) >= 20]
    controls = bool(re.search(r'select tags|showing results?\b|loading\.{2,}|javascript:void', content, re.I))
    return len(links) >= 20 and controls and not substantive


def body_diagnostics(content, *, kind='', metadata=None, documents=()):
    text = content.strip()
    corrupt = (text.count('\ufffd') > max(3, len(text)*0.01) or
               sum(ord(c)<32 and c not in '\n\r\t' for c in text)>max(3,len(text)*0.01))
    title=(metadata or {}).get('extracted_title','')
    blocked = bool(re.search(r'just a moment|verify you are human|enable javascript and cookies|access denied|your request has been flagged as potentially automated', text, re.I))
    blocked = blocked or (bool(re.fullmatch(r'request access|access denied|security verification',title,re.I)) and
                          bool(re.search(r'captcha|bot test|programmatic access|IP access',text,re.I)))
    js_shell = len(text) < 300 and bool(re.search(r'enable javascript|javascript\s+(?:is required|must be enabled|needs to be enabled)', text, re.I))
    login = len(text) < 600 and bool(re.search(r'sign in to (?:read|continue)|subscribe to (?:read|continue)', text, re.I))
    # Strip standard US government identity chrome before judging body substance.
    government_shell = False
    if 'official website of the United States government' in text:
        residue = text
        for pattern in (r'An official website of the United States government', r"Here['’]s how you know",
            r'Official websites use \.gov', r'A \.gov website belongs to an official government organization in the United States\.',
            r'Secure \.gov websites use HTTPS', r'A lock \(.*?Share sensitive information only on official, secure websites\.'):
            residue = re.sub(pattern, '', residue, flags=re.I | re.S)
        government_shell = len(residue.strip()) < 80
    state = 'empty_text' if not text else 'corrupt_text' if corrupt else 'access_interstitial' if blocked else 'government_banner_only' if government_shell else 'javascript_shell' if js_shell else 'login_preview' if login else 'navigation_shell' if navigation_shell(text) else 'thin' if len(text) < 200 else 'readable'
    return {'schema': 'body_diagnostics_v1', 'state': state, 'chars': len(text),
            'paragraph_count': sum(bool(s.strip()) for s in text.splitlines()),
            'table_count': (metadata or {}).get('html_table_count', sum(d.get('metadata', {}).get('table_count', 0) for d in documents)),
            'page_reading_gaps': [d.get('metadata', {}).get('page') for d in documents if d.get('metadata', {}).get('reading_gap')],
            'usable_text': state in {'readable', 'thin'}, 'truth_verified': False}
