import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import article_hero_image  # noqa: F401  (reads a data file from the cwd)
except FileNotFoundError:
    import types
    sys.modules["article_hero_image"] = types.SimpleNamespace(
        hero_image_workflow=None, HERO_WIDTH_ATTR=1, HERO_HEIGHT_ATTR=1)
import publish_article as pa


class FakeRedis:
    def __init__(self):
        self.data = {}
    def get(self, key):
        return self.data.get(key)
    def set(self, key, value):
        self.data[key] = value
    def delete(self, key):
        self.data.pop(key, None)


class DeleteArticleWebTest(unittest.TestCase):
    """Several articles can share one pattern (symbol/start/days/years)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.posts = []
        for slug, published in (("old", "2026-08-01T00:00:00Z"),
                                ("new", "2026-08-21T00:00:00Z"),
                                ("mid", "2026-08-10T00:00:00Z")):
            path = self.root / f"{slug}.html"
            path.write_text("<p>x</p>")
            self.posts.append({"slug": slug, "symbol": "MRK", "resource_id": "2",
                               "pattern_start_date": "2026-08-21", "pattern_days": 90,
                               "lookback_years": "20", "published_date": published,
                               "path": str(path), "url": f"https://t/{slug}.html"})
        (self.root / "posts.json").write_text(json.dumps(self.posts))
        self.redis = FakeRedis()
        self.key = pa.make_redis_key("2", "MRK", "2026-08-21", 90, "20")
        self.images_deleted = []
        self.refreshed = []
        patches = [
            patch.object(pa.config, "news_root_folder", str(self.root)),
            patch.object(pa, "redis_client3", self.redis),
            patch.object(pa, "build_home", lambda: None),
            patch.object(pa, "delete_search_index_entry", lambda root, url: None),
            patch.object(pa, "_delete_article_images",
                         lambda *a: self.images_deleted.append(a)),
        ]
        for name in ("generate_sitemap", "generate_news_sitemap",
                     "generate_rss_feed", "generate_llms_txt"):
            patches.append(patch.object(pa, name, lambda n=name: self.refreshed.append(n)))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _left(self):
        return [p["slug"] for p in json.loads((self.root / "posts.json").read_text())]

    def _show(self, slug):
        post = next(p for p in self.posts if p["slug"] == slug)
        self.redis.set(self.key, json.dumps({"entry": post}))

    def _delete(self, slug=None):
        return pa.delete_article_web("2", "MRK", "2026-08-21", "90", "20", "0", slug=slug)

    def test_deletes_the_article_the_portfolio_shows(self):
        self._show("mid")
        result = self._delete()
        self.assertEqual(result["slug"], "mid")
        self.assertEqual(self._left(), ["old", "new"])

    def test_without_redis_entry_deletes_newest_not_oldest(self):
        self.assertEqual(self._delete()["slug"], "new")
        self.assertEqual(self._left(), ["old", "mid"])

    def test_exact_slug_wins(self):
        self._show("new")
        self.assertEqual(self._delete(slug="old")["slug"], "old")

    def test_unknown_slug_deletes_nothing(self):
        self.assertFalse(self._delete(slug="ghost")["removed"])
        self.assertEqual(len(self._left()), 3)

    def test_siblings_keep_redis_key_and_images(self):
        self._show("new")
        self._delete()
        kept = json.loads(self.redis.get(self.key))["entry"]["slug"]
        self.assertEqual(kept, "mid")                 # newest one left
        self.assertEqual(self.images_deleted, [])

    def test_last_article_of_pattern_cleans_up(self):
        for _ in range(3):
            self._delete()
        self.assertIsNone(self.redis.get(self.key))
        self.assertEqual(len(self.images_deleted), 1)

    def test_listings_are_refreshed(self):
        self._delete()
        self.assertEqual(sorted(self.refreshed), ["generate_llms_txt", "generate_news_sitemap",
                                                  "generate_rss_feed", "generate_sitemap"])


if __name__ == "__main__":
    unittest.main()
