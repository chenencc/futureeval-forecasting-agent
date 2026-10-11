"""Whole saved text delivery, exact selection and partial-absence safeguards."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.research_loop import source_review as review, dispatch, state, gap_feedback


class SourceWindowTests(unittest.TestCase):
    def fixture(self, root, *, long=False):
        from ForecastAgent.tests.test_research_source_review import SourceReviewTests
        helper=SourceReviewTests(); task=helper.fixture(root)
        url='https://example.org/a'
        task.bundle['pages'][url]['content']=(
            'Official roster\n\nBrowse the current roster below.\n\n' +
            ''.join(f'Office {i}\n\nMinister {i}\n\nDepartment {i}\n\n' for i in range(20)))
        if long:
            task.bundle['pages'][url]['content'] += ('Later background with many exact words. '*45+'\n\n')*60
        reading=task.execute('inspect_research_state',{'url':url,'limit':3},'')
        source=next(m for m in gap_feedback.pending(task) if m['url']==url)
        choice={'phase':'process_update','tool':'update_research_state','material_id':source['material_id'],
            'url':url,'revision':task.bundle['research_loop']['revision'],
            'material_sha256':state.catalog(task.bundle)['material_sha256']}
        return task,helper,reading,choice

    def test_small_roster_delivers_later_names_and_exact_reference_index(self):
        with tempfile.TemporaryDirectory() as root:
            task,_,reading,choice=self.fixture(root)
            before=copy.deepcopy(task.bundle); view=review.source_window(task,reading,choice)
            self.assertTrue(view['complete_saved_scope']);self.assertIsNone(view['next_span_offset'])
            self.assertGreater(len(view['rows']),len(reading['evidence']))
            self.assertTrue(any('Minister 19' in row[3] for row in view['rows']))
            material=state.catalog(task.bundle)
            for row in view['rows']:
                span=material['spans'][row[0]]
                self.assertEqual(row[:4],[span[k] for k in ('evidence_id','start','end','text')])
                excluded={'evidence_id','start','end','text','url','source_id','body_sha256'}
                self.assertEqual({**view['shared_representation_metadata'],**row[4]},
                    {k:v for k,v in span.items() if k not in excluded})
            self.assertEqual(task.bundle,before)

    def test_selection_binds_later_delivered_reference_without_search_or_fetch(self):
        with tempfile.TemporaryDirectory() as root:
            task,helper,reading,choice=self.fixture(root); view=review.source_window(task,reading,choice)
            selected=next(row[0] for row in view['rows'] if 'Minister 19' in row[3])
            self.assertNotIn(selected,dispatch.review_scope(task,choice)['inspected_reference_ids'])
            captured=[]
            def mutate(args,data,index):
                args['material_reviews'][0]['evidence_ids']=[selected]
                args['source_reading_basis']='complete_saved_scope'
            fake=helper.model(captured,['irrelevant'],mutate)
            from ForecastAgent.research_loop import post_supplement as post
            with patch.dict('os.environ',{'OPENROUTER_API_KEY':'fixture'}), \
                 patch.object(post,'ask_model',side_effect=fake), \
                 patch('urllib.request.urlopen',side_effect=AssertionError('No network')):
                child,report=post.run(task.bundle,Path(root)/'post',http_cap=1)
            self.assertTrue(report['steps'][0]['accepted'])
            self.assertEqual(report['steps'][0]['actual_http_attempts'],1)
            self.assertTrue(report['steps'][0]['source_reading']['complete_saved_scope'])
            for field in post.PRESERVED:self.assertEqual(child.get(field),task.bundle.get(field))
            proof=json.loads((Path(root)/'post/sources'/choice['material_id']/'source-delivery-binding.json').read_text())
            self.assertEqual(proof['selected_reference_ids'],[selected])
            self.assertFalse(proof['factual_arguments_rewritten']);self.assertFalse(proof['meaning_verified'])

    def test_partial_window_keeps_originals_and_hard_context_limit(self):
        with tempfile.TemporaryDirectory() as root:
            task,_,reading,choice=self.fixture(root,long=True)
            before=copy.deepcopy(task.bundle); view=review.source_window(task,reading,choice)
            self.assertFalse(view['complete_saved_scope']);self.assertGreater(view['omitted_catalog_spans'],0)
            self.assertIsNotNone(view['next_span_offset'])
            messages=review.messages(task,reading,choice,source_view=view)
            dispatch.validate_delivery(task,messages)
            self.assertEqual(json.loads(messages[1]['content'])['reading']['evidence'],reading['evidence'])
            self.assertEqual(task.bundle,before)

    def test_partial_irrelevance_is_rejected_before_native_edits_or_register_change(self):
        with tempfile.TemporaryDirectory() as root:
            task,helper,reading,choice=self.fixture(root,long=True)
            from ForecastAgent.research_loop import post_supplement as post
            before=copy.deepcopy(task.bundle['research_loop']);captured=[]
            with patch.dict('os.environ',{'OPENROUTER_API_KEY':'fixture'}), \
                 patch.object(post,'ask_model',side_effect=helper.model(captured,['irrelevant'])):
                child,report=post.run(task.bundle,Path(root)/'post',http_cap=1)
            self.assertFalse(report['steps'][0]['accepted'])
            self.assertTrue(report['steps'][0]['material_still_pending'])
            self.assertIn('cannot establish source-wide',report['steps'][0]['error'])
            self.assertEqual(child['research_loop'],before)

    def test_partial_positive_scope_mismatch_does_not_require_whole_book(self):
        with tempfile.TemporaryDirectory() as root:
            task,_,reading,choice=self.fixture(root,long=True)
            view=review.source_window(task,reading,choice)
            tools=review.review_tools(task,choice,navigate=False,source_view=view)
            args={'nodes':[], 'material_reviews':[{'disposition':'irrelevant','evidence_ids':[view['rows'][0][0]]}],
                  'source_reading_basis':'explicit_scope_mismatch'}
            native,_,proof=review.bind_source_window(task,choice,args,tools,view)
            self.assertEqual(native['material_reviews'],args['material_reviews'])
            self.assertNotIn('source_reading_basis',native)
            self.assertFalse(proof['complete_saved_scope']);self.assertFalse(proof['meaning_verified'])

    def test_unseen_stale_and_over_cap_selections_fail_atomically(self):
        with tempfile.TemporaryDirectory() as root:
            task,_,reading,choice=self.fixture(root)
            view=review.source_window(task,reading,choice)
            tools=review.review_tools(task,choice,navigate=False,source_view=view)
            original=copy.deepcopy(task.bundle)
            cases=[(choice,{'nodes':[],'material_reviews':[{'evidence_ids':['R_invented']}]}),
                ({**choice,'revision':choice['revision']+1},{'nodes':[],'material_reviews':[]}),
                (choice,{'nodes':[],'material_reviews':[{'evidence_ids':[r[0] for r in view['rows'][:17]]}]})]
            for frame,args in cases:
                with self.subTest(args=args),self.assertRaises(ValueError):
                    review.bind_source_window(task,frame,args,tools,view)
                self.assertEqual(task.bundle,original)

    def test_invalid_node_sibling_remains_native_isolated(self):
        with tempfile.TemporaryDirectory() as root:
            task,_,reading,choice=self.fixture(root)
            view=review.source_window(task,reading,choice)
            tools=review.review_tools(task,choice,navigate=False,source_view=view)
            args={'nodes':[{'id':'broken','evidence_ids':['R_invented']}],
                'material_reviews':[{'evidence_ids':[view['rows'][5][0]]}]}
            native,_,proof=review.bind_source_window(task,choice,args,tools,view)
            self.assertEqual(native['nodes'],args['nodes'])
            self.assertEqual(proof['selected_reference_ids'],[view['rows'][5][0]])

    def test_visible_scope_and_truncated_source_do_not_claim_complete_original(self):
        with tempfile.TemporaryDirectory() as root:
            task,_,reading,choice=self.fixture(root)
            task.bundle['pages'][choice['url']]['content_truncated']=True
            choice['material_sha256']=state.catalog(task.bundle)['material_sha256']
            view=review.source_window(task,reading,choice)
            self.assertTrue(view['complete_saved_scope']);self.assertTrue(view['source_truncated'])
            self.assertFalse(view['whole_original_or_website_verified'])


if __name__=='__main__':unittest.main()
