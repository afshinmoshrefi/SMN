import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import article_hero_image  # noqa: F401  (reads a data file from the cwd)
except FileNotFoundError:
    sys.modules["article_hero_image"] = types.SimpleNamespace(
        hero_image_workflow=None, HERO_WIDTH_ATTR=1, HERO_HEIGHT_ATTR=1)
# Repo modules first: blog_queue puts the live /home/flask paths on sys.path.
import pub_dashboard
from tests.test_pub_dashboard import DashboardFixture
import blog_queue

PATTERN = "/2/AAA/2026-10-01/30/20"


class PortfolioPublishStateTest(DashboardFixture):
    """TradeWave portfolio icon -> blog_queue -> publishing dashboard."""

    def setUp(self):
        super().setUp()
        self.bq = blog_queue.app.test_client()
        dash = self.client

        class Reply:
            def __init__(self, r):
                self._json = r.get_json()
            def json(self):
                return self._json

        def fake_get(url, params=None, timeout=None, **_):
            path = url.replace(blog_queue.SMN_DASHBOARD_URL, "")
            return Reply(dash.get(path + "?" + urlencode(params or {})))

        def fake_post(url, json=None, timeout=None, headers=None, **_):
            path = url.replace(blog_queue.SMN_DASHBOARD_URL, "")
            return Reply(dash.post(path, json=json, headers=headers or {}))

        for target, value in (("requests.get", fake_get), ("requests.post", fake_post)):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(pub_dashboard, "_shown_path", lambda *a: "")
        p.start()
        self.addCleanup(p.stop)

    def _state(self):
        return self.bq.get("/article_publish_state_bq" + PATTERN).get_json()

    def _set(self, state):
        return self.bq.post("/article_publish_state_bq", json={
            "resource_id": "2", "symbol": "AAA", "date": "2026-10-01", "days": "30",
            "years": "20", "userid": "77", "state": state})

    def test_reads_real_state_of_newest_article(self):
        body = self._state()
        self.assertEqual((body["state"], body["slug"], body["candidates"]), ("published", "a4", 5))

    def test_unpublish_then_publish_from_portfolio(self):
        r = self._set("unpublished")
        self.assertEqual(r.get_json()["message"], "success")
        # a4 is now held; the portfolio now means the newest LIVE one (a3)...
        self.assertEqual(self._state()["slug"], "a3")
        actor = self.client.get("/api/audit?slug=a4").get_json()["data"][0]["actor"]
        self.assertEqual(actor, "tw2-user-77")

    def test_publish_when_all_unpublished(self):
        for slug in ("a0", "a1", "a2", "a3", "a4"):
            self.client.post(f"/api/articles/{slug}/unpublish", json={})
        self.assertEqual(self._state()["state"], "unpublished")
        self.assertEqual(self._set("published").get_json()["slug"], "a4")
        self.assertEqual(self._state()["state"], "published")

    def test_bad_input(self):
        self.assertEqual(self._set("maybe").status_code, 400)
        r = self.bq.get("/article_publish_state_bq/2/ZZZ/2026-10-01/30/20").get_json()
        self.assertEqual(r["state"], "none")


if __name__ == "__main__":
    unittest.main()
