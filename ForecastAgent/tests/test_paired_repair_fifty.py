"""Skip excluded cases, reject policy drift and verify paired metric arithmetic."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis import repaired_fifty as replay
from ForecastAgent.analysis import paired_repair_fifty as pairing
from ForecastAgent.analysis.pilot import save
from ForecastAgent.analysis.ensemble import metrics


class PairingTests(unittest.TestCase):
    def test_before_skips_two_without_model_calls(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);ids=[str(i) for i in range(1,49)]+['43900','44799']
            for ident in ids:
                save(root/'input/handoffs'/ident/'analysis-input.json',{'request':{'id':ident},'pages':{}})
            with patch.object(replay.chain,'run_task',return_value={'status':'prepared'}) as model:
                rows=replay.run(root/'input',root/'output',9,dry_run=True,variant='before')
                self.assertEqual(model.call_count,3)
                self.assertEqual([r['id'] for r in rows if r['status']=='skipped_by_user'],['43900','44799'])

    def fixture(self,root):
        ids=[str(i) for i in range(1,49)]+['43900','44799']
        manifest={'all_ids':ids,'chain_sha256':'fixed','chain_protocol':'fixed','model':'mercury','physical_http_cap_per_task':2}
        for variant in ('old','new'):
            for b in range(10):save(root/variant/f'repaired-fifty-analysis-{b}/manifest.json',manifest)
        old=[];new=[]
        for ident,y,p,q in [('1',1,.2,.8),('2',0,.1,.9)]:
            before={'request':{'id':ident},'pages':{'https://example.org/base':{'content':'Exact original'}}}
            after={'request':before['request'],'pages':dict(before['pages'])}
            if ident=='1':after['pages']['https://example.org/addition']={'content':'Additional original'}
            save(root/'old/repaired-fifty-analysis-0/tasks'/ident/'analysis-input.json',before)
            save(root/'new/repaired-fifty-analysis-0/tasks'/ident/'analysis-input.json',after)
            old.append({'id':ident,'resolution':y,'clipped_probability_yes':p,**metrics(p,y)})
            new.append({'id':ident,'resolution':y,'clipped_probability_yes':q,**metrics(q,y)})
        for variant,rows in [('old',old),('new',new)]:
            save(root/(variant+'.json'),{'labels_sha256':'same','results':rows,'http_attempts':2,'known_tokens':100})

    def test_paired_groups_and_transitions(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root)
            report=pairing.compare(root/'old',root/'new',root/'old.json',root/'new.json',root/'out')
            self.assertAlmostEqual(report['overall']['mean_brier_change'],.1)
            self.assertEqual(report['overall']['wrong_to_correct'],1)
            self.assertEqual(report['overall']['correct_to_wrong'],1)
            self.assertAlmostEqual(report['added_body_group']['mean_brier_change'],-.6)
            self.assertEqual(report['no_added_body_group']['before']['n'],1)

    def test_policy_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root)
            path=root/'new/repaired-fifty-analysis-0/manifest.json'
            save(path,{**replay.load(path),'chain_sha256':'changed'})
            with self.assertRaisesRegex(ValueError,'policy'):
                pairing.compare(root/'old',root/'new',root/'old.json',root/'new.json',root/'out')


if __name__=='__main__':unittest.main()
