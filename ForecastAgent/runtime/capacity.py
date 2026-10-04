"""Program-owned frozen acquisition capacities, isolated from provider quotas."""
DEFAULT = {'tavily_basic': 3, 'exa_search': 1, 'initial_http': 8,
           'extract_batches': 1, 'model_decisions': 12, 'model_failures': 4,
           'model_http_dispatch': 16, 'model_http_lifetime': 72, 'dispatch_seconds': 900}
SOLID = {**DEFAULT, 'tavily_basic': 6, 'exa_search': 2, 'initial_http': 32,
         'extract_batches': 2, 'model_decisions': 24, 'model_failures': 6,
         'model_http_dispatch': 30, 'model_http_lifetime': 90, 'dispatch_seconds': 1500}
SOLID_V2 = {**SOLID, 'raw_no_progress_limit':2, 'material_no_progress_limit':4}


def freeze(bundle, request, existing):
    profile = request.get('budget_profile', 'default')
    if profile not in {'default', 'solid_v1', 'solid_v2'}:
        raise ValueError('Unknown program-owned budget profile')
    if profile in {'solid_v1','solid_v2'} and (not request.get('experiment_id') or
                                 request.get('pipeline') != 'collection'):
        raise ValueError('Expanded budgets require an explicit collection experiment')
    expected = dict(SOLID_V2 if profile=='solid_v2' else SOLID if profile == 'solid_v1' else DEFAULT)
    if profile == 'default':
        expected['tavily_basic'] = 5 if request.get('acquisition_profile') == 'collection_v2' else 3
    if 'capacity' in bundle and bundle['capacity'] != expected:
        raise ValueError('Frozen capacity changed; create a separately authorized experiment')
    if existing and profile in {'solid_v1','solid_v2'} and 'capacity' not in bundle:
        raise ValueError('Cannot enlarge an existing unfrozen ledger')
    bundle.setdefault('capacity', expected)
    return bundle['capacity']


def limits(task):
    return getattr(task, 'bundle', {}).get('capacity', DEFAULT)


def allocation(bundle):
    """Reserve supplement capacity without enlarging any frozen total."""
    request=bundle.get('request',{})
    policy=request.get('collection_stage_allocation')
    capacity=bundle.get('capacity',DEFAULT)
    if policy is None:
        return dict(capacity)
    if (policy!='material-reserve-v1' or request.get('collection_workflow')!='material-gap-v1' or
            not request.get('experiment_id') or request.get('budget_profile')!='solid_v2'):
        raise ValueError('Unknown or unauthorized collection stage allocation')
    return {**capacity,'tavily_basic':capacity['tavily_basic']-2,'exa_search':capacity['exa_search']-1,
            'model_decisions':capacity['model_decisions']-4,'model_http_dispatch':capacity['model_http_dispatch']-4,
            'dispatch_seconds':capacity['dispatch_seconds']-300}
