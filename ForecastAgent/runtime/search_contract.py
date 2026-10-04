"""One search-role contract for tool guidance and execution validation."""
def extra_roles(bundle):
    from ForecastAgent.supplement.need_ledger import enabled
    return ('recent', 'official_gap', 'gap', 'crosscheck') if enabled(bundle) else ('recent', 'official_gap')


def description(bundle, limit):
    return (f'Basic search within the frozen {limit}-attempt task ceiling. '
            'Above three attempts, search_role must be one of: '+', '.join(extra_roles(bundle))+'. '
            'A missing material requires an existing need ID and specific query; no quota grant.')
