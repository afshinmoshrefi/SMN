"""Offline recurrence regressions; every child, campaign and model call is mocked."""
import hashlib
import importlib.util
import json
import logging
import sys
import tempfile
import types
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

BLOG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BLOG))
import operational_schedule as schedule

NOW = datetime(2026, 10, 9, 18, 30, tzinfo=timezone.utc)
DAY = '2026-10-09'


class FixedClock(datetime):
    current = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


class NewsletterSafeRetryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        config = types.ModuleType('config')
        config.news_root_folder = str(self.root)
        config.news_website_url = 'https://seasonalmarketnews.com'
        config.smn_favicon = 'https://seasonalmarketnews.com/favicon.png'
        config.smn_from_name = 'SMN'
        config.smn_from_email = 'noreply@example.com'
        email_tools = types.ModuleType('email_tools')
        for name in ('get_email_groups', 'create_campaign', 'create_mailerlite_group',
                     'create_subscriber', 'assign_subscriber_to_a_group',
                     'get_subscriber_by_email'):
            setattr(email_tools, name, Mock(side_effect=AssertionError('Unmocked provider')))
        ai = types.ModuleType('AI_tools')
        ai.send_openai_prompt = Mock(side_effect=AssertionError('Model call'))
        spec = importlib.util.spec_from_file_location('tested_retry_sender', BLOG/'send_smn_emails.py')
        self.sender = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'config': config, 'email_tools': email_tools,
                                      'AI_tools': ai}), patch.object(logging, 'basicConfig'):
            spec.loader.exec_module(self.sender)
        self.sender.STATE_FILE = self.root/'campaigns.json'
        self.sender.POSTS_JSON = self.root/'posts.json'
        self.posts = [{'symbol': symbol, 'slug': 'oct9-' + symbol,
                       'url': 'https://seasonalmarketnews.com/editions/' + DAY + '/' + symbol + '/article.html',
                       'published_date': DAY + 'T17:00:00Z'}
                      for symbol in ('XLF', 'SPY', 'QQQ', 'DJI', 'DAL', 'RPM')]
        self.urls = {post['url'] for post in self.posts}
        self.sender.POSTS_JSON.write_text(json.dumps(self.posts), encoding='utf-8')
        self.state = {'daily_sent': [], 'weekly_sent': [], 'campaigns': {}}
        self.save_state()
        fake_fcntl = types.ModuleType('fcntl')
        fake_fcntl.LOCK_EX = 1
        fake_fcntl.LOCK_NB = 2
        fake_fcntl.flock = Mock()
        self.stack = __import__('contextlib').ExitStack()
        self.addCleanup(self.stack.close)
        modules = {'send_smn_emails': self.sender}
        if sys.platform == 'win32':
            modules['fcntl'] = fake_fcntl  # Scheduler lock only; sender uses real msvcrt.
        self.stack.enter_context(patch.dict(sys.modules, modules))
        self.stack.enter_context(patch.object(schedule, 'MARKERS', self.root/'markers'))
        self.stack.enter_context(patch.object(schedule, 'CONTROLLER_ROOT', self.root/'controller'))
        self.stack.enter_context(patch.object(schedule, 'datetime', FixedClock))
        self.stack.enter_context(patch.object(schedule.operational_settings, 'for_instant', return_value={
            'weekday_newsletter': {'time': '07:00', 'timezone': 'UTC'}}))
        self.verified = self.stack.enter_context(patch.object(schedule, 'verified_reader_urls', return_value=self.urls))
        self.child = self.stack.enter_context(patch.object(schedule.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)))
        self.stack.enter_context(patch.object(self.sender.requests, 'get', side_effect=AssertionError('Unmocked GET')))
        self.stack.enter_context(patch.object(self.sender.requests, 'post', side_effect=AssertionError('Unmocked POST')))
        FixedClock.current = NOW
        schedule.MARKERS.mkdir()
        self.marker = schedule.MARKERS/('production-weekday_newsletter-' + DAY + '.json')

    def save_state(self):
        self.sender.STATE_FILE.write_text(json.dumps(self.state), encoding='utf-8')

    def prior(self, **changes):
        record = {'target': 'production', 'phase': 'weekday_newsletter', 'date': DAY,
                  'status': 'failed', 'exit_code': 1,
                  'started_at': (NOW-timedelta(minutes=12)).isoformat(),
                  'finished_at': (NOW-timedelta(minutes=10)).isoformat()}
        record.update(changes)
        self.marker.write_text(json.dumps(record, indent=1) + '\n', encoding='utf-8')
        return self.marker.read_bytes()

    def tick(self, now=NOW):
        FixedClock.current = now
        return schedule.tick('production', now=now, newsletter_only=True)

    def test_failed_pre_post_attempt_retries_once_and_preserves_exact_prior_bytes(self):
        old = self.prior()
        journal = self.sender.STATE_FILE.read_bytes()
        self.assertEqual(self.tick(), 0)
        self.child.assert_called_once()
        record = json.loads(self.marker.read_text())
        self.assertEqual((record['status'], record['attempt']), ('completed', 2))
        audit = record['retry_history'][0]
        self.assertEqual((schedule.MARKERS/audit['archive']).read_bytes(), old)
        self.assertEqual(audit['sha256'], hashlib.sha256(old).hexdigest())
        self.assertEqual(audit['pre_post_evidence']['campaign_state_sha256'], hashlib.sha256(journal).hexdigest())
        self.assertEqual(self.sender.STATE_FILE.read_bytes(), journal)
        self.assertEqual(self.child.call_args.kwargs['env']['TZ'], 'UTC')
        self.assertEqual(self.child.call_args.args[0][-2:], ['--verified-reader-date', DAY])
        self.tick(NOW+timedelta(minutes=10))
        self.assertEqual(self.child.call_count, 1)

    def test_waiting_pre_post_attempt_retries_after_backoff(self):
        self.prior(status='waiting', exit_code=75)
        self.assertEqual(self.tick(), 0)
        self.child.assert_called_once()

    def test_failed_retry_preserves_all_history_and_stops_at_three_attempts(self):
        first = self.prior()
        self.child.return_value.returncode = 2
        self.assertEqual(self.tick(), 2)
        second = self.marker.read_bytes()
        self.assertEqual(self.tick(NOW+timedelta(minutes=5)), 2)
        record = json.loads(self.marker.read_text())
        self.assertEqual(record['attempt'], 3)
        self.assertEqual(len(record['retry_history']), 2)
        self.assertEqual((schedule.MARKERS/record['retry_history'][0]['archive']).read_bytes(), first)
        self.assertEqual((schedule.MARKERS/record['retry_history'][1]['archive']).read_bytes(), second)
        third = self.marker.read_bytes()
        self.assertEqual(self.tick(NOW+timedelta(minutes=30)), 0)
        self.assertEqual(self.child.call_count, 2)
        self.assertEqual(self.marker.read_bytes(), third)

    def test_recent_failure_waits_without_archiving_or_running(self):
        old = self.prior(finished_at=(NOW-timedelta(minutes=4)).isoformat())
        self.assertEqual(self.tick(), 0)
        self.child.assert_not_called()
        self.assertEqual(self.marker.read_bytes(), old)
        self.assertEqual(list(schedule.MARKERS.glob('*.attempt-*')), [])

    def test_running_completed_wrong_date_and_unproven_finish_never_retry(self):
        variants = [{'status': 'running'}, {'status': 'completed', 'exit_code': 0},
                    {'date': '2026-10-08'}, {'finished_at': None},
                    {'finished_at': 'not-a-time'}, {'finished_at': '2026-10-09T18:00:00'},
                    {'finished_at': (NOW+timedelta(minutes=10)).isoformat()},
                    {'status': 'failed', 'exit_code': 0}, {'attempt': True},
                    {'status': 'waiting', 'exit_code': 1}]
        for changes in variants:
            with self.subTest(changes=changes):
                old = self.prior(**changes)
                self.assertEqual(self.tick(), 0)
                self.child.assert_not_called()
                self.assertEqual(self.marker.read_bytes(), old)

    def test_missing_finished_field_stays_held(self):
        self.prior()
        record = json.loads(self.marker.read_text())
        del record['finished_at']
        self.marker.write_text(json.dumps(record))
        old = self.marker.read_bytes()
        self.assertEqual(self.tick(), 0)
        self.child.assert_not_called()
        self.assertEqual(self.marker.read_bytes(), old)

    def test_existing_unknown_known_and_sent_intents_never_start_another_sender(self):
        for phase, identifier, status in [('create_unknown', None, None),
                                          ('schedule_unknown', '123', None),
                                          ('scheduled', '123', 'ready'),
                                          ('provider_sent', '123', 'sent')]:
            with self.subTest(phase=phase):
                old = self.prior()
                self.state['campaigns'] = {'daily:' + DAY: {'name': 'SMN-Daily-' + DAY,
                    'phase': phase, 'campaign_id': identifier, 'provider_status': status,
                    'slugs': [post['slug'] for post in self.posts]}}
                self.save_state()
                journal = self.sender.STATE_FILE.read_bytes()
                self.assertEqual(self.tick(), 0)
                self.child.assert_not_called()
                self.assertEqual(self.marker.read_bytes(), old)
                self.assertEqual(self.sender.STATE_FILE.read_bytes(), journal)

    def test_legacy_or_other_date_partial_reservation_prevents_retry(self):
        for state in [{'daily_sent': [self.posts[0]['slug']], 'campaigns': {}},
                      {'daily_sent': [], 'campaigns': {'daily:2026-10-08': {'slugs': [self.posts[0]['slug']]}}}]:
            with self.subTest(state=state):
                old = self.prior()
                self.state = state
                self.save_state()
                self.assertEqual(self.tick(), 0)
                self.child.assert_not_called()
                self.assertEqual(self.marker.read_bytes(), old)

    def test_unreadable_missing_and_malformed_journal_never_archives_or_sends(self):
        for content in [None, b'{broken', b'[]', b'{"daily_sent":[],"campaigns":[]} ',
                        b'{"daily_sent":[],"campaigns":{"daily:2026-10-08":{}}}']:
            with self.subTest(content=content):
                old = self.prior()
                if content is None:
                    self.sender.STATE_FILE.unlink(missing_ok=True)
                else:
                    self.sender.STATE_FILE.write_bytes(content)
                with self.assertRaises((ValueError, json.JSONDecodeError)):
                    self.tick()
                self.child.assert_not_called()
                self.assertEqual(self.marker.read_bytes(), old)
                self.assertEqual(list(schedule.MARKERS.glob('*.attempt-*')), [])

    def test_incomplete_or_changed_public_gate_preserves_failed_marker(self):
        old = self.prior()
        self.verified.return_value = None
        self.assertEqual(self.tick(), 0)
        self.verified.side_effect = ValueError('Public article changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.tick()
        self.child.assert_not_called()
        self.assertEqual(self.marker.read_bytes(), old)

    def test_live_sender_lock_refuses_retry_without_marker_or_campaign_change(self):
        old = self.prior()
        journal = self.sender.STATE_FILE.read_bytes()
        with self.sender._newsletter_lock():
            with self.assertRaisesRegex(RuntimeError, 'already running'):
                self.tick()
        self.child.assert_not_called()
        self.assertEqual(self.marker.read_bytes(), old)
        self.assertEqual(self.sender.STATE_FILE.read_bytes(), journal)

    def test_catalog_missing_duplicate_slug_or_reserved_slug_never_sends(self):
        cases = [self.posts[:-1], [*self.posts[:-1], {**self.posts[-1], 'slug': self.posts[0]['slug']}],
                 [*self.posts[:-1], {**self.posts[-1], 'slug': None}]]
        for posts in cases:
            with self.subTest(posts=posts):
                old = self.prior()
                self.sender.POSTS_JSON.write_text(json.dumps(posts))
                with self.assertRaisesRegex(ValueError, 'catalog'):
                    self.tick()
                self.child.assert_not_called()
                self.assertEqual(self.marker.read_bytes(), old)

    def test_changed_existing_archive_is_never_overwritten(self):
        old = self.prior()
        digest = hashlib.sha256(old).hexdigest()
        archive = self.marker.with_name(self.marker.stem + '.attempt-1-' + digest + '.json')
        archive.write_bytes(b'peer evidence')
        with self.assertRaisesRegex(ValueError, 'archive changed'):
            self.tick()
        self.child.assert_not_called()
        self.assertEqual(self.marker.read_bytes(), old)
        self.assertEqual(archive.read_bytes(), b'peer evidence')

    def test_existing_matching_archive_resumes_only_local_preparation(self):
        old = self.prior()
        digest = hashlib.sha256(old).hexdigest()
        archive = self.marker.with_name(self.marker.stem + '.attempt-1-' + digest + '.json')
        archive.write_bytes(old)
        self.assertEqual(self.tick(), 0)
        self.child.assert_called_once()
        self.assertEqual(archive.read_bytes(), old)

    def test_changed_or_truncated_prior_retry_history_cannot_reset_the_bound(self):
        self.prior()
        self.child.return_value.returncode = 1
        self.assertEqual(self.tick(), 1)
        self.child.reset_mock()
        second = self.marker.read_bytes()
        record = json.loads(second)
        archive = schedule.MARKERS/record['retry_history'][0]['archive']
        archived = archive.read_bytes()
        archive.write_bytes(b'changed historical evidence')
        with self.assertRaisesRegex(ValueError, 'history bytes changed'):
            self.tick(NOW+timedelta(minutes=5))
        self.child.assert_not_called()
        self.assertEqual(self.marker.read_bytes(), second)
        archive.write_bytes(archived)
        record['retry_history'] = []
        self.marker.write_text(json.dumps(record))
        truncated = self.marker.read_bytes()
        with self.assertRaisesRegex(ValueError, 'history is malformed'):
            self.tick(NOW+timedelta(minutes=5))
        self.child.assert_not_called()
        self.assertEqual(self.marker.read_bytes(), truncated)

    def test_other_phase_failed_marker_is_unchanged(self):
        marker = schedule.MARKERS/('production-selector-' + DAY + '.json')
        marker.write_bytes(b'{"status":"failed"}')
        with patch.object(schedule, 'due', return_value=[('selector', DAY)]):
            self.assertEqual(self.tick(), 0)
        self.child.assert_not_called()
        self.assertEqual(marker.read_bytes(), b'{"status":"failed"}')

    def test_missing_daily_group_is_a_pre_post_error_not_success(self):
        journal = self.sender.STATE_FILE.read_bytes()
        self.sender.get_email_groups.return_value = {}
        self.sender.get_email_groups.side_effect = None
        with self.assertRaisesRegex(RuntimeError, 'SMN-DAILY group not found'):
            self.sender.daily_send(verified_urls=self.urls, edition_date=date.fromisoformat(DAY))
        self.sender.create_campaign.assert_not_called()
        self.assertEqual(self.sender.STATE_FILE.read_bytes(), journal)

    def test_missing_group_then_safe_retry_queues_one_campaign_without_a_model(self):
        self.sender.get_email_groups.side_effect = None
        self.sender.get_email_groups.return_value = {}
        self.sender.create_campaign.side_effect = None
        self.sender.create_campaign.return_value = ('123', 'created')

        def synthetic_child(*_args, **_kwargs):
            try:
                self.sender.daily_send(verified_urls=self.urls, edition_date=date.fromisoformat(DAY))
            except RuntimeError:
                return types.SimpleNamespace(returncode=1)
            return types.SimpleNamespace(returncode=0)

        self.child.side_effect = synthetic_child
        with patch.object(self.sender, '_schedule_campaign_explicit', return_value={
                'data': {'id': '123', 'status': 'ready'}}) as provider_schedule:
            self.assertEqual(self.tick(), 1)
            failed = self.marker.read_bytes()
            self.assertEqual(json.loads(failed)['status'], 'failed')
            self.assertEqual(json.loads(self.sender.STATE_FILE.read_text())['campaigns'], {})
            self.sender.get_email_groups.return_value = {'SMN-DAILY': 'group'}
            self.assertEqual(self.tick(NOW+timedelta(minutes=5)), 0)
            record = json.loads(self.marker.read_text())
            self.assertEqual((record['status'], record['attempt']), ('completed', 2))
            self.assertEqual((schedule.MARKERS/record['retry_history'][0]['archive']).read_bytes(), failed)
            self.tick(NOW+timedelta(minutes=20))
            self.sender.create_campaign.assert_called_once()
            provider_schedule.assert_called_once()
        campaign = json.loads(self.sender.STATE_FILE.read_text())['campaigns']['daily:' + DAY]
        self.assertEqual((campaign['phase'], campaign['provider_status']), ('scheduled', 'ready'))
        self.assertNotIn('provider_counts', campaign)  # Queued acknowledgement is not delivery.


if __name__ == '__main__':
    unittest.main()
