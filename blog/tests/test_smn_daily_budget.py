"""Daily allowance boundaries with synthetic evidence and mocked providers only."""
from contextlib import ExitStack
from datetime import datetime, timezone
import inspect
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import model_job_evidence as evidence
import production_continuity as publication
import production_continuity_schedule as schedule
import smn_daily
import smn_daily_control
import smn_held_recovery
import smn_recovery
import smn_subscription_daily as controller
try:
    import fcntl
except ModuleNotFoundError:  # Import-only Windows fixture; no installer is run.
    with patch.dict(sys.modules, {'fcntl': types.SimpleNamespace()}):
        import smn_subscription_publish
else:
    import smn_subscription_publish
import smn_visual
from subscription_writer import load_json, save_json, sha256

DAY = '2026-10-09'
CURRENT = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)


class DailyBudgetTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.edition = self.root/DAY/'chatgpt'
        self.jobs = self.edition/'jobs'
        self.jobs.mkdir(parents=True)
        self.day = smn_daily.Day(self.edition, DAY, roles={}, profile='chatgpt')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # All file effects are temporary. No provider, process or network call.
        self.provider = self.stack.enter_context(patch('smn_models.run', side_effect=AssertionError('Unexpected provider call')))
        self.stack.enter_context(patch('smn_daily.time.sleep'))
        fake = types.SimpleNamespace(LOCK_EX=1, LOCK_NB=2, LOCK_UN=4, flock=lambda *_: None)
        self.stack.enter_context(patch.dict(sys.modules, {'fcntl': fake}))
        self.stack.enter_context(patch.object(schedule, '_eligible', return_value=True))
        self.stack.enter_context(patch.object(schedule, 'preserved_release_day', return_value=False))

    def reserve(self, count):
        for number in range(count):
            job = self.jobs/f'job-{number}'
            job.mkdir()
            save_json(job/'state.json', {'status': 'ready'})

    def files(self):
        return {p.relative_to(self.edition).as_posix(): p.read_bytes()
                for p in self.edition.rglob('*') if p.is_file()}

    def test_all_normal_defaults_share_owner_allowance_and_smaller_caps_remain(self):
        self.assertEqual(evidence.DAILY_JOB_LIMIT, 60)
        self.assertEqual(schedule.MAX_JOBS, 60)
        for operation in (smn_daily.Day, publication.publish_available,
                          smn_daily_control.run, smn_held_recovery.plan,
                          smn_held_recovery.recover, smn_subscription_publish.publish_edition,
                          smn_visual.article):
            with self.subTest(operation=operation):
                self.assertEqual(inspect.signature(operation).parameters['max_jobs'].default, 60)
        self.assertEqual(smn_daily.Day(self.edition, DAY, roles={}, profile='chatgpt', max_jobs=12).max_jobs, 12)

    def test_explicit_reader_allowance_cannot_exceed_daily_60(self):
        for limit in (59, 60):
            self.assertEqual(smn_daily.Day(self.edition, DAY, roles={}, profile='chatgpt', max_jobs=limit).max_jobs, limit)
        for limit in (61, -1, 60.5, True):
            with self.subTest(limit=limit), self.assertRaisesRegex(smn_daily.Hold, 'cap is 60'):
                smn_daily.Day(self.edition, DAY, roles={}, profile='chatgpt', max_jobs=limit)
        self.provider.assert_not_called()

    def test_59_reservations_allow_exact_60th_and_61st_never_dispatches(self):
        self.reserve(59)
        self.assertEqual(self.day.jobs_used(), 59)
        last = self.jobs/'last'
        last.mkdir()
        save_json(last/'state.json', {'status': 'ready'})
        self.provider.side_effect = None
        self.provider.return_value = {'synthetic_success': True}
        self.assertEqual(self.day.run_job(last), {'synthetic_success': True})
        self.assertEqual(self.day.jobs_used(), 60)
        self.provider.assert_called_once()
        excess = self.jobs/'excess'
        excess.mkdir()
        save_json(excess/'state.json', {'status': 'ready'})
        before = self.files()
        for reader in (self.day, smn_daily.Day(self.edition, DAY, roles={}, profile='chatgpt')):
            with self.assertRaisesRegex(smn_daily.Hold, 'budget of 60'):
                reader.run_job(excess)
            self.assertEqual(reader.jobs_used(), 61)
        self.provider.assert_called_once()
        self.assertEqual(self.files(), before)

    def test_failed_60th_is_retained_and_resume_cannot_reset_budget(self):
        self.reserve(60)
        job = self.jobs/'job-59'
        self.provider.side_effect = RuntimeError('503 overloaded')
        with self.assertRaisesRegex(smn_daily.Hold, 'cumulative'):
            self.day.run_job(job)
        self.assertEqual(self.provider.call_count, 1)
        self.assertEqual(self.day.jobs_used(), 60)
        self.assertFalse(list(job.glob('transient-attempt-*')))
        exhausted = load_json(job/'retry-exhausted.json')
        self.assertEqual((exhausted['jobs_used'], exhausted['max_jobs']), (60, 60))
        before = self.files()
        resumed = smn_daily.Day(self.edition, DAY, roles={}, profile='chatgpt')
        with self.assertRaisesRegex(smn_daily.Hold, 'retained terminal'):
            resumed.run_job(job)
        self.assertEqual(resumed.jobs_used(), 60)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.provider.call_count, 1)

    def test_retry_at_59_reserves_60_without_losing_failure_or_attempt_kind(self):
        for prefix in evidence.ATTEMPT_PREFIXES:
            with self.subTest(prefix=prefix):
                folder = self.edition/prefix
                reader = smn_daily.Day(folder, DAY, roles={}, profile='chatgpt')
                for number in range(59): (folder/'jobs'/str(number)).mkdir(parents=True)
                job = folder/'jobs/58'
                save_json(job/'state.json', {'status': 'failed_needs_review', 'reason': '503 overloaded'})
                reader._archive(job, '503 overloaded', prefix.rstrip('-'))
                archive = job/(prefix+'1')
                self.assertEqual(load_json(archive/'state.json')['reason'], '503 overloaded')
                self.assertEqual(reader.jobs_used(), 60)
                self.assertEqual(smn_daily.Day(folder, DAY, roles={}, profile='chatgpt').jobs_used(), 60)
                with self.assertRaisesRegex(smn_daily.Hold, 'cumulative'):
                    reader._archive(job, 'Another failure', prefix.rstrip('-'))
                self.assertEqual(reader.jobs_used(), 60)

    def test_saved_valid_receipt_reuses_at_61_without_new_job_or_relabel(self):
        self.reserve(61)
        job = self.jobs/'job-0'
        (job/'prompt.txt').write_text('Synthetic fixed evidence', encoding='utf-8')
        save_json(job/'schema.json', {'type': 'object'})
        save_json(job/'output.json', {'passed': True})
        hashes = {name: sha256((job/name).read_bytes()) for name in ('prompt.txt', 'schema.json')}
        manifest = {'job_id': job.name, 'stage': 'review', 'provider': 'openai',
                    'model': 'gpt-test', 'effort': 'low', 'publish': False,
                    'evidence_sha256': 'synthetic-bound-evidence', 'input_hashes': hashes,
                    'valid_until': '2026-10-07T10:00:00+00:00'}
        save_json(job/'job.json', manifest)
        receipt = {'job_id': job.name, 'stage': 'review', 'provider': 'openai',
                   'model_requested': 'gpt-test', 'effort_requested': 'low', 'publish': False,
                   'status': 'output_ready_for_smn_validation', 'billing_source': 'subscription',
                   'api_fallback': False, 'evidence_sha256': manifest['evidence_sha256'],
                   'input_hashes': hashes, 'output_sha256': sha256((job/'output.json').read_bytes()),
                   'finished_utc': '2026-10-07T09:00:00+00:00'}
        save_json(job/'receipt.json', receipt)
        before = self.files()
        self.assertEqual(self.day.run_job(job), receipt)
        self.assertEqual(self.files(), before)
        self.provider.assert_not_called()
        (job/'prompt.txt').write_text('Changed evidence', encoding='utf-8')
        with self.assertRaisesRegex(smn_daily.Hold, 'investigation'):
            self.day.run_job(job)
        self.provider.assert_not_called()

    def test_progress_59_runs_but_60_and_61_stop_without_reset(self):
        for count in (59, 60, 61):
            with self.subTest(count=count):
                root = self.root/str(count)
                jobs = root/DAY/'chatgpt/jobs'
                for number in range(count): (jobs/str(number)).mkdir(parents=True)
                with patch.object(controller, 'run', return_value={'providers': {'chatgpt': {'passed': False}}}) as run, \
                        patch.object(schedule, 'verify_generation', return_value={'complete': False}):
                    result = schedule.progress(root, DAY, CURRENT)
                    self.assertEqual(result['jobs_used'], count)
                    self.assertEqual(result['max_jobs'], 60)
                    self.assertEqual(result['status'], 'pending' if count == 59 else 'needs_attention')
                    self.assertEqual(run.call_count, int(count == 59))
                    if count >= 60:
                        self.assertEqual(schedule.progress(root, DAY, CURRENT), result)
                        run.assert_not_called()
                self.assertEqual(evidence.jobs_used(jobs.parent), count)

    def test_previous_40_budget_terminal_resumes_with_count_and_diagnostic_preserved(self):
        self.reserve(40)
        before, used = schedule._fingerprint(self.root, DAY)
        path = self.root/DAY/'continuity-progress.json'
        old = {'date': DAY, 'status': 'needs_attention', 'fingerprint': before,
               'max_jobs': 40, 'jobs_used': used, 'attempts': 3,
               'reason': schedule.BUDGET_EXHAUSTED_REASON}
        save_json(path, old)
        original = self.files()
        with patch.object(controller, 'run', return_value={'providers': {}}) as run, \
                patch.object(schedule, 'verify_generation', return_value={'complete': False}):
            result = schedule.progress(self.root, DAY, CURRENT)
        run.assert_called_once()
        self.assertEqual((result['jobs_used'], result['max_jobs'], result['attempts']), (40, 60, 4))
        self.assertEqual(result['recovered_from']['max_jobs'], 40)
        self.assertEqual(result['recovered_from']['reason'], old['reason'])
        self.assertEqual(self.files(), original)

    def test_raise_does_not_reopen_other_or_current_exhausted_terminal_holds(self):
        self.reserve(60)
        fingerprint, _ = schedule._fingerprint(self.root, DAY)
        path = self.root/DAY/'continuity-progress.json'
        for status, reason, limit in (('needs_attention', 'Source evidence still missing', 40),
                ('generation_complete', schedule.BUDGET_EXHAUSTED_REASON, 40),
                ('needs_attention', schedule.BUDGET_EXHAUSTED_REASON, 60)):
            with self.subTest(status=status, reason=reason, limit=limit):
                prior = {'status': status, 'reason': reason, 'max_jobs': limit,
                         'jobs_used': 60, 'fingerprint': fingerprint}
                save_json(path, prior)
                with patch.object(controller, 'run') as run:
                    self.assertEqual(schedule.progress(self.root, DAY, CURRENT), prior)
                    run.assert_not_called()

    def test_scheduled_reader_and_reconciler_receive_same_explicit_limit(self):
        save_json(self.edition/'production/posts.json', [{'symbol': 'AAA'}])
        reader = Mock()
        reader.state = {'articles': {}}
        reader.check.return_value = {'passed': True}
        with patch.object(controller, 'Day', return_value=reader) as build, \
                patch.object(controller, 'authenticate'), patch.object(controller, 'freeze_inputs'), \
                patch.object(controller, 'release_login_holds'), patch.object(smn_recovery, 'checkpoint'):
            controller.run_profile(self.edition, DAY, 'chatgpt', self.root/'inputs', continuity=True)
        self.assertEqual(build.call_args.kwargs['max_jobs'], 60)
        save_json(self.edition/'smn-daily-state.json', {'date': DAY, 'articles': {}, 'publication_origin': controller.ORIGIN})
        with patch.object(smn_daily, 'Day', return_value=reader) as build, \
                patch.object(smn_recovery, 'watch_workers', return_value={}), \
                patch.object(smn_recovery, 'repair_held_articles', return_value=[]):
            smn_recovery.supervise(self.root, DAY, CURRENT)
        self.assertEqual(build.call_args.kwargs['max_jobs'], 60)

    def test_production_ceiling_accepts_59_60_but_rejects_61_before_transaction(self):
        save_json(self.edition/'production-publication-receipt.json', {'status': 'live_verified'})
        with patch.object(publication, 'require_policy'), \
                patch.object(publication, 'selected_lineup', return_value=[]), \
                patch.object(publication, 'reconcile_legacy', return_value={'complete': True}) as resume:
            for limit in (59, 60):
                self.assertTrue(publication.publish_available(self.edition, DAY, 'production', max_jobs=limit)['complete'])
            with self.assertRaisesRegex(ValueError, 'cap is 60'):
                publication.publish_available(self.edition, DAY, 'production', max_jobs=61)
        self.assertEqual(resume.call_count, 2)
        self.provider.assert_not_called()

    def test_visual_retry_counts_transient_and_auth_attempts_before_prepare(self):
        self.reserve(58)
        for prefix in ('transient-attempt-', 'authentication-retry-'):
            (self.jobs/'job-0'/(prefix+'1')).mkdir()
        out = self.edition/'results/AAA'
        out.mkdir(parents=True)
        (out/'article.html').write_text('Synthetic page', encoding='utf-8')
        image = out/'qa-test.png'
        image.write_bytes(b'synthetic image')
        answer = {'passed': False, 'defects': []}
        receipt = {'model_requested': 'test', 'effort_requested': 'low', 'job_id': 'synthetic', 'output_sha256': 'synthetic'}
        with patch.object(smn_visual, '_layout', return_value={}), \
                patch.object(smn_visual, '_tiles', return_value=[image]), \
                patch.object(smn_visual, '_page_prompt', return_value='synthetic'), \
                patch.object(smn_visual, '_job', return_value=(answer, receipt, self.jobs/'job-0')) as job:
            with self.assertRaisesRegex(ValueError, 'budget of 60'):
                smn_visual.article(self.edition, DAY, 'AAA', {}, {}, self.day.run_job)
        job.assert_called_once()
        self.provider.assert_not_called()
        self.assertEqual(evidence.jobs_used(self.edition), 60)


if __name__ == '__main__':
    unittest.main()
