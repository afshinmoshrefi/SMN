"""Dev continuity ticks use saved work and remain independent of research."""
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

BLOG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BLOG))
import production_continuity_schedule as schedule
import operational_schedule
import smn_subscription_daily

DAY = '2026-10-05'
AT_SEVEN = datetime(2026, 10, 5, 11, 0, tzinfo=timezone.utc)


class ContinuityScheduleTests(unittest.TestCase):
    def test_release_floor_preserves_recovered_day_without_worker_or_publication(self):
        activation = self.root/'activation.json'
        activation.write_text(json.dumps({'publication_policy': 'continuity-v1',
            'continuity_first_edition_date': '2026-10-06'}))
        before = {p.relative_to(self.root).as_posix(): p.read_bytes()
                  for p in self.root.rglob('*') if p.is_file()}
        with patch.object(schedule, 'ACTIVATION', activation), \
                patch.object(smn_subscription_daily, 'run') as generation, \
                patch('production_continuity.publish_available') as publication, \
                patch('smn_recovery.watch_workers') as watch, \
                patch('smn_recovery.worker_start') as worker, \
                patch.object(sys, 'argv', ['schedule', 'reconcile', '--enable-production-continuity',
                    '--root', str(self.root), '--date', DAY]), patch('builtins.print'):
            self.assertEqual(schedule.progress(self.root, DAY, AT_SEVEN)['status'], 'preserved_release_day')
            self.assertEqual(schedule.deliver(self.root, DAY, AT_SEVEN)['status'], 'preserved_release_day')
            self.assertEqual(schedule.main(), 0)
            generation.assert_not_called(); publication.assert_not_called()
            watch.assert_not_called(); worker.assert_not_called()
            self.assertFalse(schedule.preserved_release_day('2026-10-06'))
        self.assertEqual(before, {p.relative_to(self.root).as_posix(): p.read_bytes()
                                 for p in self.root.rglob('*') if p.is_file()})

    def test_malformed_release_floor_cannot_start_generation(self):
        activation = self.root/'activation.json'
        activation.write_text(json.dumps({'publication_policy': 'continuity-v1',
            'continuity_first_edition_date': '2026-10-6'}))
        with patch.object(schedule, 'ACTIVATION', activation), \
                patch.object(smn_subscription_daily, 'run') as generation:
            with self.assertRaises(ValueError): schedule.progress(self.root, DAY, AT_SEVEN)
            generation.assert_not_called()

    def test_budget_exhaustion_does_not_consume_deadline_publication_capacity(self):
        with patch.object(schedule, '_fingerprint', return_value=('exhausted', 60)), \
                patch.object(smn_subscription_daily, 'run') as generation, \
                patch('production_continuity.publish_available', return_value={
                    'status': 'live_verified', 'complete': False, 'published_symbols': ['AAA']}) as publication:
            self.assertEqual(schedule.progress(self.root, DAY, AT_SEVEN)['status'], 'needs_attention')
            self.assertEqual(schedule.deliver(self.root, DAY, AT_SEVEN)['published_symbols'], ['AAA'])
            generation.assert_not_called(); publication.assert_called_once()

    def test_completed_legacy_day_reconciles_without_new_generation(self):
        receipt=self.root/DAY/'chatgpt/production-publication-receipt.json'
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({'status':'live_verified'}))
        with patch('production_continuity.reconcile_legacy',return_value={'complete':True}) as reconcile,patch.object(smn_subscription_daily,'run') as run:
            result=schedule.progress(self.root,DAY,AT_SEVEN)
            run.assert_not_called()
            reconcile.assert_called_once()
            self.assertEqual(result['status'],'generation_complete')

    def test_reconcile_cli_only_calls_get_poll_after_policy_guard(self):
        mail=types.ModuleType('send_smn_emails');mail.poll_pending_campaigns=lambda *_:{'campaign':'queued'}
        with patch.dict(sys.modules,{'send_smn_emails':mail}),patch.object(sys,'argv',['production_continuity_schedule.py','reconcile','--enable-production-continuity','--root',str(self.root),'--date',DAY]),patch('builtins.print'),patch('production_continuity.require_policy') as policy:
            self.assertEqual(schedule.main(),0)
            policy.assert_called_once()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        fake = types.ModuleType('fcntl')
        fake.LOCK_EX, fake.LOCK_NB, fake.LOCK_UN = 1, 2, 4
        fake.flock = lambda *_args: None
        installed = patch.dict(sys.modules, {'fcntl': fake})
        installed.start()
        self.addCleanup(installed.stop)
        publication = types.ModuleType('production_continuity')
        publication.publish_available = lambda *_a, **_k: None
        publication.require_policy = lambda *_a: None
        publication.reconcile_legacy = lambda *_a: {'complete':True}
        publication_patch = patch.dict(sys.modules, {'production_continuity': publication})
        publication_patch.start()
        self.addCleanup(publication_patch.stop)
        self.last = self.root/'last-run.json'

    def test_deadline_delivery_does_not_wait_for_research_lock_or_send_mail(self):
        self.last.write_text(json.dumps({'date': DAY, 'status': 'running'}))
        with patch('production_continuity.publish_available', return_value={
                'status': 'live_verified', 'coverage_status': 'notice',
                'complete': False}) as publish, \
                patch.object(smn_subscription_daily, 'run', side_effect=AssertionError('research called')):
            result = schedule.deliver(self.root, DAY, AT_SEVEN)
        self.assertEqual(result['coverage_status'], 'notice')
        self.assertEqual(json.loads(self.last.read_text())['status'], 'running')
        publish.assert_called_once_with(self.root.resolve()/DAY/'chatgpt', DAY, 'production',
                                        repo=BLOG.parent, max_jobs=60)

    def test_notice_is_not_republished_each_minute_but_new_evidence_wakes_delivery(self):
        with patch('production_continuity.publish_available', return_value={
                'status': 'live_verified', 'coverage_status': 'notice',
                'complete': False}) as publish:
            schedule.deliver(self.root, DAY, AT_SEVEN)
            schedule.deliver(self.root, DAY, AT_SEVEN+timedelta(minutes=1))
            self.assertEqual(publish.call_count, 1)
            selection = self.root/DAY/'inputs/input-selection.json'
            selection.parent.mkdir(exist_ok=True)
            selection.write_text(json.dumps({'date': DAY, 'symbols': ['AAA']}))
            schedule.deliver(self.root, DAY, AT_SEVEN+timedelta(minutes=2))
            self.assertEqual(publish.call_count, 2)

    def test_delivery_window_and_weekend_are_hard_boundaries(self):
        with patch('production_continuity.publish_available') as publish:
            self.assertEqual(schedule.deliver(self.root, DAY, AT_SEVEN-timedelta(minutes=1))['status'],
                             'before_delivery_window')
            self.assertEqual(schedule.deliver(self.root, '2026-10-04', AT_SEVEN)['status'],
                             'before_delivery_window')
            publish.assert_not_called()

    def test_cli_rejects_non_production_policy_before_any_work(self):
        with patch.object(sys, 'argv', ['continuity_schedule.py', 'deliver',
                '--enable-production-continuity', '--root', str(self.root), '--date', DAY]), \
                patch('production_continuity.require_policy', side_effect=ValueError('Wrong policy')), \
                patch.object(schedule, 'deliver') as deliver, \
                patch('builtins.print'):
            self.assertEqual(schedule.main(), 2)
            deliver.assert_not_called()

    def test_finished_edition_can_publish_before_target_but_not_before_start(self):
        path = self.root/DAY/'continuity-progress.json'
        path.parent.mkdir()
        path.write_text(json.dumps({'date':DAY, 'status':'generation_complete'}))
        with patch('production_continuity.publish_available', return_value={
                'status':'live_verified', 'complete':True}) as publish:
            early = schedule.deliver(self.root, DAY, AT_SEVEN-timedelta(hours=2))
            self.assertEqual(early['status'], 'before_delivery_window')
            done = schedule.deliver(self.root, DAY, AT_SEVEN-timedelta(minutes=30))
            self.assertTrue(done['complete'])
            publish.assert_called_once()

    def test_held_reader_remains_pending_and_retries_with_backoff(self):
        result = {'providers': {'chatgpt': {'passed': False, 'reason': 'review held'}}}
        with patch.object(smn_subscription_daily, 'run', return_value=result) as run:
            first = schedule.progress(self.root, DAY, AT_SEVEN)
            self.assertEqual(first['status'], 'pending')
            self.assertEqual(first['jobs_used'], 0)
            self.assertEqual(first['max_jobs'], 60)
            self.assertEqual(schedule.progress(self.root, DAY, AT_SEVEN+timedelta(minutes=5)), first)
            second = schedule.progress(self.root, DAY, AT_SEVEN+timedelta(minutes=10))
            self.assertEqual(second['status'], 'pending')
            self.assertEqual(second['unchanged_attempts'], 2)
            self.assertEqual(datetime.fromisoformat(second['next_attempt_utc']),
                             AT_SEVEN+timedelta(minutes=30))
            self.assertEqual(run.call_count, 2)
            run.assert_called_with(self.root.resolve(), DAY, publish=False,
                                   target='production', scheduled=True, continuity=True)
        self.assertFalse(self.last.exists())

    def test_crash_receipt_resumes_without_duplicate_earlier_attempt(self):
        path = self.root/DAY/'continuity-progress.json'
        path.parent.mkdir()
        path.write_text(json.dumps({'date': DAY, 'status': 'running', 'attempts': 1,
            'unchanged_attempts': 0,
            'next_attempt_utc': (AT_SEVEN+timedelta(minutes=15)).isoformat()}))
        with patch.object(smn_subscription_daily, 'run', return_value={
                'providers': {'chatgpt': {'passed': True}}}) as run,patch.object(schedule,'verify_generation',return_value={'complete':True,'verified_symbols':['AAA']}):
            self.assertEqual(schedule.progress(self.root, DAY, AT_SEVEN)['status'], 'running')
            run.assert_not_called()
            finished = schedule.progress(self.root, DAY, AT_SEVEN+timedelta(minutes=15))
            self.assertEqual(finished['status'], 'generation_complete')
            self.assertEqual(finished['attempts'], 2)
            self.assertIsNone(finished['next_attempt_utc'])
            self.assertEqual(schedule.progress(self.root, DAY, AT_SEVEN+timedelta(hours=1)), finished)
            run.assert_called_once()

    def test_controller_busy_does_not_clobber_live_last_run(self):
        self.last.write_text(json.dumps({'date': DAY, 'status': 'running', 'owner': 'daily'}))
        with patch.object(smn_subscription_daily, 'run', side_effect=BlockingIOError()):
            result = schedule.progress(self.root, DAY, AT_SEVEN)
        self.assertEqual(result['status'], 'busy')
        self.assertEqual(json.loads(self.last.read_text())['owner'], 'daily')

    def test_budget_ceiling_prevents_new_model_call(self):
        from smn_daily import Day, Hold
        edition = self.root/DAY/'chatgpt'
        jobs = edition/'jobs'
        jobs.mkdir(parents=True)
        for number in range(61):
            (jobs/f'job-{number}').mkdir()
        day = Day(edition, DAY, roles={}, profile='chatgpt')
        with patch('smn_models.run') as model:
            with self.assertRaisesRegex(Hold, 'budget'):
                day.run_job(jobs/'job-60')
            model.assert_not_called()

    def test_progress_preflight_counts_failed_attempts_and_starts_no_job_at_cap(self):
        jobs = self.root/DAY/'chatgpt/jobs'
        jobs.mkdir(parents=True)
        for number in range(59):
            (jobs/f'job-{number}').mkdir()
        (jobs/'job-0/failed-attempt-1').mkdir()
        with patch.object(smn_subscription_daily, 'run') as run:
            record = schedule.progress(self.root, DAY, AT_SEVEN)
            self.assertEqual(record['status'], 'needs_attention')
            self.assertEqual(record['jobs_used'], 60)
            run.assert_not_called()

    def test_continuity_finishes_subject_before_next_research(self):
        canonical = self.root/'inputs'
        posts = self.root/'production/posts.json'
        posts.parent.mkdir()
        posts.write_text(json.dumps([{'symbol': 'AAA'}, {'symbol': 'BBB'}]))
        calls = []
        class FakeDay:
            def __init__(self, *_args, **_kwargs):
                self.state = {'articles': {}}
            def save(self): pass
            def release_transient_holds(self): pass
            def research(self): calls.append(('research', self.symbols[0]))
            def articles(self): calls.append(('articles', self.symbols[0]))
            def visual(self): calls.append(('visual', self.symbols[0]))
            def check(self): return {'passed': True}
        with patch.object(smn_subscription_daily, 'Day', FakeDay), \
                patch.object(smn_subscription_daily, 'authenticate'), \
                patch.object(smn_subscription_daily, 'freeze_inputs'), \
                patch.object(smn_subscription_daily, 'release_login_holds'),patch('smn_recovery.checkpoint'):
            result = smn_subscription_daily.run_profile(
                self.root, DAY, 'chatgpt', canonical, publication_origin=smn_subscription_daily.ORIGIN, continuity=True)
        self.assertTrue(result['passed'])
        self.assertEqual(calls, [('research', 'AAA'), ('articles', 'AAA'), ('visual', 'AAA'),
                                 ('research', 'BBB'), ('articles', 'BBB'), ('visual', 'BBB')])

    def test_continuity_never_starts_comparison_provider(self):
        import subscription_inputs
        import subscription_capture
        from contextlib import contextmanager
        @contextmanager
        def no_lock(_root):
            yield
        def captured(root, _date):
            root.mkdir(parents=True, exist_ok=True)
            (root/'input-heroes.json').write_text('{}')
            return {'status': 'captured'}
        with patch.object(smn_subscription_daily, 'lock', no_lock), \
                patch.object(subscription_inputs, 'capture', side_effect=captured), \
                patch.object(subscription_inputs, 'prepare_heroes'), \
                patch.object(subscription_capture, 'engine'), \
                patch.object(smn_subscription_daily, 'authenticate') as auth, \
                patch.object(smn_subscription_daily, 'run_profile', return_value={'passed': False}) as profile:
            result = smn_subscription_daily.run(self.root, DAY, target='production', continuity=True)
        self.assertFalse(result['claude_comparison'])
        self.assertEqual(list(result['providers']), ['chatgpt'])
        self.assertEqual(auth.call_count, 1)
        profile.assert_called_once()

    def test_research_resume_merges_subject_after_crash_without_erasing_approval(self):
        from smn_daily import Day
        edition = self.root/DAY/'chatgpt'
        folder = edition/'research'
        folder.mkdir(parents=True)
        (folder/'AAA.json').write_text(json.dumps({'sources': ['approved']}))
        (folder/'BBB.json').write_text(json.dumps({'sources': ['pending']}))
        (edition/'sources.json').write_text(json.dumps({'AAA': {'sources': ['approved']}}))
        day = Day(edition, DAY, roles={}, profile='chatgpt', publication_origin=None)
        day.continuity = True
        day.symbols = ['BBB']
        day.research()
        self.assertEqual(json.loads((edition/'sources.json').read_text()),
                         {'AAA': {'sources': ['approved']}, 'BBB': {'sources': ['pending']}})

    def test_partial_continuity_receipt_never_releases_newsletter(self):
        inputs = self.root/DAY/'inputs'
        inputs.mkdir(parents=True)
        (inputs/'input-selection.json').write_text(json.dumps({'date': DAY, 'symbols': ['AAA']}))
        receipt = self.root/DAY/'chatgpt/production-publication-receipt.json'
        receipt.parent.mkdir()
        receipt.write_text(json.dumps({'status': 'live_verified',
            'publication_policy': 'continuity-v1', 'complete': False}))
        self.assertIsNone(operational_schedule.verified_reader_urls(self.root, DAY))
        (inputs/'input-selection.json').write_text(json.dumps({'date': DAY,
            'symbols': ['AAA', 'BBB', 'CCC', 'DDD', 'EEE', 'FFF']}))
        receipt.write_text(json.dumps({'status': 'live_verified',
            'publication_policy': 'continuity-v1', 'coverage_status': 'complete',
            'complete': True, 'expected_symbols': ['AAA', 'BBB', 'CCC', 'DDD', 'EEE', 'FFF'],
            'published_symbols': [], 'pending_symbols': ['AAA']}))
        with self.assertRaisesRegex(ValueError, 'partition'):
            operational_schedule.verified_reader_urls(self.root, DAY)


if __name__ == '__main__':
    unittest.main()
