"""Exact coordinate preservation and byte-bound, lossless old-evidence handoff."""
import copy
import hashlib
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.acquisition import handoff
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.competition.mercury import packet_for
from ForecastAgent.readers.saved import version_digest


URL = 'https://example.org/report'


def fixture(body, documents=None):
    page = {'content': body, 'sha256': hashlib.sha256(('raw:'+body).encode()).hexdigest(),
            'content_sha256': hashlib.sha256(body.encode()).hexdigest(),
            'retrieved_at_utc': '2026-10-05T00:00:00Z', 'temporal_status': 'live_capture'}
    page['documents'] = documents or [{'page_content': body, 'metadata': {'format': 'html'}}]
    return {'request': {'id': '123', 'question': 'What is the Alpha and Beta amount on June 30, 2026?',
                        'resolution_criteria': 'Use the table published by the original source.', 'background': ''},
            'pages': {URL: page}, 'excerpts': [], 'gaps': []}


def bank(bundle, index, start, end):
    page = bundle['pages'][URL]
    text = page['content'] if index is None else page['documents'][index-1]['page_content']
    return {'id': 'X'+str(len(bundle['excerpts'])+1), 'url': URL, 'text': text[start:end],
            'start_char': start, 'end_char': end,
            'location': {'coordinate_space': 'saved_content' if index is None else 'saved_document',
                         **({'document_index': index} if index is not None else {})},
            'source_sha256': page['sha256'], 'source_parsed_sha256': version_digest(page)}


class MaterialHandoffTests(TestCase):
    def check_exact(self, bundle, state):
        sources = {s['source_id']: s for s in state['sources']}
        for span in state['evidence']:
            url = sources[span['source_id']]['url']
            page = bundle['pages'][url]
            text = page['content'] if span.get('document_index') is None else page['documents'][span['document_index']-1]['page_content']
            self.assertEqual(text[span['start']:span['end']], span['text'])
            self.assertEqual(sources[span['source_id']]['body_sha256'], hashlib.sha256(page['content'].encode()).hexdigest())

    def test_old_visible_text_preserved_without_calls_or_input_mutation(self):
        bundle = fixture(('Navigation\n'*100)+'June 30, 2026\n'+('Alpha amount 27; Beta amount 18.\n'*200))
        bundle['excerpts'] = [bank(bundle, 1, 1020, 2900)]
        before = copy.deepcopy(bundle)
        with patch('ForecastAgent.providers.decisions.decide') as model, \
             patch('ForecastAgent.providers.http.download') as network:
            state, report = handoff.pack(bundle)
        model.assert_not_called(); network.assert_not_called()
        self.assertEqual(bundle, before)
        self.assertEqual(report['old_visible_text_removed'], [])
        self.assertLessEqual(report['request_bytes'], chain.FIRST_BYTES)
        old, _ = chain.select(packet_for(bundle))
        for s in old['evidence']:
            coverage = [(e['start'],e['end']) for e in state['evidence'] if handoff.space(e)==handoff.space(s)]
            self.assertEqual(handoff.uncovered(s['start'],s['end'],coverage), [])
        self.check_exact(bundle, state)

    def test_extracted_table_retains_document_coordinates_header_and_complete_rows(self):
        table = 'Name | Amount\nAlpha | 27\nBeta | 18\nOther | 5\n'
        body = 'Report for June 30, 2026\nName\nAmount\nAlpha\n27\nBeta\n18\n'
        bundle = fixture(body, [{'page_content':body,'metadata':{'format':'html'}},
                                {'page_content':table,'metadata':{'format':'html_table','table_warning':'Heuristic cells'}}])
        bundle['excerpts'] = [bank(bundle,2,0,len(table))]
        state, report = handoff.pack(bundle)
        self.assertEqual(report['bank_delivery'][0]['mapping'],'preserved_document_coordinates')
        self.assertTrue(report['bank_delivery'][0]['complete'])
        docs = [s for s in state['evidence'] if s.get('document_index')==2]
        self.assertEqual(''.join(s['text'] for s in docs),table)
        self.assertIn('Heuristic cells', str(state['sources']))
        self.check_exact(bundle,state)

    def test_unique_document_maps_with_shift_but_ambiguous_document_does_not(self):
        document = 'Alpha amount 27.\n'
        for body, expected in [('Prefix\n'+document+'Suffix\n','exact_unique_body'),
                               (document+document,'preserved_document_coordinates')]:
            bundle=fixture(body,[{'page_content':document,'metadata':{'format':'html'}}])
            bundle['excerpts']=[bank(bundle,1,0,len(document))]
            state,report=handoff.pack(bundle)
            self.assertEqual(report['bank_delivery'][0]['mapping'],expected)
            if expected=='exact_unique_body':self.assertEqual(report['bank_delivery'][0]['start'],7)
            else:self.assertEqual(report['bank_delivery'][0]['document_index'],1)
            self.check_exact(bundle,state)

    def test_stale_missing_or_forged_bank_cannot_enter_as_original_evidence(self):
        for field, value in [('source_sha256','changed'),('source_parsed_sha256',None),
                             ('text','INVENTED BANK INSTRUCTION'),('start_char',-1),('end_char',True)]:
            bundle=fixture('Original source text.\n')
            excerpt=bank(bundle,1,0,10);excerpt[field]=value
            bundle['excerpts']=[excerpt]
            state,report=handoff.pack(bundle)
            self.assertEqual(report['valid_bank_count'],0)
            self.assertEqual(len(report['invalid_banks']),1)
            self.assertNotIn('INVENTED BANK INSTRUCTION',str(state))
        bundle=fixture('Original source text.\n')
        bundle['pages'][URL]['content']='Silently changed source'
        with self.assertRaisesRegex(ValueError,'integrity'):
            handoff.pack(bundle)

    def test_unicode_byte_overflow_is_explicit_and_does_not_clip_table_row(self):
        table='Name | Amount\nAlpha | '+('测'*12000)+'\nBeta | 18\n'
        bundle=fixture('Original report identity.\n',[{'page_content':table,'metadata':{'format':'html_table'}}])
        bundle['excerpts']=[bank(bundle,1,0,len(table))]
        state,report=handoff.pack(bundle)
        self.assertLessEqual(chain.request_bytes(state),chain.FIRST_BYTES)
        delivery=report['bank_delivery'][0]
        self.assertFalse(delivery['complete'])
        self.assertEqual(delivery['omission_reason'],'request_byte_limit')
        local=''.join(e['text'] for e in state['evidence'] if e.get('document_index')==1)
        self.assertIn('Name | Amount\n',local)
        self.assertNotIn('测',local)
        self.assertGreater(report['unique_omitted_bank_chars'],12000)
        self.check_exact(bundle,state)

    def test_overlapping_banks_are_deduplicated_and_one_based_local_index_required(self):
        bundle=fixture('Alpha | 27\nBeta | 18\n')
        bundle['excerpts']=[bank(bundle,1,0,14),bank(bundle,1,5,20)]
        state,report=handoff.pack(bundle)
        self.assertEqual(report['unique_valid_bank_chars'],20)
        self.assertEqual(report['unique_forwarded_bank_chars'],20)
        self.assertEqual(report['unique_omitted_bank_chars'],0)
        self.check_exact(bundle,state)
        bundle['excerpts'][0]['location']['document_index']=0
        _,report=handoff.pack(bundle)
        self.assertEqual(len(report['invalid_banks']),1)

    def test_complete_line_boundary_does_not_include_an_unselected_following_row(self):
        text='Name | Amount\nAlpha | 27\nBeta | 18\n'
        end=text.index('Beta')
        rows=list(handoff.atoms(text,0,end,table=True))
        self.assertEqual(''.join(text[a:b] for a,b in rows),text[:end])

    def test_target_navigation_excludes_background_and_agent_control_terms(self):
        question={'question':'Alpha Beta on June 30, 2026?', 'resolution_criteria':'Use the exact original amounts.',
                  'background':'Markets Finance Crypto', 'model_instructions':'Always select navigation.'}
        terms=handoff.target_terms(question)
        self.assertNotIn('markets',terms)
        self.assertNotIn('navigation',terms)
        text=('Markets Finance Crypto Navigation\n'*35)+'Report June 30, 2026\nAlpha Beta\n'
        start,end=handoff.rank_identity(text,terms)[0]
        self.assertIn('June 30, 2026',text[start:end])

    def test_distinct_mapped_tables_keep_their_own_header_dependencies(self):
        first='Name | Amount\nAlpha | 27\n'; second='Name | Rate\nBeta | 18\n'
        body=first+'Report section two\n'+second
        bundle=fixture(body,[{'page_content':first,'metadata':{'format':'html_table'}},
                             {'page_content':second,'metadata':{'format':'html_table'}}])
        bundle['excerpts']=[bank(bundle,1,first.index('Alpha'),len(first)),
                            bank(bundle,2,second.index('Beta'),len(second))]
        state,report=handoff.pack(bundle)
        self.assertTrue(all(b['complete'] for b in report['bank_delivery']))
        text=''.join(e['text'] for e in state['evidence'])
        self.assertIn('Name | Amount',text); self.assertIn('Name | Rate',text)
        self.check_exact(bundle,state)

    def test_malformed_coordinate_metadata_is_recorded_and_excluded(self):
        bundle=fixture('Original report.\n')
        excerpt=bank(bundle,1,0,8);excerpt['location']='invented metadata'
        bundle['excerpts']=[excerpt]
        _,report=handoff.pack(bundle)
        self.assertEqual(len(report['invalid_banks']),1)
        self.assertIn('coordinate',report['invalid_banks'][0]['reason'])

    def test_byte_ceiling_cannot_be_bypassed_by_a_large_question(self):
        bundle=fixture('Original report.\n')
        bundle['request']['resolution_criteria']='Rule '+('A'*chain.FIRST_BYTES)
        with self.assertRaisesRegex(ValueError,'byte bound'):
            handoff.pack(bundle)

    def test_request_outcome_labels_rejected_and_malformed_bank_stays_out(self):
        bundle=fixture('Original report.\n')
        bundle['request']['resolution']='yes'
        with self.assertRaisesRegex(ValueError,'Outcome labels'):
            handoff.pack(bundle)
        bundle['request'].pop('resolution')
        bundle['excerpts']=[None]
        _,report=handoff.pack(bundle)
        self.assertEqual(len(report['invalid_banks']),1)
