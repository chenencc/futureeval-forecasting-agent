import base64
from datetime import datetime, timezone
from io import BytesIO
import json
from unittest import TestCase
from unittest.mock import Mock, patch

from scripts.retrieval_sources import fetch_structured, allowed_source, source_urls
from scripts.ultra_research_agent import fetch_public_page


class SourceTests(TestCase):
    def test_yahoo_excludes_cutoff_day_and_future_closes(self):
        cutoff = datetime(2026, 8, 20, tzinfo=timezone.utc)
        stamps = [int(datetime(2026, 8, d, 13, tzinfo=timezone.utc).timestamp()) for d in [19,20,21]]
        data = {'chart': {'result': [{'timestamp': stamps, 'indicators': {'quote': [{'close': [5.19,5.24,5.28]}]}}]}}
        fetch = Mock(return_value={'raw_response_base64': base64.b64encode(json.dumps(data).encode()).decode()})
        page = fetch_structured('https://finance.yahoo.com/quote/%5ETYX/history', cutoff, fetch)
        self.assertEqual([r['date'] for r in page['rows']], ['2026-08-19'])
        self.assertNotIn('2026-08-21', page['content'])
        self.assertEqual(page['withheld_rows'], 2)
        self.assertIn('period2='+str(int(cutoff.timestamp())), fetch.call_args.args[0])
        self.assertIn('%5ETYX', fetch.call_args.args[0])

    def test_alfred_requires_confirmed_vintage_and_filters_dates(self):
        cutoff = datetime(2026, 8, 20, tzinfo=timezone.utc)
        raw = b'observation_date,DGS30_2026-08-19\n2026-08-18,5.2\n2026-08-21,5.4\n'
        fetch = Mock(return_value={'raw_response_base64': base64.b64encode(raw).decode()})
        page = fetch_structured('https://fred.stlouisfed.org/series/DGS30', cutoff, fetch)
        self.assertEqual(len(page['rows']), 1)
        self.assertIn('vintage_date=2026-08-19', fetch.call_args.args[0])
        fetch.return_value = {'raw_response_base64': base64.b64encode(b'observation_date,DGS30\n2026-08-18,5.2\n').decode()}
        with self.assertRaisesRegex(ValueError, 'did not confirm'):
            fetch_structured('https://fred.stlouisfed.org/series/DGS30', cutoff, fetch)

    def test_source_urls_and_platform_credential_refusal(self):
        self.assertEqual(source_urls('[guide](https://example.org/guide.pdf)'), ['https://example.org/guide.pdf'])
        self.assertFalse(allowed_source('https://secret:token@example.org'))
        self.assertFalse(allowed_source('https://www.metaculus.com/questions/1'))
        self.assertFalse(allowed_source('file:///local'))

    @patch('scripts.ultra_research_agent.public_url', return_value=True)
    @patch('scripts.ultra_research_agent.build_opener')
    def test_direct_page_discovers_real_relative_links_and_raw_snapshot(self, opener, public):
        raw = b'<html><p>Actual source text for an announcement.</p><a href="/filing.pdf">Filing</a><a href="javascript:evil()">No</a></html>'
        response = Mock()
        response.geturl.return_value = 'https://example.org/news'
        response.headers.get_content_type.return_value = 'text/html'
        response.headers.get_content_charset.return_value = 'utf-8'
        response.read.return_value = raw
        opener.return_value.open.return_value.__enter__.return_value = response
        page = fetch_public_page('https://example.org/news')
        self.assertEqual(page['links'], ['https://example.org/filing.pdf'])
        self.assertEqual(base64.b64decode(page['raw_response_base64']), raw)

    @patch('scripts.ultra_research_agent.public_url', return_value=False)
    @patch('scripts.ultra_research_agent.build_opener')
    def test_untrusted_public_check_fails_before_fetch(self, opener, public):
        with self.assertRaises(ValueError):
            fetch_public_page('http://127.0.0.1/private')
        opener.assert_not_called()

    @patch('scripts.ultra_research_agent.public_url', return_value=True)
    @patch('scripts.ultra_research_agent.build_opener')
    def test_pdf_saved_with_page_numbers(self, opener, public):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject('/Type'):NameObject('/Font'), NameObject('/Subtype'):NameObject('/Type1'), NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream = DecodedStreamObject(); stream.set_data(b'BT /F1 12 Tf 72 720 Td (Letter price 1.80 GBP) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        buffer = BytesIO(); writer.write(buffer)
        response = Mock()
        response.geturl.return_value = 'https://example.org/guide.pdf'
        response.headers.get_content_type.return_value = 'application/pdf'
        response.headers.get_content_charset.return_value = None
        response.read.return_value = buffer.getvalue()
        opener.return_value.open.return_value.__enter__.return_value = response
        fetched = fetch_public_page('https://example.org/guide.pdf')
        self.assertIn('[PDF page 1]', fetched['content'])
        self.assertIn('Letter price 1.80 GBP', fetched['content'])
        self.assertEqual(fetched['capture_method'], 'pdf_text')
