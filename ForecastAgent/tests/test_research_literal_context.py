"""Context windows preserve original identity, visibility and native binding."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.research_loop import literal_context as context, state, source_review


class LiteralContextTests(unittest.TestCase):
    def fixture(self, root):
        from ForecastAgent.tests.test_research_source_window import SourceWindowTests
        return SourceWindowTests().fixture(root)

    def test_additive_identity_and_literal_native_binding(self):
        with tempfile.TemporaryDirectory() as root:
            task, helper, _, _ = self.fixture(root)
            before = state.catalog(task.bundle)
            task.bundle['request'][context.FIELD] = context.POLICY
            after = state.catalog(task.bundle)
            self.assertEqual(before['sources'], after['sources'])
            self.assertNotEqual(before['material_sha256'], after['material_sha256'])
            for ident, span in before['spans'].items():
                self.assertEqual(after['spans'][ident], span)
            grouped = [s for s in after['spans'].values() if s.get('reference_kind')]
            self.assertTrue(grouped)
            for span in grouped:
                body = task.bundle['pages'][span['url']]['content']
                self.assertEqual(span['text'], body[span['start']:span['end']])
                self.assertLessEqual(len(span['text']), context.MAX_CHARS)
                self.assertLessEqual(len(span['constituent_reference_ids']), context.MAX_PARTS)
                self.assertFalse(span['meaning_verified'])
            selected = next(s for s in grouped if 'Minister 19' in s['text'])
            self.assertIn('Office 19', selected['text'])
            self.assertIn('Department 19', selected['text'])
            captured = []
            def mutate(args, packet, index):
                if packet['scheduled_source']['url'] == selected['url']:
                    args['material_reviews'][0]['evidence_ids'] = [selected['evidence_id']]
            from ForecastAgent.research_loop import post_supplement as post
            task.bundle['request_hash'] = hashlib.sha256(
                json.dumps(task.bundle['request'], sort_keys=True).encode()).hexdigest()
            frozen = copy.deepcopy(task.bundle)
            with patch.dict('os.environ', {'OPENROUTER_API_KEY':'fixture'}), \
                 patch.object(post, 'ask_model', side_effect=helper.model(captured, ['irrelevant'], mutate)), \
                 patch('urllib.request.urlopen', side_effect=AssertionError('No network')):
                child, report = post.run(frozen, Path(root)/'post', http_cap=1)
            self.assertTrue(report['steps'][0]['accepted'])
            for field in post.PRESERVED:
                self.assertEqual(child.get(field), frozen.get(field))
            self.assertIn('separate observation',
                (Path(root)/'post/sources'/report['steps'][0]['material_id']/'request-input.json').read_text())

    def test_hidden_inline_image_and_disjoint_visible_scope_never_expand(self):
        with tempfile.TemporaryDirectory() as root:
            task, _, _, _ = self.fixture(root)
            from ForecastAgent.research_loop import reading_views
            task.bundle['request'].update({context.FIELD:context.POLICY,
                                         reading_views.FIELD:reading_views.POLICY})
            url = 'https://example.org/a'
            body = 'Office A\n\nMinister A\n\ndata:image/png;base64,AAAA\n\nOffice B\n\nMinister B\n\n' + 'Readable public information. '*15
            task.bundle['pages'][url]['content'] = body
            material = state.catalog(task.bundle)
            windows = [s for s in material['spans'].values() if s['url']==url and s.get('reference_kind')]
            self.assertTrue(windows)
            self.assertTrue(all('data:image' not in s['text'] for s in windows))
            a = body.index('Minister A'); b = body.index('Office B')
            task.bundle['research_map_visible_references'] = [
                {'url':url, 'body_sha256':material['sources'][url]['body_sha256'], 'start':a, 'end':a+9},
                {'url':url, 'body_sha256':material['sources'][url]['body_sha256'], 'start':b, 'end':b+8}]
            visible = state.catalog(task.bundle)
            self.assertFalse(any(s.get('reference_kind') for s in visible['spans'].values()))

    def test_decoded_post_context_preserves_view_provenance_and_post_boundary(self):
        from ForecastAgent.research_loop import reading_views
        with tempfile.TemporaryDirectory() as root:
            task, _, _, _ = self.fixture(root)
            task.bundle['request'].update({context.FIELD:context.POLICY,
                                         reading_views.FIELD:reading_views.POLICY})
            url = 'https://example.org/a'
            body = json.dumps({'post_stream':{'posts':[
                {'created_at':'2026-10-01', 'cooked':'<p>Office A</p><p>Minister A</p><p>'+'Public source details. '*15+'</p>'},
                {'created_at':'2026-10-02', 'cooked':'<p>Office B</p><p>Minister B</p><p>'+'Independent details. '*15+'</p>'}]}})
            task.bundle['pages'][url]['content'] = body
            view = reading_views.reading(body, url)
            spans = [s for s in state.catalog(task.bundle)['spans'].values() if s['url']==url and s.get('reference_kind')]
            self.assertTrue(spans)
            for span in spans:
                self.assertEqual(span['coordinate_space'], 'decoded_post_view')
                self.assertEqual(span['text'], view['text'][span['start']:span['end']])
                self.assertEqual(len(span['json_provenance']), 1)
                self.assertFalse('Office A' in span['text'] and 'Office B' in span['text'])

    def test_unknown_policy_and_stale_coordinates_fail_without_mutation(self):
        with tempfile.TemporaryDirectory() as root:
            task, _, _, _ = self.fixture(root)
            task.bundle['request'][context.FIELD] = 'invented'
            frozen = copy.deepcopy(task.bundle)
            with self.assertRaisesRegex(ValueError, 'Unknown literal context'):
                state.catalog(task.bundle)
            self.assertEqual(task.bundle, frozen)
        with self.assertRaisesRegex(ValueError, 'exact current reading coordinates'):
            context.additions([{'start':0, 'end':3, 'text':'bad'}], 'old')

    def test_prior_material_receipt_does_not_credit_new_context_scope(self):
        from ForecastAgent.research_loop import gap_feedback
        from ForecastAgent.research_loop import post_supplement as post
        with tempfile.TemporaryDirectory() as root:
            task, helper, _, _ = self.fixture(root)
            with patch.dict('os.environ', {'OPENROUTER_API_KEY':'fixture'}), \
                 patch.object(post, 'ask_model', side_effect=helper.model([], ['irrelevant'])):
                child, report = post.run(task.bundle, Path(root)/'post', http_cap=1)
            task.bundle = child
            old_materials = gap_feedback.materials(task)
            old_events = copy.deepcopy(task.bundle['research_gap_feedback']['events'])
            old_ids = {m['material_id'] for m in gap_feedback.pending(task)}
            processed = report['steps'][0]['material_id']
            self.assertNotIn(processed, old_ids)
            task.bundle['request'][context.FIELD] = context.POLICY
            current = gap_feedback.materials(task)
            replacement = next(m['material_id'] for m in current.values()
                               if m['url'] == old_materials[processed]['url'])
            self.assertNotEqual(replacement, processed)
            self.assertIn(replacement, {m['material_id'] for m in gap_feedback.pending(task)})
            self.assertEqual(task.bundle['research_gap_feedback']['events'], old_events)

    def test_long_primitive_is_not_clipped_or_certified_as_context(self):
        text = 'a'*1500 + '\n\nName\n\nOffice\n'
        spans = []
        for index, (a,b) in enumerate([(0,1500),(1502,1507),(1508,len(text))]):
            spans.append({'source_id':'x', 'url':'x', 'body_sha256':'hash',
                          'evidence_id':f'R{index}', 'start':a, 'end':b, 'text':text[a:b]})
        original = copy.deepcopy(spans)
        result = context.additions(spans, text)
        self.assertEqual(spans, original)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['text'], text[1502:])

    def test_context_delivery_preserves_literal_coverage_without_alias_repetition(self):
        from ForecastAgent.research_loop import dispatch, gap_feedback
        with tempfile.TemporaryDirectory() as root:
            task, _, _, _ = self.fixture(root)
            primitive = state.catalog(task.bundle)
            task.bundle['request'][context.FIELD] = context.POLICY
            material = state.catalog(task.bundle)
            url = 'https://example.org/a'
            reading = task.execute('inspect_research_state', {'url':url,'limit':3}, '')
            source = next(m for m in gap_feedback.pending(task) if m['url']==url)
            choice = {'url':url,'material_id':source['material_id'],
                      'phase':'process_update','tool':'update_research_state',
                      'revision':task.bundle['research_loop']['revision'],
                      'material_sha256':material['material_sha256']}
            view = source_review.source_window(task, reading, choice)
            self.assertTrue(view['complete_saved_scope'])
            self.assertGreater(view['redundant_primitive_references_omitted'], 0)
            self.assertLess(len(view['rows']), view['total_native_catalog_spans'])
            rows = sorted(view['rows'], key=lambda row:row[1])
            for left, right in zip(rows,rows[1:]):
                self.assertLessEqual(left[2],right[1])
            for span in primitive['spans'].values():
                if span['url']==url:
                    self.assertTrue(any(row[1]<=span['start'] and span['end']<=row[2] for row in rows))
            messages = source_review.messages(task, reading, choice, source_view=view)
            dispatch.validate_delivery(task, messages)
            # Existing aliases stay valid historically, but unseen aliases do
            # not become eligible selections merely because a parent was read.
            ids = {row[0] for row in rows}
            ids.update(dispatch.review_scope(task,choice)['inspected_reference_ids'])
            unseen = next(s['evidence_id'] for s in primitive['spans'].values()
                          if s['url']==url and 'Minister 19' in s['text'] and s['evidence_id'] not in ids)
            with self.assertRaisesRegex(ValueError, 'not delivered'):
                source_review.bind_source_window(task,choice,
                    {'nodes':[], 'material_reviews':[{'evidence_ids':[unseen]}]},
                    source_review.review_tools(task,choice,navigate=False,source_view=view),view)


if __name__ == '__main__':
    unittest.main()
