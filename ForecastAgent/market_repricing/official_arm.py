"""Bounded official-map production arm over the control's exact visible originals."""
import copy
import hashlib
from pathlib import Path

from . import SUPER
from .common import digest, read, write

ARMS = ('original', 'mapped', 'official_map')
VERSION = 'market-three-arm-reference-v1'


def implementation():
    root = Path(__file__).parent
    return {'version': VERSION, 'model': SUPER, 'maximum_map_http_requests': 2,
            'files_sha256_lf': {name: hashlib.sha256((root / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
                for name in ('official_arm.py', 'official_map.py', 'official_map_references.py',
                             '_official_map/UPSTREAM.json')}}


def build_state(common, heads, root):
    """A rejected or oversized sketch is a visible missing arm, not a raw-only duplicate."""
    from ForecastAgent.research_loop.decision import BYTE_CAP
    from ForecastAgent.analysis.mercury_evidence_chain import request_bytes
    from .official_map_references import construct_reference, project_reference
    root = Path(root)
    graph = construct_reference(common, root / 'map', root / 'map-provider', model=SUPER)
    if graph['status'] != 'hypothesis_only':
        return None, {'status': 'not_comparable', 'reason': 'official_map_' + graph['status'],
                      'map_delivered': False, 'graph_status': graph['status']}
    cap = min(5600, BYTE_CAP - request_bytes(common, heads) - 160)
    try:
        state = project_reference(common, graph, maximum_bytes=cap)
        if request_bytes(state, heads) > BYTE_CAP:
            raise ValueError('Official map exceeds unchanged-original context cap')
        if any(state[k] != value for k, value in common.items()):
            raise ValueError('Official map changed control originals or price context')
    except ValueError:
        return None, {'status': 'not_comparable', 'reason': 'official_map_projection_unavailable',
                      'map_delivered': False, 'graph_status': graph['status']}
    audit = {'status': 'ready', 'map_delivered': True, 'graph_sha256': graph['graph_sha256'],
             'delivered_map_sha256': digest(state['research_map']), 'same_originals': True,
             'same_price_heads': True, 'context_bytes': request_bytes(state, heads),
             'claims_verified': False, 'upstream_commit': graph['upstream_commit']}
    write(root / 'map-audit.json', audit)
    return copy.deepcopy(state), audit


def usage(root):
    """Local journal reservations; the worker ledger owns physical provider caps."""
    records = []
    for path in sorted(Path(root).rglob('http/*.json')):
        record = read(path)
        provider = 'mercury' if '/decisions' in record.get('endpoint', '') else 'super'
        response = record.get('response') or {}
        tokens = response.get('usage', {}).get('total_tokens')
        records.append({'file': path.relative_to(root).as_posix(), 'provider': provider,
                        'status': record.get('status'), 'reported_tokens': tokens})
    return {'local_request_reservations': len(records), 'worker_provider_ledger_is_authoritative': True, 'by_provider': {
        name: {'attempts': len(selected := [r for r in records if r['provider'] == name]),
               'known_tokens': sum(r['reported_tokens'] for r in selected if isinstance(r['reported_tokens'], (int, float))),
               'unknown_usage_attempts': sum(r['reported_tokens'] is None for r in selected)}
        for name in ('super', 'mercury')}, 'records': records, 'budget_reset': False}
