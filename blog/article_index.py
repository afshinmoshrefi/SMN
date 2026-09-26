#!/usr/bin/env python3
"""
article_index.py — read model for the SMN publishing dashboard.

posts.json stays the source of truth.  This module adds the three facts the
dashboard needs but posts.json does not store, caches them, and filters.

Derived fields
    slug               stable article id (unique across posts.json)
    written_date       when the article file was first created on disk
    published_date     straight from posts.json
    pattern_start_date straight from posts.json
    pattern_end_date   pattern_start_date + pattern_days
    pattern_days       straight from posts.json
    word_count         visible words in the article HTML
    reading_minutes    word_count / 225, rounded up
    pinned / pin       current pin state, from pin_store

The derived-metrics cache lives in $SMN_DASHBOARD_STATE/article_metrics.json.
``written_date`` is written once and then never recomputed, so re-generating an
article does not rewrite its history.
"""

from __future__ import annotations

import html as html_lib
import json
import math
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Append, not insert: sibling files must win over any other copy.
for _extra in ("/home/flask", "/home/flask/blog"):
    if _extra not in sys.path:
        sys.path.append(_extra)

import pin_store
from pin_store import STATE_DIR, iso, parse_dt, utcnow

try:  # the webserver has config.py; tests may not
    import config
    _NEWS_ROOT = Path(getattr(config, "news_root_folder", "/var/www/smn/"))
except Exception:  # pragma: no cover
    _NEWS_ROOT = Path("/var/www/smn/")

NEWS_ROOT = Path(os.environ.get("SMN_NEWS_ROOT", str(_NEWS_ROOT)))
POSTS_JSON = NEWS_ROOT / "posts.json"
METRICS_JSON = STATE_DIR / "article_metrics.json"
TRASH_DIR = STATE_DIR / "trash"
BACKUP_DIR = STATE_DIR / "backups"
UNPUBLISHED_JSON = STATE_DIR / "unpublished.json"
HELD_DIR = STATE_DIR / "unpublished"
LOCK_FILE = STATE_DIR / "posts.lock"
MAX_BACKUPS = 50

WORDS_PER_MINUTE = 225

_SCRIPT_STYLE_RE = re.compile(r"<(script|style|svg)\b.*?</\1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# posts.json
# --------------------------------------------------------------------------- #
def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_posts() -> List[Dict[str, Any]]:
    if not POSTS_JSON.exists():
        return []
    try:
        posts = json.loads(POSTS_JSON.read_text("utf-8"))
    except Exception:
        return []
    return posts if isinstance(posts, list) else []


@contextmanager
def posts_lock():
    """Hold an exclusive lock for a read-modify-write of posts.json.

    Every dashboard write goes through this, so two agents saving at the same
    moment cannot silently drop each other's change.
    """
    import fcntl
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def save_posts(posts: List[Dict[str, Any]], backup: bool = True) -> None:
    """Write posts.json atomically.  Backups go to the private state dir,
    never the public web root, and only the newest MAX_BACKUPS are kept."""
    if backup and POSTS_JSON.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = utcnow().strftime("%Y%m%dT%H%M%S%fZ")
        try:
            (BACKUP_DIR / f"posts.json.{stamp}").write_bytes(POSTS_JSON.read_bytes())
            for old in sorted(BACKUP_DIR.glob("posts.json.*"))[:-MAX_BACKUPS]:
                old.unlink()
        except Exception:
            pass
    _atomic_write_json(POSTS_JSON, posts)


# --------------------------------------------------------------------------- #
# unpublished articles (off the site, kept for re-publishing)
# --------------------------------------------------------------------------- #
def load_unpublished() -> Dict[str, Dict[str, Any]]:
    """slug -> {"post", "held_path", "unpublished_at", "by"}."""
    try:
        data = json.loads(UNPUBLISHED_JSON.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_unpublished(data: Dict[str, Dict[str, Any]]) -> None:
    _atomic_write_json(UNPUBLISHED_JSON, data)


# --------------------------------------------------------------------------- #
# metrics cache
# --------------------------------------------------------------------------- #
def _load_metrics() -> Dict[str, Any]:
    if not METRICS_JSON.exists():
        return {}
    try:
        data = json.loads(METRICS_JSON.read_text("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_metrics(metrics: Dict[str, Any]) -> None:
    _atomic_write_json(METRICS_JSON, metrics)


def plain_text(article_html: str) -> str:
    """Visible text of an article, for agents that do not want HTML."""
    text = _SCRIPT_STYLE_RE.sub(" ", article_html or "")
    text = re.sub(r"</(p|h[1-6]|li|div|tr|blockquote|section|article)>", "\n", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = html_lib.unescape(_TAG_RE.sub(" ", text))
    lines = [_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def count_words(article_html: str) -> int:
    text = _SCRIPT_STYLE_RE.sub(" ", article_html or "")
    text = _TAG_RE.sub(" ", text)
    text = html_lib.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return len(text.split()) if text else 0


def _file_birth_iso(path: Path) -> Optional[str]:
    try:
        st = path.stat()
    except OSError:
        return None
    birth = getattr(st, "st_birthtime", None) or st.st_ctime
    from datetime import datetime, timezone
    return iso(datetime.fromtimestamp(birth, tz=timezone.utc))


def metrics_for(post: Dict[str, Any], cache: Dict[str, Any],
                refresh: bool = False) -> Dict[str, Any]:
    """Word count and written date for one post, cached by file mtime."""
    slug = pin_store.article_slug(post)
    path = Path(post.get("path") or "")
    entry = dict(cache.get(slug) or {})

    try:
        mtime = path.stat().st_mtime if path.is_file() else None
    except OSError:
        mtime = None

    stale = refresh or entry.get("mtime") != mtime or "word_count" not in entry
    if stale and path.is_file():
        try:
            entry["word_count"] = count_words(path.read_text("utf-8", errors="replace"))
        except OSError:
            entry.setdefault("word_count", 0)
        entry["mtime"] = mtime
        entry["measured_at"] = iso(utcnow())

    # written_date is recorded once and then frozen.
    if not entry.get("written_date"):
        # Some files were copied or rebuilt after publication, so the file
        # birth time can be later than published_date.  Take the earlier one.
        candidates = [parse_dt(_file_birth_iso(path)) if path.is_file() else None,
                      parse_dt(post.get("published_date"))]
        candidates = [c for c in candidates if c is not None]
        entry["written_date"] = iso(min(candidates)) if candidates else None

    entry.setdefault("word_count", 0)
    cache[slug] = entry
    return entry


# --------------------------------------------------------------------------- #
# enrichment
# --------------------------------------------------------------------------- #
def pattern_end_date(start: Any, days: Any) -> Optional[str]:
    start_dt = parse_dt(start)
    if start_dt is None:
        return None
    try:
        n = int(float(days))
    except (TypeError, ValueError):
        return None
    return (start_dt + timedelta(days=n)).strftime("%Y-%m-%d")


def hero_file_ok(post: Dict[str, Any]) -> bool:
    """True when the hero image file exists on disk and is not empty."""
    rel = re.sub(r"^https?://[^/]+", "", post.get("hero_image") or "")
    rel = rel.split("?", 1)[0].lstrip("/")
    if not rel:
        return False
    try:
        return (NEWS_ROOT / rel).stat().st_size > 0
    except OSError:
        return False


def enrich(post: Dict[str, Any], cache: Dict[str, Any],
           pins: Dict[str, Any], refresh: bool = False) -> Dict[str, Any]:
    slug = pin_store.article_slug(post)
    stats = metrics_for(post, cache, refresh=refresh)
    words = int(stats.get("word_count") or 0)
    pin = pins.get(slug)

    out = dict(post)
    out["slug"] = slug
    out["written_date"] = stats.get("written_date")
    out["pattern_end_date"] = pattern_end_date(post.get("pattern_start_date"),
                                               post.get("pattern_days"))
    out["word_count"] = words
    out["reading_minutes"] = max(1, math.ceil(words / WORDS_PER_MINUTE)) if words else 0
    out["pinned"] = bool(pin)
    out["pin"] = pin
    # Live on the site = listed in posts.json.  Unpublished rows are marked
    # by build_index.  (publish_status is written by the pipeline but no page
    # reads it, so it is not the truth.)
    out["published"] = True
    out["hero_ok"] = hero_file_ok(post)
    return out


def build_index(refresh: bool = False, persist: bool = True) -> List[Dict[str, Any]]:
    """Every article, newest first, with derived fields attached."""
    posts = load_posts()
    cache = _load_metrics()
    pins = pin_store.pins_by_slug()
    rows = [enrich(p, cache, pins, refresh=refresh) for p in posts]
    live = {r["slug"] for r in rows}
    for slug, record in load_unpublished().items():
        if slug in live:
            continue
        post = dict(record.get("post") or {})
        post["slug"] = slug
        held = dict(post, path=record.get("held_path") or "")
        row = enrich(held, cache, {}, refresh=refresh)
        row["published"] = False
        row["original_path"] = post.get("path")
        row["unpublished_at"] = record.get("unpublished_at")
        row["unpublished_by"] = record.get("by")
        rows.append(row)
    if persist:
        try:
            _save_metrics(cache)
        except Exception:
            pass
    rows.sort(key=lambda r: r.get("published_date") or "", reverse=True)
    return rows


# --------------------------------------------------------------------------- #
# filtering
# --------------------------------------------------------------------------- #
def _day(value: Any) -> str:
    dt = parse_dt(value)
    return dt.strftime("%Y-%m-%d") if dt else ""


def _in_range(value: Any, low: Any, high: Any) -> bool:
    day = _day(value)
    if low and (not day or day < _day(low)):
        return False
    if high and (not day or day > _day(high)):
        return False
    return True


FILTER_KEYS = (
    "q", "symbol", "market_family", "direction", "tag", "status", "pinned",
    "written_from", "written_to", "published_from", "published_to",
    "pattern_start_from", "pattern_start_to",
    "pattern_end_from", "pattern_end_to",
    "min_words", "max_words", "min_pattern_days", "max_pattern_days", "hero", "scheduled",
)

SORT_KEYS = {
    "published_date": "published_date",
    "written_date": "written_date",
    "pattern_start_date": "pattern_start_date",
    "pattern_end_date": "pattern_end_date",
    "word_count": "word_count",
    "symbol": "symbol",
    "title": "title",
}


def _csv(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = str(value).split(",")
    return [str(i).strip().lower() for i in items if str(i).strip()]


def filter_rows(rows: List[Dict[str, Any]], params: Dict[str, Any]) -> List[Dict[str, Any]]:
    get = params.get

    q = (get("q") or "").strip().lower()
    symbols = _csv(get("symbol"))
    families = _csv(get("market_family"))
    directions = _csv(get("direction"))
    tags = _csv(get("tag"))
    status = (get("status") or "any").strip().lower()
    pinned = (get("pinned") or "any").strip().lower()
    hero = (get("hero") or "any").strip().lower()

    def as_num(key):
        raw = get(key)
        if raw in (None, ""):
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    min_words, max_words = as_num("min_words"), as_num("max_words")
    min_days, max_days = as_num("min_pattern_days"), as_num("max_pattern_days")

    out = []
    for row in rows:
        if q:
            blob = " ".join(str(row.get(k) or "") for k in
                            ("title", "dek", "symbol", "slug", "seo_title")).lower()
            if q not in blob:
                continue
        if symbols:
            row_syms = {str(row.get("symbol") or "").lower()}
            row_syms |= {str(t).lower() for t in (row.get("tickers") or [])}
            if not row_syms & set(symbols):
                continue
        if families and str(row.get("market_family") or "").lower() not in families:
            continue
        if directions and str(row.get("direction") or "").lower() not in directions:
            continue
        if tags and not ({str(t).lower() for t in (row.get("tags") or [])} & set(tags)):
            continue
        if status == "published" and not row.get("published"):
            continue
        if status == "unpublished" and row.get("published"):
            continue
        if pinned == "true" and not row.get("pinned"):
            continue
        if pinned == "false" and row.get("pinned"):
            continue
        if hero == "ok" and not row.get("hero_ok"):
            continue
        if hero == "missing" and row.get("hero_ok"):
            continue
        if not _in_range(row.get("written_date"), get("written_from"), get("written_to")):
            continue
        if not _in_range(row.get("published_date"), get("published_from"), get("published_to")):
            continue
        if not _in_range(row.get("pattern_start_date"), get("pattern_start_from"), get("pattern_start_to")):
            continue
        if not _in_range(row.get("pattern_end_date"), get("pattern_end_from"), get("pattern_end_to")):
            continue

        words = row.get("word_count") or 0
        if min_words is not None and words < min_words:
            continue
        if max_words is not None and words > max_words:
            continue

        try:
            days = float(row.get("pattern_days") or 0)
        except (TypeError, ValueError):
            days = 0
        if min_days is not None and days < min_days:
            continue
        if max_days is not None and days > max_days:
            continue

        out.append(row)
    return out


def sort_rows(rows: List[Dict[str, Any]], sort: str = "published_date",
              order: str = "desc") -> List[Dict[str, Any]]:
    key = SORT_KEYS.get((sort or "").strip(), "published_date")
    reverse = (order or "desc").strip().lower() != "asc"

    def sort_key(row):
        value = row.get(key)
        if key == "word_count":
            try:
                return float(value or 0)
            except (TypeError, ValueError):
                return 0.0
        return str(value or "")

    return sorted(rows, key=sort_key, reverse=reverse)


def facets(rows: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Distinct values, so a UI or an agent can build filters without guessing."""
    def distinct(key):
        return sorted({str(r.get(key)) for r in rows if r.get(key)})
    tags = sorted({str(t) for r in rows for t in (r.get("tags") or []) if t})
    return {
        "symbol": distinct("symbol"),
        "market_family": distinct("market_family"),
        "direction": distinct("direction"),
        "tag": tags,
    }
