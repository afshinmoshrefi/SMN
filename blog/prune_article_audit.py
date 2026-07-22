#!/usr/bin/env python3
"""
prune_article_audit.py
======================

Maintenance job for the article audit trail written by blog/article_audit.py.
Run daily from cron. It does two things, in order:

  1. COMPRESS cold runs. Any run whose date partition is older than
     `article_audit_compress_after_days` is tarred+gzipped into `<run>.tar.gz`
     (with its manifest.json copied out alongside for indexing) and the loose
     directory removed. Recent runs stay uncompressed so they remain directly
     greppable/diffable — the ones you actually inspect.

  2. TRIM oldest-first. After compression, delete whole runs oldest-first while
     EITHER guard trips:
       (a) self-cap      — total audit bytes exceed `article_audit_max_bytes`
       (b) free floor    — disk free fraction below `article_audit_min_free_fraction`
     ...but never below `article_audit_keep_min_runs` most-recent runs.

Everything is idempotent, bounded, and safe to run repeatedly. The core `prune()`
takes an injected clock and disk-usage function so it is fully unit-testable; the
CLI wraps it with the real clock, real config, real disk usage, and a file lock.

Layout it operates on (from article_audit.py):
    <audit_dir>/YYYY/MM/DD/<SYMBOL>_<start>_<days>_<years>_<article_id>/   (loose)
    <audit_dir>/YYYY/MM/DD/<run>.tar.gz + <run>.manifest.json             (compressed)
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
import tarfile
from pathlib import Path
from typing import Callable, List, NamedTuple, Optional


class Run(NamedTuple):
    date: datetime.date          # from the YYYY/MM/DD path partition
    name: str                    # run directory / archive base name
    kind: str                    # "loose" | "compressed"
    paths: List[Path]            # filesystem entries that make up this run
    size: int                    # total bytes on disk
    dd_dir: Path                 # parent YYYY/MM/DD directory

    @property
    def sort_key(self):
        return (self.date, self.name)


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _iter_runs(root: Path) -> List[Run]:
    """Enumerate every run under <root>/YYYY/MM/DD, loose or compressed."""
    runs: List[Run] = []
    if not root.is_dir():
        return runs
    for yyyy in sorted(p for p in root.iterdir() if p.is_dir() and p.name.isdigit() and len(p.name) == 4):
        for mm in sorted(p for p in yyyy.iterdir() if p.is_dir() and p.name.isdigit() and len(p.name) == 2):
            for dd in sorted(p for p in mm.iterdir() if p.is_dir() and p.name.isdigit() and len(p.name) == 2):
                try:
                    part = datetime.date(int(yyyy.name), int(mm.name), int(dd.name))
                except ValueError:
                    continue
                seen_compressed = set()
                # Compressed runs first so we can skip their manifest sidecars.
                for child in sorted(dd.iterdir()):
                    if child.is_file() and child.name.endswith(".tar.gz"):
                        base = child.name[: -len(".tar.gz")]
                        seen_compressed.add(base)
                        paths = [child]
                        manifest = dd / f"{base}.manifest.json"
                        if manifest.exists():
                            paths.append(manifest)
                        size = sum(p.stat().st_size for p in paths if p.exists())
                        runs.append(Run(part, base, "compressed", paths, size, dd))
                for child in sorted(dd.iterdir()):
                    if child.is_dir():
                        runs.append(Run(part, child.name, "loose", [child], _dir_size(child), dd))
    return runs


def _compress_run(run: Run, dry_run: bool) -> int:
    """Tar+gzip a loose run and drop the directory. Returns bytes reclaimed
    (uncompressed size minus resulting archive+manifest). Best-effort; never
    raises fatally."""
    run_dir = run.paths[0]
    dd = run.dd_dir
    tar_final = dd / f"{run.name}.tar.gz"
    if tar_final.exists() or dry_run:
        return 0
    tar_tmp = dd / f"{run.name}.tar.gz.tmp"
    try:
        with tarfile.open(tar_tmp, "w:gz") as tf:
            tf.add(run_dir, arcname=run.name)
        os.replace(tar_tmp, tar_final)
        manifest = run_dir / "manifest.json"
        if manifest.exists():
            shutil.copy2(manifest, dd / f"{run.name}.manifest.json")
        shutil.rmtree(run_dir)
        new_size = tar_final.stat().st_size
        sidecar = dd / f"{run.name}.manifest.json"
        if sidecar.exists():
            new_size += sidecar.stat().st_size
        return max(0, run.size - new_size)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[AUDIT-PRUNE] compress failed for {run.name}: {exc}")
        try:
            if tar_tmp.exists():
                tar_tmp.unlink()
        except OSError:
            pass
        return 0


def _delete_run(run: Run, dry_run: bool) -> None:
    if dry_run:
        return
    for path in run.paths:
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        except OSError as exc:  # pragma: no cover - defensive
            print(f"[AUDIT-PRUNE] delete failed for {path}: {exc}")


def prune(
    root,
    *,
    today: datetime.date,
    compress_after_days: int,
    max_bytes: int,
    min_free_fraction: float,
    keep_min_runs: int,
    disk_usage: Callable[[], "tuple[int, int]"],
    dry_run: bool = False,
) -> dict:
    """Compress cold runs then trim oldest-first under the two guards.

    `disk_usage()` returns (total_bytes, free_bytes) for the volume holding the
    audit dir. Injected so the policy is deterministic and unit-testable."""
    root = Path(root)
    summary = {
        "root": str(root),
        "dry_run": bool(dry_run),
        "compressed": 0,
        "compress_reclaimed_bytes": 0,
        "deleted": 0,
        "delete_reclaimed_bytes": 0,
        "deleted_runs": [],
        "runs_remaining": 0,
        "audit_bytes": 0,
        "stop_reason": None,
    }
    if not root.is_dir():
        summary["stop_reason"] = "no_audit_dir"
        return summary

    cutoff = today - datetime.timedelta(days=max(0, int(compress_after_days)))

    # --- PASS 1: compress cold loose runs ---
    for run in _iter_runs(root):
        if run.kind == "loose" and run.date < cutoff:
            reclaimed = _compress_run(run, dry_run)
            if reclaimed or dry_run:
                summary["compressed"] += 1
                summary["compress_reclaimed_bytes"] += reclaimed

    # --- PASS 2: enforce caps, oldest-first ---
    runs = sorted(_iter_runs(root), key=lambda r: r.sort_key)  # oldest first
    total = sum(r.size for r in runs)
    disk_total, disk_free = disk_usage()

    def _over_cap(t):
        return t > max_bytes

    def _under_floor(free):
        return disk_total > 0 and (free / disk_total) < min_free_fraction

    idx = 0
    remaining = len(runs)
    while idx < len(runs) and remaining > keep_min_runs and (_over_cap(total) or _under_floor(disk_free)):
        victim = runs[idx]
        _delete_run(victim, dry_run)
        summary["deleted"] += 1
        summary["delete_reclaimed_bytes"] += victim.size
        summary["deleted_runs"].append({"date": victim.date.isoformat(),
                                        "name": victim.name, "bytes": victim.size})
        total -= victim.size
        disk_free += victim.size  # deleting frees space on the same volume
        remaining -= 1
        idx += 1

    if _over_cap(total) or _under_floor(disk_free):
        summary["stop_reason"] = "keep_min_runs_floor"  # guard still trips but we stopped
    else:
        summary["stop_reason"] = "within_limits"
    summary["runs_remaining"] = remaining
    summary["audit_bytes"] = total
    return summary


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def _load_config():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import config
    return config


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Compress and trim the article audit trail.")
    parser.add_argument("--dry-run", action="store_true", help="Report actions without touching disk.")
    parser.add_argument("--root", default=None, help="Override audit dir (defaults to config.article_audit_dir).")
    args = parser.parse_args(argv)

    cfg = _load_config()
    root = Path(args.root or getattr(cfg, "article_audit_dir", "") or "").resolve()
    if not str(root) or str(root) in ("/", os.path.expanduser("~")):
        print(f"[AUDIT-PRUNE] refusing to operate on unsafe root: {root!r}")
        return 2
    if root.is_symlink():
        print(f"[AUDIT-PRUNE] refusing to follow symlinked root: {root}")
        return 2

    # Single-instance lock so overlapping cron runs cannot race.
    import fcntl
    lock_path = root.parent / ".article_audit_prune.lock"
    try:
        root.parent.mkdir(parents=True, exist_ok=True)
        lock_fh = open(lock_path, "w")
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        print("[AUDIT-PRUNE] another prune is already running; skipping.")
        return 0

    def disk_usage():
        usage = shutil.disk_usage(root if root.exists() else root.parent)
        return usage.total, usage.free

    summary = prune(
        root,
        today=datetime.date.today(),
        compress_after_days=int(getattr(cfg, "article_audit_compress_after_days", 7)),
        max_bytes=int(getattr(cfg, "article_audit_max_bytes", 10 * 1024 ** 3)),
        min_free_fraction=float(getattr(cfg, "article_audit_min_free_fraction", 0.10)),
        keep_min_runs=int(getattr(cfg, "article_audit_keep_min_runs", 200)),
        disk_usage=disk_usage,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
