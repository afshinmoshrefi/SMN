#!/usr/bin/env python3
"""
pin_store.py — manual pinning for the SMN news home page.

The home page normally orders articles by ``published_date`` descending and
treats the first entry as the headline (see rebuild_news_home.py).  A *pin*
overrides that order for one article, for a limited time.

A pin is:

    {
      "pin_id":     "pin_3f2a91c0",
      "slug":       "qqq-subscription-2026-09-08",   # stable article id
      "position":   1,                               # 1 = headline
      "expires_at": "2026-10-01T00:00:00Z" | null,   # null = until removed
      "on_expiry":  "natural" | "republish",
      "note":       "100-year pattern headline",
      "created_at": "2026-09-24T09:00:00Z",
      "created_by": "afshin"
    }

``on_expiry``:
  * ``natural``   — the article falls back to its own published_date slot.
  * ``republish`` — its published_date is rewritten to the moment the pin
                    expired, so it drops out of the pinned slot and then
                    sinks down the page exactly as a freshly published
                    article would.

State lives in ``$SMN_DASHBOARD_STATE/pins.json`` (default
/var/lib/smn-dashboard), which is outside the public web root.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

STATE_DIR = Path(os.environ.get("SMN_DASHBOARD_STATE", "/var/lib/smn-dashboard"))
PINS_JSON = STATE_DIR / "pins.json"

ON_EXPIRY_CHOICES = ("natural", "republish")
MAX_HISTORY = 500


# --------------------------------------------------------------------------- #
# time helpers
# --------------------------------------------------------------------------- #
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_dt(value: Any) -> Optional[datetime]:
    """Parse an ISO date or datetime.  A bare date means 00:00:00 UTC."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #
def _empty_state() -> Dict[str, Any]:
    return {"version": 1, "pins": [], "history": []}


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


def load_state() -> Dict[str, Any]:
    if not PINS_JSON.exists():
        return _empty_state()
    try:
        state = json.loads(PINS_JSON.read_text("utf-8"))
    except Exception:
        return _empty_state()
    if not isinstance(state, dict):
        return _empty_state()
    state.setdefault("version", 1)
    state.setdefault("pins", [])
    state.setdefault("history", [])
    return state


def save_state(state: Dict[str, Any]) -> None:
    state["history"] = state.get("history", [])[-MAX_HISTORY:]
    _atomic_write_json(PINS_JSON, state)


# --------------------------------------------------------------------------- #
# queries
# --------------------------------------------------------------------------- #
def is_expired(pin: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    expires = parse_dt(pin.get("expires_at"))
    if expires is None:
        return False
    return expires <= (now or utcnow())


def all_pins() -> List[Dict[str, Any]]:
    return list(load_state().get("pins", []))


def active_pins(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now = now or utcnow()
    pins = [p for p in load_state().get("pins", []) if not is_expired(p, now)]
    pins.sort(key=lambda p: (int(p.get("position") or 1), p.get("created_at") or ""))
    return pins


def pins_by_slug(now: Optional[datetime] = None) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for pin in active_pins(now):
        out.setdefault(pin.get("slug") or "", pin)
    out.pop("", None)
    return out


# --------------------------------------------------------------------------- #
# mutations
# --------------------------------------------------------------------------- #
def resolve_expiry(until: Any = None, days: Any = None,
                   now: Optional[datetime] = None) -> Optional[str]:
    """Turn either an absolute date or a number of days into an ISO instant."""
    now = now or utcnow()
    if until not in (None, ""):
        dt = parse_dt(until)
        if dt is None:
            raise ValueError(f"cannot parse 'until' date: {until!r}")
        return iso(dt)
    if days not in (None, ""):
        try:
            n = float(days)
        except (TypeError, ValueError):
            raise ValueError(f"'days' must be a number, got {days!r}")
        if n <= 0:
            raise ValueError("'days' must be greater than 0")
        return iso(now + timedelta(days=n))
    return None


def set_pin(slug: str, position: int = 1, until: Any = None, days: Any = None,
            on_expiry: str = "natural", note: str = "",
            created_by: str = "dashboard") -> Dict[str, Any]:
    """Create or replace the pin for ``slug``.  One pin per article."""
    slug = (slug or "").strip()
    if not slug:
        raise ValueError("slug is required")
    try:
        position = int(position)
    except (TypeError, ValueError):
        raise ValueError(f"position must be a whole number, got {position!r}")
    if position < 1:
        raise ValueError("position must be 1 or greater (1 = headline)")
    if on_expiry not in ON_EXPIRY_CHOICES:
        raise ValueError(f"on_expiry must be one of {ON_EXPIRY_CHOICES}")

    expires_at = resolve_expiry(until, days)

    state = load_state()
    pins = [p for p in state.get("pins", []) if p.get("slug") != slug]
    pin = {
        "pin_id": "pin_" + uuid.uuid4().hex[:8],
        "slug": slug,
        "position": position,
        "expires_at": expires_at,
        "on_expiry": on_expiry,
        "note": note or "",
        "created_at": iso(utcnow()),
        "created_by": created_by or "dashboard",
    }
    pins.append(pin)
    state["pins"] = pins
    save_state(state)
    return pin


def remove_pin(slug: str, reason: str = "removed") -> Optional[Dict[str, Any]]:
    state = load_state()
    kept, dropped = [], None
    for pin in state.get("pins", []):
        if dropped is None and pin.get("slug") == slug:
            dropped = pin
        else:
            kept.append(pin)
    if dropped is None:
        return None
    state["pins"] = kept
    record = dict(dropped)
    record["ended_at"] = iso(utcnow())
    record["ended_reason"] = reason
    state.setdefault("history", []).append(record)
    save_state(state)
    return dropped


# --------------------------------------------------------------------------- #
# ordering
# --------------------------------------------------------------------------- #
def article_slug(article: Dict[str, Any]) -> str:
    slug = (article.get("slug") or "").strip()
    if slug:
        return slug
    url = (article.get("url") or article.get("path") or "").rstrip("/")
    return url.rsplit("/", 1)[-1].replace(".html", "") if url else ""


def apply_pins(articles: List[Dict[str, Any]],
               now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Reorder ``articles`` (already sorted newest first) to honour the pins.

    Returns a new list.  Pinned articles get a ``_pin`` key so templates can
    show a badge.  Unknown slugs are ignored, so a deleted article can never
    break the home page.
    """
    wanted = pins_by_slug(now)
    if not wanted:
        return list(articles)

    pinned, rest = [], []
    for article in articles:
        pin = wanted.get(article_slug(article))
        if pin:
            article["_pin"] = pin
            pinned.append(article)
        else:
            article.pop("_pin", None)
            rest.append(article)

    if not pinned:
        return list(articles)

    pinned.sort(key=lambda a: (int(a["_pin"].get("position") or 1),
                               a["_pin"].get("created_at") or ""))
    out = list(rest)
    for article in pinned:
        index = max(1, int(article["_pin"].get("position") or 1)) - 1
        out.insert(min(index, len(out)), article)
    return out


# --------------------------------------------------------------------------- #
# expiry sweep
# --------------------------------------------------------------------------- #
def sweep_expired(posts: Optional[List[Dict[str, Any]]] = None,
                  now: Optional[datetime] = None,
                  on_republish: Optional[Callable[[Dict[str, Any]], None]] = None
                  ) -> Dict[str, Any]:
    """Retire expired pins.

    ``posts`` is the live posts.json list.  It is mutated in place for
    ``on_expiry == "republish"`` pins: the article's published_date becomes the
    instant the pin expired.  The caller writes posts.json back and is given
    each changed post through ``on_republish`` so it can sync Redis.

    Returns {"expired": [...], "republished": [slug, ...]}.
    """
    now = now or utcnow()
    state = load_state()
    kept, expired = [], []
    for pin in state.get("pins", []):
        (expired if is_expired(pin, now) else kept).append(pin)

    if not expired:
        return {"expired": [], "republished": []}

    by_slug = {article_slug(p): p for p in (posts or [])}
    republished: List[str] = []

    for pin in expired:
        record = dict(pin)
        record["ended_at"] = iso(now)
        record["ended_reason"] = "expired"
        if pin.get("on_expiry") == "republish":
            post = by_slug.get(pin.get("slug"))
            if post is not None:
                post["published_date"] = pin.get("expires_at") or iso(now)
                post["updated_date"] = iso(now)
                republished.append(pin.get("slug"))
                record["republished"] = True
                if on_republish:
                    on_republish(post)
        state.setdefault("history", []).append(record)

    state["pins"] = kept
    save_state(state)
    return {"expired": expired, "republished": republished}
