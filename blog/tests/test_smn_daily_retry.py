import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import smn_daily
from subscription_writer import load_json, save_json

OAUTH = ('Claude job failed: Failed to refresh OAuth token: another Claude Code process is refreshing it '
         'or exited mid-refresh.')


class RetryTests(unittest.TestCase):
    """Sept 29 prod shadow: an OAuth refresh race at 04:30 held APH for the whole day."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.day = smn_daily.Day(self.tmp.name, '2026-09-29')
        self.job = Path(self.tmp.name)/'jobs/APH-20260929-primary-discovery'
        self.job.mkdir(parents=True)
        save_json(self.job/'state.json', {'status': 'ready'})

    def run_with(self, outcomes):
        seq = iter(outcomes)
        def run(job, clis):
            o = next(seq)
            if isinstance(o, Exception):
                save_json(job/'state.json', {'status': 'failed_needs_review', 'reason': str(o)})
                raise o
            return o
        with patch.object(smn_daily.smn_models, 'run', side_effect=run), \
             patch.object(smn_daily.time, 'sleep') as sleep:
            return self.day.run_job(self.job), sleep

    def test_oauth_race_is_waited_out_and_not_counted(self):
        result, sleep = self.run_with([RuntimeError(OAUTH), RuntimeError(OAUTH), {'ok': 1}])
        self.assertEqual(result, {'ok': 1})
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [60, 180])
        self.assertEqual(self.day.jobs_used(), 1)
        self.assertEqual(len(list(self.job.glob('transient-attempt-*'))), 2)

    def test_other_failures_still_hold_after_two(self):
        with self.assertRaises(smn_daily.Hold):
            self.run_with([RuntimeError('Claude job failed: None'), RuntimeError('Claude job failed: None')])

    def test_rerun_releases_an_article_held_by_a_passing_fault(self):
        save_json(self.job/'state.json', {'status': 'failed_needs_review', 'reason': OAUTH})
        self.day.state['articles'] = {'APH': {'held': {'reason': OAUTH}}, 'XLK': {'held': {'reason': 'quote'}}}
        self.day.release_transient_holds()
        self.assertNotIn('held', self.day.state['articles']['APH'])
        self.assertIn('held', self.day.state['articles']['XLK'])
        self.assertEqual(load_json(self.job/'state.json')['status'], 'ready')


if __name__ == '__main__':
    unittest.main()
