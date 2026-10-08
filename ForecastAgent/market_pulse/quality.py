"""Body diagnostics with conservative shell and same-version rejection handling."""
import copy
import hashlib
import re

from ForecastAgent.readers.quality import body_diagnostics as BASE_DIAGNOSTICS


def diagnostics(content, **kwargs):
    result = BASE_DIAGNOSTICS(content, **kwargs)
    text = content.strip()
    # Both markers are required: ordinary articles can contain Reference #.
    cdn_error = (len(text) < 1500 and bool(re.search(r'\breference\s*#\s*\S+', text, re.I))
                 and bool(re.search(r'https?://errors\.(?:edgesuite|edgekey)\.net/', text, re.I)))
    lines = [line.strip(' *#\t') for line in text.splitlines() if line.strip()]
    menu_terms = {'skip to main content', 'investor relations', 'stock price', 'sec filings',
                  'financial data', 'quarterly earnings reports', 'newsroom', 'contact', 'privacy policy'}
    matches = sum(line.lower() in menu_terms for line in lines)
    # Cart counters and menu expand controls are not financial table values.
    body_without_controls = '\n'.join(line for line in lines if line not in {'0', '+', '-'})
    data = bool(re.search(r'\d|\$|%|\b(?:million|billion|reported|increased|decreased)\b', body_without_controls, re.I))
    menu = len(lines) >= 10 and matches >= 4 and not data and all(len(line.split()) <= 8 for line in lines)
    if cdn_error or menu:
        result.update(state='access_interstitial' if cdn_error else 'navigation_shell', usable_text=False,
                      rejection_reason='cdn_error_reference' if cdn_error else 'financial_navigation_without_body')
    result['financial_quality_version'] = 'body-p0-v1'
    return result


def page_diagnostics(page, previous=None):
    result = diagnostics(page.get('content', ''), documents=page.get('documents', []),
                         metadata=page.get('page_date_metadata') or {})
    version = previous if previous is not None else page
    same_version = (previous is None or bool(page.get('sha256')) and
                    page.get('sha256') == previous.get('sha256'))
    old = version.get('body_diagnostics') or {}
    capture = version.get('capture_status') or {}
    rejection = (old.get('usable_text') is False and old.get('state') in
                 {'access_interstitial', 'navigation_shell', 'login_preview', 'government_banner_only'})
    rejection = rejection or (capture.get('usable_text') is False and
                 capture.get('category') in {'http_error', 'access_restricted', 'blocked', 'interstitial'})
    if same_version and rejection:
        result.update(usable_text=False, state=old.get('state') if rejection and old.get('state') in
                      {'access_interstitial', 'navigation_shell', 'login_preview', 'government_banner_only'}
                      else result['state'] if not result['usable_text'] else 'capture_rejected',
                      rejection_reason='same_raw_version_rejection_preserved')
        result['prior_rejection'] = {'body_diagnostics': copy.deepcopy(old),
                                     'capture_status': copy.deepcopy(capture),
                                     'raw_sha256': version.get('sha256')}
    # An empty parser failure is recoverable; it is not an access rejection.
    result['content_sha256'] = hashlib.sha256(page.get('content', '').encode()).hexdigest()
    return result
