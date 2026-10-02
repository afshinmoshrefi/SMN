"""Focused, fully mocked tests for the two-provider subscription controller."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

BLOG = Path(__file__).resolve().parents[1]
if str(BLOG) not in sys.path:
    sys.path.insert(0, str(BLOG))

import smn_subscription_daily as controller
import install_smn_subscription as subscription_installer
import subscription_publication

try:
    import fcntl  # noqa: F401 - absent on Windows; activation tests never acquire a lock.
except ImportError:
    fcntl_stub = types.ModuleType('fcntl')
    fcntl_stub.LOCK_EX = 1
    fcntl_stub.LOCK_NB = 2
    fcntl_stub.LOCK_UN = 4
    fcntl_stub.flock = lambda *_args: None
    sys.modules['fcntl'] = fcntl_stub

import install_smn_primary_edition as installer


def _write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def _populate(source: Path, symbols=('AAA',)) -> Path:
    _write(source / 'production' / 'posts.json', json.dumps(
        [{'symbol': symbol} for symbol in symbols]).encode())
    _write(source / 'production-engine-export.json', b'{"engine":"retained"}\n')
    _write(source / 'engine-capture.json', b'{"capture":"retained"}\n')
    _write(source / 'input-selection.json', b'{"selection":"frozen"}\n')
    _write(source / 'input-heroes.json', b'{"heroes":[]}\n')
    return source


def _canonical(root: Path, symbols=('AAA',)) -> Path:
    return _populate(root / '2026-09-30' / 'inputs', symbols)


class SubscriptionDailyTests(unittest.TestCase):
    def test_schedule_and_retry_share_utc_selection_date_across_ny_midnight(self):
        for month in (1, 7):
            for hour in (3, 6):
                instant=datetime(2026,month,5,hour,tzinfo=timezone.utc)
                with patch.object(controller,'datetime') as clock:
                    clock.now.side_effect=lambda tz: instant.astimezone(tz)
                    self.assertEqual(controller.edition_date(),f'2026-{month:02d}-05')
                    clock.now.assert_called_once_with(timezone.utc)

    def test_missing_or_failed_reader_never_reports_success(self):
        for result in ({}, {'providers':{}}, {'providers':{'claude':{'passed':True}}},
                       {'providers':{'chatgpt':{'passed':False}}},
                       {'providers':{'chatgpt':{'passed':True},'claude':{'passed':False}}},
                       {'status':'waiting_unknown'}):
            self.assertEqual(controller.outcome(result),('held',2))
        self.assertEqual(controller.outcome({'status':'waiting_for_selection'}),
                         ('waiting_for_selection',75))
        self.assertEqual(controller.outcome({'providers':{'chatgpt':{'passed':True}}}),
                         ('completed',0))

    def test_main_records_waiting_failure_success_and_explicit_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            cases=[({'status':'waiting_for_selection'},75,'waiting_for_selection'),
                   ({'providers':{'chatgpt':{'passed':False,'reason':'review held'}}},2,'held'),
                   ({'providers':{'chatgpt':{'passed':True}}},0,'completed')]
            for result,code,status in cases:
                with patch.object(sys,'argv',['daily','--root',str(root),'--date','2026-09-30']), \
                        patch.object(controller,'run',return_value=result) as run, \
                        patch('builtins.print'):
                    self.assertEqual(controller.main(),code)
                run.assert_called_once_with(root,'2026-09-30',False,'production',False)
                receipt=controller.load_json(root/'last-run.json')
                self.assertEqual((receipt['date'],receipt['status'],receipt['exit_code']),
                                 ('2026-09-30',status,code))
                self.assertEqual(receipt['result'],result)

    def test_main_exception_records_server_hold_and_failure_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch.object(sys,'argv',['daily','--root',str(root)]), \
                    patch.object(controller,'edition_date',return_value='2026-09-30'), \
                    patch.object(controller,'run',side_effect=RuntimeError('selection transport failed')), \
                    patch('builtins.print'):
                self.assertEqual(controller.main(),2)
            receipt=controller.load_json(root/'last-run.json')
            self.assertEqual((receipt['status'],receipt['exit_code']),('held',2))
            self.assertEqual(receipt['reason'],'selection transport failed')
            self.assertEqual(controller.load_json(root/'2026-09-30/HOLD.json')['reason'],receipt['reason'])

    def test_freeze_inputs_copies_exact_bytes_and_holds_on_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _canonical(root)
            # Provider-local artifacts are deliberately outside the copy allowlist.
            _write(source / 'jobs' / 'old' / 'receipt.json', b'{"must_not_copy":true}')
            destination = root / '2026-09-30' / 'chatgpt'
            comparison = root / '2026-09-30' / 'claude'

            first = controller.freeze_inputs(source, destination)
            second = controller.freeze_inputs(source, comparison)
            self.assertEqual(second, first)
            for relative, digest in first.items():
                copied = destination / relative
                peer_copy = comparison / relative
                original = source / relative
                self.assertEqual(copied.read_bytes(), original.read_bytes())
                self.assertEqual(peer_copy.read_bytes(), copied.read_bytes())
                self.assertEqual(hashlib.sha256(copied.read_bytes()).hexdigest(), digest)
            self.assertFalse((destination / 'jobs').exists())
            self.assertEqual(controller.load_json(destination / 'shared-inputs.json'), first)

            _write(destination / 'production' / 'posts.json', b'{"tampered":true}')
            with self.assertRaisesRegex(ValueError, 'Frozen provider input changed'):
                controller.freeze_inputs(source, destination)

    def test_two_comparison_dates_resume_and_provider_failure_is_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = {'profiles': [], 'hero': 0, 'engine': 0}

            def capture(canonical, date):
                _populate(canonical)
                return {'status': 'captured'}

            def authenticate(profile, _root):
                calls['profiles'].append(profile)

            def run_profile(_edition, date, profile, _canonical):
                if date == '2026-09-30' and profile == 'claude':
                    raise RuntimeError('simulated provider failure')
                return {'passed': True, 'status': 'ready'}

            def prepare_heroes(_canonical, _date):
                calls['hero'] += 1

            def engine(_canonical, _date, _name):
                calls['engine'] += 1

            modules = {
                'subscription_inputs': type('Inputs', (), {'capture': staticmethod(capture),
                                                           'prepare_heroes': staticmethod(prepare_heroes)}),
                'subscription_capture': type('Capture', (), {'engine': staticmethod(engine)}),
            }

            @contextmanager
            def no_lock(_root):
                yield

            with patch.object(controller, 'lock', no_lock), \
                    patch.object(controller, 'authenticate', side_effect=authenticate), \
                    patch.object(controller, 'run_profile', side_effect=run_profile), \
                    patch.dict(sys.modules, modules):
                first = controller.run(root, '2026-09-30')
                self.assertTrue(first['claude_comparison'])
                self.assertTrue(first['providers']['chatgpt']['passed'])
                self.assertEqual(first['providers']['claude']['status'], 'held')
                self.assertFalse(first['api_writer_fallback'])

                # A failed provider does not consume a third date; rerunning the first
                # date resumes the same two-provider comparison.
                resumed = controller.run(root, '2026-09-30')
                self.assertTrue(resumed['claude_comparison'])
                second = controller.run(root, '2026-10-01')
                self.assertTrue(second['claude_comparison'])
                third = controller.run(root, '2026-10-02')
                self.assertFalse(third['claude_comparison'])
                self.assertEqual(list(third['providers']), ['chatgpt'])

            state = controller.load_json(root / 'comparison-state.json')
            self.assertEqual(state['comparison_dates'], ['2026-09-30', '2026-10-01'])
            self.assertEqual(calls['hero'], 4)
            self.assertEqual(calls['engine'], 4)

    def test_all_auth_failures_stop_before_engine_or_paid_hero_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / '2026-09-30' / 'inputs'
            calls = {'hero': 0, 'engine': 0}

            def capture(_canonical, _date):
                return {'status': 'captured'}

            def prepare_heroes(*_args):
                calls['hero'] += 1

            def engine(*_args):
                calls['engine'] += 1

            modules = {
                'subscription_inputs': type('Inputs', (), {'capture': staticmethod(capture),
                                                           'prepare_heroes': staticmethod(prepare_heroes)}),
                'subscription_capture': type('Capture', (), {'engine': staticmethod(engine)}),
            }

            @contextmanager
            def no_lock(_root):
                yield

            with patch.object(controller, 'lock', no_lock), \
                    patch.object(controller, 'authenticate', side_effect=RuntimeError('login required')), \
                    patch.dict(sys.modules, modules):
                with self.assertRaisesRegex(ValueError, 'Subscription authentication required'):
                    controller.run(root, '2026-09-30')

            self.assertEqual(calls, {'hero': 0, 'engine': 0})
            self.assertEqual(controller.load_json(root / 'comparison-state.json')['comparison_dates'],
                             ['2026-09-30'])
            self.assertFalse((source / 'input-heroes.json').exists())

    def test_production_package_rejects_claude_comparison_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / 'smn-daily-state.json', json.dumps({
                'profile': 'claude', 'publication_origin': None}).encode())
            with self.assertRaisesRegex(ValueError, 'Only the explicit ChatGPT reader edition'):
                subscription_publication.package(
                    root, '2026-09-30', 'a' * 40, {},
                    target_origin='https://seasonalmarketnews.com')
            self.assertFalse((root / 'publication-package').exists())

    def test_installer_defaults_to_dev_and_rejects_missing_or_mismatched_activation(self):
        self.assertFalse(installer.PRODUCTION)
        self.assertEqual(installer.ORIGIN, 'https://smn-dev.trxstat.com')
        self.assertEqual(installer.HOST_IP, '192.168.1.180')

    def test_cron_cutover_preserves_other_jobs_and_gates_quote_updates(self):
        selector = '0 2 * * 1-5 root cd /home/flask/blog && python select_news_articles.py'
        email = '0 7 * * 1-5 root cd /home/flask/blog && python send_smn_emails.py daily'
        sunday = '0 9 * * 0 root cd /home/flask/blog && python send_smn_emails.py'
        queue = '0 3 * * 1-5 root cd /home/flask/blog && python daily_article_queue.py'
        quote = '*/10 * * * * root cd /home/flask/blog && python update_news_quotes.py'
        original = '\n'.join((selector, email, sunday, queue, quote)) + '\n'

        changed = subscription_installer.cron_text(original)
        lines = changed.splitlines()
        self.assertEqual(lines[0], '# Subscription schedule replaces this entry: ' + selector)
        self.assertEqual(lines[1], email)
        self.assertEqual(lines[2], '# Subscription schedule replaces this entry: ' + sunday)
        self.assertEqual(lines[3], '# Subscription schedule replaces this entry: ' + queue)
        gate = '[ ! -d /var/lib/tradewave/release-state/smn-production-activation.lock ] && '
        self.assertEqual(lines[4], quote.replace('cd /home/flask/blog && ',
                                                 'cd /home/flask/blog && ' + gate, 1))
        self.assertEqual(sum('python daily_article_queue.py' in line and not line.startswith('#')
                             for line in lines), 0)
        self.assertEqual(sum('python update_news_quotes.py' in line and gate in line
                             for line in lines), 1)

    def test_cron_cutover_rejects_missing_or_duplicate_scheduler_lines(self):
        queue = '0 3 * * 1-5 root cd /home/flask/blog && python daily_article_queue.py'
        quote = '*/10 * * * * root cd /home/flask/blog && python update_news_quotes.py'
        selector = '0 2 * * 1-5 root cd /home/flask/blog && python select_news_articles.py'
        email = '0 7 * * 1-5 root cd /home/flask/blog && python send_smn_emails.py daily'
        sunday = '0 9 * * 0 root cd /home/flask/blog && python send_smn_emails.py'
        cases = {
            'missing queue': [quote, selector, email, sunday],
            'missing quote updater': [queue, selector, email, sunday],
            'missing selector': [queue, quote, email, sunday],
            'missing Sunday recap': [queue, quote, selector, email],
            'duplicate queue': [queue, queue, quote, selector, email, sunday],
            'duplicate quote updater': [queue, quote, quote, selector, email, sunday],
        }
        for name, lines in cases.items():
            with self.subTest(name=name), self.assertRaisesRegex(
                    ValueError, 'one legacy selector, daily API queue, Sunday recap and quote updater'):
                subscription_installer.cron_text('\n'.join(lines) + '\n')

        with patch.object(installer, 'read', side_effect=FileNotFoundError('no activation')):
            with self.assertRaises(FileNotFoundError):
                installer.configure_production()
        self.assertFalse(installer.PRODUCTION)

        with patch.object(installer, 'read', return_value={
                'reader_provider': 'chatgpt', 'source_commit': 'old-commit'}), \
                patch.object(installer.subprocess, 'check_output', return_value='current-commit'):
            with self.assertRaisesRegex(ValueError, 'must match this committed release'):
                installer.configure_production()

        self.assertFalse(installer.PRODUCTION)
        self.assertEqual(installer.ORIGIN, 'https://smn-dev.trxstat.com')
        self.assertEqual(installer.HOST_IP, '192.168.1.180')


if __name__ == '__main__':
    unittest.main()
