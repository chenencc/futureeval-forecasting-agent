"""One host configuration check for source menus and injected HTTP transport."""
import os
import re


def contact_user_agent(service):
    """Return a valid contact header, never include it in agent-visible metadata."""
    value = os.environ.get('NWS_USER_AGENT', '') if service == 'nws' else ''
    if not value:
        value = os.environ.get('SEC_USER_AGENT', '')
    return value if isinstance(value, str) and len(value) <= 200 and not any(
        c in value for c in '\r\n') and re.search(r'[^\s@]+@[^\s@]+\.[^\s@]+', value) else None


def missing_configuration(source_id, source):
    required = source.get('requires_configuration', [])
    if source_id.startswith('nws_'):
        ready = contact_user_agent('nws')
    elif source_id.startswith('sec_'):
        ready = contact_user_agent('sec')
    elif source_id.startswith('congress_'):
        value = os.environ.get('CONGRESS_API_KEY', '')
        ready = 0 < len(value) <= 512 and not any(c.isspace() for c in value)
    else:
        return [name for name in required if not os.environ.get(name)]
    return [] if ready else list(required)
