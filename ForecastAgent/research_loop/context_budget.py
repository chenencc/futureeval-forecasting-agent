"""Explicit task-owned context preference and hard delivery ceiling."""
FIELD = 'research_context_budget'
DEFAULT_CHARS = 28000
MAX_CHARS = 100000


def ceilings(request):
    value = request.get(FIELD)
    if value is None:
        return (DEFAULT_CHARS,)
    if not isinstance(value, dict) or set(value) != {'preferred_chars', 'maximum_chars'}:
        raise ValueError('Supply both frozen context ceilings')
    preferred, maximum = value['preferred_chars'], value['maximum_chars']
    if any(type(v) is not int for v in (preferred, maximum)) or not 1000 <= preferred <= maximum <= MAX_CHARS:
        raise ValueError('Invalid context delivery ceilings')
    return (preferred,) if preferred == maximum else (preferred, maximum)
