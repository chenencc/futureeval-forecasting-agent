"""Supplement transport URLs remain readable without changing their source text."""
import copy
import unittest
from ForecastAgent.readers.saved import select

class SavedURLTests(unittest.TestCase):
    def test_unambiguous_alias_reads_original_body_and_coordinates(self):
        pages={'https://example.org/history/':{'content':'Date | Close\nOct 8 | 12213.60'}}
        before=copy.deepcopy(pages)
        page,text,meta=select(pages,'https://example.org/history')
        self.assertIs(page,pages['https://example.org/history/'])
        self.assertEqual(text,before['https://example.org/history/']['content'])
        self.assertEqual(meta,{'coordinate_space':'saved_content'})
        self.assertEqual(pages,before)

    def test_exact_source_wins_but_ambiguous_alias_and_absent_source_are_rejected(self):
        pages={'https://example.org/history/?utm_source=a':{'content':'Version A'},
               'https://example.org/history/?utm_source=b':{'content':'Version B'}}
        self.assertEqual(select(pages,'https://example.org/history/?utm_source=a')[1],'Version A')
        with self.assertRaisesRegex(ValueError,'Ambiguous'):select(pages,'https://example.org/history')
        with self.assertRaisesRegex(ValueError,'already saved'):select(pages,'https://example.org/absent')
