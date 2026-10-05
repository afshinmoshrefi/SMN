import importlib.util
import json
import logging
import multiprocessing
import os
import sys
import tempfile
import types
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch


class NewsletterStateTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        config = types.ModuleType('config')
        config.news_root_folder = str(root)
        config.news_website_url = 'https://seasonalmarketnews.com'
        config.smn_from_name = 'SMN'
        config.smn_from_email = 'noreply@example.com'
        config.mailerlite_token = 'test-token'
        email_tools = types.ModuleType('email_tools')
        for name in ('get_email_groups', 'create_campaign', 'schedule_campaign',
                     'create_mailerlite_group', 'create_subscriber',
                     'assign_subscriber_to_a_group', 'get_subscriber_by_email'):
            setattr(email_tools, name, lambda *args, **kwargs: None)
        ai_tools = types.ModuleType('AI_tools')
        ai_tools.send_openai_prompt = lambda *args, **kwargs: None
        path = Path(__file__).resolve().parents[1] / 'send_smn_emails.py'
        spec = importlib.util.spec_from_file_location('tested_smn_newsletter_state', path)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'config': config, 'email_tools': email_tools,
                                      'AI_tools': ai_tools}), patch.object(logging, 'basicConfig'):
            spec.loader.exec_module(self.module)
        self.module.STATE_FILE = root / 'state.json'
        self.module.POSTS_JSON = root / 'posts.json'

    def test_lock_serializes_entrypoints_and_releases(self):
        module = self.module
        with module._newsletter_lock():
            with self.assertRaisesRegex(RuntimeError, 'already running'):
                module.weekly_send()
            if os.name != 'nt':
                result = multiprocessing.get_context('fork').Queue()

                def child():
                    try:
                        with module._newsletter_lock():
                            result.put('acquired')
                    except RuntimeError:
                        result.put('blocked')

                process = multiprocessing.get_context('fork').Process(target=child)
                process.start()
                self.assertEqual(result.get(timeout=5), 'blocked')
                process.join(timeout=5)
                self.assertEqual(process.exitcode, 0)
        with module._newsletter_lock():
            pass
        if os.name != 'nt':
            receive, send = multiprocessing.get_context('fork').Pipe(duplex=False)

            def exit_holding_lock():
                with module._newsletter_lock():
                    send.send('locked')
                    os._exit(0)

            process = multiprocessing.get_context('fork').Process(target=exit_holding_lock)
            process.start()
            self.assertEqual(receive.recv(), 'locked')
            process.join(timeout=5)
            self.assertEqual(process.exitcode, 0)
            with module._newsletter_lock():
                pass

    def test_schedule_uses_account_zone_across_dst_and_midnight(self):
        cases = [
            ('2026-01-15T14:18:00+00:00', ('2026-01-15', '09', '23')),
            ('2026-07-15T14:18:00+00:00', ('2026-07-15', '10', '23')),
            ('2026-07-16T00:58:00+00:00', ('2026-07-15', '21', '03')),
            ('2026-11-01T05:58:00+00:00', ('2026-11-01', '01', '03')),
        ]
        for instant, expected in cases:
            with self.subTest(instant=instant):
                self.assertEqual(self.module._schedule_fields(
                    5, datetime.fromisoformat(instant)), expected)

    def test_schedule_ack_is_queued_not_sent_and_blocks_duplicate(self):
        module = self.module
        state = module._load_state()
        day = date(2026, 10, 5)
        with patch.object(module, 'create_campaign', return_value=(123, 'created')) as create, \
                patch.object(module, '_schedule_campaign_explicit', return_value={
                    'data': {'id': '123', 'status': 'ready', 'finished_at': None}}) as schedule:
            record = module._create_and_schedule('g', 'subject', '<html/>', 'SMN-Daily-2026-10-05',
                                                 state, 'daily', day, [{'slug': 'aaa'}])
            self.assertEqual(record['phase'], 'scheduled')
            self.assertEqual(record['provider_status'], 'ready')
            self.assertEqual(module._reserved_slugs(state, 'daily'), {'aaa'})
            self.assertEqual(state['daily_sent'], [])
            self.assertEqual(schedule.call_count, 1)
            with self.assertRaisesRegex(ValueError, 'already attempted'):
                module._create_and_schedule('g', 'subject', '<html/>', 'SMN-Daily-2026-10-05',
                                            state, 'daily', day, [{'slug': 'aaa'}])
            self.assertEqual(create.call_count, 1)

    def test_uncertain_create_or_schedule_never_reposts(self):
        module = self.module
        day = date(2026, 10, 5)
        for failure_at in ('create', 'schedule'):
            with self.subTest(failure_at=failure_at):
                state = module._load_state() if not module.STATE_FILE.exists() else {'campaigns': {}}
                module.STATE_FILE.unlink(missing_ok=True)
                with patch.object(module, 'create_campaign', side_effect=(RuntimeError('timeout') if failure_at == 'create' else None),
                                  return_value=(123, 'created')), \
                        patch.object(module, '_schedule_campaign_explicit', side_effect=RuntimeError('timeout')):
                    with self.assertRaises(RuntimeError):
                        module._create_and_schedule('g', 'subject', '<html/>', 'name', state, 'daily', day,
                                                    [{'slug': 'aaa'}])
                saved = module._load_state()['campaigns']['daily:2026-10-05']
                self.assertEqual(saved['phase'], 'create_unknown' if failure_at == 'create' else 'schedule_unknown')
                with self.assertRaisesRegex(ValueError, 'already attempted'):
                    module._create_and_schedule('g', 'subject', '<html/>', 'name', state, 'daily', day,
                                                [{'slug': 'aaa'}])

    def test_reconcile_records_provider_sent_only_after_finished(self):
        module = self.module
        state = {'campaigns': {'daily:2026-10-05': {
            'name': 'SMN-Daily-2026-10-05', 'campaign_id': '123', 'phase': 'scheduled'}}}
        response = types.SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {'data': {'id': '123', 'name': 'SMN-Daily-2026-10-05',
                                   'status': 'sent', 'finished_at': '2026-10-05 14:30:01',
                                   'stats': {'sent': 138, 'hard_bounces_count': 0,
                                             'soft_bounces_count': 0}}})
        with patch.object(module.requests, 'get', return_value=response) as get:
            record = module.reconcile_campaign(state, 'daily', date(2026, 10, 5))
        self.assertEqual(record['phase'], 'provider_sent')
        self.assertEqual(record['provider_counts']['sent'], 138)
        self.assertIsNone(record['provider_counts']['delivered'])
        self.assertNotIn('daily_sent', state)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(json.loads(module.STATE_FILE.read_text())['campaigns']['daily:2026-10-05']['phase'],
                         'provider_sent')

    def test_schedule_post_uses_resolved_named_timezone_id(self):
        module = self.module
        zone_response = types.SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {'data': [{'id': '370', 'name': 'Europe/Vilnius'},
                                   {'id': '77', 'name': 'America/New_York'}]})
        schedule_response = types.SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {'data': {'id': '123', 'status': 'ready'}})
        with patch.object(module.requests, 'get', return_value=zone_response), \
                patch.object(module.requests, 'post', return_value=schedule_response) as post:
            module._schedule_campaign_explicit(123, '2026-07-15', '10', '23')
        self.assertEqual(post.call_args.kwargs['json']['schedule']['timezone_id'], 77)
        self.assertEqual(post.call_args.kwargs['json']['schedule']['hours'], '10')

    def test_unknown_timezone_refuses_schedule_post(self):
        module = self.module
        zone_response = types.SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {'data': [{'id': '370', 'name': 'Europe/Vilnius'}]})
        with patch.object(module.requests, 'get', return_value=zone_response), \
                patch.object(module.requests, 'post') as post:
            with self.assertRaisesRegex(ValueError, 'timezone ID unavailable'):
                module._schedule_campaign_explicit(123, '2026-07-15', '10', '23')
        post.assert_not_called()

    def test_legacy_reconciliation_reads_provider_and_preserves_counts(self):
        module = self.module
        module.STATE_FILE.write_text(json.dumps({'daily_sent': ['aaa'], 'campaigns': {}}))
        module.POSTS_JSON.write_text(json.dumps([
            {'slug': 'aaa', 'url': 'https://seasonalmarketnews.com/aaa',
             'published_date': '2026-10-05T12:00:00Z'}]))
        data = {'id': '123', 'name': 'SMN-Daily-2026-10-05', 'status': 'sent',
                'finished_at': '2026-10-05 14:30:01',
                'stats': {'sent': 138, 'hard_bounces_count': 0, 'soft_bounces_count': 0}}
        with patch.object(module, '_get_provider_campaign', return_value=data):
            record = module.attach_existing_campaign('daily', date(2026, 10, 5), '123', ['aaa'])
        self.assertEqual(record['provider_counts']['sent'], 138)
        self.assertIsNone(record['provider_counts']['delivered'])
        self.assertEqual(record['lineup_evidence'], 'legacy_sent_marker_and_current_catalog')
        self.assertEqual(module._load_state()['daily_sent'], ['aaa'])
        with self.assertRaisesRegex(ValueError, 'already journaled'):
            module.attach_existing_campaign('daily', date(2026, 10, 5), '123', ['aaa'])

    def test_legacy_reconciliation_rejects_wrong_campaign(self):
        module = self.module
        module.STATE_FILE.write_text(json.dumps({'daily_sent': ['aaa']}))
        module.POSTS_JSON.write_text(json.dumps([
            {'slug': 'aaa', 'url': 'https://seasonalmarketnews.com/aaa',
             'published_date': '2026-10-05T12:00:00Z'}]))
        with patch.object(module, '_get_provider_campaign', return_value={
                'id': '123', 'name': 'SMN-Daily-2026-10-04'}):
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                module.attach_existing_campaign('daily', date(2026, 10, 5), '123', ['aaa'])
        self.assertNotIn('campaigns', module._load_state())


if __name__ == '__main__':
    unittest.main()
