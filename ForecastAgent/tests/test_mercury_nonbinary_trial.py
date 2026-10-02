"""Check nonbinary decision schema, blind input and distribution adapters."""
import unittest
from ForecastAgent.analysis import mercury_nonbinary_trial as trial
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.analysis.pilot import load
from ForecastAgent.analysis.distributions import validate_cdf


class NonbinaryMercuryTests(unittest.TestCase):
    def test_twenty_specs_and_uniform_predictions(self):
        rows=load(trial.INPUT)
        self.assertEqual(len(rows),20)
        types={}
        for row in rows:
            types[row['type']]=types.get(row['type'],0)+1
            self.assertNotIn('resolution_display',row)
            self.assertNotIn('value',row)
            spec=trial.spec(row);questions=trial.questions(spec)
            probabilities={key:1/len(spec['criteria']) for key in spec['criteria']}
            result=trial.forecast({'answers':{'event_outcome':{'probabilities':probabilities}}},spec)
            self.assertNotIn('event_yes',questions)
            if row['type']=='multiple_choice':
                self.assertEqual(set(result['probabilities']),set(row['options']))
                self.assertAlmostEqual(sum(result['clipped_probabilities'].values()),1)
            elif row['type']=='date':
                self.assertFalse(result['payload_format_valid'])
                self.assertFalse(spec['official_metadata_available'])
            else:
                validate_cdf(result['continuous_cdf'],spec['meta'],clipped=True)
                self.assertEqual(len(result['continuous_cdf']),row['inbound_outcome_count']+1)
                self.assertAlmostEqual(result['raw_cdf'][0],probabilities.get('below',0))
                self.assertAlmostEqual(1-result['raw_cdf'][-1],probabilities.get('above',0))
        self.assertEqual(types,{'numeric':6,'multiple_choice':5,'discrete':6,'date':3})

    def test_custom_questions_are_used_in_byte_budget(self):
        row=load(trial.INPUT)[0];registry=trial.questions(trial.spec(row))
        packet={'question':trial.request(row),'evidence':[],'sources':[],'acquisition_gaps':[]}
        state,audit=chain.select(packet,decision_questions=registry)
        self.assertEqual(audit['request_bytes'],chain.request_bytes(state,registry))

    def test_rule_conflict_routes_without_binary_probability(self):
        answers={'evidence_sufficiency':{'score':3},'material_conflict':{'noul':0}}
        for key in trial.CHECKS:answers[key]={'probabilities':{'supported':1,'contradicted':0,'insufficient':0}}
        self.assertEqual(trial.route({'answers':answers}),[])
        answers['value_units']['probabilities']={'supported':0,'contradicted':.8,'insufficient':.2}
        self.assertIn('value_units',trial.route({'answers':answers}))


if __name__=='__main__':unittest.main()
