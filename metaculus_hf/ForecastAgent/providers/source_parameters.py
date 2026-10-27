"""Register supported date variants of an already discovered source, without HTTP."""
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit
from ForecastAgent.tavily_research import canonical_url


def parameterize(task, args):
    base = canonical_url(args['url'])
    if base not in task.catalog():
        raise ValueError('Choose an exact discovered base source URL')
    parts = urlsplit(base)
    fields = dict(parse_qsl(parts.query))
    if parts.hostname not in {'mesonet.agron.iastate.edu', 'www.mesonet.agron.iastate.edu'} or parts.path != '/sites/hist.phtml' or not fields.get('station') or not fields.get('network'):
        raise ValueError('Date parameters are implemented only for discovered IEM hist.phtml station/network sources')
    year, month = args['year'], args['month']
    if type(year) is not int or not 1900 <= year <= 2100 or type(month) is not int or not 1 <= month <= 12:
        raise ValueError('Use an integer year (1900-2100) and month (1-12)')
    if task.cutoff and (year, month) > (task.cutoff.year, task.cutoff.month):
        raise ValueError('Historical observation parameters cannot start after the effective cutoff')
    fields.update(year=str(year), month=str(month), mode='monthly')
    derived = canonical_url(urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(fields), '')))
    task.bundle['source_leads'][derived] = {'url': derived, 'origin': 'supported_source_parameters',
        'base_url': base, 'station': fields['station'], 'network': fields['network'], 'year': year, 'month': month}
    task.save()
    return {'url': derived, 'base_url': base, 'http_attempts': 0,
        'instruction': 'Read this registered exact source key; parameter registration is not a successful capture.'}
