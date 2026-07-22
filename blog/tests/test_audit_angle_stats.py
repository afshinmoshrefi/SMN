"""audit_angle_stats is a pure observer: reads the audit tree, prints per-angle
rollups, writes nothing. These tests build a tmpdir fixture tree of manifest +
review runs and check the aggregation, the legacy grouping, the TEMPLATE_CADENCE
callout, the date-window boundary, and that main() stays exit-0 and read-only."""
import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path

BLOG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BLOG))
import audit_angle_stats as aas


def make_run(root: Path, date: str, name: str, *, angle=None, engine=None,
             decision="publish", hard=(), soft=(), reviewed=True):
    y, m, d = date.split("-")
    run = root / y / m / d / name
    run.mkdir(parents=True)
    identity = {"symbol": "ABC", "article_publish_date": date}
    if engine is not None:
        identity["engine"] = engine
    if angle is not None:
        identity["angle"] = angle
    (run / "manifest.json").write_text(json.dumps(
        {"schema_version": 1, "article_id": name[-8:], "identity": identity}))
    (run / "final.html").write_text("<html><body>x</body></html>")
    if reviewed:
        (run / "review.json").write_text(json.dumps({
            "schema_version": 1, "identity": identity, "errors": [],
            "editorial": {
                "decision": decision,
                "hard_issues": [{"code": c, "detail": ""} for c in hard],
                "soft_issues": [{"code": c, "detail": ""} for c in soft],
            }}))
    return run


class CollectStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # In-window: 2026-07-14 .. 2026-07-20
        make_run(self.root, "2026-07-20", "REGIME_a", angle="REGIME", engine="angle",
                 decision="hold", hard=["TW_DAYS_TRADING"],
                 soft=["TEMPLATE_CADENCE", "REPETITION"])
        make_run(self.root, "2026-07-20", "REGIME_b", angle="REGIME", engine="angle",
                 decision="publish", soft=["TEMPLATE_CADENCE"])
        make_run(self.root, "2026-07-20", "CLOCK_a", angle="CLOCKWORK", engine="angle",
                 decision="repair", hard=["INVENTED_NUMBER"], soft=["HYPE"])
        make_run(self.root, "2026-07-19", "LEGACY_a",   # no engine/angle -> legacy
                 decision="publish", soft=["REPETITION"])
        make_run(self.root, "2026-07-19", "REGIME_c", angle="REGIME", engine="angle",
                 reviewed=False)  # queued but not yet reviewed
        # Out-of-window (older than 7 days back from 2026-07-20): must be ignored.
        make_run(self.root, "2026-07-10", "CLOCK_old", angle="CLOCKWORK", engine="angle",
                 decision="hold", soft=["TEMPLATE_CADENCE"])
        end = datetime.date(2026, 7, 20)
        self.dates = [end - datetime.timedelta(days=i) for i in range(7)]

    def test_grouping_and_counts(self):
        stats = aas.collect_stats(self.root, self.dates)
        self.assertEqual(set(stats), {"REGIME", "CLOCKWORK", "legacy"})
        self.assertEqual(stats["REGIME"]["runs"], 3)         # 2 reviewed + 1 unreviewed
        self.assertEqual(stats["CLOCKWORK"]["runs"], 1)      # old one excluded
        self.assertEqual(stats["legacy"]["runs"], 1)

    def test_decision_counts_include_unreviewed(self):
        stats = aas.collect_stats(self.root, self.dates)
        self.assertEqual(dict(stats["REGIME"]["decisions"]),
                         {"hold": 1, "publish": 1, "unreviewed": 1})

    def test_issue_frequencies_and_template_cadence(self):
        stats = aas.collect_stats(self.root, self.dates)
        self.assertEqual(stats["REGIME"]["template_cadence"], 2)
        self.assertEqual(dict(stats["REGIME"]["soft_issues"]),
                         {"TEMPLATE_CADENCE": 2, "REPETITION": 1})
        self.assertEqual(dict(stats["REGIME"]["hard_issues"]), {"TW_DAYS_TRADING": 1})
        self.assertEqual(dict(stats["CLOCKWORK"]["hard_issues"]), {"INVENTED_NUMBER": 1})
        self.assertEqual(stats["CLOCKWORK"]["template_cadence"], 0)

    def test_out_of_window_excluded(self):
        # The 2026-07-10 CLOCKWORK hold must not leak into the 7-day window.
        stats = aas.collect_stats(self.root, self.dates)
        self.assertNotIn("hold", stats["CLOCKWORK"]["decisions"])


class ReportAndMainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_main_is_exit0_and_readonly(self):
        make_run(self.root, "2026-07-20", "REGIME_a", angle="REGIME", engine="angle",
                 decision="hold", soft=["TEMPLATE_CADENCE"])
        before = sorted(p.name for p in (self.root / "2026" / "07" / "20").iterdir())
        rc = aas.main(["--root", str(self.root), "--end", "2026-07-20", "--days", "7"])
        self.assertEqual(rc, 0)
        # observer never writes into the run dirs
        after = sorted(p.name for p in (self.root / "2026" / "07" / "20").iterdir())
        self.assertEqual(before, after)

    def test_report_calls_out_angle_and_cadence(self):
        make_run(self.root, "2026-07-20", "REGIME_a", angle="REGIME", engine="angle",
                 decision="hold", soft=["TEMPLATE_CADENCE"])
        dates = [datetime.date(2026, 7, 20)]
        report = aas.format_report(aas.collect_stats(self.root, dates), self.root, dates)
        self.assertIn("ANGLE: REGIME", report)
        self.assertIn("TEMPLATE_CADENCE: 1", report)

    def test_missing_root_is_safe(self):
        self.assertEqual(aas.main(["--root", str(self.root / "nope")]), 0)

    def test_empty_window_is_safe(self):
        rc = aas.main(["--root", str(self.root), "--end", "2026-07-20", "--days", "3"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
