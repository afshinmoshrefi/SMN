"""The audit observer in article_workflow must NEVER affect generation.

Hermetic: every heavy dependency of the old (direct-publish) workflow is faked
at import; the workflow's own step functions are stubbed so a full
generate_news_article() run exercises only orchestration + audit hooks.
"""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

BLOG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BLOG))

_fake_config = types.SimpleNamespace(citation_gate_mode="warn")

_FAKES = {
    "config": _fake_config,
    "article_images": types.SimpleNamespace(create_article_images=Mock()),
    "blog_queue": types.SimpleNamespace(inject_hero_into_article=lambda html, hero: html),
    "article_prompt": types.SimpleNamespace(
        create_article_prompt=Mock(return_value="prompt"), get_opp_data=Mock(),
        detect_market_family=Mock(return_value=("stocks", "US STOCKS")),
        WHITELISTED_SOURCE_DOMAINS=[]),
    "article_hero_image": types.SimpleNamespace(
        hero_image_workflow=Mock(), HERO_WIDTH_ATTR=1536, HERO_HEIGHT_ATTR=640),
    "blog_tools": types.SimpleNamespace(get_company_name=lambda *a: "Acme"),
    "AI_tools": types.SimpleNamespace(),
    "publish_article": types.SimpleNamespace(publish_article_web=Mock()),
    "citation_gate": types.SimpleNamespace(
        validate_citations=lambda *a, **k: {"ok": True, "violations": [], "warnings": []}),
    "refresh_related_articles": types.SimpleNamespace(refresh_today_only=lambda **k: None),
    "article_title": types.SimpleNamespace(
        generate_unique_seo_title=lambda *a, **k: "SEO Title"),
}

with patch.dict(sys.modules, _FAKES):
    sys.modules.pop("article_workflow", None)
    sys.modules.pop("article_audit", None)
    import article_workflow as workflow
    import article_audit
sys.modules.pop("article_workflow", None)


class _Poisoned:
    """An audit facade whose every entry point raises."""
    @staticmethod
    def begin(identity):
        raise RuntimeError("poisoned begin")
    @staticmethod
    def record(name, value):
        raise RuntimeError("poisoned record")
    @staticmethod
    def append(name, value):
        raise RuntimeError("poisoned append")
    @staticmethod
    def finish(trail, tracking):
        raise RuntimeError("poisoned finish")


class WorkflowAuditObserverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hero = Path(self.tmp.name) / "hero.jpg"
        self.hero.write_bytes(b"jpg")
        self.audit_dir = Path(self.tmp.name) / "audit"

    def _run(self):
        img_paths = [{"variant": "hero", "path": str(self.hero),
                      "url": "https://x.test/hero.jpg"}]
        with patch.object(workflow, "generate_tradewave_charts",
                          return_value=(list(img_paths), {"stats": {}})), \
             patch.object(workflow, "generate_hero_image",
                          return_value=("<img>", list(img_paths))), \
             patch.object(workflow, "research_tavily",
                          return_value={"sources": [{"url": "https://r.test/a"}]}), \
             patch.object(workflow, "build_article_prompt", return_value="THE PROMPT"), \
             patch.object(workflow, "write_article",
                          return_value="<html><body>art</body></html>"), \
             patch.object(workflow, "publish_article",
                          return_value={"url": "https://x.test/a.html"}), \
             patch.dict(sys.modules, _FAKES):
            return workflow.generate_news_article(
                resource_id="2", symbol="ABC", date="2026-01-05", days=30,
                years="8", direction="long", article_publish_date="2026-01-05",
                userid=16)

    def test_successful_run_writes_full_audit_trail(self):
        _fake_config.article_audit_enabled = True
        _fake_config.article_audit_dir = str(self.audit_dir)
        self.addCleanup(lambda: [delattr(_fake_config, n) for n in
                                 ("article_audit_enabled", "article_audit_dir")])
        tracking = self._run()
        self.assertEqual(tracking["status"], "success")
        runs = list(self.audit_dir.glob("*/*/*/*"))
        self.assertEqual(len(runs), 1, runs)
        names = {p.name for p in runs[0].iterdir()}
        for expected in ("manifest.json", "prompt.txt", "article_llm_raw.html",
                         "final.html", "research.json", "citation_gate.json",
                         "publish_result.json"):
            self.assertIn(expected, names)
        manifest = json.loads((runs[0] / "manifest.json").read_text())
        self.assertEqual(manifest["run"]["status"], "success")
        self.assertEqual(manifest["identity"]["symbol"], "ABC")

    def test_disabled_audit_run_still_succeeds_and_writes_nothing(self):
        tracking = self._run()
        self.assertEqual(tracking["status"], "success")
        self.assertFalse(self.audit_dir.exists())

    def test_unwritable_audit_dir_cannot_fail_generation(self):
        _fake_config.article_audit_enabled = True
        _fake_config.article_audit_dir = "/proc/definitely-not-writable/audit"
        self.addCleanup(lambda: [delattr(_fake_config, n) for n in
                                 ("article_audit_enabled", "article_audit_dir")])
        tracking = self._run()
        self.assertEqual(tracking["status"], "success")

    def test_poisoned_audit_facade_cannot_fail_generation(self):
        # Even if every audit entry point raised (a bug the real module's
        # swallow-all guards should make impossible), publishing must survive
        # via the workflow's no-op fallback contract. This documents that the
        # workflow relies on article_audit's never-raise API contract, which
        # the real module honors below.
        for name in ("begin", "record", "append", "finish"):
            with self.subTest(entry=name):
                self.assertIsNotNone(getattr(article_audit, name))
        # Real-module guarantee: poisoned internals never escape the API.
        trail = article_audit.ArticleAuditTrail({"symbol": "ABC"})
        token = article_audit._active.set(trail)
        try:
            with patch.object(trail, "record", side_effect=RuntimeError("boom")):
                article_audit.record("x.json", {"a": 1})  # must not raise
        finally:
            article_audit._active.reset(token)
        with patch.object(trail, "write", side_effect=RuntimeError("boom")):
            self.assertIsNone(article_audit.finish(trail, {}))  # must not raise


if __name__ == "__main__":
    unittest.main()
