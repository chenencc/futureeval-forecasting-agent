"""Model-neutral capability metadata and a compatible native dispatch boundary.

Provider reservations remain in native handlers, never in the registry. A single
action may perform several physical requests; each must reserve independently.
"""
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Capability:
    id: str
    definition: dict
    kind: str = 'local_control'
    effects: tuple = ()
    budgets: tuple = ()
    output_kind: str = 'control_record'
    handler: str = 'native'
    version: str = 'native_v1'
    policy: str | None = None

    def manifest(self, include_schema=False):
        value = {'id': self.id, 'version': self.version, 'kind': self.kind,
                'effects': list(self.effects), 'budget_categories': list(self.budgets),
                'output_kind': self.output_kind,
                'handler': self.handler, 'policy': self.policy,
                'limits': 'Native cumulative task limits; no allowance granted by registration.'}
        if include_schema:
            value['input_schema'] = deepcopy(self.definition['function']['parameters'])
        return value


# This is the sole action-effect declaration. Legacy handlers keep their own
# request reservations and validation while migration proceeds through adapters.
NETWORK_METADATA = {
    'search_tavily': ('discovery', ('tavily_basic',)),
    'search_exa': ('discovery', ('exa_search',)),
    'extract_failed_pages': ('rescue', ('basic_extract',)),
    **{name: ('fetch', ('initial_http',)) for name in (
        'fetch_page', 'fetch_pages', 'read_sources', 'collect_official',
        'collect_dataset', 'collect_archive', 'collect_polymarket', 'follow_source_resource')},
    'render_source': ('render', ('initial_http', 'browser')),
    'refresh_sources': ('refresh', ('initial_http_or_update_http',)),
    'read_sources': ('fetch', ('initial_http', 'browser', 'basic_extract')),
}


@lru_cache(maxsize=1)
def registry():
    from ForecastAgent.tools.registry import TOOLS, COLLECTION_TOOLS
    from ForecastAgent.runtime.source_reading import TOOLS as source_tools
    from ForecastAgent.runtime.intelligent_acquisition import TOOLS as selection_tools
    from ForecastAgent.research_loop.runtime import TOOLS as research_tools
    from ForecastAgent.channels.native import capabilities as channel_capabilities
    definitions = {t['function']['name']: t for t in TOOLS + COLLECTION_TOOLS + source_tools + selection_tools + research_tools}
    result = {}
    for name, definition in definitions.items():
        kind, budgets = NETWORK_METADATA.get(name, ('local_control', ()))
        effects = ('network', 'material_write') if name in NETWORK_METADATA else ()
        if name in {'list_documents', 'read_document', 'search_saved_text', 'find_passages',
                    'read_dataset_rows', 'inspect_source_structure', 'read_market_snapshot'}:
            kind, effects = 'read', ('saved_read',)
        if name in {'inspect_research_state', 'update_research_state'}:
            kind, effects = 'research', ('saved_read', 'map_write')
        result[name] = Capability(name, deepcopy(definition), kind, effects, budgets,
                                  'source_material' if effects and 'network' in effects else 'control_record')
    for capability in channel_capabilities():
        if capability.id in result:
            raise ValueError('Duplicate capability ID: ' + capability.id)
        result[capability.id] = capability
    return result


def get(name):
    try:
        return registry()[name]
    except KeyError:
        raise ValueError('Unregistered capability: ' + name) from None


def register(capability):
    """Repository code registers an extension once; model input cannot do so."""
    if capability.id in registry():
        raise ValueError('Duplicate capability ID: ' + capability.id)
    if capability.id != capability.definition['function']['name']:
        raise ValueError('Capability ID and schema function name differ')
    registry()[capability.id] = deepcopy(capability)


def network_tools():
    return {name for name, c in registry().items() if 'network' in c.effects}


def produces_material(name):
    return 'material_write' in get(name).effects


def dispatch(task, name, args, key, native):
    """One dispatch boundary; addon calls cannot bypass task policy checks."""
    capability = get(name)
    if capability.handler != 'native':
        if capability.policy is not None and task.bundle['request'].get('capability_policy') != capability.policy:
            raise ValueError('Native channel capabilities are not enabled for this task')
        from importlib import import_module
        from ForecastAgent.runtime.contracts import check_schema
        check_schema(args, capability.definition['function']['parameters'])
        module, function = capability.handler.split(':', 1)
        if not module.startswith('ForecastAgent.'):
            raise ValueError('Extension handler must be registered repository code')
        return getattr(import_module(module), function)(task, name, args)
    return native(name, args, key)


def configure(task, tools):
    """Validate offered names and expose optional tools from this same registry."""
    for tool in tools:
        get(tool['function']['name'])
    from ForecastAgent.channels.native import enabled
    if enabled(task):
        tools = tools + [deepcopy(c.definition) for c in registry().values() if c.handler != 'native' and
                         (c.policy is None or c.policy == task.bundle['request'].get('capability_policy'))]
    names = [t['function']['name'] for t in tools]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate agent tool definitions')
    return tools


def catalog(task=None):
    from ForecastAgent.channels.native import enabled
    active = task is not None and enabled(task)
    return {'version': 'capabilities_v1', 'capabilities': [c.manifest() for c in registry().values()
            if c.policy is None or active], 'registration_grants_budget': False}
