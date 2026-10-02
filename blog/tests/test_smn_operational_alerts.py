import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import smn_operational_alerts as alerts
import install_smn_operational_alerts as installer


SETTINGS = {'alerts_enabled': True,
            'alert_recipients': ['one@example.com', 'two@example.com'],
            'daily_generation': {'start_time': '05:30', 'target_time': '07:00',
                                 'timezone': 'America/New_York',
                                 'no_start_grace_minutes': None, 'stall_minutes': None}}


class OperationalAlertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        state_env = patch.dict(os.environ, {'SMN_DASHBOARD_STATE':str(self.root/'dashboard')})
        state_env.start()
        self.addCleanup(state_env.stop)
        self.day = self.root/'2026-10-02'
        self.day.mkdir()
        self.six = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)
        self.seven = datetime(2026, 10, 2, 11, 0, tzinfo=timezone.utc)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def frozen(self):
        self.write(self.day/'inputs/input-selection.json', {'symbols':['ABC', 'XYZ']})
        posts = [{'symbol':symbol,
                  'url':f'https://seasonalmarketnews.com/editions/2026-10-02/{symbol}/article.html',
                  'published_date':'2026-10-02T10:00:00Z', 'edition_id':'subscription-2026-10-02'}
                 for symbol in ('ABC', 'XYZ')]
        self.write(self.day/'inputs/production/posts.json', posts)
        return posts

    def test_target_is_not_deadline_and_no_start_requires_configured_grace(self):
        sent = []
        sender = lambda recipient, incident, from_addr: sent.append((recipient, incident['kind'])) or True
        with patch.dict(os.environ, {'RESEND_API_KEY':'test-only'}):
            alerts.run(self.root, self.seven, SETTINGS, sender=sender)
            self.assertFalse(sent)
            configured = json.loads(json.dumps(SETTINGS))
            configured['daily_generation']['no_start_grace_minutes'] = 30
            alerts.run(self.root, self.seven, configured, sender=sender)
            alerts.run(self.root, self.seven, configured, sender=sender)
        self.assertEqual(sent, [(recipient, 'no-start') for recipient in SETTINGS['alert_recipients']])

    def test_confirmed_hold_alerts_during_run_but_transient_retry_does_not(self):
        self.write(self.root/'last-run.json', {'date':'2026-10-02','status':'running'})
        self.write(self.day/'chatgpt/smn-daily-state.json', {'articles':{
            'ABC':{'held':{'reason':'HTTP 429 rate limit'}},
            'XYZ':{'held':{'reason':'Missing captured primary quotation'}}}})
        incidents = alerts.inspect(self.root, self.six, SETTINGS)
        self.assertEqual([i['kind'] for i in incidents], ['article-held'])
        self.assertIn('XYZ', incidents[0]['detail'])

    def test_completed_process_without_complete_publication_alerts(self):
        self.write(self.root/'last-run.json', {'date':'2026-10-02','status':'completed'})
        posts = self.frozen()
        probe = lambda origin, urls: (posts, {url:'available' for url in urls.values()})
        incidents = alerts.inspect(self.root, self.six, SETTINGS, public_probe=probe)
        self.assertTrue(any(i['kind'] == 'edition-incomplete' for i in incidents))
        self.write(self.day/'chatgpt/production-publication-receipt.json',
                   {'status':'live_verified','production_written':True})
        incidents = alerts.inspect(self.root, self.six, SETTINGS,
                                   public_probe=lambda origin, urls: (posts[:1], {url:'available' for url in urls.values()}))
        self.assertTrue(any('XYZ' in i['detail'] for i in incidents))
        incidents = alerts.inspect(self.root, self.six, SETTINGS, public_probe=probe)
        self.assertFalse(incidents)

    def test_run_hold_alerts_immediately_and_initial_baseline_is_quiet(self):
        self.write(self.root/'last-run.json', {'date':'2026-10-02','status':'held', 'reason':'source check failed'})
        sent = []
        sender = lambda recipient, incident, from_addr: sent.append(recipient) or True
        with patch.dict(os.environ, {'RESEND_API_KEY':'test-only'}):
            alerts.run(self.root, self.six, SETTINGS, sender=sender, baseline=True)
            self.assertFalse(sent)
            self.write(self.root/'last-run.json', {'date':'2026-10-02','status':'held', 'reason':'new source hold'})
            alerts.run(self.root, self.six, SETTINGS, sender=sender)
        self.assertEqual(sent, SETTINGS['alert_recipients'])

    def test_reader_failure_alerts_while_comparison_controller_is_running(self):
        self.write(self.root/'last-run.json', {'date':'2026-10-02','status':'running'})
        self.write(self.day/'chatgpt/smn-daily-state.json', {'articles':{
            'A':{'finalized':True}, 'B':{'held':{'reason':'FDS forecast evidence unsupported'}}}})
        self.write(self.day/'chatgpt/daily-check.json', {'passed':False,'held':['B']})
        kinds = [i['kind'] for i in alerts.inspect(self.root, self.six, SETTINGS)]
        self.assertIn('article-held', kinds)

    def test_claude_only_hold_is_not_reader_failure(self):
        self.write(self.root/'last-run.json', {'date':'2026-10-02','status':'held',
            'result':{'providers':{'chatgpt':{'passed':True}, 'claude':{'passed':False}}}})
        self.write(self.day/'chatgpt/production-publication-receipt.json',
                   {'status':'live_verified','production_written':True})
        posts = self.frozen()
        self.assertEqual(alerts.inspect(self.root, self.six, SETTINGS,
                         public_probe=lambda origin, urls: (posts, {url:'available' for url in urls.values()})), [])

    def test_resend_uses_stable_per_recipient_idempotency_key(self):
        calls = []
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
        def open_request(request, timeout):
            calls.append(request)
            return Response()
        incident = alerts._incident('2026-10-02','article-held','ABC held','ABC alert')
        with patch.dict(os.environ, {'RESEND_API_KEY':'test-only'}), patch.object(alerts, 'urlopen', side_effect=open_request):
            self.assertTrue(alerts.send_resend('one@example.com', incident))
            self.assertTrue(alerts.send_resend('one@example.com', incident))
        self.assertEqual(calls[0].get_header('Idempotency-key'), calls[1].get_header('Idempotency-key'))
        self.assertEqual(json.loads(calls[0].data)['from'], alerts.DEFAULT_FROM)

    def test_only_unsent_recipient_is_retried(self):
        self.write(self.root/'last-run.json', {'date':'2026-10-02','status':'held', 'reason':'source check failed'})
        attempts = []
        def sender(recipient, incident, from_addr):
            attempts.append(recipient)
            return recipient != 'two@example.com' or attempts.count(recipient) > 1
        with patch.dict(os.environ, {'RESEND_API_KEY':'test-only'}):
            alerts.run(self.root, self.six, SETTINGS, sender=sender)
            alerts.run(self.root, self.six, SETTINGS, sender=sender)
        self.assertEqual(attempts, ['one@example.com', 'two@example.com', 'two@example.com'])

    def test_weekend_has_no_generation_alert(self):
        saturday = datetime(2026, 10, 3, 11, 0, tzinfo=timezone.utc)
        configured = json.loads(json.dumps(SETTINGS))
        configured['daily_generation']['no_start_grace_minutes'] = 30
        self.assertEqual(alerts.inspect(self.root, saturday, configured), [])

    def test_scheduler_selector_failure_alerts_without_controller_receipt(self):
        ledger = self.root/'schedule-runs'
        self.write(ledger/'production-selector-2026-10-02.json',
                   {'target':'production','phase':'selector','date':'2026-10-02',
                    'status':'failed','exit_code':2})
        result = alerts.inspect(self.root, self.six, SETTINGS, ledger_dir=ledger)
        self.assertEqual([i['kind'] for i in result], ['scheduler-failed'])

    def test_public_url_404_is_missing_and_network_unknown_is_unverified(self):
        posts = self.frozen()
        self.write(self.root/'last-run.json', {'date':'2026-10-02','status':'completed'})
        self.write(self.day/'chatgpt/production-publication-receipt.json',
                   {'status':'live_verified','production_written':True})
        def probe(status):
            return lambda origin, urls: (posts, {url:status for url in urls.values()})
        missing = alerts.inspect(self.root, self.six, SETTINGS, public_probe=probe('missing'))
        unknown = alerts.inspect(self.root, self.six, SETTINGS, public_probe=probe('unknown'))
        self.assertEqual(missing[0]['kind'], 'edition-incomplete')
        self.assertEqual(unknown[0]['kind'], 'verification-unavailable')

    def test_transient_article_retry_alerts_only_after_terminal_reader_check(self):
        self.write(self.root/'last-run.json', {'date':'2026-10-02','status':'running'})
        self.write(self.day/'chatgpt/smn-daily-state.json', {'articles':{
            'ABC':{'held':{'reason':'HTTP 429 rate limit'}}}})
        self.assertEqual(alerts.inspect(self.root, self.six, SETTINGS), [])
        self.write(self.day/'chatgpt/daily-check.json', {'passed':False,'held':['ABC']})
        result = alerts.inspect(self.root, self.six, SETTINGS)
        self.assertEqual([i['kind'] for i in result], ['reader-incomplete'])

    def test_dev_uses_dev_receipt_and_public_origin(self):
        self.frozen()
        self.write(self.root/'last-run.json', {'date':'2026-10-02','status':'completed','target':'dev'})
        self.write(self.day/'chatgpt/dev-publication-receipt.json', {'status':'live_verified'})
        def probe(origin, urls):
            self.assertEqual(origin, alerts.ORIGINS['dev'])
            posts = [{'symbol':symbol,'url':url,'published_date':'2026-10-02',
                      'edition_id':'subscription-2026-10-02'} for symbol,url in urls.items()]
            return posts, {url:'available' for url in urls.values()}
        self.assertEqual(alerts.inspect(self.root, self.six, SETTINGS, public_probe=probe, target='dev'), [])

    def test_status_reports_preferences_separately_from_relay_readiness(self):
        self.write(self.root/'last-run.json', {'date':'2026-10-02','status':'held','reason':'source hold'})
        with patch.dict(os.environ, {'RESEND_API_KEY':''}):
            result = alerts.run(self.root, self.six, SETTINGS)
        status = json.loads((self.root/'dashboard/operational-alerts-status.json').read_text())
        self.assertEqual(result['sent'], 0)
        self.assertTrue(status['preferences_enabled'])
        self.assertFalse(status['credential_available'])
        self.assertFalse(status['send_ready'])
        self.assertNotIn('RESEND_API_KEY', json.dumps(status))

    def test_configured_stall_uses_saved_reader_progress(self):
        configured = json.loads(json.dumps(SETTINGS))
        configured['daily_generation']['stall_minutes'] = 20
        self.write(self.root/'last-run.json', {'date':'2026-10-02','status':'running',
                   'utc':'2026-10-02T10:00:00+00:00'})
        progress = self.day/'chatgpt/jobs/ABC-20261002-write/state.json'
        self.write(progress, {'status':'running'})
        os.utime(progress, (self.seven.timestamp() - 5*60, self.seven.timestamp() - 5*60))
        self.assertEqual(alerts.inspect(self.root, self.seven, configured), [])
        os.utime(progress, (self.seven.timestamp() - 25*60, self.seven.timestamp() - 25*60))
        result = alerts.inspect(self.root, self.seven, configured)
        self.assertEqual([i['kind'] for i in result], ['no-progress'])
        posts = self.frozen()
        self.write(self.day/'chatgpt/production-publication-receipt.json',
                   {'status':'live_verified','production_written':True})
        self.assertEqual(alerts.inspect(self.root, self.seven, configured,
                         public_probe=lambda origin, urls: (posts, {url:'available' for url in urls.values()})), [])

    def test_installer_unit_is_separate_and_explicitly_targets_dev(self):
        units = installer.unit_texts(Path(__file__).resolve().parents[2], 'dev')
        service = units[installer.SERVICE]
        self.assertIn('EnvironmentFile='+str(installer.SECRET_FILE), service)
        self.assertIn('--target dev', service)
        self.assertNotIn('smn-newsletter', service)

    def test_waiting_for_selection_needs_configured_grace(self):
        self.write(self.root/'last-run.json', {'date':'2026-10-02','status':'waiting_for_selection'})
        self.assertEqual(alerts.inspect(self.root, self.seven, SETTINGS), [])
        configured = json.loads(json.dumps(SETTINGS))
        configured['daily_generation']['no_start_grace_minutes'] = 30
        self.assertEqual([i['kind'] for i in alerts.inspect(self.root, self.seven, configured)], ['no-start'])

    def test_recovered_reader_suppresses_old_scheduler_failure(self):
        ledger = self.root/'schedule-runs'
        self.write(ledger/'production-selector-2026-10-02.json',
                   {'target':'production','phase':'selector','date':'2026-10-02',
                    'status':'failed','exit_code':2})
        posts = self.frozen()
        self.write(self.root/'last-run.json', {'date':'2026-10-02','status':'completed'})
        self.write(self.day/'chatgpt/production-publication-receipt.json',
                   {'status':'live_verified','production_written':True})
        self.assertEqual(alerts.inspect(self.root, self.seven, SETTINGS, ledger_dir=ledger,
                         public_probe=lambda origin, urls: (posts, {url:'available' for url in urls.values()})), [])

    def test_events_and_visual_artifacts_count_as_progress(self):
        configured = json.loads(json.dumps(SETTINGS))
        configured['daily_generation']['stall_minutes'] = 20
        self.write(self.root/'last-run.json', {'date':'2026-10-02','status':'running',
                   'utc':'2026-10-02T10:00:00+00:00'})
        event = self.day/'chatgpt/jobs/ABC-20261002-write/events.jsonl'
        event.parent.mkdir(parents=True)
        event.write_text('{}\n')
        os.utime(event, (self.seven.timestamp()-5*60, self.seven.timestamp()-5*60))
        self.assertEqual(alerts.inspect(self.root, self.seven, configured), [])

    def test_production_installer_requires_exact_proof_and_snapshots(self):
        repo = Path(__file__).resolve().parents[2]
        def command(*args):
            if args[:2] == ('hostname', '-I'): return installer.HOSTS['production']
            if 'rev-parse' in args: return 'a'*40
            return ''
        with patch.object(installer, 'run', side_effect=command):
            with self.assertRaisesRegex(ValueError, 'Dev proof'):
                installer._preflight(repo, 'production')
            proof = self.root/'proof.json'
            snapshot = self.root/'snapshots.json'
            self.write(proof, {'source_commit':'b'*40,'status':'dev_qualified',
                               'live_verification_sha256':'x'})
            self.write(snapshot, {'source_commit':'a'*40,'date':self.seven.date().isoformat(),
                                  'production_web_snapshot':'web','production_app_snapshot':'app','approved_by':'human'})
            with self.assertRaisesRegex(ValueError, 'Exact-release'):
                installer._preflight(repo, 'production', proof, snapshot)

    def test_installer_failure_cleanup_and_rollback_preserve_peer_edit(self):
        repo = Path(__file__).resolve().parents[2]
        units = self.root/'units'
        units.mkdir()
        receipt = self.root/'activation.json'
        def systemctl(command, check=False):
            if command[1:3] == ['enable','--now']:
                raise RuntimeError('injected activation failure')
        with patch.object(installer, 'UNIT_DIR', units), patch.object(installer, 'RECEIPT', receipt), \
             patch.object(installer, '_secret_ready', return_value=True), \
             patch.object(installer, '_preflight', return_value=('a'*40, None)), \
             patch.object(installer.os, 'geteuid', return_value=0, create=True), \
             patch.object(installer.subprocess, 'run', side_effect=systemctl):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                installer.install(repo, 'dev')
        self.assertFalse((units/installer.SERVICE).exists())
        self.assertEqual(json.loads(receipt.read_text())['status'], 'rolled_back')
        unit = units/installer.SERVICE
        timer = units/installer.TIMER
        unit.write_text('owned')
        timer.write_text('owned')
        self.write(receipt, {'status':'active','units':{str(unit):installer.digest(unit),
                                                    str(timer):installer.digest(timer)}})
        unit.write_text('peer edit')
        with patch.object(installer, 'UNIT_DIR', units):
            with self.assertRaisesRegex(ValueError, 'changed after installation'):
                installer.rollback(receipt)
        self.assertEqual(unit.read_text(), 'peer edit')


if __name__ == '__main__':
    unittest.main()
