"""Held-out cohort isolation and preservation gates."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import save,load
from ForecastAgent.experiments import material_heldout
from ForecastAgent.tests.test_material_gap_workflow import bundle


class HeldoutTests(unittest.TestCase):
    def test_manifest_excludes_all_repair_cases_and_fixes_ten_material_checklists(self):
        m=load(material_heldout.MANIFEST)
        self.assertEqual(len(m['cases']),10)
        self.assertEqual(len({r['id'] for r in m['cases']}),10)
        self.assertFalse(set(m['excluded_ids']) & {r['id'] for r in m['cases']})
        self.assertTrue(all(len(r['required_materials'])==2 for r in m['cases']))
        with tempfile.TemporaryDirectory() as tmp:
            for r in m['cases']:
                restored=material_heldout.restore(r['case'],tmp)
                self.assertEqual(hashlib.sha256(restored.read_bytes()).hexdigest(),r['sha256'])

    def test_prepare_preserves_provider_counts_and_does_not_invent_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);parent=root/'download';output=root/'out'
            b=bundle();b['request']['id']='test';b['model_attempts']=[{'status':'received'}]*5
            b['sessions']=[{'model_decisions':5}]
            save(parent/'tasks/test/bundle.json',b)
            source=parent/'tasks/test/bundle.json'
            manifest={'excluded_ids':[], 'cases':[{'case':1,'id':'test','path':'tasks/test/bundle.json',
                'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'required_materials':['a','b']}]}
            fake=root/'manifest.json';save(fake,manifest)
            with patch.object(material_heldout,'MANIFEST',fake):
                selected,prepared,result=material_heldout.prepare(1,parent,output)
            restored=load(prepared/'acquisition/bundle.json')
            self.assertEqual(restored,b)
            self.assertNotIn('capacity',restored)
            self.assertEqual(load(output/'offline.json')['remaining_decisions'],7)
            with patch.object(material_heldout,'MANIFEST',fake):
                with self.assertRaisesRegex(ValueError,'continuation audit'):
                    material_heldout.prepare(1,parent,output)


if __name__=='__main__':unittest.main()
