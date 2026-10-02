import json
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
        binding = patch.object(operational_settings, 'FILE', state/'operational-settings.json')
        binding.start()
        self.addCleanup(binding.stop)
        self.client = pub_dashboard.app.test_client()

    def test_defaults_and_future_schedule(self):
        default = operational_settings.load()
        self.assertEqual(default['alert_recipients'], [])
        self.assertFalse(default['alerts_enabled'])
        self.assertEqual(default['daily_generation'], {'start_time': '05:30', 'target_time': '07:00',
                                                       'timezone': 'America/New_York',
                                                       'no_start_grace_minutes': None, 'stall_minutes': None})
        self.assertEqual(default['sunday_summary']['time'], '09:00')
        monday = datetime(2026, 10, 5, 9, 30, tzinfo=timezone.utc)
        self.assertEqual(operational_schedule.due(monday, 'production', default), [('daily', '2026-10-05')])
        self.assertEqual(operational_schedule.due(monday.replace(hour=8), 'production', default), [('selector', '2026-10-05')])
        self.assertEqual(operational_schedule.due(monday, 'dev', default), [('daily', '2026-10-05')])
        self.assertEqual(operational_schedule.due(monday.replace(hour=10), 'dev', default), [('daily', '2026-10-05')])
        sunday = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(operational_schedule.due(sunday, 'production', default), [('sunday_summary', '2026-10-04')])

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
