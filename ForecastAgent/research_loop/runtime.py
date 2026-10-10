"""Local acquisition tools; no nested model, search or fetch calls."""
import copy

from ForecastAgent.runtime.contracts import check_schema
from ForecastAgent.research_loop import POLICY
from ForecastAgent.research_loop.schema import TOOLS, GUIDE
from ForecastAgent.research_loop.state import initialize, enabled, catalog, view
from ForecastAgent.research_loop.acceptance import accept, ENVELOPE

NAMES = {tool['function']['name'] for tool in TOOLS}


def validate_args(task, name, args, tools=None):
    if not enabled(task.bundle) or tools is not None and name not in {t['function']['name'] for t in tools}:
        raise ValueError('Research tool is not available')
    schema = ENVELOPE if name == 'update_research_state' else TOOLS[0]['function']['parameters']
    from ForecastAgent.research_loop import grounding
    from ForecastAgent.research_loop import delta
    if delta.enabled(task.bundle) and name == 'update_research_state':
        schema=delta.schema(schema)
    from ForecastAgent.research_loop import gap_feedback
    if gap_feedback.enabled(task.bundle) and name == 'update_research_state':
        schema=gap_feedback.schema(schema,envelope=True)
    if grounding.enabled(task.bundle) and name == 'inspect_research_state':
        schema = inspection_schema(schema)
    if gap_feedback.enabled(task.bundle) and name == 'inspect_research_state':
        schema=copy.deepcopy(schema)
        schema['properties']['material_offset']={'type':'integer','minimum':0}
        schema['properties']['offset']['description']='Span-index cursor, not a character or byte coordinate. Copy next_offset from the previous reading of the SAME url/query/date filters. Default 0.'
    check_schema(args, schema)


def inspection_schema(base):
    """Local filters never authorize search, fetch or access outside saved scope."""
    result=copy.deepcopy(base)
    result['properties'].update(url={'type':'string','maxLength':2000},
        start_date={'type':'string','maxLength':10}, end_date={'type':'string','maxLength':10},
        source_offset={'type':'integer','minimum':0,'description':'Paginates source metadata ONLY. To choose body text, supply url and optionally query or offset. Does not choose the reading URL.'})
    result['properties']['limit']['description']='Deliver 1-4 original spans per call; default 3. Use next_offset or a query for additional material. Do not increase this limit.'
    return result


def configure(task, tools):
    initialize(task.bundle)
    tools = tools + copy.deepcopy(TOOLS) if enabled(task.bundle) else tools
    from ForecastAgent.research_loop.fusion import configure as configure_fusion
    return configure_fusion(task, tools)


def guide(task, system):
    if not enabled(task.bundle):
        return system
    # Replace one acquisition-only sentence with an explicitly scoped exception.
    system = system.replace('Do not predict, assign event probabilities,\nfact-check, submit forecasts, or trade.',
        'Do not assign probabilities, certify facts, submit forecasts or trade.\nMaintain provisional research notes under the experimental policy below.')
    from ForecastAgent.research_loop import fusion, grounding
    from ForecastAgent.research_loop import gap_feedback
    guide_text = fusion.GUIDE if fusion.enabled(task) else GUIDE
    from ForecastAgent.research_loop import dispatch
    if dispatch.enabled(task.bundle):
        values = dispatch.limits(task.bundle['request'])
        guide_text = guide_text.replace('At most three updates',
            'At most ' + str(values['map_updates']) + ' updates')
        guide_text += ('\nA program research_dispatch phase narrows this turn to saved-source '
            'processing or one obtainable-gap acquisition. Follow its exact source and ID '
            'registry. Do not spend the phase loading skills or browsing unrelated navigation. '
            'The map is a research aid; missing future outcomes remain unknown.\n')
    return system + guide_text + (grounding.GUIDE if grounding.enabled(task.bundle) else '') + (gap_feedback.GUIDE if gap_feedback.enabled(task.bundle) else '')


def execute(task, name, args):
    if not enabled(task.bundle):
        raise ValueError('Research state policy is disabled')
    validate_args(task, name, args)
    if name == 'update_research_state':
        from ForecastAgent.research_loop import post_supplement
        if (post_supplement.enabled(task.bundle['request']) and not task.bundle.get(post_supplement.PHASE)
                and task.bundle['research_loop']['revision'] >= task.bundle['research_loop']['update_cap'] -
                post_supplement.reserved_revisions(task.bundle['request'])):
            raise ValueError('Final map revision is reserved for post-supplement local reading')
        from ForecastAgent.research_loop import fusion, simple_map, grounding
        from ForecastAgent.research_loop import delta
        from ForecastAgent.research_loop import gap_feedback
        before = copy.deepcopy(task.bundle['research_loop'].get('current'))
        reviews = None
        format_bindings = []
        submitted_hash = None
        if gap_feedback.enabled(task.bundle):
            from ForecastAgent.analysis.pilot import digest
            from ForecastAgent.research_loop import quote_bindings
            submitted_hash = digest(args)
            ledger = gap_feedback.initialize(task)
            if len(ledger['events']) >= gap_feedback.event_cap(task):
                raise ValueError('Gap feedback lifetime receipt cap exhausted')
            args = copy.deepcopy(args)
            reviews = args.pop('material_reviews')
            if not args['revision_reason'].strip():
                raise ValueError('Explain what changed or why the graph did not change in revision_reason')
            args, format_bindings = quote_bindings.prepare(task,args)
        delta_report=None
        prior_observations={r['evidence_id'] for n in (task.bundle['research_loop'].get('current') or {}).get('nodes',[])
            if n['kind']=='observation' for r in n['bindings']}
        review_pending=task.bundle.get('research_acquisition',{}).get('pending_map_update',False)
        if delta.enabled(task.bundle):
            args,delta_report=delta.expand(task.bundle,args)
        result = accept(task.bundle, args, task.cutoff,
            map_protocol=simple_map.PROTOCOL if fusion.enabled(task) else 'legacy',
            stage_grounding=grounding.enabled(task.bundle),
            preserve_rejected=bool(delta_report and delta_report['update_mode']=='merge'))
        if reviews is not None:
            current={n['id']:n for n in (task.bundle['research_loop'].get('current') or {}).get('nodes',[])}
            for record in format_bindings:
                record['applied'] = current.get(record['node_id'],{}).get(record['field'])==record['literal_value']
            result['acceptance']['submitted_proposal_sha256']=submitted_hash
            result['acceptance']['quote_format_bindings']=format_bindings
        if delta_report:
            result['acceptance']['delta']=copy.deepcopy(delta_report)
        if delta_report and result.get('committed',not result.get('cached')):
            from ForecastAgent.analysis.pilot import digest
            event=task.bundle['research_loop']['events'][-1]
            event['acceptance']['delta']=delta_report
            if reviews is not None:
                event['acceptance']['submitted_proposal_sha256']=submitted_hash
                event['acceptance']['quote_format_bindings']=copy.deepcopy(format_bindings)
            event['event_sha256']=digest({k:v for k,v in event.items() if k!='event_sha256'})
            result['event_sha256']=event['event_sha256']
        acknowledge=result.get('committed',True)
        if reviews is not None:
            result = gap_feedback.review(task,reviews,before,args['revision_reason'],result)
            acknowledge = result['material_acknowledged']
        elif delta_report:
            new_observations={r['evidence_id'] for n in (task.bundle['research_loop'].get('current') or {}).get('nodes',[])
                if n['kind']=='observation' for r in n['bindings']}-prior_observations
            acknowledge=acknowledge and (bool(new_observations) or not review_pending)
            result['material_acknowledged']=acknowledge
        if fusion.enabled(task) and acknowledge: fusion.mapped(task)
        task.save()
        return result
    materials = catalog(task.bundle, task.cutoff)
    from ForecastAgent.research_loop import grounding
    query = args.get('query', '').casefold()
    spans = [s for s in materials['spans'].values()
             if not query or query in s['text'].casefold() or query in s['url'].casefold()]
    if grounding.enabled(task.bundle):
        spans = grounding.select(materials,args)
    from ForecastAgent.research_loop import reading_views
    if reading_views.enabled(task.bundle) and not args.get('query') and not args.get('start_date'):
        # Headings remain in source_context. Default reading delivers body lines,
        # not only the document title. Explicit queries can still select headings.
        spans=reading_views.substantive(spans)
    offset, limit = args.get('offset', 0), args.get('limit', 3)
    page = spans[offset:offset + limit]
    result = {'research_state': view(task.bundle, task.cutoff), 'evidence': copy.deepcopy(page),
        'total_matches': len(spans), 'next_offset': offset + len(page) if offset + len(page) < len(spans) else None,
        'excluded_sources': materials['excluded_sources'], 'no_progress': True,
        'material_sha256': materials['material_sha256']}
    result['reading_cursor']={'selectors':{k:args[k] for k in ('url','query','start_date','end_date') if k in args},
        'span_offset':offset,'next_span_offset':result['next_offset'],
        'total_matching_spans':len(spans),'delivered_spans':len(page),
        'instruction':'offset counts matching SPANS, not characters. Copy next_span_offset only with the same selectors. None means this filtered reading is exhausted; change query/URL or update notes.'}
    if grounding.enabled(task.bundle):
        result.update(source_catalog=grounding.inventory(task.bundle, materials, offset=args.get('source_offset',0)),
            source_context=grounding.context(materials,page),
            reading_scope={'matching_spans':len(spans), 'delivered_spans':len(page),
                'unread_matching_spans':max(0,len(spans)-len(page)),
                'instruction':'Delivered excerpts are not the whole saved archive; inspect locally before further acquisition.'})
    from ForecastAgent.research_loop import gap_feedback
    if gap_feedback.enabled(task.bundle):
        inventory=gap_feedback.materials(task,materials)
        by_url={m['url']:m['material_id'] for m in inventory.values()}
        for span in result['evidence']:
            span['material_id']=by_url[span['url']]
        result['gap_feedback']=gap_feedback.view(task,args.get('material_offset',0))
    return result


def filter_tools(task, tools):
    if not enabled(task.bundle):
        return tools
    report = view(task.bundle, task.cutoff)
    omit = set()
    if not report['remaining_updates']:
        omit.add('update_research_state')
    from ForecastAgent.research_loop import post_supplement
    if (post_supplement.enabled(task.bundle['request']) and not task.bundle.get(post_supplement.PHASE)
            and report['remaining_updates'] <= post_supplement.reserved_revisions(task.bundle['request'])):
        omit.add('update_research_state')
    if task.bundle.get('result') or task.bundle.get('control', {}).get('forced_close'):
        omit.update(NAMES)
    tools = [entry for entry in tools if entry['function']['name'] not in omit]
    from ForecastAgent.research_loop import fusion
    if fusion.enabled(task):
        tools=copy.deepcopy(tools)
        need_ids=[n['id'] for n in task.bundle.get('plan') or []]
        node_ids=[n['id'] for n in fusion.targets(task)]+[n['id'] for n in
            (task.bundle['research_loop'].get('current') or {}).get('nodes',[])]
        from ForecastAgent.research_loop import gap_feedback
        gap_ids=[g['gap_id'] for g in gap_feedback.gaps(task)] if gap_feedback.enabled(task.bundle) else []
        def bind(schema):
            for name,prop in schema.get('properties',{}).items():
                ids=need_ids if name=='need_ids' else node_ids if name==fusion.LINK_FIELD else gap_ids if name==gap_feedback.LINK else []
                if ids and prop.get('type')=='array':
                    prop['items']['enum']=ids
                    prop['description']='Array of current '+('material need IDs' if name=='need_ids' else 'research gap IDs' if name==gap_feedback.LINK else 'research target/map node IDs')+'. Copy IDs from this enum; these namespaces are distinct.'
                bind(prop)
            if isinstance(schema.get('items'),dict):bind(schema['items'])
        for entry in tools:
            bind(entry['function']['parameters'])
            if entry['function']['name']=='inspect_research_state' and gap_feedback.enabled(task.bundle):
                entry['function']['parameters']['properties']['offset']['description']='Span-index cursor, not a character/byte coordinate. Copy last_reading_cursor.next_span_offset with the SAME url/query/date filters. Default 0.'
                entry['function']['parameters']['properties']['material_offset']={'type':'integer','minimum':0,
                    'description':'Paginates pending source receipts only. Use url/query to read that source.'}
            if entry['function']['name']=='update_research_state':
                props=entry['function']['parameters']['properties']
                # Review-only patches are valid only when an existing graph survives.
                if not task.bundle['research_loop'].get('current'):
                    props['nodes']['minItems'] = 1
                props['revision_reason'].update(minLength=1,
                    description='Nonempty explanation, at most 300 characters. Summarize the actual change; put source-specific explanations in material_reviews. Do not fabricate a fact.')
                props['expected_revision']['enum']=[report['revision']]
                props['material_sha256']['enum']=[report['material_sha256']]
                from ForecastAgent.research_loop import delta
                if delta.enabled(task.bundle):
                    old=[n['id'] for n in (task.bundle['research_loop'].get('current') or {}).get('nodes',[])]
                    if old:props['retired_node_ids']['items']['enum']=old
                    current=task.bundle['research_loop'].get('current') or {}
                    if current.get('material_sha256')==report['material_sha256']:
                        props['revision_kind']['enum']=['interpretation_correction']
                        props['revision_kind']['description']='The saved material is unchanged. Incorporate newly read spans or revise notes with interpretation_correction; this is not a new acquisition.'
                from ForecastAgent.research_loop import grounding
                if grounding.enabled(task.bundle):
                    frame = fusion.binding_frame(task)
                    refs = list(frame['inspected_reference_handles'])
                    refs.extend(s['evidence_id'] for s in frame.get('source_context', []))
                    refs.extend(i for n in (task.bundle['research_loop'].get('current') or {}).get('nodes', [])
                                for i in n['evidence_ids'])
                    if refs:
                        props['nodes']['items']['properties']['evidence_ids']['items']['enum']=list(dict.fromkeys(refs))
                if gap_feedback.enabled(task.bundle):
                    links=props['material_reviews']['items']['properties']['gap_ids']
                    if gap_ids:
                        links['items']['enum']=gap_ids
                    else:
                        links['maxItems']=0
                    props['nodes']['items']['properties']['stage_basis']['description']='At most 180 characters. Copy a SHORT literal stage-supporting phrase from a bound reference; otherwise use empty string and event_stage=unknown.'
    return tools
