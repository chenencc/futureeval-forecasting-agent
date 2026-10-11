"""Graph ablation on the unchanged release Mercury routing and selected originals.

The original arm owns the reread schedule. The mapped arm receives precisely the
same source spans at each stage, not the original arm's answers. No submission,
provider failover or fresh acquisition is available through this module.
"""
import argparse
import copy
import hashlib
import importlib.util
import os
from contextlib import contextmanager, ExitStack
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis import mercury_evidence_chain as chain, mercury_nonbinary_trial as typed
from ForecastAgent.analysis.distributions import payload
from ForecastAgent.analysis.pilot import digest
from ForecastAgent.competition.mercury import distribution_spec
from ForecastAgent.acquisition.pipeline import reject_outcomes
from ForecastAgent.acquisition import handoff, handoff_replay
from ForecastAgent.releases import context_decisions as release
from ForecastAgent.research_loop import decision, live_trial, map_paired_trial, forecast_map, reference_map, target_logic, source_scope
from ForecastAgent.runtime.records import load, save
from ForecastAgent.runtime.task_lock import task_lock

CORE = ('releases/context_decisions.py', 'analysis/mercury_evidence_chain.py',
        'analysis/mercury_nonbinary_trial.py', 'analysis/distributions.py',
        'competition/mercury.py', 'acquisition/context_delivery.py',
        'acquisition/handoff.py', 'acquisition/handoff_replay.py',
        'analysis/referenced.py', 'analysis/inputs.py', 'readers/saved.py',
        'providers/decisions.py', 'analysis/pilot.py', 'tavily_research.py')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def verify_control(control):
    from ForecastAgent.releases.manifest import verify
    verify(control, expected_version='1.0.5')
    root = Path(__file__).resolve().parents[1]; control = Path(control)/'ForecastAgent'
    hashes = {name: sha(control/name) for name in CORE}
    # The reader is explicitly loaded from the immutable control, not substituted
    # by the development alias resolver. Other core source must match exactly.
    if any(sha(root/name) != value for name, value in hashes.items() if name != 'readers/saved.py'):
        raise ValueError('Release Mercury core differs from the pinned control')
    return hashes


@contextmanager
def pinned_reader(control):
    verify_control(control)
    path = Path(control)/'ForecastAgent/readers/saved.py'
    spec = importlib.util.spec_from_file_location('frozen_release_reader', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    with ExitStack() as stack:
        for consumer in (release.delivery, handoff, handoff_replay):
            stack.enter_context(patch.object(consumer, 'saved_select', module.select))
        stack.enter_context(patch.object(handoff, 'version_digest', module.version_digest))
        yield


def registry(question):
    spec = None if question['question_type'] == 'binary' else distribution_spec(question)
    return (chain.questions() if spec is None else typed.questions(spec)), spec


def mapped_state(bundle, original, heads):
    notes, audit = decision.scoring_map(bundle, original)
    mapped = copy.deepcopy(original)
    if notes.get('factual_grounding_present'):
        mapped['research_map'] = notes
    if chain.request_bytes(mapped, heads) > chain.SECOND_BYTES:
        mapped = copy.deepcopy(original)
        audit = {**audit, 'scoring_map_omitted': True, 'status': 'map_exceeds_shared_request_cap'}
    if any(mapped[k] != v for k, v in original.items()):
        raise ValueError('Map changed original question, instructions or evidence')
    return mapped, audit


def contextual_original(bundle, original, heads):
    enriched, audit = source_scope.attach(bundle, original)
    if chain.request_bytes(enriched, heads) > chain.SECOND_BYTES:
        return copy.deepcopy(original), {**audit, 'sideband_present': False, 'omitted': 'shared_request_cap'}
    return enriched, audit


def decode(response, question, spec):
    if spec is None:
        raw = {'probability_yes': response['answers']['event_yes']['noul']}
    else:
        forecast = typed.forecast(response, spec)
        raw = {'probability_yes_per_category': forecast['probabilities']} if question['question_type'] == 'multiple_choice' else {'continuous_cdf': forecast['raw_cdf']}
    return payload(question, raw)


def paired_release(bundle, mapped_bundle, folder, *, mapped_first=False, source_context=False):
    """Run the actual release function; only its provider seam is intercepted."""
    folder = Path(folder); rows = []; saved_call = chain.call
    heads, spec = registry(bundle['request'])

    def paired_call(original, stage_folder, supplied_heads=None):
        if supplied_heads != heads:
            raise ValueError('Release decision registry changed')
        stage = Path(stage_folder).name
        if stage not in ('first', 'second') or stage in {r['stage'] for r in rows}:
            raise ValueError('Unexpected or duplicate release stage')
        context_audit = None
        if source_context:
            original, context_audit = contextual_original(bundle, original, heads)
        enriched, audit = mapped_state(mapped_bundle, original, heads)
        independent = 'research_map' in enriched
        replies = {}; mapped_error = None
        for arm in (('mapped', 'original') if mapped_first else ('original', 'mapped')):
            if arm == 'mapped' and not independent:
                continue
            selected = original if arm == 'original' else enriched
            target = folder/arm/stage
            # Never turn a failed/reserved physical receipt into an implicit retry.
            if list((target/'http').glob('*.json')) and not (target/'response.json').exists():
                raise ValueError('Preserved unfinished stage requires inspection')
            try:
                replies[arm] = saved_call(selected, target, heads)
            except Exception as exc:
                if arm == 'original':
                    raise
                # An experimental map/provider failure must not alter the
                # native release arm's choice to read or reread originals.
                mapped_error = {'type': type(exc).__name__, 'message': str(exc)[:500]}
                save(target/'failure.json', mapped_error)
        if not independent:
            replies['mapped'] = replies['original']
        row = {'stage': stage, 'same_originals': True, 'original_state_sha256': digest(original),
               'originals': copy.deepcopy(original), 'registry_sha256': digest(heads),
               'map_audit': audit, 'independent_map_request': independent and mapped_error is None,
               'source_context_audit': context_audit,
               'mapped_error': mapped_error,
               'original_payload': decode(replies['original'], bundle['request'], spec),
               'mapped_payload': decode(replies['mapped'], bundle['request'], spec) if 'mapped' in replies else None,
               'mapped_diagnostic_reasons': (chain.route if spec is None else typed.route)(replies['mapped']) if 'mapped' in replies else None}
        rows.append(row); save(folder/'paired-stages.json', rows)
        return replies['original']

    with patch.object(chain, 'call', paired_call):
        result = release.run(bundle, folder/'release-routing')
    if result['payload'] != rows[-1]['original_payload']:
        raise ValueError('Release final payload differs from the actual original reply')
    return {'status': 'completed', 'original': result['payload'],
            'mapped': rows[-1]['mapped_payload'], 'stages': rows,
            'independent_final_pair': rows[-1]['independent_map_request'],
            'routing_owner': 'original_release_arm', 'second_call_required': len(rows) == 2,
            'mapped_own_routing_applied': False, 'submitted': False,
            'scope': ('Release Mercury selector, registry and original-owned reread schedule with equal saved DOM scope sidebands. Not the unaugmented production reading input or a full worker comparison.' if source_context else
                'Exact release Mercury selector, registry, diagnostics, conditional reread and payload. Mapped scoring uses the same stage schedule; no acquisition or reasoning-service fallback comparison.')}


def _run(parent, folder, control, *, execute=False, max_http=5, mapped_first=False, map_protocol=forecast_map.PROTOCOL, source_context=False):
    parent, folder = Path(parent), Path(folder)
    if not 0 <= max_http <= 5:
        raise ValueError('Reserve at most one map and four Mercury attempts')
    if map_protocol not in {forecast_map.PROTOCOL, reference_map.PROTOCOL}:
        raise ValueError('Unsupported release-pair map protocol')
    bundle = load(parent); reject_outcomes(bundle['request']); core = verify_control(control)
    heads, _ = registry(bundle['request']); first, selection = release.select_first(bundle, heads)
    if source_context:
        first, context_audit = contextual_original(bundle, first, heads)
    child, references = map_paired_trial.freeze_references(bundle, first)
    if map_protocol == reference_map.PROTOCOL:
        child['request'][reference_map.FIELD] = reference_map.POLICY
        child['request'][target_logic.FIELD] = target_logic.POLICY
        child['request']['forecast_target_registry_policy'] = 'question_and_research_targets_v1'
    identity = {'protocol': 'release-schedule-map-ablation-v1', 'parent_sha256': live_trial.sha(parent),
                'release_core_sha256_lf': core, 'model': live_trial.SUPER,
                'mercury_model': 'inception/mercury-decide:free', 'registry_sha256': digest(heads),
                'first_original_sha256': digest(first), 'max_http': max_http,
                'mapped_first': mapped_first,
                'map_coverage': 'Release first request only; later reread originals never fabricated into map',
                'searches': 0, 'fetches': 0, 'submitted': False, 'full_worker_comparison': False}
    if map_protocol != forecast_map.PROTOCOL:
        identity['map_protocol'] = map_protocol
    if source_context:
        identity['source_context'] = source_scope.VERSION
        identity['source_context_audit'] = context_audit
        identity['reading_scope'] = 'Saved DOM scope sideband delivered equally to both arms. Not the unaugmented production reading input.'
    folder.mkdir(parents=True, exist_ok=True)
    with task_lock(folder):
        if (folder/'closure.json').exists():
            raise ValueError('Closed experiment cannot be restarted')
        if (folder/'identity.json').exists() and load(folder/'identity.json') != identity:
            raise ValueError('Frozen experiment identity changed')
        save(folder/'identity.json', identity); save(folder/'common-first.json', first)
        save(folder/'reference-audit.json', references); save(folder/'selection.json', selection)
        if not execute:
            return {'status': 'prepared', 'new_http': 0, **identity}
        if max_http != 5:
            raise ValueError('Execution must reserve the complete five-attempt ceiling')
        if list(folder.rglob('http/*.json')):
            raise ValueError('Inspect existing physical receipts before attempting continuation')
        try:
            child = live_trial.map_stage(child, first, folder/'map', os.environ['OPENROUTER_API_KEY'],
                http_cap=1, map_protocol=map_protocol, frozen_scoring_coverage=True)
        except Exception as exc:
            save(folder/'map-unavailable.json', {'type': type(exc).__name__,
                'message': str(exc)[:500], 'original_release_still_available': True})
        save(folder/'map-package.json', child)
        result = paired_release(bundle, child, folder/'analysis', mapped_first=mapped_first, source_context=source_context)
        result['usage'] = live_trial.usage(list(folder.rglob('http/*.json')))
        if result['usage']['totals']['http_attempts'] > max_http:
            raise ValueError('Reserved physical request ceiling exceeded')
        save(folder/'result.json', result)
        save(folder/'closure.json', {'status': 'finished', 'restarts_allowed': False,
                                    'outcomes_pending': True})
        return result


def run(parent, folder, control, *, execute=False, max_http=5, mapped_first=False, map_protocol=forecast_map.PROTOCOL, source_context=False):
    with pinned_reader(control):
        return _run(parent, folder, control, execute=execute, max_http=max_http, mapped_first=mapped_first, map_protocol=map_protocol, source_context=source_context)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--mapped-first', action='store_true')
    parser.add_argument('--map-protocol', choices=[forecast_map.PROTOCOL, reference_map.PROTOCOL], default=forecast_map.PROTOCOL)
    parser.add_argument('--source-context', action='store_true')
    args = parser.parse_args()
    run(args.parent, args.root, args.control, execute=args.execute, mapped_first=args.mapped_first, map_protocol=args.map_protocol, source_context=args.source_context)


if __name__ == '__main__':
    main()
