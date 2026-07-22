"""daily_audit_review is a pure observer: reads audit runs, writes review.json."""
import datetime
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

BLOG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BLOG))
import daily_audit_review as dar


def make_run(root: Path, date: str, name: str, html='<html><body>x</body></html>'):
    y, m, d = date.split("-")
    run = root / y / m / d / name
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps(
        {"identity": {"symbol": "ABC", "start_date": date, "days": 30}}))
    (run / "final.html").write_text(html)
    (run / "research.json").write_text(json.dumps({"sources": [{"url": "https://r.test/a"}]}))
    return run


class DailyAuditReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_reviews_each_run_once_and_is_idempotent(self):
        run = make_run(self.root, "2026-07-16", "ABC_2026-07-16_30_8_aaaa")
        with patch.object(dar, "SUMMARY_LOG", self.root / "log.jsonl"):
            self.assertEqual(dar.main(["--date", "2026-07-16", "--root", str(self.root), "--no-llm"]), 0)
            review = json.loads((run / "review.json").read_text())
            self.assertIn("citation_gate", review)
            self.assertEqual(review["identity"]["symbol"], "ABC")
            first = (run / "review.json").read_text()
            self.assertEqual(dar.main(["--date", "2026-07-16", "--root", str(self.root), "--no-llm"]), 0)
            self.assertEqual((run / "review.json").read_text(), first)

    def test_broken_run_is_recorded_not_raised(self):
        run = make_run(self.root, "2026-07-16", "BAD_2026-07-16_30_8_bbbb")
        (run / "final.html").unlink()
        (run / "article_llm_raw.html").unlink(missing_ok=True)
        with patch.object(dar, "SUMMARY_LOG", self.root / "log.jsonl"):
            self.assertEqual(dar.main(["--date", "2026-07-16", "--root", str(self.root), "--no-llm"]), 0)
        review = json.loads((run / "review.json").read_text())
        self.assertTrue(review["errors"])

    def test_llm_failure_is_captured_as_error_field(self):
        run = make_run(self.root, "2026-07-16", "LLM_2026-07-16_30_8_cccc")
        broken = types.SimpleNamespace(review_article=lambda *a, **k:
                                       (_ for _ in ()).throw(RuntimeError("api down")))
        with patch.dict(sys.modules, {"editorial_review": broken}), \
             patch.object(dar, "SUMMARY_LOG", self.root / "log.jsonl"):
            self.assertEqual(dar.main(["--date", "2026-07-16", "--root", str(self.root)]), 0)
        review = json.loads((run / "review.json").read_text())
        self.assertTrue(any("editorial review failed" in e for e in review["errors"]))
        self.assertIn("citation_gate", review)

    def test_missing_root_and_empty_day_are_safe(self):
        self.assertEqual(dar.main(["--root", str(self.root / "nope")]), 0)
        with patch.object(dar, "SUMMARY_LOG", self.root / "log.jsonl"):
            self.assertEqual(dar.main(["--date", "2026-01-01", "--root", str(self.root), "--no-llm"]), 0)

    def test_default_date_is_yesterday(self):
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        run = make_run(self.root, yesterday, f"YDA_{yesterday}_30_8_dddd")
        with patch.object(dar, "SUMMARY_LOG", self.root / "log.jsonl"):
            self.assertEqual(dar.main(["--root", str(self.root), "--no-llm"]), 0)
        self.assertTrue((run / "review.json").exists())


if __name__ == "__main__":
    unittest.main()
