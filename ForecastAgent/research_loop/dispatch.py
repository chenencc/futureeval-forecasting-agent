"""Opt-in bounded scheduling of saved-material review and gap acquisition.

This module routes work, not truth. It never creates receipts, probabilities,
source URLs, provider calls or renewed allowances.
"""
import copy
import hashlib
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


def discovered_candidates(task, *, include_rule_leads=False):
    """Offer observed leads for agent selection, never a required fetch batch.

    Scores order navigation only. A four-item fetch limit must not become a
    four-item relevance filter before the agent sees titles and source scope.
    Historical replay retains its archive routing rather than live captures.
    """
    if task.cutoff or task.budget()['page_fetch_remaining'] <= 0:
        return {'urls': [], 'sources': []}
    from ForecastAgent.tavily_research import canonical_url
    from ForecastAgent.evidence.acquisition_quality import discovery_score
    from ForecastAgent.runtime.collection_actions import named_primary
    attempted = {canonical_url(a['url']) for a in task.bundle.get('fetch_attempts', []) if a.get('url')}
    attempted.update(canonical_url(u) for u in task.bundle['pages'])
    observed = {canonical_url(hit['url']) for result in
        task.bundle.get('searches', []) + task.bundle.get('exa_searches', [])
        for hit in result.get('results', []) if hit.get('url')}
    if include_rule_leads:
        observed.update(canonical_url(u) for u, row in task.bundle.get('source_leads', {}).items()
                        if row.get('origin', '').startswith('question_'))
    known = task.catalog()
    urls = sorted((u for u in known if canonical_url(u) in observed and canonical_url(u) not in attempted),
        key=lambda u: (-discovery_score(task.bundle['request'], u, known[u].get('title') or '',
            named_primary(task, u), known[u].get('published_date')), u))[:24]
    return {'urls': urls, 'sources': [{'source_id': 'L' + hashlib.sha256(u.encode()).hexdigest()[:12],
        'url': u, 'title': known[u].get('title') or '',
        'published_date': known[u].get('published_date'),
        'discovery_excerpt': str(known[u].get('content') or known[u].get('text') or '')[:600],
        'evidence_status': 'unread_discovery_lead'} for u in urls]}


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
    candidates = discovered_candidates(task, include_rule_leads=True) if discovery and attempts < 2 else None
    if candidates and candidates['urls']:
        return {'phase': 'capture_discovery', 'revision': revision,
                'discovery_round': discovery_round, 'tool': 'read_sources',
                'candidates': candidates,
                'instruction': 'Select one bounded batch from these already discovered unread URLs using '
                'read_sources before additional map interpretation. A saved background page '
                'does not fulfill this new discovery batch. Choose the most useful exact '
                'rule-source detail or dated baseline; ranking does not verify relevance. '
                'Do not fetch every candidate. Compare titles and discovery excerpts against '
                'the target entity, metric, subgroup, event stage and date. Prefer an exact '
                'current baseline over a related issue, historical term or subgroup. '
                'Select source_ids from this registry; the program binds exact original URLs. '
                'Use an actual JSON array. Future outcomes remain unknown.'}
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
    # After a committed source review, offer one existing unread lead batch
    # before draining every other saved page into notes. The final review still
    # owns pending material; a graph cannot monopolize the initial acquisition.
    reviewed = any(s['phase'] == 'process_update' and s['revision'] < revision for s in history)
    frontier_attempts = sum(s['phase'] == 'capture_frontier' and s['revision'] == revision for s in history)
    if reviewed and usable and can_network and frontier_attempts < 2:
        candidates = discovered_candidates(task)
        if candidates['urls']:
            return {'phase':'capture_frontier', 'revision':revision, 'tool':'read_sources',
                    'candidates':candidates,
                    'instruction':'Use this existing unread discovery batch to extend the evidence '
                    'before another saved-page map cycle. Choose relevant exact-source detail or '
                    'independent context. These are observed leads, not verified evidence. '
                    'Failed captures stay gaps and pending originals remain pending. '
                    'Select source_ids from the supplied registry; the program binds original URLs. '
                    'Use a JSON array; all existing capture and model caps apply.'}
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
        if choice['phase'] in {'capture_discovery', 'capture_frontier'}:
            ordered = [u for u in choice['candidates']['urls']
                       if u in known and u not in failed and u not in task.bundle['pages']]
        if not ordered:
            return []  # Preserve an explicit routing gap; do not grant arbitrary URLs.
        for entry in tools:
            if entry['function']['name'] == 'read_sources' and ordered:
                schema = entry['function']['parameters']
                if choice['phase'] in {'capture_discovery', 'capture_frontier'}:
                    sources = choice['candidates']['sources']
                    ids = [s['source_id'] for s in sources if s['url'] in ordered]
                    selection = schema['properties'].pop('urls')
                    selection['items'] = {'type': 'string', 'enum': ids}
                    selection['description'] = 'Select observed source IDs; exact URLs are bound by the program.'
                    schema['properties']['source_ids'] = selection
                    schema['required'] = ['source_ids' if k == 'urls' else k for k in schema.get('required', [])]
                    if 'source_ids' not in schema['required']:
                        schema['required'].append('source_ids')
                    schema['additionalProperties'] = False
                else:
                    schema['properties']['urls']['items']['enum'] = ordered
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


def bind_arguments(task, name, args, tools, choice):
    """Validate the emitted ID registry, then restore the native URL interface.

    Raw model arguments remain in HTTP receipts. Binding is exact, never fuzzy;
    an unknown ID or mixed URL/ID request fails before a network reservation.
    The native handler retains its existing effects, validation and quotas.
    """
    if not choice or choice['phase'] not in {'capture_discovery', 'capture_frontier'} or name != 'read_sources':
        return args, tools, None
    from ForecastAgent.runtime.contracts import check_schema
    entry = next(t for t in tools if t['function']['name'] == name)
    check_schema(args, entry['function']['parameters'])
    ids = args['source_ids']
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('Choose distinct observed source IDs')
    registry = {s['source_id']: s['url'] for s in choice['candidates']['sources']}
    urls = [registry[i] for i in ids]
    if any(u not in task.catalog() for u in urls):
        raise ValueError('Source registry no longer matches discovered catalog')
    bound = copy.deepcopy(args)
    bound['urls'] = urls
    del bound['source_ids']
    native_tools = copy.deepcopy(tools)
    schema = next(t['function']['parameters'] for t in native_tools if t['function']['name'] == name)
    selection = schema['properties'].pop('source_ids')
    selection['items'] = {'type': 'string', 'enum': choice['candidates']['urls']}
    schema['properties']['urls'] = selection
    schema['required'] = ['urls' if k == 'source_ids' else k for k in schema['required']]
    return bound, native_tools, {'policy': 'exact_observed_source_id_v1',
        'source_ids': ids, 'urls': urls, 'url_rewriting': False}


def status(task):
    from ForecastAgent.research_loop import gap_feedback
    processing = gap_feedback.coverage(task)
    return {'status': processing['status'],
            'pending_material_count': processing['pending_material_count'],
            'pending_material_ids': [m['material_id'] for m in processing['pending']],
            'target_evidence_complete': None, 'truth_verified': False}
