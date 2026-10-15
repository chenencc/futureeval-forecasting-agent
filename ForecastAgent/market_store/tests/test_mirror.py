"""A failed domain must not starve the other local mirror or report success."""
import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ForecastAgent.market_store.cli import mirror


class MirrorTests(unittest.TestCase):
    def run_mirror(self, market_error=None, competition_error=None):
        with TemporaryDirectory() as folder:
            root=Path(folder)/'markets/store'
            root.mkdir(parents=True)
            (root/'competition-policy.json').write_text('{"enabled":true}')
            with patch('ForecastAgent.market_store.cli.Archives') as archives, \
                 patch('ForecastAgent.market_store.cli.import_catalog',side_effect=market_error,return_value={}), \
                 patch('ForecastAgent.market_store.local_revisions.import_revisions',return_value={}), \
                 patch('ForecastAgent.pipeline_ops.audit',return_value={'status':'ok'}), \
                 patch('ForecastAgent.competition_store.ingest.sync',side_effect=competition_error,
                       return_value={'status':'ok','finished_at':'now','stats':{}}) as competition, \
                 contextlib.redirect_stdout(io.StringIO()):
                archives.return_value.pending_history.return_value=([],[])
                if market_error or competition_error:
                    with self.assertRaisesRegex(RuntimeError,'incomplete'):
                        mirror(SimpleNamespace(root=root))
                else:
                    mirror(SimpleNamespace(root=root))
                competition.assert_called_once()
            return json.loads((root/'latest-sync.json').read_bytes())

    def test_market_failure_does_not_prevent_competition_ingestion(self):
        report=self.run_mirror(market_error=ValueError('history collision'))
        self.assertEqual(report['competition']['status'],'ok')
        self.assertEqual(report['market_status'],'failed')
        self.assertEqual(report['status'],'partial_failure')

    def test_competition_failure_does_not_invalidate_market_commit(self):
        report=self.run_mirror(competition_error=ValueError('missing catalog'))
        self.assertEqual(report['market_status'],'ok')
        self.assertEqual(report['competition']['status'],'failed')
        self.assertEqual(report['status'],'partial_failure')

    def test_both_domains_must_succeed_for_overall_success(self):
        report=self.run_mirror()
        self.assertEqual(report['status'],'ok')
        self.assertEqual(report['market_status'],'ok')


if __name__=='__main__': unittest.main()
