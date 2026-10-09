"""Offline scheduler recovery with exact, native-gate-checked dispositions.

The reader entry point and tick lock are isolated: these tests dispatch no jobs,
claim no real Linux lock custody, and never turn review acceptance into complete
publication. The existing fixture supplies synthetic source/review custody; the
editorial gate itself validates each disposition without a mocked pass result.
"""
import copy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

BLOG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BLOG))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import editorial_gate as gate
import production_continuity_schedule as schedule
import smn_subscription_daily
import test_essential_advisory_policy as advisory_fixtures

DAY = '2026-09-30'
NOW = datetime(2026, 9, 30, 19, 0, tzinfo=timezone.utc)


@contextmanager
def offline_tick_lock(_path):
    yield True


class TerminalAdvisoryDispositionTests(unittest.TestCase):
    def setUp(self):
        for blocked in (patch('subprocess.Popen', side_effect=AssertionError('External process forbidden')),
                        patch('socket.create_connection', side_effect=AssertionError('Network forbidden'))):
            blocked.start()
            self.addCleanup(blocked.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.edition = self.root/DAY/'chatgpt'
        self.fixture = advisory_fixtures.EssentialAdvisoryTests(
            'test_exact_coverage_presentation_reuses_original_negative_report')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        shutil.copytree(self.fixture.f.root, self.edition)
        self.fixture.f.root = self.edition
        self.fixture.f.result = self.edition/'results/SPY'
        self.fixture.f.job = self.edition/'jobs/SPY-20260930-review'
        self.fixture.result = self.fixture.f.result
        self.fixture.path = self.fixture.result/'editorial-advisory-disposition.json'
        self.result, self.disposition = self.fixture.result, self.fixture.path
        self.review_path = self.fixture.f.job/'output.json'
        self.assertEqual(self.fixture.ctx, gate.context(self.edition, 'SPY', DAY))
        self.fixture.record()
        self.disposition_bytes = self.disposition.read_bytes()
        self.negative_review_bytes = self.review_path.read_bytes()
        self.article_bytes = (self.result/'article.json').read_bytes()
        self.held = {'utc': NOW.isoformat(), 'reason': 'Retained formal coverage warning'}
        self.state_path = self.edition/'smn-daily-state.json'
        self.write(self.state_path, {'date': DAY, 'articles': {'SPY': {
            'draft': True, 'mechanical_ok': True, 'review_stage': 'review',
            'editorially_finalized': False, 'finalized': False, 'held': self.held}}})
        self.progress_path = self.root/DAY/'continuity-progress.json'
        for mocked in (patch.object(schedule, '_lock', offline_tick_lock),
                       patch.object(schedule, 'preserved_release_day', return_value=False),
                       patch.object(schedule, '_eligible', return_value=True)):
            mocked.start()
            self.addCleanup(mocked.stop)

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def terminal(self):
        fingerprint, used = schedule._fingerprint(self.root, DAY)
        prior = {'date': DAY, 'status': 'needs_attention',
                 'reason': 'No substantive progress after bounded retries; retained review hold',
                 'fingerprint': fingerprint, 'attempts': 6, 'unchanged_attempts': 6,
                 'jobs_used': used, 'max_jobs': schedule.MAX_JOBS,
                 'updated_utc': NOW.isoformat(), 'next_attempt_utc': None}
        self.write(self.progress_path, prior)
        return prior

    def assert_preserved(self):
        self.assertEqual(self.review_path.read_bytes(), self.negative_review_bytes)
        self.assertEqual((self.result/'article.json').read_bytes(), self.article_bytes)
        self.assertEqual(json.loads(self.state_path.read_text(encoding='utf-8'))
                         ['articles']['SPY']['held'], self.held)

    def test_valid_bound_disposition_wakes_reader_once_without_claiming_completion(self):
        self.disposition.unlink()
        prior = self.terminal()
        with self.assertRaisesRegex(ValueError, 'Unresolved coverage'):
            gate.verify_review(self.result, self.review_path)
        observed = []

        def saved_reader(root, day, **options):
            self.assertEqual((root, day), (self.root.resolve(), DAY))
            self.assertEqual(options, {'publish': False, 'target': 'production',
                                      'scheduled': True, 'continuity': True})
            proof = gate.verify_review(self.result, self.review_path)
            observed.append(proof)
            return {'providers': {'chatgpt': {'passed': False,
                    'reason': 'Review accepted; final article and pixel proof still pending'}}}

        incomplete = schedule.verify_generation(self.root, DAY)
        self.assertFalse(incomplete['complete'])
        with patch.object(smn_subscription_daily, 'run', side_effect=saved_reader) as reader, \
                patch.object(schedule, 'verify_generation', wraps=schedule.verify_generation) as completion:
            self.assertEqual(schedule.progress(self.root, DAY, NOW), prior)
            reader.assert_not_called()
            self.disposition.write_bytes(self.disposition_bytes)
            recovered = schedule.progress(self.root, DAY, NOW + timedelta(minutes=1))
            self.assertEqual(recovered['status'], 'pending')
            self.assertEqual(recovered['artifact_verification'], incomplete)
            self.assertEqual(recovered['recovered_from']['fingerprint'], prior['fingerprint'])
            self.assertEqual(recovered['recovered_from']['status'], 'needs_attention')
            self.assertEqual(recovered['attempts'], 7)
            self.assertNotEqual(recovered['fingerprint'], prior['fingerprint'])
            self.assertEqual(schedule.progress(self.root, DAY, NOW + timedelta(minutes=2)), recovered)
            reader.assert_called_once()
            completion.assert_called_once()
        self.assertTrue(observed[0]['passed'])
        self.assertFalse(observed[0]['original_review_passed'])
        self.assertFalse(observed[0]['advisory_disposition']['record']['human_reviewed'])
        self.assertFalse(observed[0]['advisory_disposition']['record']['is_model_receipt'])
        self.assert_preserved()

    def test_unchanged_valid_disposition_does_not_restart_a_terminal_reader(self):
        self.assertTrue(gate.verify_review(self.result, self.review_path)['passed'])
        prior = self.terminal()
        with patch.object(smn_subscription_daily, 'run') as reader, \
                patch.object(schedule, 'verify_generation') as completion:
            for minutes in (1, 20, 90):
                self.assertEqual(schedule.progress(self.root, DAY, NOW + timedelta(minutes=minutes)), prior)
            reader.assert_not_called()
            completion.assert_not_called()
        self.assert_preserved()

    def test_invalid_binding_wakes_recheck_but_native_gate_and_completion_stay_held(self):
        self.disposition.unlink()
        prior = self.terminal()
        invalid = json.loads(self.disposition_bytes)
        invalid['article_sha256'] = 'changed-article-binding'
        self.write(self.disposition, invalid)
        with self.assertRaises(ValueError):
            gate.verify_review(self.result, self.review_path)

        def rejected_reader(*_args, **_kwargs):
            gate.verify_review(self.result, self.review_path)
            self.fail('An invalid disposition cannot clear the native review hold')

        with patch.object(smn_subscription_daily, 'run', side_effect=rejected_reader) as reader, \
                patch.object(schedule, 'verify_generation') as completion:
            rejected = schedule.progress(self.root, DAY, NOW + timedelta(minutes=1))
            self.assertEqual(rejected['status'], 'pending')
            self.assertEqual(rejected['artifact_verification'], {'complete': False})
            self.assertIn('Advisory disposition', rejected['reason'])
            self.assertEqual(rejected['recovered_from']['fingerprint'], prior['fingerprint'])
            self.assertEqual(schedule.progress(self.root, DAY, NOW + timedelta(minutes=2)), rejected)
            reader.assert_called_once()
            completion.assert_not_called()
        self.assert_preserved()

    def test_exact_disposition_add_edit_and_removal_are_durable_changes(self):
        self.disposition.unlink()
        missing, used = schedule._fingerprint(self.root, DAY)
        self.disposition.write_bytes(self.disposition_bytes)
        added, added_used = schedule._fingerprint(self.root, DAY)
        changed = copy.deepcopy(json.loads(self.disposition_bytes))
        changed['issues'][0]['reason'] += ' Additional independent explanation.'
        self.write(self.disposition, changed)
        edited, edited_used = schedule._fingerprint(self.root, DAY)
        self.assertTrue(gate.verify_review(self.result, self.review_path)['passed'])
        self.disposition.unlink()
        removed, removed_used = schedule._fingerprint(self.root, DAY)
        self.assertEqual(len({missing, added, edited}), 3)
        self.assertEqual(removed, missing)
        self.assertEqual((used, added_used, edited_used, removed_used), (1, 1, 1, 1))
        self.assert_preserved()

    def test_timestamp_log_and_hold_prose_changes_do_not_wake_terminal_recovery(self):
        prior = self.terminal()
        state = json.loads(self.state_path.read_text(encoding='utf-8'))
        state['updated_utc'] = (NOW + timedelta(minutes=1)).isoformat()
        state['articles']['SPY']['updated_utc'] = state['updated_utc']
        state['articles']['SPY']['held'] = {'utc': state['updated_utc'], 'reason': 'Same hold, different prose'}
        self.write(self.state_path, state)
        self.write(self.edition/'events.json', {'utc': state['updated_utc'], 'message': 'Another tick'})
        self.write(self.progress_path, {**prior, 'updated_utc': state['updated_utc']})
        before, _ = schedule._fingerprint(self.root, DAY)
        self.disposition.touch()
        self.assertEqual(schedule._fingerprint(self.root, DAY)[0], before)
        self.assertEqual(before, prior['fingerprint'])
        with patch.object(smn_subscription_daily, 'run') as reader:
            result = schedule.progress(self.root, DAY, NOW + timedelta(minutes=2))
            self.assertEqual(result['status'], 'needs_attention')
            reader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
