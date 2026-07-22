import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import article_audit


IDENTITY = {
    "article_id": "abcd1234",
    "resource_id": "2",
    "symbol": "AAPL",
    "start_date": "2026-07-15",
    "days": "10",
    "years": "10",
    "direction": "long",
    "mode": "2",
    "pattern_mode": "consecutive",
    "article_publish_date": "2026-07-15",
}

# A decoy "credential" placed on the fake config to prove secrets never leak into
# the manifest via the config snapshot.
SECRET_SENTINEL = "super-secret-openai-key-DO-NOT-LEAK"


class ArticleAuditBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # article_audit resolves config from sys.modules['config'] at call time,
        # so inject a fake one carrying decoy secrets we assert never leak.
        self.fake_config = SimpleNamespace(
            article_audit_enabled=True,
            article_audit_dir=self.tmp.name,
            citation_gate_mode="warn",
            publication_enabled=True,
            OPENAI_KEY=SECRET_SENTINEL,          # must never appear on disk
            TAVILY_API_KEY=SECRET_SENTINEL,      # must never appear on disk
        )
        self._patch = patch.dict(sys.modules, {"config": self.fake_config})
        self._patch.start()

    def tearDown(self):
        # Clear any active trail left in the context, then restore config.
        article_audit.finish(None, {})
        self._patch.stop()
        self.tmp.cleanup()

    def _run_dir(self):
        base = Path(self.tmp.name) / "2026" / "07" / "15"
        dirs = list(base.glob("AAPL_*"))
        self.assertEqual(len(dirs), 1, f"expected one run dir, got {dirs}")
        return dirs[0]


class ArticleAuditTests(ArticleAuditBase):
    def test_disabled_is_a_complete_noop(self):
        self.fake_config.article_audit_enabled = False
        trail = article_audit.begin(IDENTITY)
        self.assertIsNone(trail)
        self.assertFalse(article_audit.is_active())
        # These must be safe no-ops and write nothing.
        article_audit.record("prompt.txt", "hello")
        article_audit.append("image_prompts.json", {"prompt": "x"})
        self.assertIsNone(article_audit.finish(trail, {"status": "held_for_review"}))
        self.assertEqual(list(Path(self.tmp.name).rglob("*.json")), [])

    def test_records_are_written_with_manifest(self):
        trail = article_audit.begin(IDENTITY)
        self.assertIsNotNone(trail)
        self.assertTrue(article_audit.is_active())
        article_audit.record("prompt.txt", "RENDERED PROMPT BODY")
        article_audit.record("research.json", {"symbol": "AAPL", "sources": [1, 2]})
        article_audit.record("article_llm_raw.html", "<article>raw</article>")
        article_audit.append("image_prompts.json", {"provider": "flux", "prompt": "hero one"})
        article_audit.append("image_prompts.json", {"provider": "flux", "prompt": "hero two"})
        out = article_audit.finish(trail, {"status": "held_for_review", "steps": {}})
        self.assertIsNotNone(out)

        run_dir = self._run_dir()
        self.assertEqual((run_dir / "prompt.txt").read_text(), "RENDERED PROMPT BODY")
        self.assertEqual(
            json.loads((run_dir / "research.json").read_text())["sources"], [1, 2]
        )
        images = json.loads((run_dir / "image_prompts.json").read_text())
        self.assertEqual([i["prompt"] for i in images], ["hero one", "hero two"])

        manifest = json.loads((run_dir / "manifest.json").read_text())
        self.assertEqual(manifest["identity"]["symbol"], "AAPL")
        self.assertEqual(manifest["run"]["status"], "held_for_review")
        self.assertIn("git_commit", manifest["provenance"])
        # Hardcoded research/synthesis models are always known.
        self.assertEqual(manifest["provenance"]["models"]["research_synthesis"], "gpt-5-mini")
        # File index lists every artifact with a size + hash.
        names = {f["name"] for f in manifest["files"]}
        self.assertEqual(names, {"prompt.txt", "research.json", "article_llm_raw.html",
                                 "image_prompts.json"})
        self.assertTrue(all(f["bytes"] > 0 and len(f["sha256"]) == 64 for f in manifest["files"]))

    def test_config_snapshot_never_leaks_secrets(self):
        trail = article_audit.begin(IDENTITY)
        article_audit.record("prompt.txt", "body")
        article_audit.finish(trail, {"status": "error"})
        run_dir = self._run_dir()

        manifest_text = (run_dir / "manifest.json").read_text()
        self.assertNotIn(SECRET_SENTINEL, manifest_text)
        # Only whitelisted, non-secret flags are snapshotted.
        flags = json.loads(manifest_text)["provenance"]["config_flags"]
        self.assertIn("citation_gate_mode", flags)
        self.assertTrue(set(flags).issubset(set(article_audit._CONFIG_FLAG_WHITELIST)))

    def test_none_values_write_no_file(self):
        trail = article_audit.begin(IDENTITY)
        article_audit.record("research.json", None)   # e.g. mode with no research
        article_audit.record("prompt.txt", "kept")
        article_audit.finish(trail, {"status": "held_for_review"})
        run_dir = self._run_dir()
        self.assertFalse((run_dir / "research.json").exists())
        self.assertTrue((run_dir / "prompt.txt").exists())

    def test_active_trail_cleared_after_finish(self):
        trail = article_audit.begin(IDENTITY)
        self.assertTrue(article_audit.is_active())
        article_audit.finish(trail, {"status": "held_for_review"})
        self.assertFalse(article_audit.is_active())
        # Post-finish records are inert.
        article_audit.record("prompt.txt", "late write")
        self.assertFalse((self._run_dir() / "late.txt").exists())

    def test_write_failure_is_fail_open(self):
        # Point the audit dir at a path whose parent is a regular file so
        # os.makedirs raises; finish() must swallow it and return None.
        blocker = Path(self.tmp.name) / "blocker"
        blocker.write_text("i am a file, not a directory")
        self.fake_config.article_audit_dir = str(blocker / "nested")
        trail = article_audit.begin(IDENTITY)
        article_audit.record("prompt.txt", "body")
        self.assertIsNone(article_audit.finish(trail, {"status": "error"}))
        # And the context is still cleared despite the failure.
        self.assertFalse(article_audit.is_active())


if __name__ == "__main__":
    unittest.main()
