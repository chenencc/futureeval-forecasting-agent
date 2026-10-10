"""Explicit graph patches preserve old valid nodes instead of implicit deletion."""
import copy
from ForecastAgent.research_loop.schema import NODE
from ForecastAgent.runtime.contracts import ContractError

FIELD = 'research_update_policy'
POLICY = 'explicit_delta_v1'


def enabled(bundle):
    return bundle.get('request', {}).get(FIELD) == POLICY


def node_input(node):
    return {k:copy.deepcopy(v) for k,v in node.items() if k in NODE['properties']}


def schema(base):
    result=copy.deepcopy(base)
    result['properties']['update_mode']={'type':'string','enum':['merge','replace'],
        'description':'Prefer merge: nodes, relations and material requests are additions or replacements; omitted old entries survive. Only retired_node_ids deletes nodes. replace requires every omitted old ID to be retired.'}
    result['required'].append('update_mode')
    return result


def expand(bundle, proposal):
    raw=copy.deepcopy(proposal); mode=raw.pop('update_mode')
    old=(bundle['research_loop'].get('current') or {})
    old_nodes={n['id']:node_input(n) for n in old.get('nodes',[])}
    retire=raw['retired_node_ids']; supplied={n.get('id') for n in raw['nodes'] if isinstance(n,dict)}
    invalid=set(retire)-set(old_nodes)
    if invalid or len(set(retire))!=len(retire) or set(retire)&supplied:
        raise ContractError('invalid_retirement','retired_node_ids',
            f'Retire existing IDs only, once, without also supplying them. Existing: {sorted(old_nodes)}; invalid: {sorted(invalid)}.')
    if mode=='replace':
        missing=set(old_nodes)-supplied-set(retire)
        if missing:
            raise ContractError('missing_retirement','retired_node_ids',
                f'Every removed node must be explicitly retired. Missing IDs: {sorted(missing)}. Use merge to preserve omitted nodes.')
    else:
        raw['nodes']=[n for ident,n in old_nodes.items() if ident not in set(retire)|supplied]+raw['nodes']
        retained={n['id'] for n in raw['nodes'] if isinstance(n,dict) and 'id' in n}
        relations=[{k:v for k,v in r.items() if k!='verified'} for r in old.get('relations',[])
            if {r['from_id'],r['to_id']}<=retained]
        incoming={(r.get('from_id'),r.get('to_id'),r.get('kind')) for r in raw['relations'] if isinstance(r,dict)}
        raw['relations']=[r for r in relations if (r['from_id'],r['to_id'],r['kind']) not in incoming]+raw['relations']
        incoming_needs={(n.get('target'),n.get('role')) for n in raw['material_requests'] if isinstance(n,dict)}
        raw['material_requests']=[copy.deepcopy(n) for n in old.get('material_requests',[])
            if set(n['node_ids'])<=retained and (n['target'],n['role']) not in incoming_needs]+raw['material_requests']
    normalization=None
    if old and old.get('material_sha256')==raw['material_sha256'] and raw['revision_kind']=='material_update':
        # Reading another saved paragraph changes a map interpretation, not the
        # physical acquisition identity. Only bookkeeping is normalized here.
        raw['revision_kind']='interpretation_correction'
        normalization={'requested':'material_update','effective':'interpretation_correction',
            'reason':'Same saved material identity; local reading/interpretation update only.'}
    return raw, {'policy':POLICY,'update_mode':mode,'revision_kind_normalization':normalization,
        'preserved_omitted_node_ids':sorted(set(old_nodes)-supplied-set(retire)) if mode=='merge' else [],
        'explicitly_retired_node_ids':retire}


def content_changed(current, nodes, relations, needs):
    """Material hashes and rejection-generated prose never constitute map progress."""
    if not current:return bool(nodes)
    prior_nodes={n['id']:node_input(n) for n in current.get('nodes',[])}
    new_nodes={n['id']:node_input(n) for n in nodes}
    prior_relations=[{k:v for k,v in r.items() if k!='verified'} for r in current.get('relations',[])]
    return (prior_nodes!=new_nodes or prior_relations!=relations or current.get('material_requests',[])!=needs)
