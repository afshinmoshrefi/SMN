import json
import hashlib
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import operational_schedule
import operational_settings
import pub_dashboard
import smn_subscription_daily
import dashboard_auth


class OperationalSettingsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        state = Path(temp.name)
        self.state = state
        binding = patch.object(operational_settings, 'FILE', state/'operational-settings.json')
        binding.start()
        self.addCleanup(binding.stop)
        self.client = pub_dashboard.app.test_client()

    def test_first_admin_save_of_alert_preferences_without_schedule_change(self):
        settings = operational_settings.load()
        settings['alerts_enabled'] = True
        settings['alert_recipients'] = ['ops@example.com']
        identity = {'kind': 'admin', 'name': 'Operator', 'user_id': 'test'}
        with patch.object(dashboard_auth, 'auth_required', return_value=True), \
                patch.object(dashboard_auth, 'check_bearer', return_value=None), \
                patch.object(pub_dashboard, '_session_identity', return_value=identity), \
                patch.object(pub_dashboard, 'AUDIT_LOG', self.state/'audit.jsonl'):
            response = self.client.put('/api/operational-settings', json=settings,
                headers={'X-SMN-Dashboard': '1', 'Origin': 'http://localhost'})
            self.assertEqual(response.status_code, 200, response.get_json())
            saved = response.get_json()['data']['settings']
            self.assertNotIn('effective_from', saved)
            self.assertEqual(operational_settings.load()['alert_recipients'], ['ops@example.com'])
            self.assertEqual(operational_settings.load()['daily_generation'],
                             operational_settings.DEFAULTS['daily_generation'])
            for bad in (None, [], {'alerts_enabled': 'yes'},
                        {**settings, 'daily_generation': []}):
                with self.subTest(bad=bad):
                    response = self.client.put('/api/operational-settings', json=bad,
                        headers={'X-SMN-Dashboard': '1'})
                    self.assertEqual(response.status_code, 400, response.get_json())

    def test_defaults_and_future_schedule(self):
        default = operational_settings.load()
        self.assertEqual(default['alert_recipients'], [])
        self.assertFalse(default['alerts_enabled'])
        self.assertEqual(default['daily_generation'], {'start_time': '05:30', 'target_time': '07:00',
                                                       'timezone': 'America/New_York',
                                                       'no_start_grace_minutes': None, 'stall_minutes': None})
        self.assertEqual(default['sunday_summary']['time'], '09:00')
        self.assertEqual(default['weekday_newsletter'], {'time': '07:00', 'timezone': 'America/New_York'})
        monday = datetime(2026, 10, 5, 9, 30, tzinfo=timezone.utc)
        self.assertEqual(operational_schedule.due(monday, 'production', default), [('daily', '2026-10-05')])
        self.assertEqual(operational_schedule.due(monday.replace(hour=8), 'production', default), [('selector', '2026-10-05')])
        self.assertEqual(operational_schedule.due(monday, 'dev', default), [('daily', '2026-10-05')])
        self.assertEqual(operational_schedule.due(monday.replace(hour=10), 'dev', default), [('daily', '2026-10-05')])
        sunday = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(operational_schedule.due(sunday, 'production', default), [('sunday_summary', '2026-10-04')])
        self.assertEqual(operational_schedule.due(monday.replace(hour=11, minute=0),
            'production', default, newsletter_only=True), [('weekday_newsletter', '2026-10-05')])
        self.assertEqual(operational_schedule.due(monday, 'production', default, newsletter_only=True), [])

    def test_newsletter_requires_exact_verified_live_reader_lineup(self):
        day = '2026-10-01'
        root, web = self.state/'runs', self.state/'web'
        inputs = root/day/'inputs'
        primary = root/day/'chatgpt'/'primary'
        inputs.mkdir(parents=True)
        primary.mkdir(parents=True)
        web.mkdir()
        symbols = ['AAA', 'BBB']
        (inputs/'input-selection.json').write_text(json.dumps({'date': day, 'symbols': symbols}))
        (root/day/'schedule-settings.json').write_text(json.dumps({
            'date': day, 'daily_generation': operational_settings.DEFAULTS['daily_generation']}))
        urls, files, posts = [], {}, []
        for symbol in symbols:
            rel = f'editions/{day}/{symbol}/article.html'
            url = 'https://seasonalmarketnews.com/' + rel
            article = web/rel
            article.parent.mkdir(parents=True)
            article.write_text('verified '+symbol)
            urls.append(url)
            files[rel] = hashlib.sha256(article.read_bytes()).hexdigest()
            posts.append({'url': url, 'source_commit': 'abc', 'edition_id': 'subscription-'+day,
                          'published_date': day+'T12:00:00Z', 'publish_status': 'true'})
            (primary/(symbol+'.receipt.json')).write_text(json.dumps({
                'fetched_utc': day+'T10:00:00+00:00'}))
        (web/'posts.json').write_text(json.dumps(posts))
        receipt = {'status': 'live_verified', 'production_written': True, 'edition_date': day,
                   'source_commit': 'abc', 'urls': urls, 'files': files}
        receipt_path = root/day/'chatgpt'/'production-publication-receipt.json'
        receipt_path.write_text(json.dumps(receipt))
        self.assertEqual(operational_schedule.verified_reader_urls(root, day, web), set(urls))
        receipt['urls'] = urls[:1]
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, 'lineup'):
            operational_schedule.verified_reader_urls(root, day, web)
        receipt['urls'] = urls
        receipt_path.write_text(json.dumps(receipt))
        (web/f'editions/{day}/BBB/article.html').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            operational_schedule.verified_reader_urls(root, day, web)

    def test_newsletter_time_cannot_precede_seven_new_york(self):
        settings = operational_settings.load()
        settings['weekday_newsletter'] = {'time': '06:59', 'timezone': 'America/New_York'}
        with self.assertRaisesRegex(ValueError, '07:00 America/New_York'):
            operational_settings.validate(settings)

    def test_validated_persistent_settings_and_admin_route(self):
        settings = operational_settings.load()
        settings['alerts_enabled'] = True
        settings['alert_recipients'] = ['ops@example.com']
        settings['daily_generation'] = {**settings['daily_generation'], 'start_time': '06:00'}
        settings['sunday_summary']['enabled'] = False
        with patch.dict('os.environ', {'SMN_DASHBOARD_AUTH': 'off'}):
            response = self.client.put('/api/operational-settings', json=settings,
                                       headers={'X-SMN-Dashboard': '1'})
            self.assertEqual(response.status_code, 200, response.get_json())
            saved = response.get_json()['data']['settings']
            self.assertEqual(saved['daily_generation']['timezone'], 'America/New_York')
            self.assertEqual(saved, json.loads(operational_settings.FILE.read_text()))
            self.assertEqual(self.client.get('/api/operational-settings').get_json()['data']['settings'], saved)
            bad = dict(settings, alert_recipients=['not-an-address'])
            self.assertEqual(self.client.put('/api/operational-settings', json=bad,
                                             headers={'X-SMN-Dashboard': '1'}).status_code, 400)
            self.assertEqual(operational_settings.load(), saved)
            self.assertEqual(self.client.put('/api/operational-settings', json=settings).status_code, 403)
            self.assertEqual(self.client.put('/api/operational-settings', json=settings,
                headers={'X-SMN-Dashboard': '1', 'Origin': 'https://elsewhere.example'}).status_code, 403)
        with patch.object(dashboard_auth, 'auth_required', return_value=True), \
                patch.object(dashboard_auth, 'check_bearer', return_value={'kind': 'agent', 'name': 'worker'}):
            self.assertEqual(self.client.get('/api/operational-settings').status_code, 403)

    def test_scheduled_window_rejects_overnight_source(self):
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            date = '2026-10-05'
            at_start = datetime(2026, 10, 5, 9, 30, tzinfo=timezone.utc)
            self.assertTrue(smn_subscription_daily.scheduled_window(root, date, at_start))
            source = root/date/'chatgpt'/'sources.json'
            source.parent.mkdir(parents=True)
            source.write_text('{}')
            import os
            old = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc).timestamp()
            os.utime(source, (old, old))
            with self.assertRaisesRegex(ValueError, 'Pre-window'):
                smn_subscription_daily.scheduled_window(root, date, at_start)
            with self.assertRaisesRegex(ValueError, 'before'):
                smn_subscription_daily.scheduled_window(root, date,
                    datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc))
            source.unlink()
            self.assertTrue(smn_subscription_daily.scheduled_window(root, date,
                datetime(2026, 10, 5, 11, 0, tzinfo=timezone.utc)))
            primary = root/date/'chatgpt'/'primary'
            primary.mkdir()
            text = primary/'AAA.txt'
            text.write_text('source')
            import hashlib
            receipt = {'edition_date': date, 'symbol': 'AAA',
                       'fetched_utc': '2026-10-05T09:00:00+00:00',
                       'text_sha256': hashlib.sha256(text.read_bytes()).hexdigest()}
            receipt_path = primary/'AAA.receipt.json'
            receipt_path.write_text(json.dumps(receipt))
            fresh = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc).timestamp()
            os.utime(text, (fresh, fresh))
            os.utime(receipt_path, (fresh, fresh))
            with self.assertRaisesRegex(ValueError, 'receipt predates'):
                smn_subscription_daily.scheduled_window(root, date,
                    datetime(2026, 10, 5, 11, 0, tzinfo=timezone.utc))

    def test_evening_edit_takes_effect_next_local_day(self):
        old = operational_settings.load()
        changed = json.loads(json.dumps(old))
        changed['daily_generation']['start_time'] = '06:00'
        evening = datetime(2026, 10, 3, 1, 0, tzinfo=timezone.utc)  # Oct 2, 21:00 New York
        saved = operational_settings.save(changed, 'test', evening)
        self.assertEqual(saved['effective_from']['daily_generation'], '2026-10-03')
        self.assertEqual(operational_settings.for_instant(evening)['daily_generation'], old['daily_generation'])
        next_morning = datetime(2026, 10, 3, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(operational_settings.for_instant(next_morning)['daily_generation']['start_time'], '06:00')


if __name__ == '__main__':
    unittest.main()
