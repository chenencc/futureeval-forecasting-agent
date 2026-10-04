"""Review-only executions preserve consumed attempts and immutable parent data."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import save, load
from ForecastAgent.tests.test_material_gap_workflow import bundle
from ForecastAgent.experiments.material_review_only import run
from ForecastAgent.supplement import material_review


class ReviewOnlyTests(unittest.TestCase):
    def parent(self, root):
        b = bundle()
        b['pages']['https://lab.example/launch'] = {'content':
            'The evaluated LLM International Math Olympiad system is not released to the public. '*8}
        b['capacity']={'model_http_lifetime':5,'model_http_dispatch':5,'model_decisions':4,'model_failures':3}
        b['model_attempts']=[{'status':'received'}]
        b['sessions']=[{'started_at':'2020-01-01T00:00:00Z','model_decisions':1,'attempts_before':0}]
        save(root/'acquisition/bundle.json', b);save(root/'intelligence-bundle.json', b)
        save(root/'manifest.json', {'question_id':'test', 'repair_caps':{'tavily':3,'exa':1,'http':16,'browser':6}})
        save(root/'supplement/state.json', {'attempts':[],
            'material_model_attempts':[{'status':'received','file':'material-model-0001.json'}],
            'material_reviews':[{'status':'failed','error_code':'output_truncated'}]})
        save(root/'supplement/material-model-0001.json', {'old':True})

    def test_exact_parent_preservation_one_decision_and_idempotence(self):
        def model(messages, key, **kw):
            token=kw['observer']('reserve', {'status':'reserved'})
            msg={'content':'{"bindings":[],"priority_source_ids":[],"deferred_source_ids":[],"next_search":null}'}
            kw['observer']('complete', {'status':'received','response':{'choices':[{'message':msg,'finish_reason':'stop'}], 'usage':{'total_tokens':100}}}, token)
            return msg
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);parent=root/'parent';out=root/'output';self.parent(parent)
            with patch('ForecastAgent.providers.model.ask_model', side_effect=model) as transport:
                result=run(parent,out,'dummy','123')
                self.assertEqual(result['status'],'bound')
                self.assertEqual(result['new_model_attempts'],1)
                self.assertEqual(result['cumulative_model_attempts'],3)
                self.assertTrue(result['parent_unchanged'])
                self.assertEqual(run(parent,out,'dummy','123'),result)
                self.assertEqual(transport.call_count,1)
            self.assertEqual(load(out/'material-model-0001.json'),{'old':True})
            self.assertEqual(len(load(out/'state.json')['material_reviews']),2)

    def test_existing_physical_cap_blocks_retry_without_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);parent=root/'parent';self.parent(parent)
            b=load(parent/'acquisition/bundle.json');b['capacity']['model_http_lifetime']=2
            save(parent/'acquisition/bundle.json',b)
            def model(messages,key,**kw):kw['observer']('reserve',{'status':'reserved'})
            with patch('ForecastAgent.providers.model.ask_model',side_effect=model):
                result=run(parent,root/'output','dummy','123')
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['new_model_attempts'],0)
            self.assertFalse(result['budget_reset'])


if __name__ == '__main__':unittest.main()
