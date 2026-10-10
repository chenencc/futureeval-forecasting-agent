"""Channel capability declarations and frozen task identity; no transport."""
import copy
import hashlib
import json
from pathlib import Path
from ForecastAgent.tools.intelligence_box import core
from ForecastAgent.tools.original_navigation import MAX_VIEW_CHARS, MAX_VIEWS

POLICY = 'native_channels_v1'
FIELD = 'capability_policy'
DONOR = 'dde8b02'
NETWORK_NAMES = {'intelligence_fetch', 'intelligence_read', 'intelligence_profile', 'intelligence_bill_text'}

def enabled(task):
    return task.bundle['request'].get(FIELD) == POLICY

def capabilities():
    from ForecastAgent.tools.capabilities import Capability
    result = []
    for definition in core.TOOL_DEFINITIONS:
        name = definition['function']['name']
        definition = copy.deepcopy(definition)
        network = name in NETWORK_NAMES
        material = network or name == 'intelligence_part'
        if network:
            definition['function']['parameters']['properties']['need_ids'] = {
                'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': {'type': 'string'}}
            definition['function']['parameters'].setdefault('required', []).append('need_ids')
            definition['function']['description'] += ' Shares native HTTP slots; no separate allowance. Current live captures only.'
        if name in {'intelligence_outline', 'intelligence_search', 'intelligence_part'}:
            props = definition['function']['parameters']['properties']
            props['url'] = {'type': 'string', 'description': 'Exact saved native URL; use this OR capture_id.'}
            definition['function']['parameters']['required'] = [k for k in definition['function']['parameters']['required'] if k != 'capture_id']
            definition['function']['description'] += ' Use exactly one saved url or capture_id. Part returns native reference handles separately from normalized unit offsets.'
        if name == 'intelligence_profile':
            from ForecastAgent.tools.intelligence_box.profiles import PROFILES
            definition['function']['parameters']['properties']['profile_id']['enum'] = list(PROFILES)
        effects = ('network', 'material_write') if network else ('saved_read', 'material_write') if material else ('saved_read',)
        result.append(Capability(name, definition, 'channel' if network else 'read', effects,
            ('initial_http',) if network else (), 'source_material' if material else 'navigation',
            'ForecastAgent.channels.native:execute', POLICY, POLICY))
    return result

def code_identity():
    root = Path(__file__).parents[1]
    files = [*root.joinpath('channels').glob('*.py'), *root.joinpath('tools/intelligence_box').glob('*.py'),
             root / 'tools/capabilities.py', root / 'tools/original_navigation.py', root / 'tools/channels.py',
             root / 'runtime/retrieval.py', root / 'runtime/budget.py', root / 'runtime/contracts.py',
             root / 'research_loop/fusion.py', root / 'research_loop/material_events.py',
              root / 'intelligence/development_collection.py',
              root / 'intelligence/pipeline.py',
             *root.joinpath('readers').glob('*.py')]
    from ForecastAgent.tools.capabilities import registry
    for capability in registry().values():
        if capability.handler != 'native':
            module = capability.handler.split(':', 1)[0]
            path = root.parent / (module.replace('.', '/') + '.py')
            if path.is_file() and path.resolve().is_relative_to(root.resolve()):
                files.append(path)
    return {str(p.relative_to(root)).replace('\\', '/'): hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            for p in sorted(files)}

def initialize(task):
    policy = task.bundle['request'].get(FIELD, 'disabled')
    if policy not in {'disabled', POLICY}:
        raise ValueError('Unknown capability policy')
    if policy == 'disabled':
        return
    if task.bundle['pipeline'] != 'collection':
        raise ValueError('Native channel tools require collection mode')
    identity = {'policy': POLICY, 'donor': DONOR, 'code': code_identity(),
                'document_byte_cap': 8_000_000, 'max_view_chars': MAX_VIEW_CHARS,
                'max_views': MAX_VIEWS, 'budget_authority': 'native_fetch_attempts'}
    from ForecastAgent.tools.capabilities import registry
    identity['registry_sha256'] = hashlib.sha256(json.dumps(
        {name: c.manifest(include_schema=True) for name,c in sorted(registry().items())},
        sort_keys=True).encode()).hexdigest()
    ledger = task.bundle.setdefault('channel_tools', {'identity': identity, 'captures': {}, 'operations': [], 'views': {}})
    if ledger['identity'] != identity:
        raise ValueError('Frozen channel tool identity changed; preserve the task before migration')
    channels = task.bundle['channel_catalog']['channels']
    if not any(c['id'] == 'native_official_channels' for c in channels):
        channels.append({'id': 'native_official_channels', 'kind': 'source_adapter',
            'tools': sorted(NETWORK_NAMES), 'formats': ['native_records', 'official_document'],
            'credentials': ['SEC_USER_AGENT for SEC', 'CONGRESS_API_KEY for Congress'],
            'cost': 'Native shared HTTP attempts; no additional search or model calls',
            'limits': 'Frozen native request caps; explicit pagination; no automatic retries or redirects',
            'temporal_support': 'Current captures, not verified historical vintages', 'availability': 'implemented_opt_in'})
        channels.append({'id': 'original_navigation', 'kind': 'local_reader',
            'tools': ['intelligence_outline', 'intelligence_search', 'intelligence_part'],
            'formats': ['sections', 'pdf_pages', 'table_rows', 'provisions'], 'credentials': [],
            'cost': 'Zero HTTP/search/model calls', 'limits': 'Bounded saved bytes and versioned parsed views',
            'temporal_support': 'Requires an eligible original capture', 'availability': 'implemented_opt_in'})
    from ForecastAgent.research_loop.material_events import observe
    observe(task, 'restored_or_external_stage')
