"""Opt-in P2 guidance for the existing collector and its original search ledger."""
from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path

from ForecastAgent.market_pulse import mechanism, research_identity
from ForecastAgent.market_pulse.collection import prepare as financial_prepare, acquisition_policy


def policy(request):
    files = (Path(__file__), Path(mechanism.__file__), Path(research_identity.__file__))
    return {'schema': mechanism.VERSION, 'contract': mechanism.contract(request),
        'implementation_sha256_lf': {p.name: hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest() for p in files},
        'search_lifetime_max': {'tavily_basic': 3, 'exa': 1},
        'search_budget_owner': 'original_RetrievalTask', 'budget_reset': False}


def prepare(request):
    normalized = copy.deepcopy(request)
    identity = research_identity.profile(normalized)
    if identity.get('issuer_original_ref'):
        normalized['issuer_label'] = identity['issuer_label']
        normalized['financial_issuer_original_ref'] = identity['issuer_original_ref']
    result = financial_prepare(normalized)
    frozen = policy(result)
    if result.get('financial_mechanism_policy', frozen) != frozen:
        raise ValueError('P2 acquisition policy changed; existing ledgers cannot migrate silently')
    result['financial_mechanism_policy'] = frozen
    return result


def checked(task):
    request = task.bundle['request']
    frozen = request.get('financial_mechanism_policy')
    if frozen is None:
        return None
    if frozen != policy(request):
        raise ValueError('Frozen P2 acquisition policy or issuer contract changed')
    return frozen


def collect(request, root):
    """Explicit experiment entry point; production keeps its existing collector."""
    from ForecastAgent.releases.v1_0_5 import collect as release_collect
    prepared = prepare(request)
    with collector_policy():
        return release_collect(prepared, root)


@contextmanager
def collector_policy():
    """Process-local integration. Every existing tool/budget gate still executes."""
    with acquisition_policy():
        from ForecastAgent.runtime import guidance, task_protocol, retrieval
        original_system = guidance.collection_system
        original_view = task_protocol.task_view
        original_execute = retrieval.RetrievalTask.execute

        def system(task, skills):
            value = original_system(task, skills)
            frozen = checked(task)
            if frozen is None: return value
            return value + '\nP2 financial operating-mechanism research:\n' + json.dumps(frozen['contract']) + """
Select useful mechanism evidence rather than forcing every driver to exist for
every issuer. Use saved original report tables and complete row/column context
first. Revenue drivers include volume, price/mix, segments and currency. EPS
drivers include margins, expenses, taxes, diluted shares and one-off charges.
Guidance drivers include prior guidance, original management language and demand.
Separate actual observations, management guidance and analyst consensus. Match
issuer, fiscal quarter, accounting basis and units; unknowns remain explicit.
Use search_tavily with finance topic only when saved materials and observed
official details do not address a useful gap. The SAME original task search
ledger owns all attempts: lifetime three basic Tavily searches and one Exa.
Never reset quotas or require a not-yet-published resolving release to finish.
Prefer observed primary detail URLs; do not synthesize issuer identifiers or URLs.
Export missing or unread materials explicitly; missing facts are not negatives.
"""

        def view(task):
            value = original_view(task)
            frozen = checked(task)
            if frozen is not None:
                value['financial_mechanisms'] = frozen['contract']
                value['financial_remaining_searches'] = {
                    'tavily_basic': max(0, 3-len(task.bundle.get('searches', []))),
                    'exa': max(0, 1-len(task.bundle.get('exa_searches', [])))}
            return value

        def execute(task, name, args, key):
            frozen = checked(task)
            if frozen is not None and name in {'search_tavily', 'search_exa'}:
                field, maximum = ('searches', 3) if name == 'search_tavily' else ('exa_searches', 1)
                if len(task.bundle.get(field, [])) >= maximum:
                    raise ValueError('P2 lifetime search allowance exhausted in original task ledger')
            return original_execute(task, name, args, key)

        guidance.collection_system = system
        task_protocol.task_view = view
        retrieval.RetrievalTask.execute = execute
        try:
            yield
        finally:
            guidance.collection_system = original_system
            task_protocol.task_view = original_view
            retrieval.RetrievalTask.execute = original_execute
