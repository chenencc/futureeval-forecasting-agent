import copy
import unittest

from ForecastAgent.engineering.common import digest
from ForecastAgent.engineering.contracts import validate, ContractError
from .fixtures import packet, CLOCK


class GraphAndReceipt(unittest.TestCase):
    def graph(self):
        p=packet();source=p['sources'][0]
        return p,{'schema':'forecast-research-graph-v1','question_key':p['question']['key'],'input_sha256':digest(p),
            'nodes':[{'id':'fact','kind':'observed_claim','statement':'Recorded pre-event observations.',
                      'references':[{'source_id':source['id'],'body_sha256':source['body_sha256'],'quote':source['text'][:40]}]},
                     {'id':'condition','kind':'condition','statement':'A later event must occur.','references':[]}],
            'edges':[{'from':'fact','to':'condition','relation':'supports'}]}

    def test_graph_exact_original_binding_and_hypothesis_types(self):
        p,g=self.graph();validate('graph',g,packet=p)
        self.assertEqual(g['nodes'][1]['kind'],'condition')

    def test_graph_cannot_use_missing_quote_or_other_body(self):
        for change in ('body_sha256','quote','source_id'):
            p,g=self.graph();g['nodes'][0]['references'][0][change]='0'*64 if change=='body_sha256' else 'fabricated'
            with self.assertRaises(ContractError):validate('graph',g,packet=p)

    def test_graph_rejects_unbound_claim_duplicate_id_and_missing_endpoint(self):
        for change in ('unbound','duplicate','endpoint'):
            p,g=self.graph()
            if change=='unbound':g['nodes'][0]['references']=[]
            elif change=='duplicate':g['nodes'][1]['id']='fact'
            else:g['edges'][0]['to']='missing'
            with self.assertRaises(ContractError):validate('graph',g,packet=p)

    def test_graph_never_rebinds_to_another_packet(self):
        p,g=self.graph();p['gaps']=[{'code':'different_material'}]
        with self.assertRaises(ContractError):validate('graph',g,packet=p)

    def test_accepted_receipt_requires_exact_payload_and_confirmed_clock(self):
        candidate={'question':123,'probability_yes':.4}
        r={'schema':'forecast-delivery-receipt-v1','question_key':'metaculus:123','candidate_payload_sha256':digest(candidate),
           'status':'accepted','confirmed_at':CLOCK,'payload':candidate,'evidence_record_sha256':'a'*64}
        validate('receipt',r,candidate=candidate)
        for update in ({'confirmed_at':None},{'question_key':'metaculus:999'},{'candidate_payload_sha256':'0'*64},{'payload':{'question':123,'probability_yes':.5}}):
            with self.assertRaises(ContractError):validate('receipt',r|update,candidate=candidate)

    def test_unknown_receipt_cannot_be_promoted_without_candidate_proof(self):
        r={'schema':'forecast-delivery-receipt-v1','question_key':'metaculus:123','candidate_payload_sha256':'a'*64,
           'status':'unknown','confirmed_at':None,'payload':{},'evidence_record_sha256':'b'*64}
        validate('receipt',r)
        with self.assertRaises(ContractError):validate('receipt',r|{'status':'accepted'})
