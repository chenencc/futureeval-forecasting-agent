"""Opt-in bounded scheduling of saved-material review and gap acquisition.

This module routes work, not truth. It never creates receipts, probabilities,
source URLs, provider calls or renewed allowances.
"""
import copy
import hashlib
import json
import re
from urllib.parse import urlsplit, parse_qsl

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
    from ForecastAgent.research_loop import context_budget
    context_budget.ceilings(request)
    policy = request.get(FIELD, 'disabled')
    if policy not in {'disabled', POLICY}:
        raise ValueError('Unknown research dispatch policy')
    if policy == 'disabled':
        if context_budget.FIELD in request:
            raise ValueError('Context budget requires research dispatch policy')
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


def linked_sources(task):
    """Bind a lead to a literal link in its saved parent, without certifying it.

    A source_leads row alone is not proof of discovery. Catalog eligibility still
    owns temporal and destination guards; no URL is synthesized or rewritten.
    """
    from ForecastAgent.tavily_research import canonical_url
    from ForecastAgent.runtime.collection_actions import named_primary
    from ForecastAgent.readers.material_structure import observable_url
    parents = []
    for parent_url, page in task.bundle['pages'].items():
        if not page.get('content'):
            continue
        rule_parent = bool(named_primary(task, parent_url)) or any(
            canonical_url(u) == canonical_url(parent_url) and row.get('origin', '').startswith('question_')
            for u, row in task.bundle.get('source_leads', {}).items())
        parents.append((parent_url, page, rule_parent))
    result = {}
    for url, lead in task.bundle.get('source_leads', {}).items():
        if lead.get('origin') != 'page_link':
            continue
        matches = []
        # The first discovery row can point to a tracking alias. Prefer a real
        # captured primary parent that also contains this exact link, without
        # changing the saved row or treating equal body hashes as equal URLs.
        for parent_url, page, rule_parent in parents:
            if observable_url(url, parent_url) is None:
                continue
            links = page.get('links') or []
            index = next((i for i, link in enumerate(links) if isinstance(link, str)
                          and canonical_url(link) == canonical_url(url)), None)
            if index is not None:
                matches.append((not rule_parent, parent_url, index, page, rule_parent))
        if not matches:
            continue
        _, parent_url, index, page, rule_parent = sorted(matches, key=lambda row: row[:3])[0]
        links = page['links']
        parts = urlsplit(url)
        formats = {v.lower() for k, v in parse_qsl(parts.query)
                   if k.lower() in {'output', 'format', 'export'}}
        data_link = parts.path.lower().endswith(('.csv', '.json', '.xlsx', '.xls', '.pdf')) or bool(
            formats & {'csv', 'json', 'xlsx', 'xls', 'pdf'})
        result[canonical_url(url)] = {
            'parent_url': parent_url,
            'parent_body_sha256': hashlib.sha256(str(page.get('content') or '').encode()).hexdigest(),
            'link_index': index, 'literal_link_sha256': hashlib.sha256(links[index].encode()).hexdigest(),
            'source_role': 'linked_from_rule_source' if rule_parent else 'observed_page_link',
            'data_link': data_link, 'relevance_verified': False}
    return result


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
    from ForecastAgent.readers.material_structure import observable_url
    attempted = {canonical_url(a['url']) for a in task.bundle.get('fetch_attempts', []) if a.get('url')}
    attempted.update(canonical_url(u) for u in task.bundle['pages'])
    observed = {canonical_url(hit['url']) for result in
        task.bundle.get('searches', []) + task.bundle.get('exa_searches', [])
        for hit in result.get('results', []) if hit.get('url')}
    if include_rule_leads:
        observed.update(canonical_url(u) for u, row in task.bundle.get('source_leads', {}).items()
                        if row.get('origin', '').startswith('question_'))
    linked = linked_sources(task)
    observed.update(linked)
    known = task.catalog()
    urls = sorted((u for u in known if canonical_url(u) in observed and canonical_url(u) not in attempted
                   and observable_url(u, u) is not None),
        key=lambda u: (not (linked.get(canonical_url(u), {}).get('data_link') and
                            linked.get(canonical_url(u), {}).get('source_role') == 'linked_from_rule_source'),
            -discovery_score(task.bundle['request'], u, known[u].get('title') or '',
            named_primary(task, u), known[u].get('published_date')), u))[:24]
    return {'urls': urls, 'sources': [{'source_id': 'L' + hashlib.sha256(u.encode()).hexdigest()[:12],
        'url': u, 'title': known[u].get('title') or '',
        'published_date': known[u].get('published_date'),
        'discovery_excerpt': str(known[u].get('content') or known[u].get('text') or '')[:600],
        'evidence_status': 'unread_discovery_lead',
        **({'link_provenance': linked[canonical_url(u)]} if canonical_url(u) in linked else {})} for u in urls]}


def review_scope(task, choice):
    """Resolve the scheduled material and its actually inspected references."""
    from ForecastAgent.research_loop import gap_feedback, state
    material = state.catalog(task.bundle, task.cutoff)
    source = gap_feedback.materials(task, material).get(choice['material_id'])
    if (source is None or source['url'] != choice['url'] or
            choice['revision'] != task.bundle['research_loop']['revision'] or
            choice['material_sha256'] != material['material_sha256']):
        raise ValueError('Scheduled review no longer matches the current material scope')
    result = {k: copy.deepcopy(source[k]) for k in (
        'material_id', 'url', 'body_sha256', 'scope_sha256', 'inspected_reference_ids',
        'bound_observation_node_ids', 'same_body_material_ids')}
    result['protected_observation_node_ids'] = [n['id'] for n in
        (task.bundle['research_loop'].get('current') or {}).get('nodes', [])
        if n['kind'] == 'observation' and any(b['url'] != source['url'] for b in n.get('bindings', []))]
    return result


def model_context(task, choice, *, forced_tool=None):
    """Fit optional discovery previews without dropping identities or rules.

    Local projection attempts consume no provider allowance. The full choice
    remains authoritative for native argument binding and dispatch receipts.
    """
    from ForecastAgent.runtime.context import collection_context, encode, MAX_CONTEXT_CHARS, ContextProjectionError
    from ForecastAgent.research_loop.context_budget import ceilings
    discovery = choice and choice.get('phase') in {'capture_discovery', 'capture_frontier'}
    # Long titles and repeated dictionary keys can exhaust the remaining window
    # even after previews are removed. Pack navigation as a table before failing;
    # every source ID, exact URL and publication date survives every tier.
    tiers = (('objects', 600, None), ('objects', 300, None), ('objects', 0, None),
             ('rows', 0, None), ('rows', 0, 80), ('rows', 0, 0)) if discovery else (('objects', None, None),)
    failures = []
    budgets = ceilings(task.bundle.get('request', {}))
    trials = [(ceiling, tier) for ceiling in budgets for tier in tiers]
    for tier_index, (ceiling, (registry_format, preview_chars, title_chars)) in enumerate(trials):
        projected = copy.deepcopy(choice)
        if projected and projected['phase'] == 'process_update':
            projected['review_scope'] = review_scope(task, choice)
            projected['instruction'] += (' Return exactly one material_review for this material_id. '
                'Use only its inspected_reference_ids; unrelated retained nodes do not incorporate it. '
                'Merge only changed nodes, preserving previous source bindings. Deferred is valid '
                'when interpretation is unfinished; do not force a fact or an irrelevant verdict. '
                'Do not emit expected_revision or material_sha256: the program binds the '
                'unchanged scheduled execution frame, including its current revision. '
                'Never rebind or retire protected_observation_node_ids for this source; '
                'use a distinct ID for a new source, even when another URL has identical bytes.')
        if discovery:
            candidates = projected['candidates']
            candidates.pop('urls', None)  # Every exact URL remains in sources.
            for source in candidates['sources']:
                excerpt = source.get('discovery_excerpt', '')
                source['discovery_excerpt'] = excerpt[:preview_chars]
                source['excerpt_omitted_chars'] = max(0, len(excerpt) - preview_chars)
            if registry_format == 'rows':
                columns = ['source_id', 'url', 'title', 'published_date',
                           'discovery_excerpt', 'title_omitted_chars', 'excerpt_omitted_chars', 'link_provenance']
                rows = []
                for source in candidates['sources']:
                    title = source.get('title') or ''
                    visible_title = title if title_chars is None else title[:title_chars]
                    rows.append([source['source_id'], source['url'], visible_title,
                                 source.get('published_date'), source['discovery_excerpt'],
                                 len(title)-len(visible_title), source['excerpt_omitted_chars'],
                                 source.get('link_provenance')])
                projected['candidates'] = {'source_columns': columns, 'source_rows': rows,
                    'total_source_count': len(rows),
                    'instruction': 'Navigation table only; titles and previews may be shortened with explicit omitted-character counts. All exact source IDs, URLs and known publication dates are retained. Full search results remain saved; omitted text is not absent evidence.'}
        packet = ({'role': 'user', 'content': json.dumps(
            {'research_dispatch': projected}, ensure_ascii=False)} if projected else None)
        remaining = ceiling - (len(encode(packet)) + 1 if packet else 0)
        try:
            messages = collection_context(task, forced_tool=forced_tool, max_chars=remaining)
            if packet:
                messages.append(packet)
            size = len(encode(messages))
            if size > ceiling:
                raise ContextProjectionError('Context ceiling exceeded after dispatch projection; no model HTTP issued')
        except ContextProjectionError as exc:
            failures.append(str(exc))
            if tier_index == len(trials)-1:
                raise
            continue
        if choice:
            task.bundle.setdefault('dispatch_context_projections', []).append({
                'phase': choice['phase'], 'preview_chars': preview_chars,
                'registry_format': registry_format, 'title_chars': title_chars,
                'source_ids': [s['source_id'] for s in (choice or {}).get('candidates', {}).get('sources', [])],
                'message_chars': size, 'ceiling_chars': ceiling,
                'preferred_ceiling_chars': budgets[0], 'maximum_ceiling_chars': budgets[-1],
                'local_projection_failures': failures, 'provider_calls': 0})
        return messages


def validate_delivery(task, messages):
    """Check the final wire-bound messages against the task's registered cap.

    This runs after supplemental routing too. Legacy collection remains 28K;
    only explicit research dispatch can register a larger hard ceiling.
    """
    from ForecastAgent.runtime.context import encode, MAX_CONTEXT_CHARS, ContextProjectionError
    from ForecastAgent.research_loop.context_budget import ceilings
    request = task.bundle.get('request', {})
    limits(request)
    maximum = ceilings(request)[-1] if enabled(task.bundle) else MAX_CONTEXT_CHARS
    if len(encode(messages)) > maximum:
        raise ContextProjectionError('Context ceiling exceeded after final routing; no model HTTP issued')


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
    if choice['phase'] == 'process_update':
        scope = review_scope(task, choice)
        for tool in tools:
            if tool['function']['name'] == 'update_research_state':
                schema = tool['function']['parameters']
                # The model supplies interpretation, not bookkeeping. Native
                # concurrency checks use the unchanged scheduled frame.
                for key in ('expected_revision', 'material_sha256'):
                    schema['properties'].pop(key, None)
                schema['required'] = [k for k in schema.get('required', [])
                                      if k not in {'expected_revision', 'material_sha256'}]
                reviews = schema['properties']['material_reviews']
                reviews.update(minItems=1, maxItems=1)
                props = reviews['items']['properties']
                props['material_id']['enum'] = [scope['material_id']]
                props['evidence_ids']['items']['enum'] = scope['inspected_reference_ids']
                reviews['description'] = ('Exactly one receipt for the scheduled source. '
                    'Only its inspected references are allowed. Deferred may cite no references. '
                    'A receipt does not establish factual truth or force a graph edit.')
                schema['required'] = list(dict.fromkeys(schema.get('required', []) + ['material_reviews']))
                tool['function']['description'] += (' This scheduled turn omits expected_revision '
                    'and material_sha256 arguments; the program binds both. Emit neither field.')
    return tools


def bind_arguments(task, name, args, tools, choice):
    """Validate the emitted ID registry, then restore the native URL interface.

    Raw model arguments remain in HTTP receipts. Binding is exact, never fuzzy;
    an unknown ID or mixed URL/ID request fails before a network reservation.
    The native handler retains its existing effects, validation and quotas.
    """
    if choice and choice['phase'] == 'process_update' and name == 'update_research_state':
        from ForecastAgent.runtime.contracts import check_schema
        scope = review_scope(task, choice)
        if set(args) & {'expected_revision', 'material_sha256'}:
            raise ValueError('Scheduled execution frame is program-owned; omit revision and material hash arguments')
        # Rebuild the dynamic restrictions at execution, even if a caller supplied
        # the unfiltered native schema. Do not repair the emitted receipt.
        restricted = tools_for(task, tools, choice)
        entry = next(t for t in restricted if t['function']['name'] == name)
        check_schema(args, entry['function']['parameters'])
        current = {n['id']: n for n in (task.bundle['research_loop'].get('current') or {}).get('nodes', [])}
        protected = set(scope['protected_observation_node_ids'])
        if protected & set(args.get('retired_node_ids', [])):
            raise ValueError('Scheduled source review cannot retire another source observation')
        for node in args.get('nodes', []):
            if node['id'] in protected and (node['kind'] != 'observation' or
                    set(node['evidence_ids']) != set(current[node['id']]['evidence_ids'])):
                raise ValueError('Use a distinct observation ID; scheduled review cannot rebind another source')
        bound = {**copy.deepcopy(args), 'expected_revision': choice['revision'],
                 'material_sha256': choice['material_sha256']}
        native = copy.deepcopy(restricted)
        schema = next(t['function']['parameters'] for t in native if t['function']['name'] == name)
        schema['properties'].update(
            expected_revision={'type': 'integer', 'enum': [choice['revision']]},
            material_sha256={'type': 'string', 'enum': [choice['material_sha256']], 'maxLength': 64})
        schema['required'] += ['expected_revision', 'material_sha256']
        return bound, native, {'policy': 'exact_scheduled_material_review_v2',
            **scope, 'arguments_rewritten': True, 'factual_arguments_rewritten': False,
            'program_bound_fields': {k: bound[k] for k in ('expected_revision', 'material_sha256')},
            'meaning_verified': False}
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
