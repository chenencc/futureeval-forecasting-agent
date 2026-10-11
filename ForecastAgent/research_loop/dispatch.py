"""Opt-in bounded scheduling of saved-material review and gap acquisition.

This module routes work, not truth. It never creates receipts, probabilities,
source URLs, provider calls or renewed allowances.
"""
import copy
import re

FIELD = 'research_dispatch_policy'
POLICY = 'material_gap_dispatch_v1'
LIMIT_FIELD = 'research_dispatch_limits'
DEFAULTS = {'map_updates': 10, 'post_review_http': 6,
            'post_review_revisions': 3, 'initial_http': 16}
RANGES = {'map_updates': (3, 12), 'post_review_http': (2, 8),
          'post_review_revisions': (1, 4), 'initial_http': (8, 32)}


def enabled(bundle):
    return bundle.get('request', {}).get(FIELD) == POLICY


def limits(request):
    policy = request.get(FIELD, 'disabled')
    if policy not in {'disabled', POLICY}:
        raise ValueError('Unknown research dispatch policy')
    if policy == 'disabled':
        if LIMIT_FIELD in request:
            raise ValueError('Research dispatch limits require an explicit policy')
        return {'map_updates': 3, 'post_review_http': 2,
                'post_review_revisions': 1, 'initial_http': 8}
    values = request.get(LIMIT_FIELD, DEFAULTS)
    if not isinstance(values, dict) or set(values) != set(RANGES):
        raise ValueError('Supply the complete frozen dispatch limits')
    for key, (lower, upper) in RANGES.items():
        if type(values[key]) is not int or not lower <= values[key] <= upper:
            raise ValueError('Invalid dispatch limit: ' + key)
    if values['post_review_revisions'] >= values['map_updates']:
        raise ValueError('Leave map revisions for native acquisition')
    return copy.deepcopy(values)


def ledger(task):
    from ForecastAgent.research_loop import gap_feedback, fusion
    if not enabled(task.bundle):
        return None
    if not fusion.enabled(task) or not gap_feedback.enabled(task.bundle):
        raise ValueError('Material dispatch requires map and receipt policies')
    fusion.initialize(task)
    frozen = limits(task.bundle['request'])
    record = task.bundle.setdefault('research_dispatch',
        {'policy': POLICY, 'limits': frozen, 'selections': []})
    if record['policy'] != POLICY or record['limits'] != frozen:
        raise ValueError('Frozen dispatch policy or allowances changed')
    return record


def choose(task):
    """A phase consumes decisions; it cannot override hard closure or Exa duty."""
    record = ledger(task)
    if not record or task.bundle.get('result') or task.bundle['control'].get('forced_close'):
        return None
    if task.bundle.get('plan') is None:
        return None
    from ForecastAgent.research_loop import state, gap_feedback
    material = state.catalog(task.bundle, task.cutoff)
    revision = task.bundle['research_loop']['revision']
    history = record['selections']
    # Preserve the release discovery-to-reading obligation even when an earlier
    # page is already saved. Otherwise repeated map work can starve a newly
    # discovered primary detail page until the same model budget is exhausted.
    from ForecastAgent.runtime.collection_actions import discovery_read_action
    discovery = discovery_read_action(task)
    discovery_round = len(task.bundle.get('searches', [])) + len(task.bundle.get('exa_searches', []))
    attempts = sum(s['phase'] == 'capture_discovery' and s.get('discovery_round') == discovery_round
                   for s in history)
    if discovery and attempts < 2:
        return {'phase': 'capture_discovery', 'revision': revision,
                'discovery_round': discovery_round, 'tool': 'read_sources',
                'candidates': discovery,
                'instruction': 'Capture one batch of these already discovered unread URLs using '
                'read_sources before additional map interpretation. A saved background page '
                'does not fulfill this new discovery batch. Choose the most useful exact '
                'rule-source detail or dated baseline; ranking does not verify relevance. '
                'Use an actual JSON array for urls. Future outcomes remain unknown.'}
    pending = gap_feedback.pending(task)
    # Two review proposals per exact source scope. Failed interpretations remain
    # pending, but cannot starve all subsequent sources or extend a soft stop.
    usable = [m for m in pending if sum(s.get('material_id') == m['material_id']
        and s['phase'] == 'process_update' for s in history) < 2]
    last = history[-1] if history else {}
    from ForecastAgent.research_loop import predictive_focus
    if predictive_focus.enabled(task.bundle):
        for gap in gap_feedback.gaps(task):
            need = {k:v for k,v in gap.items() if k in predictive_focus.NEED['properties']}
            try:
                local = predictive_focus.validate_need(task.bundle, need) == 'saved_unread'
            except (ValueError, TypeError, KeyError):
                local = False
            used = sum(s.get('gap_id') == gap['gap_id'] and s['phase'] == 'process_update' for s in history)
            if not local or used >= 2 or task.bundle['research_loop']['update_cap'] - revision <= record['limits']['post_review_revisions']:
                continue
            source = next((m for m in gap_feedback.materials(task, material).values() if m['url'] == gap['source_url']), None)
            if source is None:
                continue
            ready = (last.get('phase') == 'process_read' and last.get('gap_id') == gap['gap_id']
                     and last.get('revision') == revision and last.get('material_sha256') == material['material_sha256']
                     and bool(source['inspected_reference_ids']))
            return {'phase':'process_update' if ready else 'process_read', 'revision':revision,
                    'gap_id':gap['gap_id'], 'material_sha256':material['material_sha256'],
                    'material_id':source['material_id'], 'url':source['url'],
                    'tool':'update_research_state' if ready else 'inspect_research_state',
                    'instruction':'Read the exact saved source for this gap, then correct its interpretation/request. '
                                  'No network fetch; preserve uncertainty and use source-specific receipts.'}
    gaps = [g for g in gap_feedback.gaps(task)
            if predictive_focus.network_candidate(task.bundle, g)
            and g.get('importance') != 'low'
            and g.get('attempts_without_readable_body', 0) < 2]
    # One explicit agent acquisition opportunity after each committed graph.
    # It must use a real declared obtainable gap, not a guessed future outcome.
    acquired = any(e['map_revision'] == revision and e.get('research_gap_ids')
                   for e in task.bundle['research_acquisition']['events'])
    phase_attempts = sum(s['phase'] == 'acquire_gap' and s['revision'] == revision for s in history)
    budget = task.budget()
    can_network = budget['page_fetch_remaining'] > 0
    if revision and gaps and can_network and not acquired and phase_attempts < 2:
        # Suggested tools are action categories, not callable function names.
        # Bind page capture to the native collection interface explicitly.
        tool = 'read_sources' if gaps[0].get('suggested_tool') == 'page_fetch' and (task.catalog() or task.bundle['pages']) else None
        return {'phase': 'acquire_gap', 'revision': revision,
                'gaps': gaps[:3], 'tool': tool,
                'instruction': 'Choose ONE native acquisition action for an obtainable listed gap. '
                'Link its research_gap_ids and research_node_ids. Copy saved/discovered URLs only. '
                'A future realization is not an obtainable document; preserve that uncertainty. '
                'suggested_tool is an intent category, not a function name. Call only an offered '
                'tool. All array arguments must be JSON arrays, never encoded strings.'}
    remaining = task.bundle['research_loop']['update_cap'] - revision
    reserve = record['limits']['post_review_revisions']
    if usable and remaining > reserve:
        source = sorted(usable, key=lambda m: (sum(s.get('material_id') == m['material_id']
            for s in history), m['url']))[0]
        if last.get('phase') == 'process_read':
            source = next((m for m in usable if m['material_id'] == last.get('material_id')), source)
        ready = (last.get('phase') == 'process_read'
                 and last.get('material_id') == source['material_id']
                 and last.get('material_sha256') == material['material_sha256']
                 and last.get('revision') == revision
                 and bool(source['inspected_reference_ids']))
        phase = 'process_update' if ready else 'process_read'
        return {'phase': phase, 'revision': revision,
                'material_sha256': material['material_sha256'],
                'material_id': source['material_id'], 'url': source['url'],
                'tool': 'update_research_state' if ready else 'inspect_research_state',
                'instruction': 'Process this exact saved source. Read its substantive text, then '
                'return its source-bound receipt. Incorporation requires a retained observation '
                'of this source; irrelevant/deferred are valid. No full-page reading claim.'}
    return None


def record_selection(task, choice):
    if choice:
        ledger(task)['selections'].append(copy.deepcopy(choice))
        task.save()


def tools_for(task, tools, choice):
    if not choice:
        return tools
    tools = copy.deepcopy(tools)
    # A domain hint does not permit invented URLs. Lexical ordering is only
    # navigation; it cannot certify target relevance or source authority.
    if choice['tool'] == 'read_sources':
        known = {**task.catalog(), **task.bundle['pages']}
        failed = {r.get('url') for r in task.bundle.get('fetch_attempts', [])
                  if r.get('status') in {'failed', 'reserved'}}
        target = ' '.join(g.get('target', '') for g in choice.get('gaps', []))
        terms = set(re.findall(r'[a-z0-9]{4,}', (target or task.bundle['request']['question']).lower()))
        ordered = sorted((u for u in known if u not in failed), key=lambda u: (
            u in task.bundle['pages'],
            -len(terms & set(re.findall(r'[a-z0-9]{4,}',
                (u + ' ' + str(known[u].get('title', ''))).lower()))), u))[:24]
        if choice['phase'] == 'capture_discovery':
            ordered = [u for u in choice['candidates']['urls']
                       if u in known and u not in failed and u not in task.bundle['pages']]
        if not ordered:
            return []  # Preserve an explicit routing gap; do not grant arbitrary URLs.
        for entry in tools:
            if entry['function']['name'] == 'read_sources' and ordered:
                entry['function']['parameters']['properties']['urls']['items']['enum'] = ordered
    if choice['phase'] == 'acquire_gap':
        from ForecastAgent.tools.capabilities import get
        selected = [t for t in tools if 'network' in get(t['function']['name']).effects]
        for entry in selected:
            schema = entry['function']['parameters']
            if 'research_gap_ids' in schema.get('properties', {}):
                schema['properties']['research_gap_ids']['minItems'] = 1
                schema['properties']['research_gap_ids']['items']['enum'] = [g['gap_id'] for g in choice['gaps']]
                schema['required'] = list(dict.fromkeys(schema.get('required', []) + ['research_gap_ids']))
        return selected
    if choice['phase'] == 'process_read':
        for tool in tools:
            if tool['function']['name'] == 'inspect_research_state':
                schema = tool['function']['parameters']
                schema['properties']['url']['enum'] = [choice['url']]
                schema['required'] = list(dict.fromkeys(schema.get('required', []) + ['url']))
    return tools


def status(task):
    from ForecastAgent.research_loop import gap_feedback
    processing = gap_feedback.coverage(task)
    return {'status': processing['status'],
            'pending_material_count': processing['pending_material_count'],
            'pending_material_ids': [m['material_id'] for m in processing['pending']],
            'target_evidence_complete': None, 'truth_verified': False}
