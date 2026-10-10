"""Channel capability declarations and frozen task identity; no transport."""
import copy
import hashlib
import json
from pathlib import Path
from ForecastAgent.tools.intelligence_box import core
from ForecastAgent.tools.original_navigation import MAX_VIEW_CHARS, MAX_VIEWS

POLICY = 'native_channels_v1'
FIELD = 'capability_policy'
DONOR = '15d9f45'
from ForecastAgent.tools.intelligence_box.compatibility import NETWORK_NAMES as COMPAT_NETWORK_NAMES
NETWORK_NAMES = set(COMPAT_NETWORK_NAMES) | {'intelligence_discover', 'intelligence_acquire_link'}

def enabled(task):
    return task.bundle['request'].get(FIELD) == POLICY

def capabilities():
    from ForecastAgent.tools.intelligence_box.compatibility import capabilities as compatible
    from ForecastAgent.tools.capabilities import Capability
    result = compatible()
    # The donor keeps these pending; this host supplies the native admission,
    # projection and interruption-recovery adapter before exposing them.
    for original in core.TOOL_DEFINITIONS:
        name = original['function']['name']
        if name not in {'intelligence_discover', 'intelligence_acquire_link'}:
            continue
        definition = copy.deepcopy(original)
        schema = definition['function']['parameters']
        schema['properties']['need_ids'] = {
            'type':'array', 'minItems':1, 'maxItems':8, 'items':{'type':'string'}}
        schema['required'] = list(dict.fromkeys(schema['required'] + ['need_ids']))
        definition['function']['description'] += ' Native shared HTTP only. Use catalog routes or previously discovered exact URLs; download requires a task-owned parent index.'
        result.append(Capability(name, definition, kind='channel',
            effects=('network', 'material_write'), budgets=('initial_http',),
            output_kind='source_material', handler='ForecastAgent.channels.native:execute',
            version='native_discovery_v1', policy=POLICY))
    return result


def code_identity():
    root = Path(__file__).parents[1]
    files = [*root.joinpath('channels').glob('*.py'), *root.joinpath('tools/intelligence_box').glob('*.py'),
             root / 'tools/capabilities.py', root / 'tools/original_navigation.py', root / 'tools/channels.py',
             root / 'runtime/retrieval.py', root / 'runtime/budget.py', root / 'runtime/contracts.py',
             root / 'runtime/tool_selection.py',
             root / 'research_loop/fusion.py', root / 'research_loop/material_events.py',
             root / 'research_loop/delivery.py', root / 'runtime/context.py',
             root / 'research_loop/state.py', root / 'research_loop/gap_feedback.py',
             root / 'research_loop/post_supplement.py', root / 'research_loop/dispatch.py',
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
