"""Resumable P2 acquisition pilot over immutable saved inputs; no scoring."""
import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError

from ForecastAgent.analysis.pilot import Journal, digest, load, save
from ForecastAgent.market_pulse import mechanism
from ForecastAgent.market_pulse.quality import page_diagnostics
from ForecastAgent.market_pulse.financial import issuer_profile, page_scope
from ForecastAgent.market_pulse.research_identity import profile as research_profile
from ForecastAgent.providers import ultra
from ForecastAgent.readers import saved
from ForecastAgent.runtime.task_lock import task_lock

DEFAULT_LIMITS = {'model_http': 2, 'local_reads': 4, 'free_captures': 2}


def input_files(package, variables, state):
    paths = {'package': Path(package), 'variables': Path(variables)}
    if state: paths['state'] = Path(state)
    return {key: {'path': str(p.resolve()), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for key, p in paths.items()}


def unchanged(identity):
    return all(hashlib.sha256(Path(row['path']).read_bytes()).hexdigest() == row['sha256'] for row in identity.values())


def available(plan, journal, limits, network):
    executed = {a['action_id'] for a in journal['actions']}
    passages = {a['passage_key'] for a in journal['actions'] if a.get('passage_key')}
    reads = sum(a['tool'] == 'read_saved' for a in journal['actions'])
    fetches = sum(a['tool'] == 'fetch_public' for a in journal['actions'])
    used_captures = {a['url'] for a in journal['actions'] if a['tool'] == 'fetch_public'}
    return [a for a in plan['actions'] if a['action_id'] not in executed and a.get('passage_key') not in passages and
        ((a['tool'] == 'read_saved' and reads < limits['local_reads']) or
         (a['tool'] == 'fetch_public' and network and fetches < limits['free_captures'] and a['args']['url'] not in used_captures))]


def current_materials(bundle, journal, root):
    """Derive a working view without replacing any original capture."""
    result = copy.deepcopy(bundle)
    packets = []
    for entry in journal['actions']:
        folder = root/'materials'/entry['action_id']
        if entry['status'] == 'saved_read':
            packet = load(folder/'reading-packet.json')
            if entry.get('packet_json_sha256') and digest(packet) != entry['packet_json_sha256']:
                raise ValueError('Supplement reading packet changed')
            _, text, _ = saved.select(result['pages'], packet['url'], packet['location'].get('document_index'))
            if hashlib.sha256(text.encode()).hexdigest() != packet['original_text_sha256'] or packet['content'] != text[packet['start_char']:packet['end_char']]:
                raise ValueError('Supplement reading coordinates differ from original source')
            visible = copy.deepcopy(packet)
            visible['model_omitted_chars'] = max(0, len(packet['content']) - 9000)
            visible['content'] = packet['content'][:9000]
            packets.append(visible)
        elif entry['status'] == 'captured_candidate':
            page = load(folder/'capture.json')
            if digest(page) != entry['capture_json_sha256']:
                raise ValueError('Supplement capture changed')
            result['pages'].setdefault(entry['url'], page)
    return result, packets


def continuations(bundle, packets):
    """Resume the exact selected document at the first undisplayed character."""
    actions = []
    for packet in packets:
        index = packet['location'].get('document_index')
        _, text, _ = saved.select(bundle['pages'], packet['url'], index)
        start = packet['start_char'] + len(packet['content'])
        if start >= len(text):
            continue
        action = {'tool': 'read_saved', 'driver': packet['driver'], 'inventory_role': packet['inventory_role'],
            'args': {'url': packet['url'], 'document_index': index, 'start_char': start, 'max_chars': 9000},
            'content_sha256': digest(text), 'preview': text[start:start+350],
            'source_truncated': packet['source_truncated'], 'continuation': True,
            'routing_hint': {'source_role': 'unfinished_original_read', 'semantic_interpretation_verified': False},
            'passage_key': digest((packet['driver'], packet['url'], digest(text), start))}
        action['action_id'] = digest(action)[:20]
        if not any(a['action_id'] == action['action_id'] for a in actions): actions.append(action)
    return actions


def balanced(actions, limit=32):
    groups = {}
    for action in actions: groups.setdefault((action['driver'], action.get('tool')), []).append(action)
    result = []
    while groups and len(result) < limit:
        for driver in list(groups):
            result.append(groups[driver].pop(0))
            if not groups[driver]: del groups[driver]
            if len(result) == limit: break
    return result


def execute(action, bundle, folder, *, fetcher=None):
    """An attempted action is always terminal. The runner reserves it first."""
    fetcher = fetcher or ultra.fetch_public_page
    folder.mkdir(parents=True, exist_ok=True)
    if action['tool'] == 'read_saved':
        args = action['args']
        _, text, _ = saved.select(bundle['pages'], args['url'], args.get('document_index'))
        if digest(text) != action['content_sha256']:
            raise ValueError('Selected saved source changed')
        result = saved.read_document(bundle['pages'], args)
        result.update(driver=action['driver'], inventory_role=action['inventory_role'],
            original_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            text_start=result['start_char'], text_end=result['end_char'],
            semantic_interpretation_verified=False, enters_analysis_automatically=False)
        save(folder/'reading-packet.json', result)
        return {'status': 'saved_read', 'packet': str(folder/'reading-packet.json'),
            'packet_json_sha256': digest(result),
            'readable_chars': len(result['content']), 'truncated': result['next_start'] is not None or result['source_truncated']}
    if action['tool'] != 'fetch_public':
        raise ValueError('Unsupported acquisition tool')
    url = action['args']['url']
    try:
        page = fetcher(url, preserve_raw_on_failure=True)
    except HTTPError as exc:
        raw = exc.read(32001)
        (folder/'http-error.bin').write_bytes(raw)
        return {'status': 'http_error', 'http_status': exc.code,
            'raw_error_sha256': hashlib.sha256(raw).hexdigest(), 'raw_error_truncated': len(raw) > 32000}
    save(folder/'capture.json', page)
    quality = page_diagnostics(page)
    scope = page_scope(url, page, research_profile(bundle['request']))
    return {'status': 'captured_candidate' if quality['usable_text'] and scope['eligible_for_target'] else 'unusable_or_scope_gap',
        'capture': str(folder/'capture.json'), 'capture_json_sha256': digest(page),
        'quality': quality, 'scope': scope, 'readable_chars': len(page.get('content', '')),
        'semantic_interpretation_verified': False}


def run(package, variables, output, *, state=None, network=False, limits=None, ask=None, fetcher=None):
    limits = copy.deepcopy(limits or DEFAULT_LIMITS)
    if set(limits) != set(DEFAULT_LIMITS) or any(type(v) is not int or not 0 <= v <= 8 for v in limits.values()):
        raise ValueError('Explicit bounded supplement limits required')
    root = Path(output); root.mkdir(parents=True, exist_ok=True)
    bundle, values = load(package), load(variables)
    library = load(state)['original_evidence_library'] if state else None
    plan = mechanism.plan(bundle, values, library=library)
    model = ultra.configured_model()
    if not model.endswith(':free'):
        raise ValueError('P2 pilot model must be an explicitly configured free model')
    identity = {'schema': mechanism.VERSION, 'inputs': input_files(package, variables, state),
        'plan_sha256': digest(plan), 'network_enabled': network, 'limits': limits, 'model': model,
        'implementation_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (Path(__file__), Path(mechanism.__file__))}, 'old_budgets_reset': False}
    with task_lock(root):
        if (root/'identity.json').exists() and load(root/'identity.json') != identity:
            raise ValueError('Frozen P2 identity or budgets changed; resume the original stage')
        save(root/'identity.json', identity); save(root/'plan.json', plan)
        if (root/'summary.json').exists() and load(root/'summary.json')['status'] == 'closed_with_gaps':
            if not unchanged(identity['inputs']): raise ValueError('Parent input changed')
            return load(root/'summary.json')
        journal_path = root/'journal.json'
        journal = load(journal_path) if journal_path.exists() else {'actions': [], 'choices': [], 'gaps': [], 'status': 'running'}
        # Ambiguous reservations consume allowance and are never redispatched.
        for action in journal['actions']:
            if action['status'] == 'reserved':
                action.update(status='interrupted_unknown', retry_allowed=False)
        save(journal_path, journal)
        observer = Journal(root/'model-http', limits['model_http'])
        while True:
            working, read_packets = current_materials(bundle, journal, root)
            live_plan = mechanism.plan(working, values, library=library)
            live_plan['actions'] = continuations(working, read_packets) + live_plan['actions']
            candidates = available(live_plan, journal, limits, network)
            if not candidates:
                journal['status'] = 'closed_with_gaps'; break
            if len(list((root/'model-http').glob('*.json'))) >= limits['model_http']:
                journal['status'] = 'paused_model_budget'; break
            # Bounded catalog slices expose explicit omitted counts. Bodies are
            # read only after a source choice, then exact packets are archived.
            visible = balanced(candidates)
            prompt = {'contract': plan['contract'], 'inventory_slots': [
                {k: v for k, v in slot.items() if k in ('role', 'status', 'period', 'fact_ids')}
                for slot in plan['inventory_before']['slots']],
                'available_actions': visible, 'catalog_omitted_actions': len(candidates)-len(visible),
                'previous_results': journal['actions'], 'saved_reading_packets': read_packets,
                'search_handoffs': live_plan['search_handoffs'],
                'instruction': 'Choose an available ID or action_id=stop and driver=none.'}
            messages = [{'role': 'system', 'content': mechanism.SYSTEM}, {'role': 'user', 'content': json.dumps(prompt)}]
            try:
                message = (ask or ultra.ask_ultra)(messages, os.environ.get('OPENROUTER_API_KEY', ''),
                    tools=[mechanism.TOOL], forced_tool='choose_research_action', observer=observer,
                    max_output_tokens=2200, reasoning={'max_tokens': 500}, require_tool=True,
                    deadline=time.monotonic()+180)
                choice = mechanism.choose(message, visible, {d['id'] for d in plan['contract']['drivers']})
            except Exception as exc:
                journal.update(status='waiting_provider_or_format', error_type=type(exc).__name__)
                # Transport receipts carry the redacted diagnostic; never print
                # provider exceptions that could contain credential material.
                save(journal_path, journal); break
            journal['choices'].append(choice)
            journal['gaps'] = choice['remaining_gaps']
            if choice['action_id'] == 'stop':
                journal['status'] = 'closed_with_gaps'; save(journal_path, journal); break
            selected = next(a for a in visible if a['action_id'] == choice['action_id'])
            receipt = {'action_id': selected['action_id'], 'tool': selected['tool'], 'driver': selected['driver'],
                'url': selected['args']['url'], 'status': 'reserved', 'started_at_utc': ultra.utc_now()}
            if selected.get('passage_key'): receipt['passage_key'] = selected['passage_key']
            journal['actions'].append(receipt); save(journal_path, journal)
            try:
                result = execute(selected, working, root/'materials'/selected['action_id'], fetcher=fetcher)
                receipt.update(result)
            except Exception as exc:
                receipt.update(status='failed', error_type=type(exc).__name__, retry_allowed=False)
            receipt['finished_at_utc'] = ultra.utc_now(); save(journal_path, journal)
        if not unchanged(identity['inputs']): raise ValueError('Immutable parent research inputs changed')
        summary = {'schema': mechanism.VERSION, 'question_id': plan['inventory_before']['question_id'],
            'issuer': plan['contract']['issuer'], 'status': journal['status'], 'actions': journal['actions'],
            'choices': journal['choices'], 'remaining_gaps': journal['gaps'],
            'model_http_attempts': len(list((root/'model-http').glob('*.json'))),
            'free_capture_reservations': sum(a['tool']=='fetch_public' for a in journal['actions']),
            'search_handoffs': plan['search_handoffs'], 'tavily_calls': 0, 'exa_calls': 0,
            'parent_inputs_unchanged': True, 'old_budgets_reset': False, 'submitted': False,
            'supplement_captured_at_utc': ultra.utc_now(),
            'new_material_enters_analysis_automatically': False,
            'coverage_improvement_verified': False, 'interpretation_verified': False}
        save(root/'summary.json', summary)
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True); parser.add_argument('--variables', required=True)
    parser.add_argument('--state'); parser.add_argument('--output', required=True)
    parser.add_argument('--network', action='store_true')
    parser.add_argument('--model-http', type=int, default=2)
    parser.add_argument('--free-captures', type=int, default=2)
    parser.add_argument('--local-reads', type=int, default=4)
    args = parser.parse_args()
    result = run(args.package, args.variables, args.output, state=args.state, network=args.network,
        limits={'model_http': args.model_http, 'local_reads': args.local_reads, 'free_captures': args.free_captures})
    print(json.dumps({k: result[k] for k in ('question_id','issuer','status','model_http_attempts',
        'free_capture_reservations','tavily_calls','exa_calls','parent_inputs_unchanged')}))


if __name__ == '__main__': main()
