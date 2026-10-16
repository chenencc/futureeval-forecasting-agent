"""Unknown mutations never fall back; audited observational loss stays explicit."""
import copy
import os
import unittest
from unittest.mock import patch
from ForecastAgent.infrastructure import checkpoint_recovery as recovery
from ForecastAgent.infrastructure import market_checkpoint
from ForecastAgent.market_repricing import github_state


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.repo = recovery.RECOVERY['repository']
        def run(ident, name='Market repricing research monitor'):
            return {'id': ident, 'name': name, 'head_branch': 'main', 'conclusion': 'failure',
                'head_sha': recovery.RECOVERY['lost_run_head_sha'], 'head_repository': {'full_name': self.repo}}
        self.run = run
        self.after = run(38830791500)
        self.lost = run(recovery.RECOVERY['lost_run_id'])
        self.source = run(recovery.RECOVERY['source_run_id']); self.source['conclusion'] = 'success'
        self.runs = [self.after, self.lost, self.source]
        self.artifact = {'id': recovery.RECOVERY['source_artifact_id'], 'name': 'market-repricing-v2-monitor-state',
            'digest': recovery.RECOVERY['source_artifact_digest'], 'expired': False}
        self.artifacts = {self.source['id']: [self.artifact]}
        self.jobs = {self.after['id']: [{'steps': [{'name': 'Run research monitor', 'conclusion': 'skipped'}]}],
            self.lost['id']: [{'steps': [{'name': name, 'conclusion': conclusion} for name, conclusion in
                [('Run research monitor','success'), ('Seal interrupted monitor state','success'),
                 ('Build bounded lossless monitor checkpoint','failure'), ('Publish independent monitor checkpoint','skipped')]]}]}
        self.env = patch.dict(os.environ, {'GITHUB_REPOSITORY': self.repo, 'GITHUB_RUN_ID': '99999999999'})
        self.env.start(); self.addCleanup(self.env.stop)

    def api(self, path):
        if '/workflows/' in path: return {'workflow_runs': self.runs}
        ident = int(path.split('/runs/')[1].split('/')[0])
        if '/artifacts?' in path: return {'artifacts': self.artifacts.get(ident, [])}
        if '/jobs?' in path: return {'jobs': self.jobs.get(ident, [])}
        return next(r for r in self.runs if r['id'] == ident)

    def find(self, **kwargs):
        with patch.object(github_state, 'api', self.api): return recovery.find_checkpoint('monitor', 'main', **kwargs)

    def test_exact_audited_gap_has_original_artifact_and_permanent_gap(self):
        result = self.find()
        self.assertEqual(result['id'], self.artifact['id'])
        self.assertFalse(result['_audited_recovery']['budgets_reset'])
        self.assertFalse(result['_audited_recovery']['lost_raw_observations_recovered'])
        self.assertEqual(result['_audited_recovery']['lost_report_intake_http_attempts'], 0)

    def test_unknown_new_mutation_blocks_before_any_older_artifact(self):
        self.jobs[self.after['id']][0]['steps'][0]['conclusion'] = 'success'
        with self.assertRaisesRegex(ValueError, '38830791500'): self.find()

    def test_known_run_wrong_identity_blocks(self):
        self.lost['head_sha'] = 'f'*40
        with self.assertRaisesRegex(ValueError, 'identity'): self.find()

    def test_changed_original_artifact_blocks(self):
        self.artifact['digest'] = 'sha256:'+'f'*64
        with self.assertRaisesRegex(ValueError, 'checkpoint differs'): self.find()

    def test_emergency_snapshot_is_newest_authoritative_state(self):
        emergency = dict(self.artifact, id=123, name='market-repricing-v2-monitor-emergency-state')
        self.artifacts[self.after['id']] = [emergency]
        result = self.find()
        self.assertEqual(result['id'], 123)
        self.assertNotIn('_audited_recovery', result)

    def test_ambiguous_publications_are_not_silently_chosen(self):
        self.artifacts[self.after['id']] = [self.artifact, dict(self.artifact, name='market-repricing-v2-monitor-emergency-state')]
        with self.assertRaisesRegex(ValueError, 'Ambiguous'): self.find()

    def test_explicit_failed_monitor_never_used_as_discovery_source(self):
        with self.assertRaisesRegex(ValueError, 'successfully'): self.find(exact_run=str(self.lost['id']))

    def test_more_than_fifty_thousand_index_entries_fit_bounded_envelope(self):
        row = {'sha256': 'a'*64, 'size': 0}
        files = {'state.json': row} | {f'http/{i}.json': row for i in range(50001)}
        self.assertEqual(market_checkpoint.validate_index(files), 0)
        with patch.object(market_checkpoint, 'MAX_FILES', 2):
            with self.assertRaisesRegex(ValueError, 'oversized'): market_checkpoint.validate_index(files)

    def test_capacity_preflight_stops_before_a_mutation(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'state.json').write_text('{}')
            with patch.object(market_checkpoint, 'MAX_FILES', 2), patch.object(market_checkpoint, 'RESERVED_NEW_FILES', 2):
                with self.assertRaisesRegex(ValueError, 'before new work'): market_checkpoint.preflight(folder)


if __name__ == '__main__': unittest.main()
