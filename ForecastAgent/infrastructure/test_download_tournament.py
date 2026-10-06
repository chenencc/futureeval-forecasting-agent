"""Verify bounded archive pagination, full group reads and fixed round identity."""
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from ForecastAgent.infrastructure.download_tournament import download, save, validate_url


def post(ident, children=None, project=33129):
    result = {'id': ident, 'title': 'Question', 'curation_status': 'approved',
              'projects': {'default_project': {'id': project}}}
    if children is None:
        result['question'] = {'id': ident + 1000, 'type': 'binary', 'status': 'resolved',
                              'resolution': 0, 'resolution_criteria': 'Official outcome.'}
    else:
        result['group_of_questions'] = {'questions': children}
    return result


class ArchiveReader:
    def __init__(self, wrong_project=False):
        self.records = []
        self.children = [{'id': 100 + i, 'type': 'numeric', 'status': 'closed',
                          'resolution': None, 'resolution_criteria': 'Measured value.'} for i in range(5)]
        self.wrong_project = wrong_project

    def read(self, url, target):
        self.records.append({'method': 'GET', 'url': url})
        if '/projects/' in url:
            data = {'id': 33129, 'start_date': '2026-10-05T00:00:00Z',
                    'questions_count_including_subquestions': 6}
        elif '/posts/1/' in url:
            data = post(1, self.children)
        elif '/posts/2/' in url:
            data = post(2)
        else:
            params = parse_qs(urlsplit(url).query)
            scope = params.get('statuses', ['approved'])[0]
            if 'offset' in params:
                data = {'results': [], 'next': url + '&offset=200'}
            elif scope == 'approved':
                data = {'results': [post(1, self.children[:3], 99 if self.wrong_project else 33129)],
                        'next': url + '&offset=100'}
            elif scope == 'draft':
                data = {'results': [post(2)], 'next': None}
            else:
                data = {'results': [], 'next': None}
        save(target, data)
        return data


class DownloadTests(unittest.TestCase):
    def test_empty_terminal_page_full_group_and_visible_zero_resolution(self):
        with tempfile.TemporaryDirectory() as parent:
            reader = ArchiveReader()
            root = Path(parent) / 'archive'
            summary = download(reader, root, 33129, '2026-10-05', 6, pause=0)
            self.assertEqual(summary['question_count'], 6)
            self.assertEqual(summary['resolution_available_count'], 1)
            self.assertTrue(summary['matches_project_reported_count'])
            self.assertEqual(summary['scans'][0]['pages'], 2)
            self.assertTrue(all(r['method'] == 'GET' for r in reader.records))
            self.assertEqual(summary['forecasts_submitted'], 0)
            self.assertEqual(len(json.loads((root / 'questions.json').read_text())), 6)
            self.assertEqual(len(json.loads((root / 'manifest.json').read_text())['files']),
                             len([p for p in root.rglob('*') if p.is_file()]) - 1)

    def test_list_identity_mismatch_stops_before_cross_round_details(self):
        with tempfile.TemporaryDirectory() as parent:
            reader = ArchiveReader(wrong_project=True)
            with self.assertRaisesRegex(ValueError, 'another tournament'):
                download(reader, Path(parent) / 'archive', 33129, '2026-10-05', pause=0)
            self.assertFalse(any('/posts/1/' in r['url'] for r in reader.records))

    def test_existing_archive_is_preserved_and_wrong_start_is_rejected(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent)
            marker = root / 'marker.txt';marker.write_text('preserve')
            with self.assertRaisesRegex(ValueError, 'fresh'):
                download(ArchiveReader(), root, 33129, '2026-10-05', pause=0)
            self.assertEqual(marker.read_text(), 'preserve')
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                download(ArchiveReader(), root / 'new', 33129, '2026-09-21', pause=0)

    def test_credential_cannot_follow_an_external_or_write_endpoint(self):
        for url in ('https://example.com/api/posts/',
                    'http://www.metaculus.com/api/posts/',
                    'https://www.metaculus.com/api/questions/bulk-forecast-comment/'):
            with self.assertRaises(ValueError):validate_url(url)


if __name__ == '__main__':
    unittest.main()
