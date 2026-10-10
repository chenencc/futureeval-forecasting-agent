"""Saved-source integrity, local date scope and bounded complete-row delivery."""
import copy
import tempfile
import unittest
from pathlib import Path

from ForecastAgent.analysis.pilot import save, digest
from ForecastAgent.acquisition.handoff_replay import audit_spans
from ForecastAgent.research_loop import target_pack_v2 as pack, target_pack as old, forecast_brief, brief_trial
from ForecastAgent.tests.test_research_loop import source_bundle


def bundle(text, title='What will the ORION index close be in October 2026?'):
    b = source_bundle('binary')
    b['request'].update(question=title, resolution_criteria='Use https://example.org/report for the target observation.',
                        as_of_utc='2026-10-07T00:00:00Z')
    b['pages']['https://example.org/report']['content'] = text
    return b


class ContextTests(unittest.TestCase):
    def test_flat_rows_complete_with_headers_and_latest_first(self):
        text='# ORION index\nDate\nClose\nChange %\nOct 06, 2026\n123.45\n-0.56%\nOct 05, 2026\n124.00\n1.42%\n'
        b=bundle(text);heads,_=forecast_brief.registry(b['request'])
        state,audit=pack.pack(b,heads)
        visible=''.join(e['text'] for e in state['evidence'])
        self.assertIn('Date\nClose\nChange %\n',visible)
        self.assertIn('Oct 06, 2026\n123.45\n-0.56%\n',visible)
        rows=[a for a in pack.groups(text) if a['kind']=='flat_table_row']
        self.assertEqual(len(rows),2);self.assertTrue(all(r['table_structure_complete'] for r in rows))
        self.assertFalse(audit_spans(b,state));self.assertLessEqual(audit['request_bytes'],22000)

    def test_date_heading_stays_local_without_next_section(self):
        text='# October 1, 2026\n\n## ORION measurement\n\nORION close was 123 points.\n\n# March 1, 2021\n\n## Prior series\n\nDifferent baseline was 55 points.\n'
        matching=next(g for g in pack.groups(text) if 'ORION close was' in text[slice(*g['ranges'][-1])])
        visible=''.join(text[a:b] for a,b in matching['ranges'])
        self.assertIn('October 1, 2026',visible);self.assertNotIn('March 1, 2021',visible)
        self.assertNotIn('Different baseline',visible)

    def test_old_heading_not_promoted_by_future_date_in_body(self):
        text='# May 28, 2026\n\n## ORION schedule\n\nORION announced a launch on October 6, 2026.\n'
        target=old.profile({'question':'Will ORION launch in 2026?','resolution_criteria':''})
        target.update(content_terms=['orion','launch'],literal_years=[2026])
        item=next(g for g in pack.groups(text) if 'announced' in text[slice(*g['ranges'][-1])])
        sig=pack.signals(text,item,target,{'role':'other_source'},pack.date(2026,10,7))
        self.assertEqual(sig['recency_hint'],1)
        self.assertEqual(sig['literal_heading_dates'][0]['iso'],'2026-05-28')
        self.assertFalse(sig['date_scope_verified'])

    def test_dates_and_ids_alone_are_not_quantitative_observations(self):
        text='Suggested citation: ORION report 2026, data submitted up to 3 September 2026. [Source](https://example.org/123456).'
        target=old.profile({'question':'ORION report in 2026?','resolution_criteria':''})
        target.update(content_terms=['orion','report'],literal_years=[2026])
        sig=pack.signals(text,pack.groups(text)[0],target,{'role':'rule_page'},pack.date(2026,10,7))
        self.assertFalse(sig['quantified_navigation_hint'])

    def test_source_authority_cannot_bind_wrong_station(self):
        rule=['https://data.example.org/series?id=123&datum=MLLW']
        self.assertEqual(pack.source_role('https://data.example.org/series?id=999&datum=MLLW',rule),'rule_authority')
        self.assertEqual(pack.source_role('https://data.example.org/series?id=123&datum=MLLW&start=20261001',rule),'rule_page')
        self.assertEqual(pack.source_role('https://data.example.org/other',rule),'rule_authority')
        self.assertEqual(pack.source_role('https://fake-data.example.org/series?id=123',rule),'other_source')

    def test_incomplete_flat_row_is_not_certified(self):
        text='# ORION index\nDate\nClose\nChange %\nOct 06, 2026\n123.45\n-0.56%\nOct 05, 2026\n124\n1.42%\nOct 02, 2026\n125.0\n'
        rows=[g for g in pack.groups(text) if g['kind']=='flat_table_row']
        self.assertEqual(len(rows),2)
        self.assertTrue(all('Oct 02' not in ''.join(text[a:b] for a,b in g['ranges']) for g in rows))

    def test_nontable_intro_and_footer_survive_segmentation(self):
        text='ORION history is a prior baseline.\nDate\nClose\nChange %\nOct 06, 2026\n123.45\n-0.56%\nOct 05, 2026\n124\n1.42%\n\nORION range assumptions are uncertain.\n'
        prose=''.join(text[a:b] for g in pack.groups(text) if g['kind']=='prose' for a,b in g['ranges'])
        self.assertIn('prior baseline',prose);self.assertIn('range assumptions',prose)
        self.assertNotIn('123.45',prose)

    def test_question_metadata_and_outcomes_never_rank(self):
        b=bundle('ORION close was 123 points on October 6, 2026.\n');heads,_=forecast_brief.registry(b['request'])
        first,_=pack.pack(b,heads)
        b['request']['acquisition_profile']='false target repeated metadata'
        second,audit=pack.pack(b,heads)
        self.assertEqual(first['evidence'],second['evidence'])
        self.assertNotIn('false target',str(audit['target_profile']))
        b['request']['resolution']='yes'
        with self.assertRaises(ValueError):pack.pack(b,heads)

    def test_raw_bundle_rules_and_registry_preserved(self):
        b=bundle('# October 6, 2026\n\nORION close was 123 points.\n');before=copy.deepcopy(b)
        heads,_=forecast_brief.registry(b['request']);head_hash=digest(heads)
        state,audit=pack.pack(b,heads);control,_=old.pack(b,heads)
        self.assertEqual(b,before);self.assertEqual(digest(heads),head_hash)
        self.assertEqual({k:v for k,v in state.items() if k not in ('sources','evidence','context_omitted')},
                         {k:v for k,v in control.items() if k not in ('sources','evidence','context_omitted')})
        self.assertEqual(audit['model_http'],0);self.assertFalse(audit_spans(b,state))

    def test_latest_human_count_wins_space_over_old_navigation(self):
        text='# AQUILA surveillance\n\n# October 1, 2026\n\nHuman cases across 15 countries.\n\n# January 1, 2021\n\n' + ('AQUILA surveillance country case report directory.\n'*300)
        b=bundle(text,'How many countries report human cases for AQUILA in 2026?');heads,_=forecast_brief.registry(b['request'])
        state,audit=pack.pack(b,heads,limit=7000)
        self.assertIn('Human cases across 15 countries.',''.join(e['text'] for e in state['evidence']))
        self.assertLessEqual(audit['request_bytes'],7000)

    def test_explicit_wrong_saved_hash_is_rejected(self):
        b=bundle('ORION close is 123 points.');b['pages']['https://example.org/report']['content_sha256']='bad'
        heads,_=forecast_brief.registry(b['request'])
        with self.assertRaises(ValueError):pack.pack(b,heads)

    def test_frozen_selector_profile_cannot_migrate_on_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'parent.json';save(path,bundle('ORION close is 123 points.'))
            root=Path(tmp)/'trial'
            report=brief_trial.run([path],root,execute=False,max_http=3,brief_protocol='references',context_protocol='target-v2')
            self.assertEqual(report['new_http'],0)
            with self.assertRaises(ValueError):brief_trial.run([path],root,execute=False,max_http=3,brief_protocol='references')


if __name__=='__main__':unittest.main()
