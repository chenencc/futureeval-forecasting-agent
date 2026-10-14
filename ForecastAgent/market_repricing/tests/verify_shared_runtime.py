"""Shared module survives engine activation and produces bounded archived views."""
import os,unittest,copy,json
from ForecastAgent import acquisition_shared
from ForecastAgent.acquisition_shared import contract
from ForecastAgent.market_repricing.runtime import activate


class SharedRuntime(unittest.TestCase):
    def test_exact_shared_module_survives_selected_engine_activation(self):
        before=contract
        activate(os.environ['MARKET_ENGINE_DIRECTORY'])
        from ForecastAgent.acquisition_shared import contract as after
        self.assertIs(before,after)
        self.assertEqual(acquisition_shared.identity()['version'],'shared-acquisition-p0-v1')

    def test_full_saved_cell_map_does_not_overflow_tool_metadata(self):
        from ForecastAgent.acquisition_shared.tool_output import compact_view
        value={'content':'[Sheet1 row1] A1=Date','sheet_cell_view':{'raw_source_sha256':'a'*64,'rows':['x'*400000]},
              'body_diagnostics':{'data_coverage':{'rendered_cells':{'month_literal_cells':[{'month':'2026-10'}]*1000}}}}
        before=copy.deepcopy(value);result=compact_view(value)
        self.assertEqual(value,before);self.assertLess(len(json.dumps(result)),5000)
        self.assertTrue(result['saved_cell_view_available'])


if __name__=='__main__':unittest.main()
