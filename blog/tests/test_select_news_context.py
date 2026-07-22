"""Pure-function tests for the Phase 4 news-context threading in
select_news_articles (news_context_from_item / _map_news_direction /
_norm_news_date). No LLMs, no network.

select_news_articles imports config/redis/blog_tools/AI_tools at module level;
all are absent on the local (offline) box, so we install permissive fakes only
when the real modules cannot be imported (on dev the real ones are used).
"""
import importlib
import sys
import types
import unittest
from pathlib import Path

BLOG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BLOG))


class _StubConfig(types.ModuleType):
    def __getattr__(self, name):
        return ''            # str so module-level .rstrip() at import is safe


class _PermMod(types.ModuleType):
    def __getattr__(self, name):
        return lambda *a, **k: None   # satisfies `from X import name`


def _import_target(name, fakes):
    """Import `name`, installing fakes only for deps that are absent (offline
    local box) and removing them immediately after import so the rest of the
    suite sees an untouched sys.modules. Real deps (dev) are left alone."""
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
_cfg.appserver_url = 'http://localhost'
_cfg.news_root_folder = '/tmp/news'
sna = _import_target('select_news_articles', {
    'config':     _cfg,
    'redis':      types.ModuleType('redis'),
    'blog_tools': _PermMod('blog_tools'),
    'AI_tools':   _PermMod('AI_tools'),
})


class MapNewsDirectionTests(unittest.TestCase):
    def test_bullish_bearish_kept(self):
        self.assertEqual(sna._map_news_direction('bullish'), 'bullish')
        self.assertEqual(sna._map_news_direction('BEARISH'), 'bearish')
        self.assertEqual(sna._map_news_direction('  Bullish '), 'bullish')

    def test_neutral_unknown_blank_map_to_empty(self):
        for v in ('neutral', 'unknown', '', None, 'bull', 'positive'):
            self.assertEqual(sna._map_news_direction(v), '')


class NormNewsDateTests(unittest.TestCase):
    def test_valid_shapes(self):
        self.assertEqual(sna._norm_news_date('2026-07-24'), '2026-07-24')
        # ISO datetime truncated to the date
        self.assertEqual(sna._norm_news_date('2026-07-24T09:30:00Z'), '2026-07-24')

    def test_invalid_returns_empty(self):
        for v in ('July 24', '2026/07/24', '', None, 'garbage', '20260724'):
            self.assertEqual(sna._norm_news_date(v), '')


class NewsContextFromItemTests(unittest.TestCase):
    def test_full_news_item(self):
        ctx = sna.news_context_from_item({
            'news_headline': '  Foo Corp beats Q2  ',
            'news_date':     '2026-07-24T12:00:00',
            'news_sentiment': 'Bullish',
        })
        self.assertEqual(ctx, {
            'news_headline':  'Foo Corp beats Q2',   # stripped
            'news_date':      '2026-07-24',           # normalized
            'news_direction': 'bullish',              # mapped
        })

    def test_neutral_sentiment_drops_direction(self):
        ctx = sna.news_context_from_item({'news_sentiment': 'neutral',
                                          'news_headline': 'x', 'news_date': 'bad'})
        self.assertEqual(ctx['news_direction'], '')
        self.assertEqual(ctx['news_date'], '')       # bad date -> ''

    def test_pattern_only_all_empty(self):
        # No news peg (pattern-only candidate) -> every field empty string.
        self.assertEqual(sna.news_context_from_item({}),
                         {'news_headline': '', 'news_date': '', 'news_direction': ''})
        self.assertEqual(sna.news_context_from_item(None),
                         {'news_headline': '', 'news_date': '', 'news_direction': ''})

    def test_keys_are_stable(self):
        # Contract with blog_queue/article_processor: exactly these three keys.
        self.assertEqual(set(sna.news_context_from_item({}).keys()),
                         {'news_headline', 'news_date', 'news_direction'})


if __name__ == '__main__':
    unittest.main()
