"""Export toolbox contracts for the host's native Capability boundary.

This module declares compatibility only. It never reserves, fetches, registers
itself or projects native evidence coordinates. Those remain host-owned.
"""
from copy import deepcopy
from .core import TOOL_DEFINITIONS
from .profiles import PROFILES

POLICY='native_channels_v1'
NETWORK_NAMES=frozenset({'intelligence_fetch','intelligence_read','intelligence_profile','intelligence_bill_text'})
PENDING_NETWORK_NAMES=frozenset({'intelligence_discover','intelligence_acquire_link'})
NAVIGATION_NAMES=frozenset({'intelligence_outline','intelligence_search','intelligence_part'})
HANDLER='ForecastAgent.channels.native:execute'
VERSION='toolbox_native_compat_v1'


def specs():
    """Twelve native-compatible tools; unsupported network extensions stay hidden."""
    result=[]
    for original in TOOL_DEFINITIONS:
        definition=deepcopy(original); name=definition['function']['name']
        if name in PENDING_NETWORK_NAMES: continue
        schema=definition['function']['parameters']; props=schema['properties']
        network=name in NETWORK_NAMES; material=network or name=='intelligence_part'
        if network:
            props['need_ids']={'type':'array','minItems':1,'maxItems':8,'items':{'type':'string'}}
            schema.setdefault('required',[]).append('need_ids')
            definition['function']['description']+=' Native shared HTTP allowance only. Host validates live-mode admission and need IDs before transport.'
        if name in NAVIGATION_NAMES:
            props['url']={'type':'string','description':'Exact saved native URL; choose this OR capture_id.'}
            schema['required']=[k for k in schema['required'] if k!='capture_id']
            definition['function']['description']+=' Choose exactly one url or capture_id. Host binds native references separately from normalized offsets.'
        if name=='intelligence_profile': props['profile_id']['enum']=list(PROFILES)
        effects=('network','material_write') if network else ('saved_read','material_write') if material else ('saved_read',)
        result.append({'id':name,'definition':definition,'kind':'channel' if network else 'read',
            'effects':effects,'budgets':('initial_http',) if network else (),
            'output_kind':'source_material' if material else 'navigation',
            'handler':HANDLER,'version':VERSION,'policy':POLICY})
    return result


def capabilities(factory=None):
    """Use the host Capability type without importing its runtime on standalone use."""
    if factory is None:
        from ForecastAgent.tools.capabilities import Capability
        factory=Capability
    return [factory(**spec) for spec in specs()]


def compatibility_manifest():
    return {'version':VERSION,'policy':POLICY,'registration_grants_budget':False,
        'native_tools':[dict({k:v for k,v in s.items() if k!='definition'},input_schema=s['definition']['function']['parameters']) for s in specs()],
        'pending_tools':[{'id':name,'effects':['network','material_write'],'budget_categories':['initial_http'],
            'exposed':False,'reason':'Native preflight, discovered-source admission, request reservation, projection and interruption recovery are not implemented for this extension.'} for name in sorted(PENDING_NETWORK_NAMES)],
        'host_responsibilities':['native cumulative request reservation before every transport','task-owned credentials and current-mode admission','immutable frozen code/schema identity; no implicit task migration','native quote handles and parsed-view provenance','whole-directory snapshot restore including channel-tools journal/raw files'],
        'production_ready':False}
