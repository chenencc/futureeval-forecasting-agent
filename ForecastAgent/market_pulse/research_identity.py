"""Bind financial issuer identity from original question layouts, not leaf labels."""
import copy
import re
from ForecastAgent.market_pulse.financial import issuer_profile, tokens


def profile(request):
    original = issuer_profile(request)
    if 'guidance' not in request.get('question', '').casefold():
        return original
    title = request.get('question', '')
    match = re.search(r'\b(?:What will be|What will|What is)\s+(.+?)[\u2019\x27]s\s+forward guidance\b', title, re.I)
    if not match:
        if request.get('issuer_label'):
            return original
        raise ValueError('Guidance issuer is not explicitly established in original title')
    issuer = match[1].strip()
    if request.get('issuer_label') and tokens(request['issuer_label']) != tokens(issuer):
        raise ValueError('Explicit guidance issuer conflicts with original title')
    view = copy.deepcopy(request); view['issuer_label'] = issuer
    result = issuer_profile(view)
    result['issuer_original_ref'] = {'field': 'question', 'start': match.start(1),
        'end': match.end(1), 'quote': title[match.start(1):match.end(1)]}
    return result
