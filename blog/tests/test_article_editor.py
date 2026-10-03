import json
from pathlib import Path
from unittest.mock import patch

from test_pub_dashboard import DashboardFixture
import article_editor as editor
import article_editor_routes
import article_index
import pub_dashboard


class ArticleEditorTests(DashboardFixture):
    def setUp(self):
        super().setUp()
        self.setting = patch.dict(pub_dashboard.app.config, {"TESTING": True})
        self.setting.start()
        self.addCleanup(self.setting.stop)

    def open(self, slug="a0"):
        response = self.client.post(f"/api/articles/{slug}/editor")
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["data"]

    def test_dashboard_renders_editor_controls(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Edit with ChatGPT", response.text)
        self.assertIn("Edit with Claude", response.text)

    def changed(self):
        draft = self.open()
        ident = draft["id"]
        with patch("article_editor.threading.Thread"):
            response = self.client.post(f"/api/editor/{ident}/messages", json={"version": 0, "provider": "chatgpt", "message": "Improve the introduction"})
        self.assertEqual(response.status_code, 200)
        internal = editor.get(ident, "test-owner")
        original = editor.current(internal)
        output = {k: original[k] for k in ("html", "title", "dek")}
        output.update(html=original["html"].replace("word", "clear", 1), summary="Improved opening")
        with patch("article_editor.run_subscription", return_value=(output, {"billing_source": "subscription", "api_fallback": False})):
            editor.finish_job(ident, "test-owner", internal["job_id"], "Improve the introduction", "chatgpt")
        return editor.get(ident, "test-owner")

    def test_draft_changes_never_touch_live_until_publish_and_retry_is_safe(self):
        before = (self.news / "a0.html").read_bytes()
        draft = self.changed()
        self.assertEqual(before, (self.news / "a0.html").read_bytes())
        response = self.client.post(f"/api/editor/{draft['id']}/publish", json={"version": 1})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertIn("clear", (self.news / "a0.html").read_text())
        self.assertEqual(self.client.post(f"/api/editor/{draft['id']}/publish", json={"version": 1}).status_code, 200)

    def test_provider_switch_receives_current_draft_and_history(self):
        draft = self.changed()
        with patch("article_editor.threading.Thread"):
            r = self.client.post(f"/api/editor/{draft['id']}/messages", json={"version": 1, "provider": "claude", "message": "Shorten opening"})
        self.assertEqual(r.status_code, 200)
        carried = editor.get(draft["id"], "test-owner")
        self.assertIn("clear", editor.current(carried)["html"])
        self.assertEqual([m["provider"] for m in carried["messages"]], ["chatgpt", "chatgpt", "claude"])

    def test_live_conflict_does_not_overwrite(self):
        draft = self.changed()
        (self.news / "a0.html").write_text("A separate edit")
        r = self.client.post(f"/api/editor/{draft['id']}/publish", json={"version": 1})
        self.assertEqual(r.status_code, 409)
        self.assertEqual((self.news / "a0.html").read_text(), "A separate edit")

    def test_publish_failure_restores_article_and_catalog(self):
        draft = self.changed()
        original = (self.news / "a0.html").read_bytes()
        posts = article_index.load_posts()
        real = article_index.save_posts
        def fail_once(value, backup=True):
            if backup:
                raise OSError("disk failure")
            return real(value, backup=False)
        with patch.object(article_index, "save_posts", side_effect=fail_once):
            with self.assertRaises(OSError):
                self.client.post(f"/api/editor/{draft['id']}/publish", json={"version": 1})
        self.assertEqual((self.news / "a0.html").read_bytes(), original)
        self.assertEqual(article_index.load_posts(), posts)
        self.assertEqual(editor.get(draft["id"], "test-owner")["status"], "idle")

    def test_version_conflicts_restore_and_discard(self):
        draft = self.changed()
        ident = draft["id"]
        self.assertEqual(self.client.post(f"/api/editor/{ident}/restore", json={"version": 0, "restore_version": 0}).status_code, 409)
        r = self.client.post(f"/api/editor/{ident}/restore", json={"version": 1, "restore_version": 0}).get_json()["data"]
        self.assertFalse(r["dirty"])
        self.assertEqual(r["version"], 2)
        self.assertEqual(self.client.post(f"/api/editor/{ident}/discard", json={"version": 2}).status_code, 200)
        self.assertNotEqual(self.open()["id"], ident)

    def test_preview_is_sandboxed_and_draft_is_private(self):
        draft = self.changed()
        r = self.client.get(f"/api/editor/{draft['id']}/preview?version=1")
        self.assertIn("clear", r.text)
        self.assertIn("sandbox allow-scripts", r.headers["Content-Security-Policy"])
        self.assertNotIn("allow-same-origin", r.headers["Content-Security-Policy"])
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        with self.assertRaises(editor.EditorError):
            editor.get(draft["id"], "other-user")

    def test_only_owner_admin_may_use_subscription(self):
        with patch.dict(pub_dashboard.app.config, {"TESTING": False}), patch.dict("os.environ", {"SMN_EDITOR_OWNER_USER_ID": "owner"}):
            self.assertEqual(self.client.post("/api/articles/a0/editor").status_code, 403)

    def test_worker_failure_preserves_draft_and_no_auto_retry(self):
        draft = self.open()
        with patch("article_editor.threading.Thread"):
            editor.start(draft["id"], "test-owner", 0, "claude", "Edit")
        running = editor.get(draft["id"], "test-owner")
        with patch("article_editor.run_subscription", side_effect=RuntimeError("sensitive diagnostic")):
            editor.finish_job(draft["id"], "test-owner", running["job_id"], "Edit", "claude")
        failed = editor.get(draft["id"], "test-owner")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["version"], 0)
        self.assertNotIn("sensitive", failed["error"])

    def test_only_one_model_job_across_articles(self):
        first, second = self.open(), self.open("a1")
        with patch("article_editor.threading.Thread"):
            editor.start(first["id"], "test-owner", 0, "chatgpt", "Edit")
            with self.assertRaises(editor.EditorError):
                editor.start(second["id"], "test-owner", 0, "claude", "Edit")

    def test_protected_blocks_figures_and_resources(self):
        source = '<article><p>Return 12%.</p><svg><text>5</text></svg><script>draw(12)</script></article>'
        masked, blocks = editor.mask(source)
        self.assertEqual(editor.restore_blocks(masked, blocks), source)
        editor.validate(source, source.replace("Return", "Historical return"))
        for altered in (source.replace("12%", "13%"), source.replace("draw(12)", "fetch(12)"), source.replace("<p>", '<p onclick="evil()">'), source + '<img src="https://evil.test/collect">'):
            with self.assertRaises(editor.EditorError):
                editor.validate(source, altered)

    def test_metadata_and_source_link_removal_are_rejected(self):
        with self.assertRaises(editor.EditorError):
            editor.validate_metadata('<img src=x onerror=alert(1)>')
        with self.assertRaises(editor.EditorError):
            editor.validate('<p><a href="https://source.test">Source</a></p>', '<p>Source</p>')
        self.assertEqual(editor.apply_edits('<p>Hello</p>', [{"before": "Hello", "after": "Welcome"}]), '<p>Welcome</p>')
        with self.assertRaises(editor.EditorError):
            editor.apply_edits('Hi Hi', [{"before": "Hi", "after": "Welcome"}])

    def test_interrupted_publication_recovers_both_files(self):
        draft = self.changed()
        old = draft["post"]
        new = dict(old, title="Updated title")
        marker = editor.root() / "publishing" / (draft["id"] + ".json")
        editor.subscription_writer.save_json(marker, {"draft_id": draft["id"], "owner": "test-owner",
            "old_post": old, "new_post": new, "old_html": draft["revisions"][0]["html"], "new_html": editor.current(draft)["html"]})
        (self.news / "a0.html").write_text(editor.current(draft)["html"])
        self.client.get(f"/api/editor/{draft['id']}")
        self.assertEqual((self.news / "a0.html").read_text(), draft["revisions"][0]["html"])
        self.assertFalse(marker.exists())
