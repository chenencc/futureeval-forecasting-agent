"""Observable extraction diagnostics, never source reliability or truth scores."""
import re


def body_diagnostics(content, *, kind='', metadata=None, documents=()):
    text = content.strip()
    title=(metadata or {}).get('extracted_title','')
    blocked = bool(re.search(r'just a moment|verify you are human|enable javascript and cookies|access denied|your request has been flagged as potentially automated', text, re.I))
    blocked = blocked or (bool(re.fullmatch(r'request access|access denied|security verification',title,re.I)) and
                          bool(re.search(r'captcha|bot test|programmatic access|IP access',text,re.I)))
    js_shell = len(text) < 300 and bool(re.search(r'enable javascript|javascript is required', text, re.I))
    login = len(text) < 600 and bool(re.search(r'sign in to (?:read|continue)|subscribe to (?:read|continue)', text, re.I))
    state = 'access_interstitial' if blocked else 'javascript_shell' if js_shell else 'login_preview' if login else 'thin' if len(text) < 200 else 'readable'
    return {'schema': 'body_diagnostics_v1', 'state': state, 'chars': len(text),
            'paragraph_count': sum(bool(s.strip()) for s in text.splitlines()),
            'table_count': (metadata or {}).get('html_table_count', sum(d.get('metadata', {}).get('table_count', 0) for d in documents)),
            'page_reading_gaps': [d.get('metadata', {}).get('page') for d in documents if d.get('metadata', {}).get('reading_gap')],
            'usable_text': state in {'readable', 'thin'}, 'truth_verified': False}
