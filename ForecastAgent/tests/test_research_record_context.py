"""Record delimiters retain generic fields without assigning their meaning."""
import copy
import tempfile
import unittest

from ForecastAgent.research_loop import literal_context as context, state, source_review, gap_feedback


class RecordContextTests(unittest.TestCase):
    def fixture(self, root, body):
        from ForecastAgent.tests.test_research_source_review import SourceReviewTests
        task = SourceReviewTests().fixture(root)
        url = 'https://example.org/a'
        task.bundle['pages'][url]['content'] = body
        task.bundle['request'][context.FIELD] = context.RECORD_POLICY
        return task, url

    def test_product_records_keep_preceding_fields_with_each_multiline_link(self):
        body = ('Product catalog\n\n'+'Published catalog information. '*15+'\n\n'
                'Price: 21 units\n\nInventory: 5\n\n[Alpha product\n\nSeries A](/products/alpha)\n\n'
                'Price: 42 units\n\nInventory: 9\n\n[Beta product\n\nSeries B](/products/beta)\n\n')
        with tempfile.TemporaryDirectory() as root:
            task, url = self.fixture(root, body)
            material = state.catalog(task.bundle)
            preferred = material['sources'][url]['context_delivery_references']
            records = [material['spans'][r['evidence_id']] for r in preferred if r.get('record_delimiter')]
            self.assertEqual(len(records), 2)
            alpha = next(s for s in records if '[Alpha product' in s['text'])
            beta = next(s for s in records if '[Beta product' in s['text'])
            self.assertIn('Price: 21', alpha['text']);self.assertNotIn('Price: 42',alpha['text'])
            self.assertIn('Price: 42', beta['text']);self.assertNotIn('Alpha product',beta['text'])
            self.assertTrue(all(r['record_relation_verified'] is False for r in preferred if r.get('record_delimiter')))
            for s in records:
                self.assertEqual(s['text'],body[s['start']:s['end']])

    def test_v1_aliases_remain_valid_and_record_projection_has_no_overlaps(self):
        body = ('Official personnel list.\n\n'+'Public organization details. '*12+'\n\n'+
                ''.join(f'Office {i}\n\nDepartment {i}\n\n[Person {i}\n\nConstituency {i}](/profiles/{i})\n\n'
                        for i in range(15)))
        with tempfile.TemporaryDirectory() as root:
            task, url = self.fixture(root,body)
            task.bundle['request'][context.FIELD]=context.POLICY
            prior = state.catalog(task.bundle)
            task.bundle['request'][context.FIELD]=context.RECORD_POLICY
            current = state.catalog(task.bundle)
            for ident,span in prior['spans'].items():
                self.assertEqual(span,current['spans'][ident])
            reading=task.execute('inspect_research_state',{'url':url,'limit':3},'')
            choice={'url':url,'material_id':next(m['material_id'] for m in gap_feedback.pending(task) if m['url']==url),
                    'revision':task.bundle['research_loop']['revision'],
                    'material_sha256':current['material_sha256']}
            view=source_review.source_window(task,reading,choice)
            self.assertTrue(view['complete_saved_scope'])
            rows=view['rows']
            self.assertTrue(all(a[2]<=b[1] for a,b in zip(rows,rows[1:])))
            for span in current['spans'].values():
                if span['url']==url and not span.get('reference_kind'):
                    self.assertTrue(any(r[1]<=span['start'] and span['end']<=r[2] for r in rows))
            self.assertTrue({r['evidence_id'] for r in view['record_delimiter_index']} <= {r[0] for r in rows})

    def test_single_line_links_images_and_malformed_links_use_ordinary_partition(self):
        body = ('Report content. '*20+'\n\n[Inline link](/one)\n\n'
                '![Image caption\n\nNo pixels supplied](/image)\n\n'
                '[Unclosed profile\n\nUnknown\n\nFinal paragraph.\n\n')
        with tempfile.TemporaryDirectory() as root:
            task,url=self.fixture(root,body)
            m=state.catalog(task.bundle)
            primitive=[s for s in m['spans'].values() if s['url']==url and not s.get('reference_kind')]
            old=context.additions(primitive,body)
            new=context.additions(primitive,body,policy=context.RECORD_POLICY)
            self.assertEqual(new,old)

    def test_long_intro_and_long_lines_obey_both_caps_without_clipping_primitives(self):
        body = ''.join(f'Introduction paragraph {i}. '+'Long preserved source text. '*12+'\n\n' for i in range(25))
        body += 'Office: director\n\nDepartment: research\n\n[Example individual\n\nProfile](/example)\n\n'
        body += 'x'*2200+'\n\nOther preserved paragraph.\n\n'
        with tempfile.TemporaryDirectory() as root:
            task,url=self.fixture(root,body);m=state.catalog(task.bundle)
            preferred=m['sources'][url]['context_delivery_references']
            for ref in preferred:
                s=m['spans'][ref['evidence_id']]
                self.assertLessEqual(len(s['text']),context.MAX_CHARS)
                self.assertLessEqual(len(s['constituent_reference_ids']),context.MAX_PARTS)
            record=next(m['spans'][r['evidence_id']] for r in preferred if r.get('record_delimiter'))
            self.assertIn('Office: director',record['text']);self.assertIn('Example individual',record['text'])
            self.assertEqual(record['text'],body[record['start']:record['end']])
            self.assertTrue(any(len(s['text'])>=1400 for s in m['spans'].values()
                                if s['url']==url and not s.get('reference_kind')))

    def test_hidden_gap_prevents_a_delimiter_from_joining_invisible_context(self):
        text='Office unknown\n\nSECRET OMITTED\n\n[Person\n\nArea](/profile)\n\n'
        a=text.index('[Person');b=text.index('Area')
        spans=[{'source_id':'s','url':'s','body_sha256':'h','evidence_id':'R1','start':0,'end':16,'text':text[:16]},
               {'source_id':'s','url':'s','body_sha256':'h','evidence_id':'R2','start':a,'end':b,'text':text[a:b]},
               {'source_id':'s','url':'s','body_sha256':'h','evidence_id':'R3','start':b,'end':len(text),'text':text[b:]}]
        before=copy.deepcopy(spans)
        result=context.additions(spans,text,policy=context.RECORD_POLICY)
        self.assertEqual(spans,before)
        self.assertTrue(result)
        self.assertTrue(all('SECRET' not in s['text'] and 'Office unknown' not in s['text'] for s in result))


if __name__=='__main__':unittest.main()
