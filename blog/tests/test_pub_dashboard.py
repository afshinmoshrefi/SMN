import json
import os  # noqa: F401
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import article_index
import pin_store
import pub_dashboard
import schedule_store


def _post(slug, published, symbol="AAA", html_path=None):
    return {
        "resource_id": "1", "symbol": symbol, "tickers": [symbol],
        "market_family": "US", "pattern_start_date": "2026-10-01",
        "pattern_days": 30, "lookback_years": "20", "direction": "long",
        "title": f"Title {slug}", "dek": "dek", "slug": slug,
        "url": f"https://example.test/articles/{slug}.html",
        "path": str(html_path or ""), "published_date": published,
        "publish_status": "true", "tags": [],
    }


class DashboardFixture(unittest.TestCase):
    """Temp posts.json, state dir and patched side effects."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.news, self.state = root / "news", root / "state"
        self.news.mkdir()
        posts = []
        for i in range(5):
            html = self.news / f"a{i}.html"
            html.write_text("<article><p>" + "word " * (100 * (i + 1)) + "</p></article>")
            posts.append(_post(f"a{i}", f"2026-09-{10 + i:02d}T12:00:00Z", html_path=html))
        (self.news / "posts.json").write_text(json.dumps(posts))

        patches = {
            (pin_store, "STATE_DIR"): self.state,
            (pin_store, "PINS_JSON"): self.state / "pins.json",
            (article_index, "NEWS_ROOT"): self.news,
            (article_index, "POSTS_JSON"): self.news / "posts.json",
            (article_index, "METRICS_JSON"): self.state / "article_metrics.json",
            (article_index, "TRASH_DIR"): self.state / "trash",
            (article_index, "BACKUP_DIR"): self.state / "backups",
            (article_index, "LOCK_FILE"): self.state / "posts.lock",
            (article_index, "UNPUBLISHED_JSON"): self.state / "unpublished.json",
            (article_index, "HELD_DIR"): self.state / "unpublished",
            (pub_dashboard, "USE_HOME_PIPELINE"): False,
            (schedule_store, "SCHEDULE_JSON"): self.state / "schedule.json",
            (schedule_store, "LOCK_FILE"): self.state / "schedule.lock",
            (pub_dashboard, "NEWS_ROOT"): self.news,
            (pub_dashboard, "POSTS_JSON"): self.news / "posts.json",
            (pub_dashboard, "TRASH_DIR"): self.state / "trash",
            (pub_dashboard, "AUDIT_LOG"): self.state / "audit.jsonl",
            (pub_dashboard, "REFRESH_FLAG"): self.state / "refresh.pending",
            (pub_dashboard, "REFRESH_LOCK"): self.state / "refresh.lock",
        }
        env = patch.dict("os.environ", {"SMN_DASHBOARD_AUTH": "off"})
        env.start()
        self.addCleanup(env.stop)
        for (module, name), value in patches.items():
            p = patch.object(module, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.search_calls = []
        self.refreshes = []
        for name, value in (("sync_redis", lambda post, delete=False: None),
                            ("rebuild_home", lambda: {"ran": False, "reason": "test"}),
                            ("search_index", lambda post, remove: self.search_calls.append(
                                (post["slug"], remove))),
                            ("site_refresh", lambda rebuild=True: self.refreshes.append(rebuild)),
                            ("queue_refresh", lambda wanted=True: self.refreshes.append(bool(wanted))
                             or {"queued": bool(wanted)})):
            p = patch.object(pub_dashboard, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.client = pub_dashboard.app.test_client()


class DashboardTestCase(DashboardFixture):
    # ---------------------------------------------------------------- read
    def test_list_newest_first_with_derived_fields(self):
        body = self.client.get("/api/articles").get_json()
        self.assertTrue(body["ok"])
        self.assertEqual([r["slug"] for r in body["data"]], ["a4", "a3", "a2", "a1", "a0"])
        top = body["data"][0]
        self.assertEqual(top["pattern_end_date"], "2026-10-31")
        self.assertEqual(top["word_count"], 500)
        self.assertLessEqual(top["written_date"], top["published_date"])

    def test_filters_and_fields(self):
        body = self.client.get("/api/articles?min_words=300&published_to=2026-09-13"
                               "&fields=title").get_json()
        self.assertEqual([r["slug"] for r in body["data"]], ["a3", "a2"])
        self.assertEqual(set(body["data"][0]), {"slug", "title"})

    def test_include_text(self):
        data = self.client.get("/api/articles/a0?include=text").get_json()["data"]
        self.assertTrue(data["text"].startswith("word word"))
        self.assertNotIn("<p>", data["text"])

    def test_unknown_slug_is_structured_404(self):
        response = self.client.get("/api/articles/nope")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"]["code"], "not_found")

    # ---------------------------------------------------------------- pins
    def test_pin_moves_article_to_position_and_is_audited(self):
        r = self.client.put("/api/pins/a0", json={"position": 1, "days": 3},
                            headers={"X-Actor": "codex"})
        self.assertEqual(r.status_code, 200)
        order = [row["slug"] for row in self.client.get("/api/order").get_json()["data"]]
        self.assertEqual(order[:3], ["a0", "a4", "a3"])
        audit = self.client.get("/api/audit").get_json()["data"]
        self.assertEqual((audit[0]["action"], audit[0]["actor"]), ("pin", "codex"))

    def test_pin_rejects_bad_input(self):
        self.assertEqual(self.client.put("/api/pins/a0", json={"position": 0})
                         .get_json()["error"]["code"], "bad_pin")
        self.assertEqual(self.client.put("/api/pins/a0", json={"days": 2, "until": "2026-12-01"})
                         .get_json()["error"]["code"], "conflicting_expiry")
        self.assertEqual(self.client.put("/api/pins/ghost", json={}).status_code, 404)

    def test_expired_natural_pin_returns_to_own_slot(self):
        pin_store.set_pin("a0", position=1, days=1)
        later = pin_store.utcnow() + timedelta(days=2)
        posts = article_index.load_posts()
        result = pin_store.sweep_expired(posts, now=later)
        self.assertEqual(len(result["expired"]), 1)
        self.assertEqual(result["republished"], [])
        self.assertEqual(pin_store.active_pins(later), [])

    def test_expired_republish_pin_gets_fresh_published_date(self):
        pin = pin_store.set_pin("a0", position=1, days=1, on_expiry="republish")
        later = pin_store.utcnow() + timedelta(days=2)
        posts = article_index.load_posts()
        pin_store.sweep_expired(posts, now=later)
        post = next(p for p in posts if p["slug"] == "a0")
        self.assertEqual(post["published_date"], pin["expires_at"])

    def test_broken_pin_file_never_breaks_ordering(self):
        self.state.mkdir(parents=True, exist_ok=True)
        pin_store.PINS_JSON.write_text("{not json")
        rows = [{"slug": "x"}, {"slug": "y"}]
        self.assertEqual(pin_store.apply_pins(rows), rows)

    # ---------------------------------------------------------------- write
    def test_create_update_delete_roundtrip(self):
        r = self.client.post("/api/articles", json={
            "symbol": "zzz", "title": "New One", "html": "<p>one two three</p>",
            "published_date": "2026-09-20T00:00:00Z"}, headers={"X-Actor": "astra"})
        self.assertEqual(r.status_code, 201)
        slug = r.get_json()["data"]["article"]["slug"]
        self.assertEqual(slug, "new-one")
        self.assertEqual(self.client.post("/api/articles", json={
            "symbol": "zzz", "title": "New One", "html": "<p>x</p>"}).status_code, 409)

        self.client.patch(f"/api/articles/{slug}", json={"title": "Renamed",
                                                         "html": "<p>" + "w " * 50 + "</p>"})
        data = self.client.get(f"/api/articles/{slug}").get_json()["data"]
        self.assertEqual((data["title"], data["word_count"]), ("Renamed", 50))

        r = self.client.delete(f"/api/articles/{slug}")
        trash = Path(r.get_json()["data"]["trash"])
        self.assertTrue((trash / "post.json").is_file())
        self.assertEqual(self.client.get(f"/api/articles/{slug}").status_code, 404)

        actions = [e["action"] for e in self.client.get("/api/audit").get_json()["data"]]
        self.assertEqual(actions, ["delete", "update", "create"])

    def test_backups_go_to_private_state_dir(self):
        self.client.patch("/api/articles/a0", json={"title": "x"})
        self.assertEqual(list(self.news.glob("posts.json.bak*")), [])
        self.assertEqual(len(list((self.state / "backups").glob("posts.json.*"))), 1)

    def test_create_requires_fields(self):
        error = self.client.post("/api/articles", json={"symbol": "X"}).get_json()["error"]
        self.assertEqual(error["code"], "missing_fields")

    # ---------------------------------------------------------------- hero
    def _hero_call(self, slug, reply_status=200, reply=None):
        sent = {}

        class Reply:
            status_code = reply_status
            def json(self_inner):
                return reply if reply is not None else {"message": "success"}

        def fake_get(url, params=None, timeout=None):
            sent.update(url=url, params=params)
            return Reply()

        with patch("requests.get", fake_get):
            response = self.client.post(f"/api/articles/{slug}/hero/recreate", json={})
        return response, sent

    def _set_hero(self, slug, url):
        posts = article_index.load_posts()
        next(p for p in posts if p["slug"] == slug)["hero_image"] = url
        article_index.save_posts(posts, backup=False)

    def test_hero_recreate_sends_exact_article_id(self):
        self._set_hero("a1", "https://example.test/articles/US/2026/10/01/hero_AAA_0a1b2c3d.jpg")
        response, sent = self._hero_call("a1")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/article_prompt/1/AAA/2026-10-01/30/20/0/hero/dashboard", sent["url"])
        self.assertEqual(sent["params"], {"direction": "long", "article_id": "0a1b2c3d"})

    def test_hero_recreate_plain_name_sends_none(self):
        self._set_hero("a1", "https://example.test/articles/US/2026/10/01/hero_AAA.jpg")
        _, sent = self._hero_call("a1")
        self.assertEqual(sent["params"]["article_id"], "none")

    def test_hero_recreate_refuses_edition_articles(self):
        self._set_hero("a2", "https://example.test/editions/2026-09-08/AAA/assets/hero-abc.png")
        response, sent = self._hero_call("a2")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(sent, {})

    def test_hero_recreate_reports_failure(self):
        self._set_hero("a1", "https://example.test/articles/US/2026/10/01/hero_AAA.jpg")
        response, _ = self._hero_call("a1", 500, {"message": "failed", "reason": "model down"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"]["message"], "model down")

    def test_hero_filter(self):
        body = self.client.get("/api/articles?hero=missing").get_json()
        self.assertEqual(body["meta"]["total"], 5)   # test posts have no hero files

    # ---------------------------------------------------------------- publish / unpublish
    def test_unpublish_then_publish_roundtrip(self):
        live_file = self.news / "a2.html"
        r = self.client.post("/api/articles/a2/unpublish", json={"reason": "check facts"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(live_file.exists())                       # URL stops serving
        self.assertNotIn("a2", [p["slug"] for p in article_index.load_posts()])
        self.assertEqual(self.search_calls[-1], ("a2", True))

        row = self.client.get("/api/articles/a2?include=text").get_json()["data"]
        self.assertFalse(row["published"])
        self.assertEqual(row["word_count"], 300)                   # read from held copy
        self.assertTrue(row["text"].startswith("word"))
        listed = self.client.get("/api/articles?status=unpublished").get_json()
        self.assertEqual([x["slug"] for x in listed["data"]], ["a2"])
        self.assertEqual(self.client.get("/api/articles?status=published")
                         .get_json()["meta"]["total"], 4)

        r = self.client.post("/api/articles/a2/publish", json={})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(live_file.is_file())
        post = next(p for p in article_index.load_posts() if p["slug"] == "a2")
        self.assertEqual(post["published_date"], "2026-09-12T12:00:00Z")   # date kept
        self.assertEqual(self.search_calls[-1], ("a2", False))
        actions = [e["action"] for e in self.client.get("/api/audit").get_json()["data"]]
        self.assertEqual(actions[:2], ["publish", "unpublish"])

    def test_publish_as_new_gets_fresh_date_and_goes_first(self):
        self.client.post("/api/articles/a0/unpublish", json={})
        self.client.post("/api/articles/a0/publish", json={"as_new": True})
        first = self.client.get("/api/articles?limit=1").get_json()["data"][0]
        self.assertEqual(first["slug"], "a0")

    def test_unpublished_article_blocks_edit_pin_and_twice(self):
        pin_store.set_pin("a1", position=1)
        self.client.post("/api/articles/a1/unpublish", json={})
        self.assertEqual(pin_store.active_pins(), [])              # pin removed
        self.assertEqual(self.client.patch("/api/articles/a1", json={"title": "x"})
                         .get_json()["error"]["code"], "unpublished")
        self.assertEqual(self.client.put("/api/pins/a1", json={})
                         .get_json()["error"]["code"], "unpublished")
        self.assertEqual(self.client.post("/api/articles/a1/unpublish", json={})
                         .get_json()["error"]["code"], "already_unpublished")
        self.assertEqual(self.client.post("/api/articles/a3/publish", json={})
                         .get_json()["error"]["code"], "already_published")

    def test_empty_date_folders_are_removed_but_not_above_articles(self):
        r = self.client.post("/api/articles", json={
            "symbol": "zzz", "title": "Folder Test", "html": "<p>x</p>",
            "published_date": "2019-05-06T00:00:00Z"})
        day = self.news / "articles" / "US" / "2019" / "05" / "06"
        self.assertTrue(day.is_dir())
        self.client.delete("/api/articles/folder-test")
        self.assertFalse((self.news / "articles" / "US").exists())
        self.assertTrue((self.news / "articles").is_dir())

    def test_delete_unpublished_article_goes_to_trash(self):
        self.client.post("/api/articles/a1/unpublish", json={})
        r = self.client.delete("/api/articles/a1")
        self.assertEqual(r.status_code, 200)
        self.assertTrue((Path(r.get_json()["data"]["trash"]) / "a1.html").is_file())
        self.assertEqual(self.client.get("/api/articles/a1").status_code, 404)

    def test_delete_live_article_updates_search_and_site(self):
        self.client.delete("/api/articles/a1")
        self.assertEqual(self.search_calls, [("a1", True)])
        self.assertEqual(self.refreshes, [True])

    # ---------------------------------------------------------------- default list = home order
    def test_default_list_puts_pinned_first_then_home_order(self):
        pin_store.set_pin("a0", position=2, days=3)           # oldest, pinned to #2
        self.client.post("/api/articles/a2/unpublish", json={})
        body = self.client.get("/api/articles").get_json()
        rows = body["data"]
        self.assertEqual([(r["slug"], r["section"]) for r in rows],
                         [("a0", "pinned"), ("a4", "home"), ("a3", "home"),
                          ("a1", "home"), ("a2", "unpublished")])
        self.assertEqual([r["home_position"] for r in rows], [2, 1, 3, 4, None])
        self.assertEqual(body["meta"]["sections"],
                         {"pinned": 1, "home": 3, "off_home": 0, "unpublished": 1})
        self.assertEqual(body["meta"]["sort"], "home")

    def test_several_pins_show_in_display_order(self):
        pin_store.set_pin("a1", position=3)
        pin_store.set_pin("a0", position=1)
        rows = self.client.get("/api/articles").get_json()["data"]
        self.assertEqual([r["slug"] for r in rows[:2]], ["a0", "a1"])
        self.assertEqual([r["home_position"] for r in rows[:2]], [1, 3])

    def test_other_sorts_still_work(self):
        pin_store.set_pin("a0", position=1)
        rows = self.client.get("/api/articles?sort=published_date").get_json()["data"]
        self.assertEqual(rows[0]["slug"], "a4")

    # ---------------------------------------------------------------- move / order
    def _order(self):
        return [r["slug"] for r in self.client.get("/api/order").get_json()["data"]]

    def test_move_holds_article_in_place(self):
        self.assertEqual(self._order(), ["a4", "a3", "a2", "a1", "a0"])
        r = self.client.post("/api/order/move", json={"slug": "a4", "position": 3},
                             headers={"X-Actor": "afshin"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._order(), ["a3", "a2", "a4", "a1", "a0"])
        row = self.client.get("/api/order").get_json()["data"][2]
        self.assertTrue(row["held"])
        self.client.delete("/api/pins/a4")                         # release
        self.assertEqual(self._order(), ["a4", "a3", "a2", "a1", "a0"])

    def test_move_keeps_existing_pin_end_date(self):
        pin = pin_store.set_pin("a0", position=1, days=5)
        self.client.post("/api/order/move", json={"slug": "a0", "position": 2})
        self.assertEqual(pin_store.pins_by_slug()["a0"]["expires_at"], pin["expires_at"])

    def test_set_order_in_one_call(self):
        r = self.client.put("/api/order", json={"slugs": ["a0", "a2"]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._order()[:2], ["a0", "a2"])
        bad = self.client.put("/api/order", json={"slugs": ["a0", "ghost"]}).get_json()
        self.assertEqual(bad["error"]["code"], "bad_slugs")

    def test_move_rejects_bad_input(self):
        self.assertEqual(self.client.post("/api/order/move", json={"slug": "a0", "position": 0})
                         .get_json()["error"]["code"], "bad_position")
        self.assertEqual(self.client.post("/api/order/move", json={"slug": "zz", "position": 1})
                         .status_code, 404)

    # ---------------------------------------------------------------- schedule
    def _schedule(self, slug, action, at, who="afshin", **extra):
        headers = {"X-Actor": who} if who else {}
        return self.client.post("/api/schedule", headers=headers,
                                json={"slug": slug, "action": action, "at": at, **extra})

    def test_schedule_publish_runs_when_due_and_records_who(self):
        self.client.post("/api/articles/a1/unpublish", json={})
        at = pin_store.iso(pin_store.utcnow() + timedelta(hours=2))
        r = self._schedule("a1", "publish", at, as_new=True)
        self.assertEqual(r.status_code, 201)
        item = r.get_json()["data"]
        self.assertEqual((item["created_by"], item["status"]), ("afshin", "pending"))

        row = self.client.get("/api/articles/a1").get_json()["data"]
        self.assertEqual(row["schedules"][0]["id"], item["id"])
        self.assertEqual(self.client.get("/api/articles?scheduled=true")
                         .get_json()["meta"]["total"], 1)

        # Not due yet: nothing happens, it stays unpublished.
        self.assertEqual(pub_dashboard.run_due_schedules()["ran"], [])
        self.assertFalse(self.client.get("/api/articles/a1").get_json()["data"]["published"])

        later = pin_store.utcnow() + timedelta(hours=3)
        with pub_dashboard.app.test_request_context("/"):
            out = pub_dashboard.run_due_schedules(later)
        self.assertEqual(out["ran"][0]["status"], "done")
        row = self.client.get("/api/articles/a1").get_json()["data"]
        self.assertTrue(row["published"])
        self.assertEqual(row["schedules"], [])
        who = [e["actor"] for e in self.client.get("/api/audit?slug=a1").get_json()["data"]]
        self.assertIn("afshin (scheduled)", who)

    def test_unpublished_without_schedule_stays_unpublished(self):
        self.client.post("/api/articles/a1/unpublish", json={})
        far = pin_store.utcnow() + timedelta(days=365)
        self.assertEqual(pub_dashboard.run_due_schedules(far)["ran"], [])
        self.assertFalse(self.client.get("/api/articles/a1").get_json()["data"]["published"])

    def test_schedule_window_publish_then_unpublish(self):
        self.client.post("/api/articles/a1/unpublish", json={})
        now = pin_store.utcnow()
        on, off = now + timedelta(hours=1), now + timedelta(hours=5)
        self.assertEqual(self._schedule("a1", "publish", pin_store.iso(on)).status_code, 201)
        self.assertEqual(self._schedule("a1", "unpublish", pin_store.iso(off)).status_code, 201)
        with pub_dashboard.app.test_request_context("/"):
            pub_dashboard.run_due_schedules(now + timedelta(hours=2))
            self.assertTrue(self.client.get("/api/articles/a1").get_json()["data"]["published"])
            pub_dashboard.run_due_schedules(now + timedelta(hours=6))
        self.assertFalse(self.client.get("/api/articles/a1").get_json()["data"]["published"])

    def test_schedule_validation(self):
        soon = pin_store.iso(pin_store.utcnow() + timedelta(hours=1))
        past = pin_store.iso(pin_store.utcnow() - timedelta(hours=1))
        self.assertEqual(self._schedule("a1", "publish", soon, who=None)
                         .get_json()["error"]["code"], "who_required")
        self.assertEqual(self._schedule("a1", "publish", soon)
                         .get_json()["error"]["code"], "already_published")
        self.assertEqual(self._schedule("a1", "unpublish", past)
                         .get_json()["error"]["code"], "time_in_past")
        self.assertEqual(self._schedule("a1", "fly", soon)
                         .get_json()["error"]["code"], "bad_action")
        self.assertEqual(self._schedule("a1", "unpublish", "next tuesday")
                         .get_json()["error"]["code"], "bad_time")
        self.assertEqual(self._schedule("zz", "unpublish", soon).status_code, 404)

    def test_naive_time_is_read_in_new_york(self):
        when = schedule_store.parse_when("2026-12-01T07:00")        # EST, UTC-5
        self.assertEqual(pin_store.iso(when), "2026-12-01T12:00:00Z")
        when = schedule_store.parse_when("2026-07-01T07:00", "UTC")
        self.assertEqual(pin_store.iso(when), "2026-07-01T07:00:00Z")

    def test_cancel_and_replace(self):
        at1 = pin_store.iso(pin_store.utcnow() + timedelta(hours=1))
        at2 = pin_store.iso(pin_store.utcnow() + timedelta(hours=2))
        first = self._schedule("a2", "unpublish", at1).get_json()["data"]
        second = self._schedule("a2", "unpublish", at2).get_json()["data"]
        pending = self.client.get("/api/schedule").get_json()["data"]
        self.assertEqual([x["id"] for x in pending], [second["id"]])     # replaced
        self.assertEqual(schedule_store.get(first["id"])["status"], "cancelled")
        r = self.client.delete(f"/api/schedule/{second['id']}", headers={"X-Actor": "bo"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/schedule").get_json()["data"], [])

    def test_create_with_publish_at_is_held_and_scheduled(self):
        at = pin_store.iso(pin_store.utcnow() + timedelta(days=1))
        r = self.client.post("/api/articles", headers={"X-Actor": "codex"}, json={
            "symbol": "zzz", "title": "Later One", "html": "<p>x y z</p>", "publish_at": at})
        self.assertEqual(r.status_code, 201)
        body = r.get_json()["data"]
        self.assertFalse(body["published"])
        self.assertEqual(body["scheduled"]["created_by"], "codex")
        self.assertNotIn("later-one", [p["slug"] for p in article_index.load_posts()])

    def test_create_unpublished_without_schedule(self):
        r = self.client.post("/api/articles", json={
            "symbol": "zzz", "title": "Held One", "html": "<p>x</p>", "publish": False})
        self.assertFalse(r.get_json()["data"]["published"])
        self.assertEqual(self.client.get("/api/schedule").get_json()["data"], [])

    def test_unpublish_with_publish_at(self):
        at = pin_store.iso(pin_store.utcnow() + timedelta(hours=4))
        r = self.client.post("/api/articles/a3/unpublish", headers={"X-Actor": "afshin"},
                             json={"publish_at": at})
        self.assertEqual(r.get_json()["data"]["scheduled"]["action"], "publish")

    def test_manual_publish_cancels_pending_publish(self):
        self.client.post("/api/articles/a1/unpublish", json={})
        self._schedule("a1", "publish", pin_store.iso(pin_store.utcnow() + timedelta(hours=1)))
        self.client.post("/api/articles/a1/publish", json={})
        self.assertEqual(self.client.get("/api/schedule").get_json()["data"], [])

    def test_delete_cancels_schedules(self):
        self._schedule("a2", "unpublish", pin_store.iso(pin_store.utcnow() + timedelta(hours=1)))
        self.client.delete("/api/articles/a2")
        self.assertEqual(self.client.get("/api/schedule").get_json()["data"], [])

    # ---------------------------------------------------------------- background refresh
    def test_drain_refresh_runs_once_per_pending_flag(self):
        calls = []
        with patch.object(pub_dashboard, "site_refresh", lambda rebuild=True: calls.append(1)):
            pub_dashboard.REFRESH_FLAG.parent.mkdir(parents=True, exist_ok=True)
            pub_dashboard.REFRESH_FLAG.touch()
            self.assertTrue(pub_dashboard.drain_refresh())
            self.assertTrue(pub_dashboard.drain_refresh())      # nothing pending
        self.assertEqual(calls, [1])
        self.assertFalse(pub_dashboard.REFRESH_FLAG.exists())

    def test_writes_do_not_wait_for_rebuild(self):
        r = self.client.post("/api/articles/a1/unpublish", json={}).get_json()
        self.assertEqual(r["data"]["rebuild"], {"queued": True})

    # ---------------------------------------------------------------- agent docs
    def test_agent_docs_exist(self):
        self.assertIn(b"X-Actor", self.client.get("/llms.txt").data)
        spec = self.client.get("/openapi.json").get_json()
        self.assertIn("/api/audit", spec["paths"])
        self.assertTrue(self.client.get("/api/schema").get_json()["ok"])


if __name__ == "__main__":
    unittest.main()
