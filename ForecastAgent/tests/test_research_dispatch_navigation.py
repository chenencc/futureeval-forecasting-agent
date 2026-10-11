"""Oversized discovery navigation must not starve exact rules and original reads."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ForecastAgent.research_loop import dispatch
from ForecastAgent.runtime.context import ContextProjectionError


class NavigationProjectionTests(unittest.TestCase):
    def choice(self):
        return {'phase':'capture_discovery', 'candidates': {'urls':[], 'sources':[
            {'source_id':f'L{i:02d}', 'url':f'https://official.example/2026/exact-metric/{i}',
             'title':'Exact issuer and target period ' + 'long title ' * 60,
             'published_date':'2026-10-11', 'discovery_excerpt':'original snippet ' * 40}
            for i in range(24)]}}

    def test_packs_complete_registry_without_mutating_saved_choice(self):
        task=SimpleNamespace(bundle={});choice=self.choice();original=copy.deepcopy(choice)
        attempts=[]
        def project(task, *, forced_tool, max_chars):
            attempts.append(max_chars)
            if max_chars < 22500:
                raise ContextProjectionError('Immutable rules and exact original span require 22500')
            return [{'role':'user','content':'x'*22400}]
        with patch('ForecastAgent.runtime.context.collection_context',project):
            messages=dispatch.model_context(task,choice,forced_tool='read_sources')
        packet=json.loads(messages[-1]['content'])['research_dispatch']['candidates']
        self.assertEqual(choice,original)
        self.assertEqual(packet['total_source_count'],24)
        rows=[dict(zip(packet['source_columns'],r)) for r in packet['source_rows']]
        for saved,visible in zip(original['candidates']['sources'],rows):
            for key in ('source_id','url','published_date'):
                self.assertEqual(visible[key],saved[key])
            self.assertEqual(visible['title']+saved['title'][len(visible['title']):],saved['title'])
            self.assertEqual(visible['title_omitted_chars'],len(saved['title'])-len(visible['title']))
        self.assertLessEqual(len(json.dumps(messages,ensure_ascii=False,separators=(',',':'))),28000)
        audit=task.bundle['dispatch_context_projections'][-1]
        self.assertEqual(audit['source_ids'],[s['source_id'] for s in original['candidates']['sources']])
        self.assertEqual(audit['provider_calls'],0)
        self.assertGreater(len(attempts),3)

    def test_true_immutable_overflow_still_fails_without_claiming_delivery(self):
        task=SimpleNamespace(bundle={});choice=self.choice();original=copy.deepcopy(choice)
        with patch('ForecastAgent.runtime.context.collection_context',side_effect=ContextProjectionError('too large')):
            with self.assertRaises(ContextProjectionError):dispatch.model_context(task,choice)
        self.assertEqual(choice,original)
        self.assertNotIn('dispatch_context_projections',task.bundle)

    def test_non_discovery_keeps_single_projection_and_exact_choice(self):
        task=SimpleNamespace(bundle={});choice={'phase':'process_read','url':'https://example.org/exact'}
        with patch('ForecastAgent.runtime.context.collection_context',return_value=[{'role':'user','content':'exact rules'}]) as project:
            messages=dispatch.model_context(task,choice,forced_tool='inspect_research_state')
        project.assert_called_once()
        self.assertEqual(json.loads(messages[-1]['content'])['research_dispatch'],choice)


if __name__=='__main__':unittest.main()
