import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import smn_daily
import smn_models
import subscription_writer as codex

SCHEMA = {'type': 'object', 'properties': {'passed': {'type': 'boolean'}},
          'required': ['passed'], 'additionalProperties': False}


class ProfileTests(unittest.TestCase):
    def test_edition_roles_cannot_change_on_resume(self):
        with tempfile.TemporaryDirectory() as root:
            first = smn_daily.Day(root, '2026-09-26', profile='claude')
            first.save()
            with self.assertRaisesRegex(smn_daily.Hold, 'roles changed'):
                smn_daily.Day(root, '2026-09-26', profile='chatgpt')

    def test_codex_prepared_images_and_discovery_scope_are_immutable(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            image = root/'screen.png'
            image.write_bytes(b'png bytes')
            until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            job = codex.prepare_job(root/'jobs', 'visual', 'inspect', SCHEMA, as_of='2026-09-26',
                                    valid_until=until, evidence_sha256='e'*64,
                                    stage='visual', model='gpt-6-luna', effort='low', images=[image])
            manifest = codex.verify_job(job)
            self.assertEqual(manifest['model'], 'gpt-6-luna')
            self.assertEqual(len([n for n in manifest['input_hashes'] if n.startswith('images/')]), 1)
            image.write_bytes(b'changed original')
            self.assertEqual(codex.verify_job(job), manifest)
            (job/'images/0-screen.png').write_bytes(b'changed copy')
            with self.assertRaisesRegex(ValueError, 'changed after ready'):
                codex.verify_job(job)
            with self.assertRaisesRegex(ValueError, 'primary-discovery'):
                codex.prepare_job(root/'jobs', 'bad', 'search', SCHEMA, as_of='2026-09-26',
                                  valid_until=until, evidence_sha256='e'*64, web_search=True)
            search = codex.prepare_job(root/'jobs', 'discovery', 'search', SCHEMA, as_of='2026-09-26',
                                       valid_until=until, evidence_sha256='e'*64,
                                       stage='primary-discovery', model='gpt-6-sol', web_search=True)
            self.assertTrue(codex.verify_job(search)['web_search'])

    def test_visual_jobs_use_day_budgeted_runner(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            day = smn_daily.Day(root, '2026-09-26', max_jobs=0)
            image = root/'screen.png'
            image.write_bytes(b'image')
            with self.assertRaisesRegex(smn_daily.Hold, 'budget'):
                import smn_visual
                smn_visual._job(root, '2026-09-26', 'ABC', 'visual', 'inspect', SCHEMA,
                                [image], day.roles, {}, 'visual', day.run_job)
            self.assertEqual(day.jobs_used(), 1)


if __name__ == '__main__':
    unittest.main()
