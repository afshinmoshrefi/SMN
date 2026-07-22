"""queue_article threads the angle-engine flag + news context onto the
blog_queue call as URL-encoded query params — only when the config flag is on.

daily_article_queue imports config/redis at module level; both are absent on
the local (offline) box, so we install permissive fakes only when the real
modules cannot be imported. On dev the real modules import and no fake is used.
"""
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

BLOG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BLOG))


class _StubConfig(types.ModuleType):
    """Permissive config: known attrs set below, anything else -> '' (a str, so
    module-level .rstrip()/.upper() at import time never blow up)."""
    def __getattr__(self, name):
        return ''


def _import_target(name, fakes):
    """Import `name` on either the real dev env or the offline local box.

    For any dependency that is absent (local Windows has no config.py / redis),
    install a fake just long enough to import the target, then remove it again
    so we never pollute sys.modules for the rest of the suite (the target keeps
    its own bound reference to the fake, which the tested functions don't need).
    Dependencies that import for real (dev) are left untouched.
    """
    added = []
    for mod_name, obj in fakes.items():
        if mod_name not in sys.modules:
            sys.modules[mod_name] = obj
            added.append(mod_name)
    try:
        return importlib.import_module(name)
    finally:
        for mod_name in added:
            sys.modules.pop(mod_name, None)


_cfg = _StubConfig('config')
_cfg.blog_queue_server = 'http://localhost:7171/'
daq = _import_target('daily_article_queue',
                     {'config': _cfg, 'redis': types.ModuleType('redis')})


BASE_ROW = {
    'pat_resource_id': '2',
    'ticker':          'nvda',
    'pat_start_date':  '2026-07-25',
    'pat_days':        '21',
    'pat_years':       '15',
    'pat_direction':   'long',
    'pat_mode':        'consecutive',
    'news_headline':   'NVDA beats Q2 & guides higher',
    'news_date':       '2026-07-24',
    'news_direction':  'bullish',
}


class QueueArticleAngleParamsTests(unittest.TestCase):
    def _capture_url(self, row, flag):
        captured = {}

        def fake_get(url, timeout=None):
            captured['url'] = url
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {'message': 'queued'}
            return resp

        # Patch the module's `config` reference so queue_article's
        # getattr(config, 'angle_engine_enabled', ...) is fully controlled
        # regardless of whether the real or a fake config was imported.
        with patch.object(daq, 'config',
                          types.SimpleNamespace(angle_engine_enabled=flag)), \
             patch.object(daq.requests, 'get', side_effect=fake_get):
            ok, _payload = daq.queue_article(row, '2026-07-25')
        self.assertTrue(ok)
        return captured['url']

    def test_base_route_intact(self):
        url = self._capture_url(dict(BASE_ROW), True)
        # ticker upper-cased, path identity preserved, pattern_mode kept
        self.assertIn('/write_news_article/2/NVDA/2026-07-25/21/15/long/', url)
        self.assertIn('pattern_mode=consecutive', url)

    def test_params_present_when_flag_on(self):
        url = self._capture_url(dict(BASE_ROW), True)
        self.assertIn('engine=angle', url)
        # urlencode: space -> '+', '&' -> '%26'
        self.assertIn('news_headline=NVDA+beats+Q2+%26+guides+higher', url)
        self.assertIn('news_date=2026-07-24', url)
        self.assertIn('news_direction=bullish', url)

    def test_params_absent_when_flag_off(self):
        url = self._capture_url(dict(BASE_ROW), False)
        self.assertNotIn('engine=', url)
        self.assertNotIn('news_headline=', url)
        self.assertNotIn('news_date=', url)
        self.assertNotIn('news_direction=', url)

    def test_missing_news_columns_degrade_to_empty(self):
        # Old CSV with no news_* columns still queues cleanly (no KeyError).
        row = {k: v for k, v in BASE_ROW.items() if not k.startswith('news_')}
        url = self._capture_url(row, True)
        self.assertIn('engine=angle', url)      # still opts into the angle engine
        self.assertIn('news_headline=', url)    # present but empty
        self.assertIn('news_direction=', url)


if __name__ == '__main__':
    unittest.main()
