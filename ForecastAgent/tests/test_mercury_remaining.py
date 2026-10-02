"""Ensure the remaining sample excludes seven cases and calls Mercury only."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis import mercury_remaining_trial as trial
from ForecastAgent.analysis.pilot import load
from ForecastAgent.providers.decisions import MODEL


class MercuryRemainingTests(unittest.TestCase):
    def test_exact_thirteen_excludes_seven(self):
        root=Path(trial.__file__).parent
        cohort=load(root/'mercury_remaining13_cohort.json')
        full=load(root/'three_route_cohort.json');seven=load(root/'error_seven_time_metadata.json')
        ids={i for batch in cohort['batches'] for i in batch}
        self.assertEqual(len(ids),13)
        self.assertEqual(ids,{i for batch in full['batches'] for i in batch}-set(seven))
        dates=load(root/'three_route_time_metadata.json')
        for ident in seven:self.assertEqual(dates[ident],seven[ident])

    def test_calls_only_mercury_with_no_analysis_or_labels_in_state(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(trial,'resolve_bundle',return_value={}), \
                patch.object(trial.referenced,'evidence_packet',return_value={'question':{}}), \
                patch.object(trial.super_direct_trial,'compact',return_value=({'question':{},'evidence':[]},{})), \
                patch.object(trial.super_direct_trial,'direct',return_value={'answers':{'event_yes':{'noul':.4}}}) as direct, \
                patch.object(trial.referenced,'run') as analysis:
            source=Path(temp)/'inputs/tasks'
            cohort=load(Path(trial.__file__).with_name('mercury_remaining13_cohort.json'))
            for ident in cohort['batches'][2]:
                folder=source/ident;folder.mkdir(parents=True);(folder/'bundle.json').write_text('{}')
            trial.run(Path(temp)/'inputs','supplements',Path(temp)/'out',3)
            analysis.assert_not_called()
            self.assertEqual(direct.call_count,3)
            for call in direct.call_args_list:
                self.assertEqual(call.args[1],MODEL)
                self.assertNotIn('resolution',call.args[0])


if __name__=='__main__':unittest.main()
