#!/usr/bin/env python3
"""
schedule_store.py — scheduled publish / unpublish for the SMN dashboard.

An unpublished article stays unpublished until someone publishes it or a
schedule does.  Each schedule records who made it.

    {
      "id":         "sch_4b1e9a20",
      "slug":       "qqq-subscription-2026-09-08",
      "action":     "publish" | "unpublish",
      "at":         "2026-09-25T11:00:00Z",      # UTC
      "as_new":     false,                       # publish only: fresh date
      "note":       "",
      "created_by": "afshin",
      "created_at": "2026-09-24T12:00:00Z",
      "status":     "pending" | "done" | "skipped" | "failed" | "cancelled",
      "finished_at": null, "result_note": ""
    }

State: $SMN_DASHBOARD_STATE/schedule.json.  pin_sweeper.py (timer, every
minute) runs the due ones through pub_dashboard.run_due_schedules().
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import article_index
from pin_store import STATE_DIR, iso, parse_dt, utcnow

SCHEDULE_JSON = STATE_DIR / "schedule.json"
LOCK_FILE = STATE_DIR / "schedule.lock"
DEFAULT_TZ = "America/New_York"
KEEP_FINISHED = 500


@contextmanager
def _lock():
    import fcntl
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _load() -> List[Dict[str, Any]]:
    try:
        data = json.loads(SCHEDULE_JSON.read_text("utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(items: List[Dict[str, Any]]) -> None:
    pending = [i for i in items if i.get("status") == "pending"]
    finished = [i for i in items if i.get("status") != "pending"][-KEEP_FINISHED:]
    article_index._atomic_write_json(SCHEDULE_JSON, pending + finished)


def parse_when(value: Any, tz: Optional[str] = None) -> datetime:
    """ISO time -> aware UTC datetime.  A time with no offset is read in ``tz``
    (default America/New_York, the market's clock)."""
    if value in (None, ""):
        raise ValueError("a time is required")
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"cannot read time {value!r}; use ISO, e.g. 2026-09-25T07:00")
    if dt.tzinfo is None:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(tz or DEFAULT_TZ))
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown time zone {tz!r}")
    return dt.astimezone(timezone.utc)


def add(slug: str, action: str, when: datetime, who: str,
        as_new: bool = False, note: str = "") -> Dict[str, Any]:
    """Add a schedule.  A pending one for the same slug+action is replaced."""
    item = {"id": "sch_" + uuid.uuid4().hex[:8], "slug": slug, "action": action,
            "at": iso(when), "as_new": bool(as_new) if action == "publish" else False,
            "note": note or "", "created_by": who, "created_at": iso(utcnow()),
            "status": "pending", "finished_at": None, "result_note": ""}
    with _lock():
        items = _load()
        for old in items:
            if (old.get("status") == "pending" and old.get("slug") == slug
                    and old.get("action") == action):
                old.update(status="cancelled", finished_at=iso(utcnow()),
                           result_note=f"replaced by {item['id']}")
        items.append(item)
        _save(items)
    return item


def items(status: Optional[str] = "pending", slug: Optional[str] = None,
          action: Optional[str] = None) -> List[Dict[str, Any]]:
    out = [i for i in _load()
           if (status is None or i.get("status") == status)
           and (slug is None or i.get("slug") == slug)
           and (action is None or i.get("action") == action)]
    out.sort(key=lambda i: i.get("at") or "")
    return out


def pending(slug: Optional[str] = None, action: Optional[str] = None) -> List[Dict[str, Any]]:
    return items("pending", slug, action)


def by_slug() -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for item in pending():
        out.setdefault(item["slug"], []).append(item)
    return out


def get(item_id: str) -> Optional[Dict[str, Any]]:
    return next((i for i in _load() if i.get("id") == item_id), None)


def due(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now = now or utcnow()
    return [i for i in pending() if (parse_dt(i.get("at")) or now) <= now]


def finish(item_id: str, status: str, note: str = "") -> None:
    with _lock():
        items_ = _load()
        for item in items_:
            if item.get("id") == item_id:
                item.update(status=status, finished_at=iso(utcnow()), result_note=note)
        _save(items_)


def drop_for(slug: str, reason: str) -> None:
    """Cancel every pending schedule of an article (e.g. it was deleted)."""
    with _lock():
        items_ = _load()
        for item in items_:
            if item.get("status") == "pending" and item.get("slug") == slug:
                item.update(status="cancelled", finished_at=iso(utcnow()),
                            result_note=reason)
        _save(items_)
