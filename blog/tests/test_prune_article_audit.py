import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prune_article_audit as pruner

TODAY = datetime.date(2026, 7, 15)


def _make_run(root: Path, d: datetime.date, name: str, payload: int = 1000) -> Path:
    dd = root / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}" / name
    dd.mkdir(parents=True)
    (dd / "manifest.json").write_text(json.dumps({"name": name, "date": d.isoformat()}))
    (dd / "prompt.txt").write_bytes(b"x" * payload)
    (dd / "final.html").write_bytes(b"y" * payload)
    return dd


def _plenty_disk():
    # total huge, ~99% free -> free floor never trips
    return (10 ** 12, 99 * 10 ** 10)


class PruneCompressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_compresses_only_cold_runs(self):
        _make_run(self.root, datetime.date(2026, 1, 5), "OLD_a")     # < cutoff -> compress
        _make_run(self.root, datetime.date(2026, 7, 1), "OLD_b")     # < cutoff -> compress
        recent = _make_run(self.root, datetime.date(2026, 7, 15), "HOT")  # >= cutoff -> loose

        summary = pruner.prune(
            self.root, today=TODAY, compress_after_days=7,
            max_bytes=10 ** 12, min_free_fraction=0.10, keep_min_runs=100,
            disk_usage=_plenty_disk,
        )

        self.assertEqual(summary["compressed"], 2)
        self.assertEqual(summary["deleted"], 0)
        # Cold runs became archives with an extracted manifest sidecar; dir gone.
        for name, mm, dd in [("OLD_a", "01", "05"), ("OLD_b", "07", "01")]:
            base = self.root / "2026" / mm / dd
            self.assertTrue((base / f"{name}.tar.gz").exists())
            self.assertTrue((base / f"{name}.manifest.json").exists())
            self.assertFalse((base / name).exists())
        # Recent run untouched and still directly readable.
        self.assertTrue(recent.exists())
        self.assertTrue((recent / "prompt.txt").exists())

    def test_idempotent_second_pass_is_noop(self):
        _make_run(self.root, datetime.date(2026, 1, 5), "OLD")
        common = dict(today=TODAY, compress_after_days=7, max_bytes=10 ** 12,
                      min_free_fraction=0.10, keep_min_runs=100, disk_usage=_plenty_disk)
        pruner.prune(self.root, **common)
        second = pruner.prune(self.root, **common)
        self.assertEqual(second["compressed"], 0)
        self.assertEqual(second["deleted"], 0)


class PruneTrimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        # Five runs on distinct ascending dates (all recent -> stay loose).
        self.dates = [datetime.date(2026, 7, d) for d in (9, 10, 11, 12, 13)]
        for i, d in enumerate(self.dates):
            _make_run(self.root, d, f"R{i}", payload=1000)

    def test_self_cap_deletes_oldest_first(self):
        # Each run ~2KB; cap at 5KB -> keep newest ~2, delete oldest.
        summary = pruner.prune(
            self.root, today=TODAY, compress_after_days=999,  # no compression
            max_bytes=5000, min_free_fraction=0.0, keep_min_runs=1,
            disk_usage=_plenty_disk,
        )
        self.assertGreaterEqual(summary["deleted"], 1)
        deleted_dates = [r["date"] for r in summary["deleted_runs"]]
        # Oldest-first ordering.
        self.assertEqual(deleted_dates, sorted(deleted_dates))
        self.assertEqual(deleted_dates[0], "2026-07-09")
        self.assertLessEqual(summary["audit_bytes"], 5000)
        self.assertFalse((self.root / "2026" / "07" / "09" / "R0").exists())
        self.assertTrue((self.root / "2026" / "07" / "13" / "R4").exists())

    def test_keep_min_runs_is_a_hard_floor(self):
        summary = pruner.prune(
            self.root, today=TODAY, compress_after_days=999,
            max_bytes=0, min_free_fraction=0.0, keep_min_runs=3,  # cap=0 -> always over
            disk_usage=_plenty_disk,
        )
        self.assertEqual(summary["deleted"], 2)          # 5 - 3
        self.assertEqual(summary["runs_remaining"], 3)
        self.assertEqual(summary["stop_reason"], "keep_min_runs_floor")

    def test_free_floor_triggers_deletion(self):
        # Caps effectively disabled; disk under the 10% floor forces reclamation.
        summary = pruner.prune(
            self.root, today=TODAY, compress_after_days=999,
            max_bytes=10 ** 12, min_free_fraction=0.10, keep_min_runs=1,
            disk_usage=lambda: (10 ** 9, 0),  # 0% free
        )
        self.assertGreaterEqual(summary["deleted"], 1)

    def test_dry_run_changes_nothing(self):
        before = sorted(p.name for p in self.root.rglob("*"))
        summary = pruner.prune(
            self.root, today=TODAY, compress_after_days=0,   # would compress all
            max_bytes=0, min_free_fraction=0.0, keep_min_runs=1,  # would delete
            disk_usage=_plenty_disk, dry_run=True,
        )
        after = sorted(p.name for p in self.root.rglob("*"))
        self.assertEqual(before, after)  # nothing actually touched
        self.assertTrue(summary["dry_run"])

    def test_missing_audit_dir_is_safe(self):
        summary = pruner.prune(
            Path(self.tmp.name) / "does-not-exist", today=TODAY, compress_after_days=7,
            max_bytes=1, min_free_fraction=0.5, keep_min_runs=1, disk_usage=_plenty_disk,
        )
        self.assertEqual(summary["stop_reason"], "no_audit_dir")
        self.assertEqual(summary["deleted"], 0)


if __name__ == "__main__":
    unittest.main()
