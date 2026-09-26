import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import smn_daily_control as control
import smn_models
from subscription_writer import save_json


class ControlTests(unittest.TestCase):
    def test_switch_affects_new_editions_but_preserves_incomplete_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control.set_profile(root, 'claude')
            save_json(root/'2026-09-25'/'smn-daily-state.json', {
                'date': '2026-09-25', 'profile': 'claude', 'roles': smn_models.load(profile='claude')})
            control.set_profile(root, 'chatgpt')
            self.assertEqual(control.edition_profile(root, '2026-09-25'), 'claude')
            self.assertEqual(control.edition_profile(root, '2026-09-26'), 'chatgpt')

    def test_completed_day_and_lock_cannot_start_duplicate_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_json(root/'2026-09-25'/'dev-publication-receipt.json', {'status': 'live_verified'})
            with patch.object(control.subprocess, 'run') as process:
                self.assertEqual(control.run(root, '2026-09-25'), 0)
                process.assert_not_called()
            (root/'.script-run.lock').mkdir()
            with self.assertRaisesRegex(RuntimeError, 'Another run'):
                control.run(root, '2026-09-26')

    def test_selected_profile_and_budget_reach_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control.set_profile(root, 'chatgpt')
            with patch.object(control.subprocess, 'run') as process:
                process.return_value.returncode = 0
                control.run(root, '2026-09-26', max_jobs=40, publish=False)
            cmd = process.call_args.args[0]
            self.assertEqual(cmd[cmd.index('--profile')+1], 'chatgpt')
            self.assertNotIn('--publish', cmd)
            self.assertFalse((root/'.script-run.lock').exists())
