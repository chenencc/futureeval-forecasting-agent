"""Multiple immutable literal fields precede fallible target interpretation."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from ForecastAgent.research_loop import source_facets as sf, field_targets as ft, state
from ForecastAgent.tests import test_research_node_reconcile as nr_tests
from ForecastAgent.research_loop import target_logic, runtime, post_supplement as post


class FieldTargetTests(unittest.TestCase):
    def sample(self):
        b, packet, card, mt, _ = nr_tests.NodeReconcileTests().sample()
        original = copy.deepcopy(packet['evidence'][0])
        quote = 'Revenue was 24 billion dollars.'
        self.assertIn(quote, original['text'])
        received = {'sources': [{'reference_id': original['evidence_id'],
            'coverage': 'content_excerpt', 'coverage_quote': quote,
            'facts': [{'kind': 'measurement', 'quote': quote, 'value': '24'},
                      {'kind': 'measurement_unit', 'quote': quote, 'value': 'billion dollars'},
                      {'kind': 'measurement_type', 'quote': quote, 'value': 'Revenue'}], 'unknowns': []}]}
        frozen = sf.registry([original], received)
        target = target_logic.targets(b)[0]['id']
        answer = {'registry_sha256': frozen['registry_sha256'], 'observations': [{
            'fact_id': frozen['facts'][0]['fact_id'],
            'context_fact_ids': [f['fact_id'] for f in frozen['facts'][1:]],
            'applicability': 'background', 'limitation': 'The final target release remains unknown.',
            'target_links': [{'target_id': target, 'role': 'baseline', 'effect': 'context',
                              'reason': 'Available same-metric observation; target-period applicability remains provisional.'}]}],
            'unknown_targets': [], 'revision_reason': 'Interpret preserved source fields separately.'}
        return b, mt, frozen, answer

    def test_every_accepted_field_is_frozen_not_only_one_primary(self):
        b, mt, frozen, answer = self.sample()
        self.assertEqual(len(frozen['facts']), 3)
        self.assertEqual(sf.verify_registry(frozen), frozen)
        before = copy.deepcopy((b, frozen, answer))
        args, audit = ft.proposal(b, frozen, answer, mt)
        self.assertEqual(args['nodes'][0]['interpretation'], 'Revenue was 24 billion dollars.')
        self.assertEqual(len(audit['field_reviews'][0]['context_fact_ids']), 2)
        self.assertEqual(audit['unselected_fact_ids'], [])
        self.assertFalse(audit['meaning_verified'])
        self.assertFalse(audit['probabilities_computed'])
        self.assertEqual((b, frozen, answer), before)

    def qualified_sample(self):
        b, mt, frozen, answer = self.sample()
        target = target_logic.targets(b)[0]['id']
        b['plan'][0]['condition'] = 'Determine whether revenue is above 30 billion dollars.'
        link = answer['observations'][0]['target_links'][0]
        link.update(source_premise_fact_ids=[frozen['facts'][0]['fact_id']],
            rule_context_target_ids=[],scope_basis='source_only',scope_assumptions=[],
            reason='The source states revenue of 24 billion dollars.')
        return b, mt, frozen, answer, target

    def test_qualified_source_only_cannot_borrow_a_rule_threshold(self):
        b, mt, frozen, answer, target = self.qualified_sample()
        answer['observations'][0]['target_links'][0]['reason'] = 'The source records 30 billion dollars.'
        args, audit = ft.proposal(b,frozen,answer,mt,qualified=True)
        self.assertEqual(len(args['nodes']),1)
        self.assertEqual(args['nodes'][0]['interpretation'], 'Revenue was 24 billion dollars.')
        self.assertEqual(args['nodes'][0]['target_links'], [])
        self.assertEqual(args['nodes'][0]['applicability'], 'unknown')
        self.assertIn('unbound numeric premise',audit['field_reviews'][0]['annotation_rejections'][0]['error'])
        self.assertEqual(audit['field_reviews'][0]['annotation_rejections'][0]['received_link']['reason'],
                         'The source records 30 billion dollars.')
        self.assertFalse(audit['meaning_verified'])

    def test_explicit_rule_comparison_is_allowed_without_becoming_source_evidence(self):
        b, mt, frozen, answer, target = self.qualified_sample()
        link=answer['observations'][0]['target_links'][0]
        link.update(rule_context_target_ids=[target],scope_basis='rule_context',
                    reason='Source revenue 24 is below the rule threshold 30.')
        args,audit=ft.proposal(b,frozen,answer,mt,qualified=True)
        self.assertEqual(len(args['nodes'][0]['target_links']),1)
        self.assertTrue(args['nodes'][0]['target_links'][0]['reason'].startswith('Model rule-context relation:'))
        origins=audit['field_reviews'][0]['qualified_attribution'][0]['numeric_origins']
        thirty=next(o for o in origins if o['literal']=='30')
        self.assertFalse(thirty['in_selected_source_quotes'])
        self.assertTrue(thirty['in_declared_rule_conditions'])
        self.assertFalse(thirty['meaning_verified'])

    def test_explicit_scope_bridge_is_hypothesis_not_dated_source_snapshot(self):
        b, mt, frozen, answer, target = self.qualified_sample()
        link=answer['observations'][0]['target_links'][0]
        bridge='Assume the earlier source metric remains comparable in the future target period.'
        link.update(scope_basis='assumption',rule_context_target_ids=[target],scope_assumptions=[bridge],
                    effect='supports',reason='Comparable revenue may remain above 30 with growth.')
        args,audit=ft.proposal(b,frozen,answer,mt,qualified=True)
        self.assertEqual(len(args['nodes']),2)
        observation,hypothesis=args['nodes']
        self.assertEqual(observation['interpretation'],'Revenue was 24 billion dollars.')
        self.assertEqual(observation['target_links'][0]['role'],'context')
        self.assertEqual(observation['target_links'][0]['effect'],'context')
        self.assertEqual(hypothesis['kind'],'assumption')
        self.assertEqual(hypothesis['hypothesis'],bridge)
        self.assertEqual(hypothesis['evidence_ids'],[])
        self.assertEqual(hypothesis['target_links'][0]['role'],'driver')
        self.assertEqual(args['relations'][0]['from_id'],observation['id'])
        self.assertEqual(args['relations'][0]['to_id'],hypothesis['id'])
        self.assertFalse(audit['source_role_meaning_verified'])

    def test_unselected_field_cannot_support_a_qualified_explanation(self):
        b, mt, frozen, answer, target=self.qualified_sample()
        # The unit field exists in the registry, but removing it from this
        # observation's context prevents attributing it as a selected premise.
        answer['observations'][0]['context_fact_ids']=[]
        answer['observations'][0]['target_links'][0]['source_premise_fact_ids']=[frozen['facts'][1]['fact_id']]
        args,audit=ft.proposal(b,frozen,answer,mt,qualified=True)
        self.assertEqual(args['nodes'][0]['target_links'],[])
        self.assertIn('outside the declared observation scope',audit['field_reviews'][0]['annotation_rejections'][0]['error'])

    def test_scope_bridge_cannot_hide_its_assumption_or_exceed_native_cap(self):
        b,mt,frozen,answer,target=self.qualified_sample()
        link=answer['observations'][0]['target_links'][0]
        link.update(scope_basis='assumption',scope_assumptions=[])
        args,audit=ft.proposal(b,frozen,answer,mt,qualified=True)
        self.assertEqual(args['nodes'][0]['target_links'],[])
        self.assertIn('nonempty assumption',audit['field_reviews'][0]['annotation_rejections'][0]['error'])
        link['scope_assumptions']=['Earlier source may be comparable to a future period.']
        tight=copy.deepcopy(mt);tight['function']['parameters']['properties']['nodes']['maxItems']=1
        args,audit=ft.proposal(b,frozen,answer,tight,qualified=True)
        self.assertEqual(len(args['nodes']),1)
        self.assertEqual(args['relations'],[])
        self.assertEqual(args['nodes'][0]['target_links'],[])
        self.assertIn('node cap together',audit['field_reviews'][0]['annotation_rejections'][0]['error'])

    def test_qualified_unknowns_and_annotations_are_labelled_not_certified(self):
        b,mt,frozen,answer,target=self.qualified_sample()
        answer['unknown_targets']=[{'target_id':target,'missing_condition':'Future realized revenue remains unknown.',
                                    'reason':'The delivered source metric does not establish the future outcome.'}]
        args,audit=ft.proposal(b,frozen,answer,mt,qualified=True)
        self.assertTrue(args['nodes'][0]['limitation'].startswith('Model limitation:'))
        self.assertTrue(args['nodes'][1]['limitation'].startswith('Model gap:'))
        self.assertEqual(audit['received_unknown_targets'],answer['unknown_targets'])
        self.assertEqual(audit['qualified_attribution_policy'],'explicit_source_rule_assumption_v1')
        self.assertFalse(audit['meaning_verified'])

    def test_flat_source_selection_keeps_multiple_fields_without_per_reference_envelopes(self):
        b, mt, frozen, answer = self.sample()
        ref = frozen['originals'][0]['evidence_id']
        raw = {'fields': [{**f, 'reference_id': ref, 'coverage': 'content_excerpt'}
                         for f in frozen['received_proposal']['sources'][0]['facts']], 'unknowns': []}
        packet = sf.registry(frozen['originals'], raw, flat=True)
        self.assertEqual(len(packet['facts']), 3)
        self.assertEqual(sf.verify_registry(packet), packet)
        self.assertEqual(packet['extraction_contract'], 'flat_fields_v1')
        self.assertEqual(packet['facts'][0]['coverage_hypotheses'], ['content_excerpt'])
        schema = sf.tool(frozen['originals'], flat=True)['function']['parameters']
        self.assertNotIn('sources', schema['properties'])
        self.assertEqual(schema['properties']['fields']['maxItems'], 48)
        message = sf.messages(None, None, frozen['originals'], original_only=True, flat=True)
        self.assertIn('no requirement to fill every fragment', message[0]['content'])
        self.assertNotIn('question', json.loads(message[1]['content']))

    def test_flat_invalid_quote_unknown_reference_and_caption_status_are_isolated(self):
        b, mt, frozen, answer = self.sample()
        ref = frozen['originals'][0]['evidence_id'];quote='Revenue was 24 billion dollars.'
        raw = {'fields': [
            {'reference_id': ref, 'kind': 'measurement', 'coverage': 'content_excerpt', 'quote': quote, 'value': '30'},
            {'reference_id': ref, 'kind': 'measurement', 'coverage': 'content_excerpt', 'quote': quote, 'value': '24'},
            {'reference_id': ref, 'kind': 'event_status', 'coverage': 'caption_text', 'quote': quote, 'value': '24'}],
            'unknowns': [{'reference_id': 'R_not_delivered', 'note': 'A nonexistent source.'}]}
        packet = sf.registry(frozen['originals'], raw, flat=True)
        self.assertEqual(len(packet['facts']), 1)
        self.assertEqual(len(packet['reading']['rejected_fields']), 3)
        self.assertEqual(packet['facts'][0]['submitted_field_indices'][0]['fact_index'], 1)
        self.assertFalse(packet['reading']['material_receipts_granted'])

    def test_flat_reference_coverage_remains_partial_not_a_reading_receipt(self):
        b, mt, frozen, answer = self.sample()
        packet = sf.registry(frozen['originals'], {'fields': [], 'unknowns': []}, flat=True)
        self.assertEqual(packet['facts'], [])
        self.assertEqual(packet['reading']['unprocessed_reference_ids'], [frozen['originals'][0]['evidence_id']])
        self.assertFalse(packet['source_field_coverage_complete'])
        self.assertFalse(packet['reading']['meaning_verified'])

    def test_heading_primary_cannot_be_rescued_by_substantive_context(self):
        b, mt, frozen, answer = self.sample()
        url = frozen['originals'][0]['url']
        b['pages'][url]['content'] = '# Revenue header\n\n' + b['pages'][url]['content']
        catalog = state.catalog(b)
        sources = [s for s in catalog['spans'].values() if s['url'] == url]
        title = next(s for s in sources if '# Revenue header' in s['text'])
        content = next(s for s in sources if 'Revenue was 24 billion dollars.' in s['text'])
        if title['evidence_id'] == content['evidence_id']:
            sources = [title]
        else:
            sources = [title, content]
        fields = {'fields': [
            {'reference_id': title['evidence_id'], 'kind': 'issuer', 'coverage': 'content_excerpt',
             'quote': '# Revenue header', 'value': 'Revenue'},
            {'reference_id': content['evidence_id'], 'kind': 'measurement', 'coverage': 'content_excerpt',
             'quote': 'Revenue was 24 billion dollars.', 'value': '24'}], 'unknowns': []}
        fresh = sf.registry(sources, fields, flat=True)
        answer['registry_sha256'] = fresh['registry_sha256']
        answer['observations'][0]['fact_id'] = fresh['facts'][0]['fact_id']
        answer['observations'][0]['context_fact_ids'] = [fresh['facts'][1]['fact_id']]
        args, audit = ft.proposal(b, fresh, answer, mt)
        self.assertIsNone(args)
        self.assertIn('heading/table separator', audit['rejected'][0]['error'])
        self.assertEqual(len(fresh['facts']), 2)
        self.assertFalse(audit['meaning_verified'])

    def test_registry_role_disagreement_preserved_for_identical_literal(self):
        b, mt, frozen, answer = self.sample()
        proposal = copy.deepcopy(frozen['received_proposal'])
        sibling = copy.deepcopy(proposal['sources'][0]['facts'][0])
        sibling['kind'] = 'event_time'
        proposal['sources'][0]['facts'].append(sibling)
        other = sf.registry(frozen['originals'], proposal)
        self.assertEqual(len(other['facts']), 3)
        self.assertEqual(other['facts'][0]['role_hypotheses'], ['measurement', 'event_time'])
        self.assertFalse(other['facts'][0]['role_verified'])
        self.assertEqual(len(other['facts'][0]['submitted_field_indices']), 2)

    def test_tampered_registry_or_native_original_fails_before_map_edit(self):
        b, mt, frozen, answer = self.sample()
        altered = copy.deepcopy(frozen); altered['facts'][0]['value'] = '30'
        with self.assertRaisesRegex(ValueError, 'registry changed'):
            ft.messages(b, altered)
        page = next(iter(b['pages'].values()))
        page['content'] += ' A changed capture.'
        with self.assertRaisesRegex(ValueError, 'native catalog'):
            ft.proposal(b, frozen, answer, mt)

    def test_unknown_field_sibling_isolated_and_prose_cannot_rewrite_quote(self):
        b, mt, frozen, answer = self.sample()
        bad = copy.deepcopy(answer['observations'][0]); bad['fact_id'] = 'L_unknown'
        answer['observations'].append(bad)
        args, audit = ft.proposal(b, frozen, answer, mt)
        self.assertEqual(len(args['nodes']), 1)
        self.assertEqual(len(audit['rejected']), 1)
        self.assertEqual(args['nodes'][0]['interpretation'], frozen['facts'][0]['quote'])
        answer['observations'][0]['quote'] = 'Revenue was 100 billion dollars.'
        args, audit = ft.proposal(b, frozen, answer, mt)
        self.assertIsNone(args)
        self.assertEqual(len(audit['rejected']), 2)

    def test_context_cannot_borrow_a_different_capture_version(self):
        b, mt, frozen, answer = self.sample()
        original = copy.deepcopy(frozen['originals'][0])
        second = {**original, 'evidence_id': 'R_other', 'body_sha256': 'f'*64}
        raw = copy.deepcopy(frozen['received_proposal'])
        extra = copy.deepcopy(raw['sources'][0]); extra['reference_id'] = 'R_other'; raw['sources'].append(extra)
        mixed = sf.registry([original, second], raw)
        # Scope guard is independently tested without admitting forged material
        # through native catalog identity checks.
        first, other = mixed['facts'][0], mixed['facts'][3]
        self.assertNotEqual(ft._source_scope(first), ft._source_scope(other))
        with self.assertRaisesRegex(ValueError, 'native catalog'):
            ft.proposal(b, mixed, {**answer, 'registry_sha256': mixed['registry_sha256']}, mt)

    def test_unknown_predicate_is_separate_gap_not_negative_observation(self):
        b, mt, frozen, answer = self.sample()
        target = target_logic.targets(b)[0]['id']
        answer['unknown_targets'] = [{'target_id': target, 'missing_condition': 'Target-period release is not observed.',
                                      'reason': 'Prior-period fields do not establish a future realization.'}]
        args, audit = ft.proposal(b, frozen, answer, mt)
        gap = args['nodes'][1]
        self.assertEqual(gap['kind'], 'unknown')
        self.assertEqual(gap['evidence_ids'], [])
        self.assertEqual(gap['target_links'][0]['effect'], 'unresolved')
        self.assertFalse(audit['material_receipts_granted'])

    def test_source_phase_hides_targets_and_relation_phase_hides_old_notes(self):
        b, mt, frozen, answer = self.sample()
        b['research_loop']['current']['nodes'][0]['interpretation'] = 'OLD_UNSUPPORTED_NOTE'
        source = sf.messages({'question': 'TARGET_ANCHOR'}, 'CLOCK_ANCHOR', frozen['originals'], original_only=True)
        data = json.loads(source[1]['content'])
        self.assertNotIn('question', data); self.assertNotIn('as_of_utc', data)
        self.assertNotIn('OLD_UNSUPPORTED_NOTE', ft.messages(b, frozen)[1]['content'])
        self.assertIn('target_conditions', json.loads(ft.messages(b, frozen)[1]['content']))

    def test_explicit_snapshot_replacement_retains_prior_note_hash_without_truth_claim(self):
        b, mt, frozen, answer = self.sample()
        args, audit = ft.proposal(b, frozen, answer, mt, replace_previous_notes=True)
        self.assertEqual(args['update_mode'], 'replace')
        self.assertEqual(args['retired_node_ids'], [n['id'] for n in b['research_loop']['current']['nodes']])
        self.assertFalse(audit['old_notes_declared_false'])
        self.assertFalse(audit['meaning_verified'])

    def test_duplicate_primary_is_not_counted_twice_and_empty_registry_needs_no_call(self):
        b, mt, frozen, answer = self.sample()
        answer['observations'] *= 2
        args, audit = ft.proposal(b, frozen, answer, mt)
        self.assertIsNone(args)
        self.assertEqual(len(audit['rejected']), 2)
        empty = sf.registry(frozen['originals'], {'sources': []})
        with self.assertRaisesRegex(ValueError, 'without a model call'):
            ft.messages(b, empty)
        with self.assertRaisesRegex(ValueError, 'bound facts'):
            ft.tool(b, empty)

    def test_native_handler_commits_batch_without_changing_original_ledgers(self):
        from ForecastAgent.tests.test_research_gap_feedback import task
        from ForecastAgent.tests.test_research_reference_map import proposal, apply
        with tempfile.TemporaryDirectory() as tmp:
            t = task(Path(tmp)/'native'); apply(t.bundle, proposal(t.bundle))
            t.bundle[post.PHASE] = True
            packet = post.node_reading_packet(t, 'observed_report')
            source = packet['evidence'][0]
            quote = 'Revenue was 24 billion dollars.'
            frozen = sf.registry([source], {'sources': [{'reference_id': source['evidence_id'],
                'coverage': 'content_excerpt', 'coverage_quote': quote,
                'facts': [{'kind': 'measurement', 'quote': quote, 'value': '24'},
                          {'kind': 'measurement_unit', 'quote': quote, 'value': 'billion dollars'}], 'unknowns': []}]})
            t.bundle['research_acquisition']['inspected_references'] = [
                {k:source[k] for k in ('evidence_id','url','body_sha256','start','end')}]
            mt = next(tool for tool in runtime.filter_tools(t, runtime.configure(t, []))
                      if tool['function']['name'] == 'update_research_state')
            answer = {'registry_sha256': frozen['registry_sha256'], 'observations': [
                {'fact_id': f['fact_id'], 'context_fact_ids': [], 'applicability': 'background',
                 'target_links': [], 'limitation': 'Literal observation; target applicability is unverified.'}
                for f in frozen['facts']], 'unknown_targets': [], 'revision_reason': 'Retain both source fields.'}
            before = copy.deepcopy(t.bundle)
            args, audit = ft.proposal(t.bundle, frozen, answer, mt, replace_previous_notes=True)
            result = runtime.execute(t, 'update_research_state', args)
            self.assertEqual(t.bundle['research_loop']['revision'], 2)
            self.assertEqual(len(t.bundle['research_loop']['current']['nodes']), 2)
            self.assertTrue(all(n['interpretation'] == quote for n in t.bundle['research_loop']['current']['nodes']))
            state.verify_journal(t.bundle['research_loop'])
            self.assertFalse(audit['meaning_verified'])
            for key in post.PRESERVED:
                self.assertEqual(t.bundle.get(key), before.get(key))
            # Native reference hydration also keeps the scope bridge as a
            # separate hypothesis, never a literal source claim.
            target=target_logic.targets(t.bundle)[0]['id']
            answer['observations'][0]['target_links']=[{'target_id':target,'role':'baseline','effect':'supports',
                'reason':'Earlier revenue may remain comparable to the target period.',
                'source_premise_fact_ids':[frozen['facts'][0]['fact_id']], 'rule_context_target_ids':[target],
                'scope_basis':'assumption','scope_assumptions':['Assume the earlier metric remains comparable in the target period.']}]
            mt = next(tool for tool in runtime.filter_tools(t, runtime.configure(t, []))
                      if tool['function']['name'] == 'update_research_state')
            args,audit=ft.proposal(t.bundle,frozen,answer,mt,replace_previous_notes=True,qualified=True)
            runtime.execute(t,'update_research_state',args)
            state.verify_journal(t.bundle['research_loop'])
            assumptions=[n for n in t.bundle['research_loop']['current']['nodes'] if n['kind']=='assumption']
            self.assertEqual(len(assumptions),1)
            self.assertEqual(assumptions[0]['claim_origin'],'hypothesis')
            self.assertEqual(assumptions[0]['bindings'],[])
            self.assertEqual(len(t.bundle['research_loop']['current']['relations']),1)
            for key in post.PRESERVED:
                self.assertEqual(t.bundle.get(key), before.get(key))

    def test_scoring_byte_limit_keeps_exact_omitted_unknown_predicate(self):
        from unittest.mock import patch
        from ForecastAgent.research_loop import decision
        b, mt, frozen, answer = self.sample()
        source = frozen['originals'][0]
        baseline = {'sources': [{'source_id': 'body', 'url': source['url'], 'body_sha256': source['body_sha256']}],
            'evidence': [{'source_id': 'body', 'start': source['start'], 'end': source['end'],
                          'text': source['text']}]}
        full, _ = decision.scoring_map(b, baseline)
        cap = len(json.dumps(full).encode()) - 1
        before = copy.deepcopy((b, baseline))
        with patch.object(decision, 'MAP_BYTE_CAP', cap):
            notes, audit = decision.scoring_map(b, baseline)
        unknown = next(n for n in b['research_loop']['current']['nodes'] if n['kind'] == 'unknown')
        self.assertIn(unknown['id'], notes['hidden_node_ids'])
        item = next(g for g in notes['omitted_unknown_predicates'] if g['node_id'] == unknown['id'])
        self.assertEqual(item['claim'], unknown['claim'])
        self.assertEqual(item['gap_reason'], unknown['gap_reason'])
        self.assertFalse(item['meaning_verified'])
        self.assertLessEqual(len(json.dumps(notes).encode()), cap)
        self.assertEqual((b, baseline), before)


if __name__ == '__main__': unittest.main()
