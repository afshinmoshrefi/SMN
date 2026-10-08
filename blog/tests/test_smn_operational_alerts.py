import json
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
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
    def test_campaign_queued_overdue_after_timezone_bound_grace_only(self):
        record={'provider_status':'queued','poll_count':4,'scheduled_for_account_time':'2026-10-02 07:00 America/New_York'}
        path=self.root/'campaign-state.json';self.write(path,{'campaigns':{'daily:2026-10-02':record}})
        with patch.object(alerts,'CAMPAIGN_STATE',path):
            for minutes in (-1,0,29,30):
                self.assertEqual(alerts._campaign_incidents(self.seven+timedelta(minutes=minutes),'production'),[])
            self.assertEqual(len(alerts._campaign_incidents(self.seven+timedelta(minutes=31),'production')),1)
            record.update(provider_status='sent',provider_finished_at='2026-10-02T11:10Z')
            self.write(path,{'campaigns':{'daily:2026-10-02':record}})
            self.assertEqual(alerts._campaign_incidents(self.seven+timedelta(hours=2),'production'),[])
        self.assertFalse(alerts._campaign_overdue({'scheduled_for_utc':'2026-10-02T11:00:00'},self.seven+timedelta(hours=2)))
    def test_campaign_failures_unknown_and_poll_exhaustion_remain_independent(self):
        path=self.root/'campaign-state.json'
        self.write(path,{'campaigns':{'daily:2026-10-02':{'provider_status':'failed'},
            'unknown':{'phase':'create_unknown'},'pending':{'provider_status':'queued','poll_count':48},
            'done':{'provider_status':'sent','provider_finished_at':'2026-10-02T12:00Z','poll_count':48}}})
        with patch.object(alerts,'CAMPAIGN_STATE',path):
            self.assertEqual(len(alerts._campaign_incidents(self.seven,'production')),3)
            self.assertEqual(alerts._campaign_incidents(self.seven,'dev'),[])
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        relay = patch.object(alerts, 'retrieve_resend', return_value=None)
        relay.start()
        self.addCleanup(relay.stop)
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
        sender = lambda recipient, incident, from_addr: sent.append((recipient, incident['kind'])) or {'status':'accepted','provider_id':'test-id'}
        with patch.dict(os.environ, {'RESEND_API_KEY':'test-only'}):
            alerts.run(self.root, self.seven, SETTINGS, sender=sender)
            self.assertFalse(sent)
            configured = json.loads(json.dumps(SETTINGS))
            configured['daily_generation']['no_start_grace_minutes'] = 30
            alerts.run(self.root, self.seven, configured, sender=sender)
            alerts.run(self.root, self.seven, configured, sender=sender)
        self.assertEqual(sent, [(recipient, 'no-start') for recipient in SETTINGS['alert_recipients']])

    def test_continuity_optin_deadline_detects_no_start_and_running_worker(self):
        policy = self.root/'activation.json'
        self.write(policy, {'reader_provider': 'chatgpt', 'publication_policy': 'continuity-v1'})
        with patch.object(alerts, 'CONTINUITY_ACTIVATION', policy):
            self.assertEqual(alerts.inspect(self.root, self.seven-timedelta(minutes=1), SETTINGS), [])
            self.assertEqual(alerts.inspect(self.root, self.seven, SETTINGS)[0]['kind'], 'deadline-missed')
            self.write(self.root/'last-run.json', {'date': '2026-10-02', 'status': 'running'})
            self.assertEqual(alerts.inspect(self.root, self.seven, SETTINGS)[0]['kind'], 'deadline-missed')
            posts = self.frozen()
            self.write(self.day/'chatgpt/production-publication-receipt.json',
                       {'status': 'live_verified', 'production_written': True})
            probe = lambda origin, urls: (posts, {url: 'available' for url in urls.values()})
            self.assertEqual(alerts.inspect(self.root, self.seven, SETTINGS, public_probe=probe), [])

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
        sender = lambda recipient, incident, from_addr: sent.append(recipient) or {'status':'accepted','provider_id':'test-id'}
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
            def read(self, *args): return b'{"id":"test-id"}'
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
            return {'status':'accepted','provider_id':'test-id'} if recipient != 'two@example.com' or attempts.count(recipient) > 1 else False
        with patch.dict(os.environ, {'RESEND_API_KEY':'test-only'}):
            alerts.run(self.root, self.six, SETTINGS, sender=sender)
            alerts.run(self.root, self.six+timedelta(minutes=5), SETTINGS, sender=sender)
        self.assertEqual(attempts, ['one@example.com', 'two@example.com', 'two@example.com'])

    def test_weekend_has_no_generation_alert(self):
        saturday = datetime(2026, 10, 3, 11, 0, tzinfo=timezone.utc)
        configured = json.loads(json.dumps(SETTINGS))
        configured['daily_generation']['no_start_grace_minutes'] = 30
        self.assertEqual(alerts.inspect(self.root, saturday, configured), [])

    def test_verified_reader_does_not_hide_failed_weekday_newsletter(self):
        posts = self.frozen()
        self.write(self.day/'chatgpt/production-publication-receipt.json',
                   {'status':'live_verified', 'production_written':True})
        ledger = self.root/'schedule-runs'
        self.write(ledger/'production-weekday_newsletter-2026-10-02.json',
                   {'target':'production', 'phase':'weekday_newsletter', 'date':'2026-10-02',
                    'status':'failed', 'exit_code':2})
        result = alerts.inspect(self.root, self.seven, SETTINGS,
            public_probe=lambda origin, urls: (posts, {url:'available' for url in urls.values()}),
            ledger_dir=ledger)
        self.assertEqual([item['kind'] for item in result], ['newsletter-delivery-failed'])
        self.assertIn('exit 2', result[0]['detail'])

    def test_partial_coverage_does_not_create_missing_newsletter_alert(self):
        self.frozen()
        self.write(self.day/'chatgpt/production-publication-receipt.json',
                   {'status':'live_verified', 'production_written':True,
                    'publication_policy':'continuity-v1', 'edition_date':'2026-10-02',
                    'expected_symbols':['ABC','XYZ'], 'published_symbols':['ABC'],
                    'pending_symbols':['XYZ'], 'coverage_status':'partial', 'complete':False})
        result = alerts.inspect(self.root, self.seven, SETTINGS,
                                public_probe=lambda origin, urls: ([], {}),
                                ledger_dir=self.root/'schedule-runs')
        self.assertFalse(any(item['kind'].startswith('newsletter-') for item in result))

    def test_stuck_weekday_and_failed_sunday_delivery_are_independent(self):
        ledger = self.root/'schedule-runs'
        self.write(ledger/'production-weekday_newsletter-2026-10-02.json',
                   {'target':'production', 'phase':'weekday_newsletter', 'date':'2026-10-02',
                    'status':'running', 'started_at':'2026-10-02T10:00:00+00:00'})
        before = self.six + timedelta(minutes=29)
        after = self.six + timedelta(minutes=31)
        self.assertFalse(any(item['kind'].startswith('newsletter-') for item in
                             alerts.inspect(self.root, before, SETTINGS, ledger_dir=ledger)))
        self.assertIn('newsletter-delivery-stuck', [item['kind'] for item in
                      alerts.inspect(self.root, after, SETTINGS, ledger_dir=ledger)])
        sunday = datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)
        self.write(ledger/'production-sunday_summary-2026-10-04.json',
                   {'target':'production', 'phase':'sunday_summary', 'date':'2026-10-04',
                    'status':'failed', 'exit_code':2})
        self.assertEqual([item['kind'] for item in alerts.inspect(self.root, sunday, SETTINGS,
                          ledger_dir=ledger)], ['newsletter-delivery-failed'])

    def test_running_orphan_ledger_uses_saved_progress_without_controller(self):
        configured = json.loads(json.dumps(SETTINGS))
        configured['daily_generation'].update(no_start_grace_minutes=15, stall_minutes=30)
        ledger = self.root/'schedule-runs'
        self.write(ledger/'production-reader-2026-10-02.json',
                   {'target':'production','phase':'reader','date':'2026-10-02',
                    'status':'running','started_at':'2026-10-02T09:30:00+00:00'})
        result = alerts.inspect(self.root, self.seven, configured, ledger_dir=ledger)
        self.assertEqual([i['kind'] for i in result], ['no-progress'])
        event = self.day/'chatgpt/jobs/ABC-write/events.jsonl'
        self.write(event, {})
        os.utime(event, (self.seven.timestamp()-60, self.seven.timestamp()-60))
        self.assertEqual(alerts.inspect(self.root, self.seven, configured, ledger_dir=ledger), [])

    def test_exhausted_continuity_retries_are_actionable(self):
        self.write(self.day/'continuity-progress.json', {'date':'2026-10-02',
            'status':'needs_attention', 'reason':'Cumulative model-job budget exhausted'})
        result = alerts.inspect(self.root, self.seven, SETTINGS)
        self.assertEqual(result[0]['kind'], 'continuity-needs-attention')
        self.assertIn('budget exhausted', result[0]['detail'])

    def test_interrupted_continuity_worker_cannot_suppress_stall_alert(self):
        configured = json.loads(json.dumps(SETTINGS))
        configured['daily_generation'].update(no_start_grace_minutes=15, stall_minutes=30)
        self.write(self.day/'continuity-progress.json', {'date':'2026-10-02',
            'status':'running', 'updated_utc':'2026-10-02T09:30:00+00:00'})
        result = alerts.inspect(self.root, self.seven, configured)
        self.assertEqual(result[0]['kind'], 'no-progress')

    def test_public_get_rejects_wrong_html_200_and_only_known_edge_addition(self):
        posts = self.frozen()
        urls = {p['symbol']:p['url'] for p in posts}
        good = b'<html><body>Approved article</body></html>'
        receipt = {'files':{u.split('/',3)[-1]:hashlib.sha256(good).hexdigest() for u in urls.values()}}
        class Response(io.BytesIO):
            status = 200
        def probe(body):
            requests = []
            def fetch(request, timeout):
                requests.append(request)
                return Response(json.dumps(posts).encode() if request.full_url.endswith('/posts.json') else body)
            with patch.object(alerts, 'urlopen', side_effect=fetch):
                result = alerts._public_probe(alerts.ORIGINS['production'], urls, receipt)
            self.assertTrue(all(r.get_method() == 'GET' for r in requests))
            return set(result[1].values())
        self.assertEqual(probe(good), {'available'})
        self.assertEqual(probe(b'<html>old edition</html>'), {'changed'})
        self.assertEqual(probe(good + alerts.CF_BEACON), {'available'})
        self.assertEqual(probe(good + alerts.CF_BEACON + b'\n'), {'available'})
        self.assertEqual(probe(good + alerts.CF_BEACON + b'\n\n'), {'changed'})
        self.assertEqual(probe(good + b'<script>unexpected()</script>'), {'changed'})

    def test_membership_public_preview_is_bound_to_saved_copy(self):
        url = 'https://smn-dev.trxstat.com/editions/2026-10-02/ABC/article.html'
        content = {'headline':{'text':'ABC & earnings'}, 'preview':[{'text':'Approved preview.'}],
                   'qualification':{'text':'Historical study.'}, 'full_article_value':{'text':'Read complete analysis.'}}
        receipt = {'membership_publication':{'new':[{'url':url, 'preview_content':content}]}}
        html = b'<main><h1>ABC &amp; earnings</h1><p role="status">Sign in</p><p>Approved preview.</p><p class="qualification">Historical study.</p><aside><h2>Complete</h2><p>Read complete analysis.</p><p class="small">Public access</p></aside></main>'
        self.assertEqual(alerts._matches_content(html, 'ABC', url, receipt), 'available')
        self.assertEqual(alerts._matches_content(html.replace(b'Approved', b'Stale'), 'ABC', url, receipt), 'changed')

    def test_membership_catalog_relative_urls_and_public_metadata_survive_cache(self):
        posts = self.frozen()
        public = [{k:v for k,v in p.items() if k != 'edition_id'} for p in posts]
        for p in public: p['url'] = '/' + p['url'].split('/',3)[-1]
        self.write(self.day/'chatgpt/production-publication-receipt.json',
                   {'status':'live_verified','production_written':True,
                    'membership_publication':{'new':[{'url':p['url']} for p in posts]}})
        calls = []
        def probe(origin, urls):
            calls.append(1)
            return public, {url:'available' for url in urls.values()}
        with patch.dict(os.environ, {'RESEND_API_KEY':''}):
            first = alerts.run(self.root, self.six, SETTINGS, public_probe=probe)
            second = alerts.run(self.root, self.six+timedelta(seconds=60), SETTINGS, public_probe=probe)
        self.assertEqual((first['observed'], second['observed']), (0,0))
        self.assertEqual(len(calls), 1)
        for changed in ('origin', 'date'):
            invalid = json.loads(json.dumps(public))
            if changed == 'origin': invalid[0]['url'] = 'https://example.com' + invalid[0]['url']
            else: invalid[0]['published_date'] = '2026-10-01'
            result = alerts.inspect(self.root, self.six, SETTINGS,
                public_probe=lambda origin, urls: (invalid, {url:'available' for url in urls.values()}))
            self.assertEqual(result[0]['kind'], 'edition-incomplete')

    def test_exact_observed_v4_beacon_only_preserves_receipt_identity(self):
        body=b'<html>Retained article</html>'
        url='https://seasonalmarketnews.com/editions/2026-10-08/OMC/article.html'
        receipt={'files':{url.split('/',3)[-1]:hashlib.sha256(body).hexdigest()}}
        beacon=alerts.CF_OBSERVED_V4_BEACON
        cases=[(body,'available'),(body+beacon,'available'),(body+beacon+b'\n','available'),
               (body+beacon*2,'changed'),(body+beacon+b'\n\n','changed'),
               (body+b'changed'+beacon,'changed'),(body+b'<script>x</script>','changed'),
               (body+beacon.replace(b'4bc70e2c',b'4bc70e2d'),'changed'),
               (body+alerts.CF_BEACON+beacon,'changed')]
        for value,expected in cases:
            with self.subTest(expected=expected,body=value):
                self.assertEqual(alerts._matches_content(value,'OMC',url,receipt),expected)

    def test_empty_provider_ack_is_not_delivery_and_acceptance_dedupes(self):
        self.write(self.root/'last-run.json', {'date':'2026-10-02','status':'held','reason':'source hold'})
        class Response(io.BytesIO):
            status = 202
        incident = alerts._incident('2026-10-02','article-held','ABC held','ABC alert')
        with patch.dict(os.environ, {'RESEND_API_KEY':'test-only'}), \
             patch.object(alerts, 'urlopen', return_value=Response(b'{}')):
            self.assertFalse(alerts.send_resend('one@example.com', incident))
        with patch.dict(os.environ, {'RESEND_API_KEY':'test-only'}):
            no_id = alerts.run(self.root, self.six, SETTINGS, sender=lambda *a: True)
            self.assertEqual(no_id['accepted'], 0)
            accepted = alerts.run(self.root, self.six+timedelta(minutes=5), SETTINGS,
                sender=lambda *a: {'status':'accepted','provider_id':'provider-123'})
            again = alerts.run(self.root, self.six+timedelta(minutes=6), SETTINGS,
                sender=lambda *a: self.fail('Accepted message must not be resent'))
        self.assertEqual(accepted['accepted'], 2)
        self.assertEqual(accepted['delivered'], 0)
        self.assertEqual(again['accepted'], 0)
        state = json.loads((self.root/'operational-alerts-state.json').read_text())
        self.assertFalse(state['delivered'])
        self.assertTrue(all(v['provider_id'] == 'provider-123' for v in state['accepted'].values()))

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

    def test_unchanged_receipt_rechecks_public_after_five_minutes(self):
        posts = self.frozen()
        self.write(self.root/'last-run.json', {'date':'2026-10-02','status':'completed'})
        self.write(self.day/'chatgpt/production-publication-receipt.json',
                   {'status':'live_verified','production_written':True})
        calls = []
        def probe(origin, urls):
            calls.append(1)
            return posts, {url: 'available' if len(calls) == 1 else 'missing' for url in urls.values()}
        with patch.dict(os.environ, {'RESEND_API_KEY':''}):
            first = alerts.run(self.root, self.six, SETTINGS, public_probe=probe)
            cached = alerts.run(self.root, self.six + timedelta(seconds=299), SETTINGS, public_probe=probe)
            expired = alerts.run(self.root, self.six + timedelta(seconds=300), SETTINGS, public_probe=probe)
        self.assertEqual(first['observed'], 0)
        self.assertEqual(cached['observed'], 0)
        self.assertEqual(expired['observed'], 1)
        self.assertEqual(len(calls), 2)

    def test_missing_transport_does_not_acknowledge_incident_or_prevent_later_delivery(self):
        self.write(self.root/'last-run.json', {'date':'2026-10-02','status':'held', 'reason':'source hold'})
        attempts = []
        sender = lambda recipient, incident, from_addr: attempts.append(recipient) or {'status':'accepted','provider_id':'test-id'}
        with patch.dict(os.environ, {'RESEND_API_KEY':''}):
            alerts.run(self.root, self.six, SETTINGS, sender=sender)
        status = json.loads((self.root/'dashboard/operational-alerts-status.json').read_text())
        self.assertEqual(status['delivery_state'], 'missing_credential')
        self.assertEqual(status['unacknowledged_incidents'], 1)
        self.assertFalse(attempts)
        with patch.dict(os.environ, {'RESEND_API_KEY':'test-only'}):
            alerts.run(self.root, self.six, SETTINGS, sender=sender)
            alerts.run(self.root, self.six, SETTINGS, sender=sender)
        self.assertEqual(attempts, SETTINGS['alert_recipients'])

    def test_partial_coverage_is_pending_not_missing_declared_public_article(self):
        posts = self.frozen()
        receipt = {'status':'live_verified','production_written':True,
                   'edition_date':'2026-10-02','publication_policy':'continuity-v1','coverage_status':'partial','complete':False,
                   'expected_symbols':['ABC','XYZ'],'published_symbols':['ABC'],'pending_symbols':['XYZ']}
        self.write(self.day/'chatgpt/production-publication-receipt.json', receipt)
        def probe(origin, urls):
            self.assertEqual(list(urls), ['ABC'])
            return posts[:1], {url:'available' for url in urls.values()}
        result = alerts.inspect(self.root, self.six, SETTINGS, public_probe=probe)
        self.assertEqual(result[0]['kind'], 'coverage-pending')
        result = alerts.inspect(self.root, self.six, SETTINGS,
                                public_probe=lambda origin, urls: ([], {url:'missing' for url in urls.values()}))
        self.assertEqual(result[0]['kind'], 'edition-incomplete')
        receipt['complete'] = True
        self.write(self.day/'chatgpt/production-publication-receipt.json', receipt)
        self.assertEqual(alerts.inspect(self.root, self.six, SETTINGS)[0]['kind'], 'edition-incomplete')

    def test_notice_with_unknown_selection_is_not_false_completion(self):
        self.write(self.day/'chatgpt/production-publication-receipt.json',
                   {'status':'live_verified','production_written':True,'publication_policy':'continuity-v1',
                    'edition_date':'2026-10-02','coverage_status':'notice','complete':False,'expected_symbols':[],
                    'published_symbols':[],'pending_symbols':[]})
        self.assertEqual(alerts.inspect(self.root, self.six, SETTINGS)[0]['kind'], 'coverage-pending')

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
