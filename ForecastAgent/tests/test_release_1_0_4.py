"""Production evidence delivery and inherited durable worker contracts."""
import copy
import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.tests import test_release_1_0_2 as base, test_release_1_0_3 as prior
from ForecastAgent.tests.test_competition_mercury import bundle
from ForecastAgent.releases import v1_0_4 as release, context_decisions as context, stress
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.acquisition.handoff_replay import audit_spans
from ForecastAgent.analysis.pilot import load


class ProductionReleaseTests(prior.ProductionReleaseTests):
    def setUp(self):
        base.ReleaseTests.setUp(self)
        for target in (base,prior,stress):
            binding=patch.object(target,'release',release)
            binding.start();self.addCleanup(binding.stop)


class EvidenceDeliveryTests(TestCase):
    def test_packing_failure_retains_exact_release_input_without_network(self):
        raw=bundle();registry=chain.questions()
        expected,_=context.delivery.baseline(raw,registry)
        with patch.object(context.delivery,'pack',side_effect=ValueError('Packing failure')):
            first,audit=context.select_first(raw,registry)
        self.assertEqual(first,expected)
        self.assertEqual(audit['mode'],'release_selector_fallback')
        self.assertEqual(audit_spans(raw,first),[])

    def test_corrupted_packing_is_rejected_and_original_body_is_unchanged(self):
        raw=bundle();before=copy.deepcopy(raw);registry=chain.questions()
        bad,_=context.delivery.pack(raw,registry)
        bad['evidence'][0]['text']='A fabricated observation'
        with patch.object(context.delivery,'pack',return_value=(bad,{})):
            first,audit=context.select_first(raw,registry)
        self.assertEqual(audit['mode'],'release_selector_fallback')
        self.assertEqual(raw,before)
        self.assertEqual(audit_spans(raw,first),[])

    def test_second_projection_failure_preserves_valid_first(self):
        raw=bundle(long=True);registry=chain.questions()
        first,_=context.select_first(raw,registry)
        with patch.object(context.delivery,'extend',side_effect=ValueError('Bad second projection')), \
             patch.object(context.chain,'select',side_effect=ValueError('Bad fallback')):
            second,audit=context.select_second(raw,first,['time_window'],registry)
        self.assertEqual(first,second)
        self.assertEqual(context.delivery.novel_chars(first,second),0)
        self.assertEqual(audit['mode'],'first_read_preserved')

    def test_compact_sources_render_valid_citations_for_all_five_formats(self):
        for kind in stress.KINDS:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                with patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}), \
                     patch('ForecastAgent.providers.decisions.decide',stress.decide):
                    result=context.run(bundle(kind),root)
                    again=context.run(bundle(kind),root)
                self.assertEqual(result,again)
                first=load(root/'first-state.json')
                self.assertEqual(audit_spans(bundle(kind),first),[])
                for span in first['evidence']:
                    source=next(s for s in first['sources'] if s['source_id']==span['source_id'])
                    self.assertIn(source['url'],result['comment'])
                release.validate_payload(bundle(kind)['request'],result['payload'])
