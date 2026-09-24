#!/usr/bin/env python3
"""
pub_dashboard.py — SMN publishing dashboard (UI + agent API).

Runs as its own Flask app, separate from blog_queue.py, so a dashboard change
can never take the publishing queue down.

  UI      GET  /
  API     GET  /api/articles                list + filter + sort + paginate
          GET  /api/articles/<slug>         one article (add ?include=html)
          POST /api/articles                add a new HTML article
          PATCH/PUT /api/articles/<slug>    replace metadata and/or HTML
          DELETE   /api/articles/<slug>     unpublish + move files to trash
          POST /api/articles/<slug>/unpublish   off the site, kept
          POST /api/articles/<slug>/publish     back on the site
          GET/POST /api/schedule, DELETE /api/schedule/<id>   scheduling
          GET/PUT /api/order, POST /api/order/move   home-page order
          GET  /api/pins                    every pin, active and expired
          PUT  /api/pins/<slug>             pin an article
          DELETE /api/pins/<slug>           unpin now
          POST /api/articles/<slug>/hero/recreate   existing blog_queue hero mode
          POST /api/rebuild                 regenerate the news home page
  Agent   GET /llms.txt  /openapi.json  /api/schema  /api/health

Every JSON reply uses the same envelope:
    success  {"ok": true,  "data": ..., "meta": {...}}
    failure  {"ok": false, "error": {"code", "message", "hint", "field"}}
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import unicodedata
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Append, not insert: sibling files must win over any other copy.
for _extra in ("/home/flask", "/home/flask/blog"):
    if _extra not in sys.path:
        sys.path.append(_extra)

from flask import Flask, Response, jsonify, render_template, request

import article_index
import pin_store
import schedule_store
from article_index import NEWS_ROOT, POSTS_JSON, TRASH_DIR
from pin_store import iso, parse_dt, utcnow

VERSION = "1.0.0"
BLOG_DIR = Path(__file__).resolve().parent
BLOG_QUEUE_URL = os.environ.get("SMN_BLOG_QUEUE_URL", "http://127.0.0.1:7171")
DEFAULT_LIMIT = 50
MAX_LIMIT = 500

app = Flask(__name__)
app.url_map.strict_slashes = False


# --------------------------------------------------------------------------- #
# envelope helpers
# --------------------------------------------------------------------------- #
def ok(data: Any, **meta) -> Response:
    body = {"ok": True, "data": data, "meta": {"version": VERSION,
                                               "generated_at": iso(utcnow()), **meta}}
    response = jsonify(body)
    response.headers["X-SMN-Dashboard-Version"] = VERSION
    return response


def fail(code: str, message: str, status: int = 400,
         hint: str = "", field: str = "") -> Tuple[Response, int]:
    body = {"ok": False, "error": {"code": code, "message": message,
                                   "hint": hint, "field": field}}
    response = jsonify(body)
    response.headers["X-SMN-Dashboard-Version"] = VERSION
    return response, status


AUDIT_LOG = pin_store.STATE_DIR / "audit.jsonl"


def actor(data: Optional[Dict[str, Any]] = None) -> str:
    """Who is acting: X-Actor header, then created_by/actor in the body."""
    data = data or {}
    return (request.headers.get("X-Actor") or data.get("actor")
            or data.get("created_by") or "dashboard").strip()[:64]


def audit(action: str, slug: str, who: str, **detail) -> None:
    """Append one line per change.  Never fatal."""
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": iso(utcnow()), "action": action, "slug": slug,
                                 "actor": who, **detail}, ensure_ascii=False) + "\n")
    except Exception as exc:  # pragma: no cover
        app.logger.warning("audit write failed: %s", exc)


def trim_fields(row: Dict[str, Any], fields: List[str]) -> Dict[str, Any]:
    if not fields:
        return row
    keep = set(fields) | {"slug"}
    return {k: v for k, v in row.items() if k in keep}


def body_json() -> Dict[str, Any]:
    data = request.get_json(silent=True)
    if data is None and request.form:
        data = request.form.to_dict()
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------- #
# site helpers
# --------------------------------------------------------------------------- #
def site_base() -> str:
    """Public origin of this site, learned from posts.json."""
    env = os.environ.get("SMN_SITE_BASE")
    if env:
        return env.rstrip("/")
    for post in article_index.load_posts():
        url = post.get("url") or ""
        match = re.match(r"^(https?://[^/]+)", url)
        if match:
            return match.group(1)
    return "https://smn-dev.trxstat.com"


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:120] or "article"


def sync_redis(post: Dict[str, Any], delete: bool = False) -> Optional[str]:
    """Keep Redis db=3 in step with posts.json.  Never fatal."""
    try:
        import publish_article as pa
        key = pa.make_redis_key(post.get("resource_id", ""),
                                str(post.get("symbol", "")),
                                post.get("pattern_start_date", ""),
                                post.get("pattern_days", ""),
                                str(post.get("lookback_years", "")))
        if delete:
            pa.delete_article_from_redis(key)
        else:
            pa.save_article_to_redis(key, post)
        return key
    except Exception as exc:  # pragma: no cover
        app.logger.warning("redis sync skipped: %s", exc)
        return None


def rebuild_home(timeout: int = 600) -> Dict[str, Any]:
    """Regenerate the news home page so a pin or an edit becomes visible."""
    script = BLOG_DIR / "rebuild_news_home.py"
    if not script.is_file():
        return {"ran": False, "reason": f"{script} not found"}
    try:
        proc = subprocess.run([sys.executable, str(script)], cwd=str(BLOG_DIR),
                              capture_output=True, text=True, timeout=timeout)
        return {"ran": True, "returncode": proc.returncode,
                "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:]}
    except subprocess.TimeoutExpired:
        return {"ran": False, "reason": f"timed out after {timeout}s"}
    except Exception as exc:
        return {"ran": False, "reason": str(exc)}


def site_refresh(rebuild: bool = True) -> Dict[str, Any]:
    """Rebuild everything that lists articles, the same set the publish path
    runs (publish_article.py): sitemaps, RSS, llms.txt, then the home page.
    Each step is independent; one failure does not stop the others."""
    steps: Dict[str, Any] = {}
    if not rebuild:
        return {"ran": False, "reason": "skipped"}
    try:
        import publish_article as pa
        for name in ("generate_sitemap", "generate_news_sitemap",
                     "generate_rss_feed", "generate_llms_txt"):
            try:
                getattr(pa, name)()
                steps[name] = "ok"
            except Exception as exc:
                steps[name] = f"failed: {exc}"
    except Exception as exc:
        steps["publish_article"] = f"import failed: {exc}"
    steps["home"] = rebuild_home()
    return steps


REFRESH_FLAG = pin_store.STATE_DIR / "refresh.pending"
REFRESH_LOCK = pin_store.STATE_DIR / "refresh.lock"


def drain_refresh() -> bool:
    """Run site_refresh() while a refresh is pending.  Only one process at a
    time does it; changes that arrive meanwhile share the next pass.
    Returns False when another process already holds the refresh lock."""
    import fcntl
    REFRESH_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(REFRESH_LOCK, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        try:
            while REFRESH_FLAG.exists():
                REFRESH_FLAG.unlink()
                result = site_refresh(True)
                app.logger.info("background site refresh: %s", result)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    return True


def queue_refresh(wanted: Any = True) -> Dict[str, Any]:
    """Ask for a site refresh (home page, sitemaps, RSS, llms.txt) without
    waiting for it.  It takes about a minute; the reply returns at once.
    The every-minute timer (pin_sweeper.py) finishes any refresh left pending."""
    if not wanted or str(wanted).lower() in ("0", "false"):
        return {"queued": False, "reason": "skipped"}
    REFRESH_FLAG.parent.mkdir(parents=True, exist_ok=True)
    REFRESH_FLAG.touch()
    threading.Thread(target=drain_refresh, name="site-refresh", daemon=True).start()
    return {"queued": True, "note": "the site updates in about a minute"}


def search_index(post: Dict[str, Any], remove: bool) -> str:
    """Add or remove one entry in search_index.json via publish_article."""
    try:
        import publish_article as pa
        if remove:
            pa.delete_search_index_entry(NEWS_ROOT, post.get("url", ""))
        else:
            pa.upsert_search_index_entry(NEWS_ROOT, post)
        return "ok"
    except Exception as exc:
        app.logger.warning("search index update skipped: %s", exc)
        return f"skipped: {exc}"


def prune_empty_dirs(start: Path) -> None:
    """Remove empty date folders left by a move, never above NEWS_ROOT/articles."""
    stop = (NEWS_ROOT / "articles").resolve()
    folder = start.resolve()
    while folder != stop and stop in folder.parents:
        try:
            folder.rmdir()           # only succeeds when empty
        except OSError:
            return
        folder = folder.parent


def unpublished_guard(slug: str):
    """409 for write actions that only make sense on a live article."""
    if slug in article_index.load_unpublished():
        return fail("unpublished", f"{slug!r} is unpublished", 409,
                    hint=f"POST /api/articles/{slug}/publish first")
    return None


def sweep() -> Dict[str, Any]:
    """Retire expired pins before any read, so the view is never stale."""
    if not any(pin_store.is_expired(p) for p in pin_store.all_pins()):
        return {"expired": [], "republished": []}
    with article_index.posts_lock():
        posts = article_index.load_posts()
        changed: List[Dict[str, Any]] = []
        result = pin_store.sweep_expired(posts, on_republish=changed.append)
        if changed:
            article_index.save_posts(posts)
    for post in changed:
        sync_redis(post)
    for pin in result["expired"]:
        audit("pin_expired", pin.get("slug", ""), "sweeper",
              republished=pin.get("slug") in result["republished"])
    return result


def attach_schedules(rows: List[Dict[str, Any]]) -> None:
    """Add each article's pending schedules (soonest first) as row["schedules"]."""
    pending = schedule_store.by_slug()
    for row in rows:
        row["schedules"] = pending.get(row.get("slug"), [])


def links_for(row: Dict[str, Any]) -> Dict[str, str]:
    slug = row.get("slug") or ""
    return {
        "self": f"/api/articles/{slug}",
        "html": f"/api/articles/{slug}?include=html",
        "pin": f"/api/pins/{slug}",
        "public_url": row.get("url") or "",
    }


def with_links(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for row in rows:
        row["_links"] = links_for(row)
    return rows


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    sweep()
    return render_template("pub_dashboard.html", version=VERSION,
                           site_base=site_base())


# --------------------------------------------------------------------------- #
# articles — read
# --------------------------------------------------------------------------- #
@app.route("/api/articles", methods=["GET"])
def api_articles():
    sweep()
    args = request.args
    try:
        limit = min(MAX_LIMIT, max(1, int(args.get("limit", DEFAULT_LIMIT))))
        offset = max(0, int(args.get("offset", 0)))
    except ValueError:
        return fail("bad_pagination", "limit and offset must be whole numbers",
                    hint="try ?limit=50&offset=0")

    rows = article_index.build_index(refresh=args.get("refresh") == "1")
    attach_schedules(rows)
    everything = article_index.facets(rows)
    rows = article_index.filter_rows(rows, args)
    wanted = (args.get("scheduled") or "any").lower()
    if wanted in ("true", "false"):
        rows = [r for r in rows if bool(r["schedules"]) == (wanted == "true")]
    rows = article_index.sort_rows(rows, args.get("sort", "published_date"),
                                   args.get("order", "desc"))
    total = len(rows)
    fields = [f.strip() for f in args.get("fields", "").split(",") if f.strip()]
    page = [dict(r) for r in rows[offset:offset + limit]]
    if fields:
        page = [trim_fields(r, fields) for r in page]
    else:
        page = with_links(page)

    return ok(page, total=total, limit=limit, offset=offset,
              returned=len(page), facets=everything,
              filters_applied={k: args.get(k) for k in article_index.FILTER_KEYS
                               if args.get(k)},
              next_offset=(offset + limit) if offset + limit < total else None,
              sortable_by=sorted(article_index.SORT_KEYS),
              docs="/llms.txt")


def _shown_path(resource_id: str, symbol: str, date: str, days: str, years: str) -> str:
    """Article path the portfolio shows for a pattern (its Redis entry), or ''."""
    try:
        import publish_article as pa
        key = pa.make_redis_key(resource_id, symbol, date, int(float(days)), years)
        raw = pa.redis_client3.get(key)
        return (json.loads(raw or "{}").get("entry") or {}).get("path") or ""
    except Exception:
        return ""


@app.route("/api/articles/by-pattern", methods=["GET"])
def api_article_by_pattern():
    """Which article a portfolio pattern means (used by the TradeWave
    portfolio's publish icon through blog_queue).

    ?resource_id=&symbol=&date=&days=&years=   One pattern can have several
    articles; this picks the one the portfolio shows (its Redis entry), else
    the newest live one, else the newest unpublished one.  Same rule as
    publish_article.delete_article_web.
    """
    a = request.args
    try:
        want = (str(a["symbol"]).upper(), str(a["date"])[:10],
                str(int(float(a["days"]))), str(a["years"]))
    except (KeyError, ValueError):
        return fail("missing_fields", "need symbol, date, days, years (and resource_id)",
                    hint="?resource_id=2&symbol=MRK&date=2026-08-21&days=90&years=20")

    def same(post):
        try:
            days = str(int(float(post.get("pattern_days") or 0)))
        except (TypeError, ValueError):
            days = ""
        return (str(post.get("symbol", "")).upper(), str(post.get("pattern_start_date", ""))[:10],
                days, str(post.get("lookback_years", ""))) == want

    live = [p for p in article_index.load_posts() if same(p)]
    held = [r["post"] for r in article_index.load_unpublished().values() if same(r["post"])]
    newest = lambda posts: max(posts, key=lambda p: p.get("published_date") or "")
    chosen, published = None, False
    if live:
        shown = _shown_path(str(a.get("resource_id", "")), want[0], want[1], want[2], want[3])
        chosen = next((p for p in live if shown and p.get("path") == shown), None) or newest(live)
        published = True
    elif held:
        chosen = newest(held)
    if chosen is None:
        return ok({"state": "none", "slug": None, "schedules": [], "candidates": 0})
    slug = pin_store.article_slug(chosen)
    return ok({"state": "published" if published else "unpublished", "slug": slug,
               "title": chosen.get("title"), "url": chosen.get("url"),
               "schedules": schedule_store.pending(slug=slug),
               "candidates": len(live) + len(held)})


@app.route("/api/articles/<slug>", methods=["GET"])
def api_article(slug):
    sweep()
    rows = article_index.build_index()
    row = next((r for r in rows if r.get("slug") == slug), None)
    if row is None:
        return fail("not_found", f"no article with slug {slug!r}", 404,
                    hint="list slugs with GET /api/articles", field="slug")
    row = dict(row)
    attach_schedules([row])
    include = {i.strip() for i in request.args.get("include", "").split(",")}
    if include & {"html", "text"}:
        path = Path(row.get("path") or "")
        raw = path.read_text("utf-8", errors="replace") if path.is_file() else None
        if "html" in include:
            row["html"] = raw
        if "text" in include:
            row["text"] = article_index.plain_text(raw) if raw else None
    row["_links"] = links_for(row)
    row["_links"]["text"] = f"/api/articles/{slug}?include=text"
    return ok(row)


# --------------------------------------------------------------------------- #
# articles — write
# --------------------------------------------------------------------------- #
REQUIRED_NEW = ("symbol", "title", "html")

WRITABLE_FIELDS = (
    "title", "dek", "seo_title", "meta_description", "symbol", "tickers",
    "market_family", "direction", "pattern_start_date", "pattern_days",
    "lookback_years", "author_id", "resource_id", "tags", "hero_image",
    "hero_alt", "publish_status", "published_date", "edition_id",
)


def _article_target(symbol: str, family: str, published: str, slug: str) -> Path:
    day = (parse_dt(published) or utcnow()).astimezone(timezone.utc)
    return (NEWS_ROOT / "articles" / (family or "US").upper()
            / day.strftime("%Y") / day.strftime("%m") / day.strftime("%d")
            / f"{slug}.html")


@app.route("/api/articles", methods=["POST"])
def api_create_article():
    data = body_json()
    missing = [f for f in REQUIRED_NEW if not data.get(f)]
    if missing:
        return fail("missing_fields",
                    "missing required field(s): " + ", ".join(missing),
                    hint="required: symbol, title, html. See GET /api/schema",
                    field=missing[0])

    who = actor(data)
    slug = slugify(data.get("slug") or data.get("title"))
    # Default: publish now.  "publish": false holds it unpublished;
    # "publish_at": <time> holds it and schedules the publish.
    publish_when = None
    if data.get("publish_at"):
        try:
            publish_when = schedule_store.parse_when(data["publish_at"], data.get("tz"))
        except ValueError as exc:
            return fail("bad_time", str(exc), field="publish_at")
        if publish_when <= utcnow():
            return fail("time_in_past", "publish_at must be in the future", field="publish_at")
    hold = publish_when is not None or str(data.get("publish", "true")).lower() == "false"
    with article_index.posts_lock():
        posts = article_index.load_posts()
        if any(pin_store.article_slug(p) == slug for p in posts):
            return fail("slug_exists", f"slug {slug!r} is already used", 409,
                        hint="send a different 'slug', or PATCH the existing article",
                        field="slug")
        post = _create_post(data, slug, who)
        posts.append(post)
        article_index.save_posts(posts)
    audit("create", slug, who, title=post["title"])
    result: Dict[str, Any] = {"article": post, "published": not hold}
    if hold:
        do_unpublish(slug, who, "created unpublished")
        if publish_when is not None:
            result["scheduled"] = schedule_store.add(slug, "publish", publish_when, who,
                                                     note="publish_at on create")
            audit("schedule", slug, who, scheduled_action="publish", at=iso(publish_when))
        result["rebuild"] = {"ran": False, "reason": "not live yet"}
    else:
        sync_redis(post)
        search_index(post, remove=False)
        result["rebuild"] = queue_refresh(data.get("rebuild", True))
    return ok(result), 201


def _create_post(data: Dict[str, Any], slug: str, who: str) -> Dict[str, Any]:
    """Write the HTML file and return the new posts.json entry."""
    now = iso(utcnow())
    published = data.get("published_date") or now
    family = str(data.get("market_family") or "US").upper()
    target = _article_target(str(data["symbol"]), family, published, slug)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(data["html"]), encoding="utf-8")

    relative = target.relative_to(NEWS_ROOT).as_posix()
    post: Dict[str, Any] = {
        "resource_id": str(data.get("resource_id") or "0"),
        "symbol": str(data["symbol"]).upper(),
        "tickers": data.get("tickers") or [str(data["symbol"]).upper()],
        "market_family": family,
        "pattern_start_date": data.get("pattern_start_date") or "",
        "pattern_days": data.get("pattern_days") or "",
        "author_id": str(data.get("author_id") or "16"),
        "direction": data.get("direction") or "",
        "title": data["title"],
        "dek": data.get("dek") or "",
        "slug": slug,
        "url": f"{site_base()}/{relative}",
        "path": str(target),
        "lookback_years": str(data.get("lookback_years") or ""),
        "published_date": published,
        "updated_date": now,
        "tags": data.get("tags") or ["manual"],
        "hero_image": data.get("hero_image") or "",
        "hero_alt": data.get("hero_alt") or "",
        "seo_title": data.get("seo_title") or data["title"],
        "meta_description": data.get("meta_description") or data.get("dek") or "",
        "publish_status": str(data.get("publish_status", "true")).lower(),
        "created_by": who,
    }
    return post


@app.route("/api/articles/<slug>", methods=["PUT", "PATCH"])
def api_update_article(slug):
    data = body_json()
    who = actor(data)
    blocked = unpublished_guard(slug)
    if blocked:
        return blocked
    with article_index.posts_lock():
        posts = article_index.load_posts()
        index = next((i for i, p in enumerate(posts)
                      if pin_store.article_slug(p) == slug), None)
        if index is None:
            return fail("not_found", f"no article with slug {slug!r}", 404,
                        hint="list slugs with GET /api/articles", field="slug")

        post = posts[index]
        old_key_post = dict(post)
        if "html" in data and data["html"] is not None:
            path = Path(post.get("path") or "")
            if not path.is_file():
                return fail("html_missing", f"article file not found: {path}", 409,
                            hint="DELETE this article and POST it again")
            path.write_text(str(data["html"]), encoding="utf-8")

        changed = [f for f in WRITABLE_FIELDS if f in data and post.get(f) != data[f]]
        for field in changed:
            post[field] = data[field]
        post["updated_date"] = iso(utcnow())
        posts[index] = post
        article_index.save_posts(posts)

    # The Redis key is built from symbol/pattern fields; drop the old key if
    # one of them changed, so no stale duplicate is left behind.
    key_fields = ("resource_id", "symbol", "pattern_start_date", "pattern_days",
                  "lookback_years")
    if any(old_key_post.get(k) != post.get(k) for k in key_fields):
        sync_redis(old_key_post, delete=True)
    sync_redis(post)
    audit("update", slug, who, fields=changed, html="html" in data)
    return ok({"article": post, "rebuild": queue_refresh(data.get("rebuild", True))})


@app.route("/api/articles/<slug>", methods=["DELETE"])
def api_delete_article(slug):
    who = actor()
    was_live = True
    with article_index.posts_lock():
        posts = article_index.load_posts()
        index = next((i for i, p in enumerate(posts)
                      if pin_store.article_slug(p) == slug), None)
        if index is not None:
            post = posts.pop(index)
            path = Path(post.get("path") or "")
            article_index.save_posts(posts)
        else:
            held = article_index.load_unpublished()
            record = held.pop(slug, None)
            if record is None:
                return fail("not_found", f"no article with slug {slug!r}", 404,
                            hint="list slugs with GET /api/articles", field="slug")
            post, path, was_live = record["post"], Path(record.get("held_path") or ""), False
            article_index.save_unpublished(held)

    # Files go to a trash folder, never rm -rf, so a mistake is recoverable.
    trashed = None
    if path.is_file():
        stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
        destination = TRASH_DIR / stamp / slug
        destination.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(destination / path.name))
        prune_empty_dirs(path.parent)
        (destination / "post.json").write_text(
            json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8")
        trashed = str(destination)

    if was_live:
        sync_redis(post, delete=True)
        search_index(post, remove=True)
    pin_store.remove_pin(slug, reason="article deleted")
    schedule_store.drop_for(slug, "article deleted")
    audit("delete", slug, who, title=post.get("title"), trash=trashed, was_live=was_live)
    refreshed = queue_refresh(was_live and request.args.get("rebuild", "1") == "1")
    return ok({"deleted": slug, "trash": trashed, "restore_hint":
               "copy the file back and re-POST post.json to /api/articles",
               "rebuild": refreshed})


class ActionError(Exception):
    """A refused publish/unpublish, carried to the API as a structured error."""

    def __init__(self, code: str, message: str, status: int = 409, hint: str = ""):
        super().__init__(message)
        self.code, self.message, self.status, self.hint = code, message, status, hint


def do_unpublish(slug: str, who: str, reason: str = "") -> Dict[str, Any]:
    """Take an article off the site but keep it, ready to publish again.

    It leaves posts.json (so the home page, sitemaps, RSS and llms.txt drop
    it), Redis and the search index, and its HTML moves to a private holding
    folder so its URL stops serving.  Images stay where they are.
    The caller runs site_refresh() afterwards (once, for a batch).
    """
    with article_index.posts_lock():
        posts = article_index.load_posts()
        index = next((i for i, p in enumerate(posts)
                      if pin_store.article_slug(p) == slug), None)
        if index is None:
            if slug in article_index.load_unpublished():
                raise ActionError("already_unpublished", f"{slug!r} is already unpublished")
            raise ActionError("not_found", f"no article with slug {slug!r}", 404,
                              "list slugs with GET /api/articles")
        post = posts[index]
        path = Path(post.get("path") or "")
        held_path = article_index.HELD_DIR / slug / (path.name or "article.html")
        if path.is_file():
            held_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(held_path))
            prune_empty_dirs(path.parent)
        held = article_index.load_unpublished()
        held[slug] = {"post": post, "held_path": str(held_path) if held_path.is_file() else "",
                      "unpublished_at": iso(utcnow()), "by": who, "reason": reason}
        article_index.save_unpublished(held)
        posts.pop(index)
        article_index.save_posts(posts)

    sync_redis(post, delete=True)
    search_index(post, remove=True)
    pin_store.remove_pin(slug, reason="unpublished")
    audit("unpublish", slug, who, reason=reason)
    return {"unpublished": slug, "held_at": str(held_path)}


def do_publish(slug: str, who: str, as_new: bool = False) -> Dict[str, Any]:
    """Put an unpublished article back at its original URL.

    as_new=True gives it a fresh published_date (now), so it goes to the top
    and then sinks like a just-published article; otherwise it keeps its date.
    The caller runs site_refresh() afterwards.
    """
    with article_index.posts_lock():
        held = article_index.load_unpublished()
        record = held.get(slug)
        if record is None:
            if any(pin_store.article_slug(p) == slug for p in article_index.load_posts()):
                raise ActionError("already_published", f"{slug!r} is already live")
            raise ActionError("not_found", f"no article with slug {slug!r}", 404,
                              "list slugs with GET /api/articles")
        post = dict(record["post"])
        target = Path(post.get("path") or "")
        source = Path(record.get("held_path") or "")
        if target.exists():
            raise ActionError("url_taken", f"a file already exists at {target}",
                              hint="delete or move that file, then try again")
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            try:
                source.parent.rmdir()          # only succeeds when empty
            except OSError:
                pass
        elif not target.is_file():
            raise ActionError("html_missing", "the held article HTML is gone",
                              hint="DELETE this article and POST it again")
        post["publish_status"] = "true"
        if as_new:
            post["published_date"] = iso(utcnow())
        post["updated_date"] = iso(utcnow())
        posts = article_index.load_posts()
        posts.append(post)
        article_index.save_posts(posts)
        held.pop(slug, None)
        article_index.save_unpublished(held)

    sync_redis(post)
    search_index(post, remove=False)
    audit("publish", slug, who, as_new=bool(as_new))
    return {"published": slug, "url": post.get("url"),
            "published_date": post.get("published_date")}


@app.route("/api/articles/<slug>/unpublish", methods=["POST"])
def api_unpublish(slug):
    """Unpublish now.  Optional "publish_at" also schedules the return."""
    data = body_json()
    who = actor(data)
    if data.get("publish_at"):
        try:
            when = schedule_store.parse_when(data["publish_at"], data.get("tz"))
        except ValueError as exc:
            return fail("bad_time", str(exc), field="publish_at")
    try:
        result = do_unpublish(slug, who, data.get("reason", ""))
    except ActionError as exc:
        return fail(exc.code, exc.message, exc.status, hint=exc.hint)
    if data.get("publish_at"):
        result["scheduled"] = schedule_store.add(slug, "publish", when, who,
                                                 as_new=bool(data.get("as_new")),
                                                 note=data.get("note", ""))
        audit("schedule", slug, who, scheduled_action="publish", at=iso(when))
    result["rebuild"] = queue_refresh(data.get("rebuild", True))
    return ok(result)


@app.route("/api/articles/<slug>/publish", methods=["POST"])
def api_publish(slug):
    """Publish an unpublished article now.  {"as_new": true} for a fresh date."""
    data = body_json()
    try:
        result = do_publish(slug, actor(data), bool(data.get("as_new")))
    except ActionError as exc:
        return fail(exc.code, exc.message, exc.status, hint=exc.hint)
    # A manual publish replaces any pending scheduled publish for it.
    for item in schedule_store.pending(slug=slug, action="publish"):
        schedule_store.finish(item["id"], "cancelled", note="published by hand")
    result["rebuild"] = queue_refresh(data.get("rebuild", True))
    return ok(result)


# --------------------------------------------------------------------------- #
# schedule
# --------------------------------------------------------------------------- #
def _is_live(slug: str) -> bool:
    return any(pin_store.article_slug(p) == slug for p in article_index.load_posts())


@app.route("/api/schedule", methods=["GET"])
def api_schedule_list():
    """Schedules, soonest first.  ?status=pending|done|failed|cancelled|any, ?slug="""
    status = request.args.get("status", "pending")
    items = schedule_store.items(status=None if status == "any" else status,
                                 slug=request.args.get("slug"))
    return ok(items, total=len(items), server_time=iso(utcnow()))


@app.route("/api/schedule", methods=["POST"])
def api_schedule_add():
    """Schedule a publish or an unpublish.

    Body: {"slug", "action": "publish"|"unpublish", "at": ISO time,
           "tz": "America/New_York" (only for times without an offset),
           "as_new": false, "note": ""}.  Needs X-Actor (who scheduled it).
    """
    data = body_json()
    who = actor(data)
    if who == "dashboard":
        return fail("who_required", "say who is scheduling", 400,
                    hint="send header X-Actor: <your name or agent id>")
    slug = str(data.get("slug") or "")
    action = str(data.get("action") or "")
    if action not in ("publish", "unpublish"):
        return fail("bad_action", "action must be 'publish' or 'unpublish'", field="action")
    try:
        when = schedule_store.parse_when(data.get("at"), data.get("tz"))
    except ValueError as exc:
        return fail("bad_time", str(exc), field="at",
                    hint="ISO time, e.g. 2026-09-25T07:00:00-04:00")
    if when <= utcnow():
        return fail("time_in_past", f"{iso(when)} is not in the future", field="at")

    live = _is_live(slug)
    held = slug in article_index.load_unpublished()
    if not live and not held:
        return fail("not_found", f"no article with slug {slug!r}", 404, field="slug")
    # The article must be in the right state by then: now, or after an earlier
    # schedule for the opposite action.
    opposite = "unpublish" if action == "publish" else "publish"
    earlier_flip = any(parse_dt(x["at"]) < when
                       for x in schedule_store.pending(slug=slug, action=opposite))
    if action == "publish" and live and not earlier_flip:
        return fail("already_published", f"{slug!r} is live; nothing to publish",
                    hint="schedule an unpublish first, or unpublish it now")
    if action == "unpublish" and held and not earlier_flip:
        return fail("already_unpublished", f"{slug!r} is not live",
                    hint="schedule a publish first")

    item = schedule_store.add(slug, action, when, who, as_new=bool(data.get("as_new")),
                              note=data.get("note", ""))
    audit("schedule", slug, who, scheduled_action=action, at=item["at"])
    return ok(item), 201


@app.route("/api/schedule/<item_id>", methods=["DELETE"])
def api_schedule_cancel(item_id):
    item = schedule_store.get(item_id)
    if item is None or item["status"] != "pending":
        return fail("not_pending", f"no pending schedule {item_id!r}", 404,
                    hint="GET /api/schedule lists pending ones")
    who = actor()
    schedule_store.finish(item_id, "cancelled", note=f"cancelled by {who}")
    audit("schedule_cancel", item["slug"], who, scheduled_action=item["action"], at=item["at"])
    return ok({"cancelled": item_id})


def run_due_schedules(now=None) -> Dict[str, Any]:
    """Run every pending schedule whose time has come, oldest first, then
    refresh the site once.  Used by the timer (pin_sweeper.py)."""
    ran: List[Dict[str, Any]] = []
    for item in schedule_store.due(now):
        who = f"{item['created_by']} (scheduled)"
        try:
            if item["action"] == "publish":
                do_publish(item["slug"], who, bool(item.get("as_new")))
            else:
                do_unpublish(item["slug"], who, item.get("note") or "scheduled")
            schedule_store.finish(item["id"], "done")
            ran.append({"id": item["id"], "slug": item["slug"],
                        "action": item["action"], "status": "done"})
        except ActionError as exc:
            # Already in the wanted state is not a failure worth alarming on.
            status = "skipped" if exc.code in ("already_published",
                                               "already_unpublished") else "failed"
            schedule_store.finish(item["id"], status, note=exc.message)
            audit("schedule_" + status, item["slug"], who, error=exc.message)
            ran.append({"id": item["id"], "slug": item["slug"],
                        "action": item["action"], "status": status,
                        "error": exc.message})
        except Exception as exc:  # pragma: no cover
            schedule_store.finish(item["id"], "failed", note=str(exc))
            audit("schedule_failed", item["slug"], who, error=str(exc))
            ran.append({"id": item["id"], "slug": item["slug"],
                        "action": item["action"], "status": "failed", "error": str(exc)})
    refreshed = site_refresh(True) if any(r["status"] == "done" for r in ran) else None
    return {"ran": ran, "refresh": refreshed}


# --------------------------------------------------------------------------- #
# pins
# --------------------------------------------------------------------------- #
@app.route("/api/pins", methods=["GET"])
def api_pins():
    sweep()
    state = pin_store.load_state()
    active = pin_store.active_pins()
    return ok({"active": active, "history": state.get("history", [])[-50:]},
              active_count=len(active))


@app.route("/api/pins/<slug>", methods=["PUT", "POST"])
def api_set_pin(slug):
    data = body_json()
    blocked = unpublished_guard(slug)
    if blocked:
        return blocked
    posts = article_index.load_posts()
    if not any(pin_store.article_slug(p) == slug for p in posts):
        return fail("not_found", f"no article with slug {slug!r}", 404,
                    hint="list slugs with GET /api/articles", field="slug")
    if data.get("until") and data.get("days"):
        return fail("conflicting_expiry", "send either 'until' or 'days', not both",
                    hint="'until': 2026-10-31   or   'days': 7", field="until")
    try:
        pin = pin_store.set_pin(
            slug=slug,
            position=data.get("position", 1),
            until=data.get("until"),
            days=data.get("days"),
            on_expiry=data.get("on_expiry", "natural"),
            note=data.get("note", ""),
            created_by=actor(data),
        )
    except ValueError as exc:
        return fail("bad_pin", str(exc), hint="see GET /api/schema for pin fields")
    audit("pin", slug, pin["created_by"], position=pin["position"],
          expires_at=pin["expires_at"], on_expiry=pin["on_expiry"])

    return ok({"pin": pin, "rebuild": queue_refresh(data.get("rebuild", True))})


@app.route("/api/pins/<slug>", methods=["DELETE"])
def api_remove_pin(slug):
    removed = pin_store.remove_pin(slug, reason="unpinned")
    if removed is None:
        return fail("not_pinned", f"{slug!r} is not pinned", 404,
                    hint="GET /api/pins lists the active pins", field="slug")
    audit("unpin", slug, actor())
    return ok({"unpinned": slug, "was": removed,
               "rebuild": queue_refresh(request.args.get("rebuild", "1") == "1")})


USE_HOME_PIPELINE = True   # tests switch this off


def home_order() -> Tuple[List[Dict[str, Any]], str]:
    """The exact home-page list, built with rebuild_news_home's own steps
    (load, interleave by family, display filter, pins)."""
    if USE_HOME_PIPELINE:
        try:
            import rebuild_news_home as home
            items = home._load_articles_from_json(limit=99)
            if not items:
                items = home._load_articles_from_redis(limit=99)
            items = home._interleave_by_family(items)
            items = home._filter_articles_for_display(items)
            return home._apply_manual_pins(items), "rebuild_news_home"
        except Exception as exc:
            app.logger.warning("home pipeline unavailable: %s", exc)
            source = f"approximate (home pipeline failed: {exc})"
    else:
        source = "approximate"
    return pin_store.apply_pins(article_index.build_index()), source


# Where each list position lands on the page, per home template.
# Mirrors _build_wire_template in rebuild_news_home.py.
WIRE_SLOTS = [(1, 1, "Lead story"), (2, 6, "Headlines"), (7, 8, "Large card"),
              (9, 11, "Small card"), (12, 10**6, "List")]


def page_slot(position: int) -> Optional[str]:
    try:
        import rebuild_news_home as home
        if getattr(home, "TEMPLATE", "") != "wire":
            return None
    except Exception:
        return None
    return next((name for lo, hi, name in WIRE_SLOTS if lo <= position <= hi), None)


def _order_row(position: int, row: Dict[str, Any]) -> Dict[str, Any]:
    pin = row.get("_pin") or row.get("pin")
    return {"position": position, "slot": page_slot(position),
            "slug": pin_store.article_slug(row),
            "title": row.get("title"), "symbol": row.get("symbol"),
            "market_family": row.get("market_family"),
            "published_date": row.get("published_date"),
            "hero_image": row.get("hero_image"),
            "pinned": bool(pin), "held": bool(pin) and not pin.get("expires_at"),
            "pin": pin}


@app.route("/api/order", methods=["GET"])
def api_order():
    """The home-page order right now.  Position 1 is the headline."""
    sweep()
    items, source = home_order()
    limit = min(MAX_LIMIT, max(1, int(request.args.get("limit", 50))))
    return ok([_order_row(i + 1, r) for i, r in enumerate(items[:limit])],
              total_on_home=len(items), source=source,
              note="position 1 is the headline article")


def _hold(slug: str, position: int, who: str, note: str) -> Dict[str, Any]:
    """Hold an article at a position.  Keeps the end date of an existing pin."""
    current = pin_store.pins_by_slug().get(slug) or {}
    return pin_store.set_pin(
        slug=slug, position=position,
        until=current.get("expires_at"),
        on_expiry=current.get("on_expiry", "natural"),
        note=note or current.get("note") or "moved in the order view",
        created_by=who)


@app.route("/api/order/move", methods=["POST"])
def api_order_move():
    """Move one article to a place in the home-page list and hold it there.

    Body: {"slug": "...", "position": 3}.  Release it with DELETE /api/pins/<slug>.
    """
    data = body_json()
    who = actor(data)
    slug = str(data.get("slug") or "")
    if not any(pin_store.article_slug(p) == slug for p in article_index.load_posts()):
        blocked = unpublished_guard(slug)
        return blocked or fail("not_found", f"no live article with slug {slug!r}", 404,
                               hint="GET /api/order lists the home page", field="slug")
    try:
        pin = _hold(slug, data.get("position"), who, data.get("note", ""))
    except ValueError as exc:
        return fail("bad_position", str(exc), field="position")
    audit("move", slug, who, position=pin["position"])
    return ok({"moved": slug, "pin": pin,
               "rebuild": queue_refresh(data.get("rebuild", True))})


@app.route("/api/order", methods=["PUT"])
def api_order_set():
    """Set the top of the home page in one call.

    Body: {"slugs": ["first", "second", ...]} holds each at 1, 2, ... .
    Articles not listed keep their normal place below.
    """
    data = body_json()
    who = actor(data)
    slugs = [str(x) for x in (data.get("slugs") or [])]
    if not slugs:
        return fail("missing_fields", "send {'slugs': [...]}", field="slugs")
    live = {pin_store.article_slug(p) for p in article_index.load_posts()}
    unknown = [x for x in slugs if x not in live]
    if unknown or len(set(slugs)) != len(slugs):
        return fail("bad_slugs", "unknown, unpublished or repeated slugs: "
                    + ", ".join(unknown or ["(repeated)"]), field="slugs")
    pins = [_hold(x, i + 1, who, data.get("note", "")) for i, x in enumerate(slugs)]
    audit("order_set", ",".join(slugs), who, count=len(slugs))
    return ok({"held": [{"slug": p["slug"], "position": p["position"]} for p in pins],
               "rebuild": queue_refresh(data.get("rebuild", True))})


@app.route("/api/articles/<slug>/hero/recreate", methods=["POST"])
def api_recreate_hero(slug):
    """Recreate the hero image with the existing blog_queue "hero" mode.

    Same call the portfolio news "Rec Hero" button makes; no new image code.
    It runs synchronously and can take a minute or two.
    """
    import requests
    from urllib.parse import quote

    blocked = unpublished_guard(slug)
    if blocked:
        return blocked
    post = next((p for p in article_index.load_posts()
                 if pin_store.article_slug(p) == slug), None)
    if post is None:
        return fail("not_found", f"no article with slug {slug!r}", 404,
                    hint="list slugs with GET /api/articles", field="slug")
    if "/editions/" in (post.get("hero_image") or ""):
        return fail("edition_hero", "this is a subscription-edition article; its hero "
                    "is made by the edition workflow, not by Rec Hero", 409,
                    hint="regenerate it through the edition workflow")
    parts = [post.get("resource_id"), post.get("symbol"),
             str(post.get("pattern_start_date") or "")[:10],
             post.get("pattern_days"), post.get("lookback_years")]
    if not all(str(x or "").strip() for x in parts):
        return fail("missing_pattern_fields",
                    "resource_id, symbol, pattern_start_date, pattern_days and "
                    "lookback_years are all needed to recreate the hero", 409,
                    hint="PATCH the missing fields first")
    who = actor(body_json())
    path = "/".join(quote(str(x), safe="") for x in parts + ["0", "hero", "dashboard"])
    url = f"{BLOG_QUEUE_URL}/article_prompt/{path}"
    try:
        params = {"direction": post.get("direction") or "long"}
        # Send the exact id the article's hero file uses, so the right file is
        # replaced even when one pattern has several articles.
        match = re.search(r"/hero_[^/]*_([0-9a-f]{8})\.jpg$", post.get("hero_image") or "")
        params["article_id"] = match.group(1) if match else "none"
        reply = requests.get(url, params=params, timeout=600)
        data = reply.json()
    except Exception as exc:
        audit("hero_recreate_failed", slug, who, error=str(exc))
        return fail("blog_queue_unreachable", str(exc), 502,
                    hint=f"is blog_queue running at {BLOG_QUEUE_URL}?")
    if reply.status_code != 200 or data.get("message") != "success":
        audit("hero_recreate_failed", slug, who, error=data.get("reason"))
        return fail("hero_failed", data.get("reason") or "hero recreation failed", 502)
    audit("hero_recreate", slug, who)
    return ok({"slug": slug, "hero_image": post.get("hero_image"),
               "via": "blog_queue article_prompt mode=hero"})


@app.route("/api/audit", methods=["GET"])
def api_audit():
    """Recent changes, newest first.  Filter with ?slug= or ?actor=."""
    limit = min(MAX_LIMIT, max(1, int(request.args.get("limit", 100))))
    want_slug, want_actor = request.args.get("slug"), request.args.get("actor")
    lines: List[Dict[str, Any]] = []
    if AUDIT_LOG.exists():
        for raw in AUDIT_LOG.read_text("utf-8").splitlines():
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if want_slug and entry.get("slug") != want_slug:
                continue
            if want_actor and entry.get("actor") != want_actor:
                continue
            lines.append(entry)
    lines.reverse()
    return ok(lines[:limit], total=len(lines))


@app.route("/api/rebuild", methods=["POST"])
def api_rebuild():
    sweep()
    return ok(rebuild_home())


# --------------------------------------------------------------------------- #
# agent affordances
# --------------------------------------------------------------------------- #
@app.route("/api/health", methods=["GET"])
def api_health():
    return ok({"status": "ok", "posts_json": str(POSTS_JSON),
               "posts_json_exists": POSTS_JSON.exists(),
               "articles": len(article_index.load_posts()),
               "active_pins": len(pin_store.active_pins()),
               "site_refresh_pending": REFRESH_FLAG.exists(),
               "state_dir": str(pin_store.STATE_DIR)})


FIELD_DOCS = {
    "slug": "Stable unique id of the article. Use it in every URL.",
    "title": "Headline shown on the site.",
    "dek": "One-sentence standfirst under the title.",
    "symbol": "Primary ticker, e.g. QQQ.",
    "tickers": "All tickers covered by the article.",
    "market_family": "US, ETF, COMM or INDX.",
    "direction": "long or short.",
    "written_date": "When the article file was first created. Derived, read-only.",
    "published_date": "Publication instant. Drives the default home-page order.",
    "pattern_start_date": "First day of the seasonal window.",
    "pattern_days": "Length of the seasonal window in calendar days.",
    "pattern_end_date": "pattern_start_date + pattern_days. Derived, read-only.",
    "word_count": "Visible words in the article HTML. Derived, read-only.",
    "reading_minutes": "word_count / 225, rounded up. Derived, read-only.",
    "publish_status": "'true' when live on the site.",
    "pinned": "True while a pin holds this article in a fixed slot.",
    "published": "True when live on the site (listed in posts.json). "
                 "False when unpublished and held for re-publishing.",
    "unpublished_at": "When it was unpublished (unpublished rows only).",
    "schedules": "Pending scheduled publish/unpublish for this article, soonest "
                 "first. Each has id, action, at (UTC), as_new, created_by.",
    "pin": "The active pin record, or null.",
    "url": "Public URL.",
    "hero_image": "Public URL of the hero image.",
    "hero_ok": "True when the hero file exists on disk and is not empty. Derived.",
    "path": "Absolute path of the article HTML on the webserver.",
}


@app.route("/api/schema", methods=["GET"])
def api_schema():
    return ok({
        "article_fields": FIELD_DOCS,
        "read_only_fields": ["slug", "written_date", "pattern_end_date",
                             "word_count", "reading_minutes", "url", "path",
                             "pinned", "pin"],
        "writable_fields": list(WRITABLE_FIELDS),
        "required_to_create": list(REQUIRED_NEW) + ["(plus 'html': the full article HTML)"],
        "filters": {
            "q": "free text over title, dek, symbol, slug",
            "symbol": "comma separated tickers",
            "market_family": "comma separated families",
            "direction": "long, short",
            "tag": "comma separated tags",
            "status": "any | published | unpublished (unpublished = held off the site)",
            "pinned": "any | true | false",
            "hero": "any | ok | missing  (missing = hero file absent or empty)",
            "scheduled": "any | true | false  (has a pending schedule)",
            "written_from / written_to": "YYYY-MM-DD range on written_date",
            "published_from / published_to": "YYYY-MM-DD range on published_date",
            "pattern_start_from / pattern_start_to": "YYYY-MM-DD range",
            "pattern_end_from / pattern_end_to": "YYYY-MM-DD range",
            "min_words / max_words": "word_count range",
            "min_pattern_days / max_pattern_days": "pattern length range",
        },
        "fields": "comma separated keys to return, e.g. fields=slug,title",
        "include": "on GET /api/articles/<slug>: html, text, or html,text",
        "actor": "send header X-Actor: <name> on writes; shows in /api/audit",
        "sort": sorted(article_index.SORT_KEYS),
        "order": ["desc", "asc"],
        "pin_fields": {
            "position": "1 = headline, 2 = second slot, and so on.",
            "until": "YYYY-MM-DD. Pin ends at 00:00 UTC on that day.",
            "days": "Number of days from now. Send 'until' or 'days', not both.",
            "on_expiry": "'natural' = fall back to its own published_date slot. "
                         "'republish' = published_date becomes the expiry instant, "
                         "so it sinks from the top like a fresh article.",
            "note": "Free text, for whoever reads the pin later.",
        },
        "envelope": {"success": {"ok": True, "data": "...", "meta": "..."},
                     "failure": {"ok": False,
                                 "error": {"code": "...", "message": "...",
                                           "hint": "...", "field": "..."}}},
    })


LLMS_TXT = """# SMN Publishing Dashboard — agent guide

Base URL: {base}
Every reply is JSON: {{"ok": true, "data": ..., "meta": ...}} on success,
{{"ok": false, "error": {{"code","message","hint","field"}}}} on failure.
An article is always addressed by its `slug`.
Send the header `X-Actor: <your-agent-name>` on every write so the audit log
shows who changed what.

## Read
GET /api/articles
    Filters: q, symbol, market_family, direction, tag, status, pinned, hero,
             written_from, written_to, published_from, published_to,
             pattern_start_from, pattern_start_to,
             pattern_end_from, pattern_end_to,
             min_words, max_words, min_pattern_days, max_pattern_days
    Paging:  limit (max 500), offset. meta.next_offset is null on the last page.
    Sort:    sort=published_date|written_date|pattern_start_date|
                  pattern_end_date|word_count|symbol|title, order=desc|asc
    Fields:  fields=slug,title,published_date returns only those keys.
             Use it to keep replies small.
    meta.facets lists every real value, so never guess a symbol or a family.
GET /api/articles/<slug>            one article
GET /api/articles/by-pattern?resource_id=&symbol=&date=&days=&years=
                                    which article a portfolio pattern means
GET /api/articles/<slug>?include=html   the same, plus the full article HTML
GET /api/articles/<slug>?include=text   the same, plus the article as plain text
GET /api/audit                      recent changes, newest first
                                    (?slug=, ?actor=, ?limit=)
GET /api/schema                     every field, filter and pin option
GET /api/health                     liveness and counts

## Write
POST /api/articles
    {{"symbol":"AAPL","title":"...","html":"<article>...</article>",
      "dek":"...","market_family":"US","direction":"long",
      "pattern_start_date":"2026-10-01","pattern_days":30,
      "lookback_years":"20","tags":["manual"]}}
    Required: symbol, title, html. The slug is made from the title unless you
    send one. Returns 409 if the slug already exists.
PATCH /api/articles/<slug>          any writable field, and/or "html"
DELETE /api/articles/<slug>         removes it from the site; the file is moved
                                    to a trash folder, never destroyed.
POST /api/articles/<slug>/unpublish take it off the site but keep it
    {{"reason":"optional"}}           (home, sitemaps, RSS, search, its URL).
                                    It stays in GET /api/articles with
                                    published=false; filter with status=unpublished.
POST /api/articles/<slug>/publish   put it back at its original URL.
    {{"as_new":true}}                 optional: fresh published_date, so it goes to
                                    the top and then sinks like a new article.
POST /api/articles/<slug>/hero/recreate
                                    recreate the hero image. Uses the same
                                    blog_queue "hero" mode as the portfolio
                                    news Rec Hero button. Slow: allow 10 min.

## Schedule
New articles publish at once (unchanged default). An unpublished article stays
unpublished until it is published by hand or by a schedule.
POST /api/schedule                  X-Actor header REQUIRED (who scheduled it)
    {{"slug":"...","action":"publish","at":"2026-09-25T07:00:00-04:00",
      "as_new":false,"note":"why"}}
    action: publish (unpublished article goes live) or unpublish (live one
    comes down). "at" is ISO; with no offset it is read in "tz" (default
    America/New_York). A new schedule replaces a pending one for the same
    slug and action. Publish at X and unpublish at Y is allowed.
GET /api/schedule                   pending ones, soonest first
                                    (?status=any|done|failed|skipped|cancelled, ?slug=)
DELETE /api/schedule/<id>           cancel a pending schedule
A timer runs due schedules every minute. Rows in GET /api/articles carry
"schedules"; filter with scheduled=true.
Shortcuts on create/unpublish:
    POST /api/articles {{..., "publish_at":"2026-09-25T07:00"}}  created held,
                                    goes live then.  {{..., "publish":false}} held,
                                    no schedule.
    POST /api/articles/<slug>/unpublish {{"publish_at":"..."}}  down now, back then.

## Home page order
GET /api/order                      the exact home-page list (same code as the
                                    home builder). position 1 = headline.
                                    held=true: fixed in place by a move.
POST /api/order/move                {{"slug":"...","position":3}}
                                    move one article and hold it there.
PUT /api/order                      {{"slugs":["a","b","c"]}}
                                    hold these at positions 1, 2, 3.
DELETE /api/pins/<slug>             release a held or pinned article.

## Pin
PUT /api/pins/<slug>
    {{"position":1,"days":7,"on_expiry":"natural","note":"why"}}
    position 1 is the headline. Use "until":"2026-10-31" instead of "days" for a
    fixed end date. Omit both to pin until removed by hand.
    on_expiry "natural": the article drops back to its own published_date slot.
    on_expiry "republish": its published_date is rewritten to the expiry
    instant, so it leaves the pinned slot and then sinks like a fresh article.
DELETE /api/pins/<slug>             unpin now
GET /api/pins                       active pins plus recent history

## Notes
Any write queues a site refresh (home page, sitemaps, RSS, llms.txt). It runs
in the background and takes about a minute; the reply does not wait for it.
GET /api/health shows site_refresh_pending. POST /api/rebuild waits for a full
home-page rebuild.
A timer retires expired pins every 5 minutes and rebuilds the home page.
Full machine spec: {base}/openapi.json
"""


@app.route("/llms.txt", methods=["GET"])
@app.route("/api/llms.txt", methods=["GET"])
def llms_txt():
    return Response(LLMS_TXT.format(base=request.host_url.rstrip("/")),
                    mimetype="text/plain; charset=utf-8")


@app.route("/openapi.json", methods=["GET"])
def openapi():
    base = request.host_url.rstrip("/")
    filter_params = [
        {"name": name, "in": "query", "required": False,
         "schema": {"type": "string"}, "description": desc}
        for name, desc in (
            ("q", "Free text over title, dek, symbol, slug."),
            ("symbol", "Comma separated tickers."),
            ("market_family", "Comma separated families: US, ETF, COMM, INDX."),
            ("direction", "long or short."),
            ("tag", "Comma separated tags."),
            ("status", "any | published | unpublished."),
            ("pinned", "any | true | false."),
            ("hero", "any | ok | missing."),
            ("scheduled", "any | true | false."),
            ("written_from", "YYYY-MM-DD, earliest written_date."),
            ("written_to", "YYYY-MM-DD, latest written_date."),
            ("published_from", "YYYY-MM-DD, earliest published_date."),
            ("published_to", "YYYY-MM-DD, latest published_date."),
            ("pattern_start_from", "YYYY-MM-DD, earliest pattern start."),
            ("pattern_start_to", "YYYY-MM-DD, latest pattern start."),
            ("pattern_end_from", "YYYY-MM-DD, earliest pattern end."),
            ("pattern_end_to", "YYYY-MM-DD, latest pattern end."),
            ("min_words", "Minimum word_count."),
            ("max_words", "Maximum word_count."),
            ("min_pattern_days", "Minimum pattern length in days."),
            ("max_pattern_days", "Maximum pattern length in days."),
            ("sort", "published_date | written_date | pattern_start_date | "
                     "pattern_end_date | word_count | symbol | title."),
            ("order", "desc | asc."),
            ("fields", "Comma separated keys to return. Keeps replies small."),
            ("limit", "Page size, max 500."),
            ("offset", "Rows to skip."),
        )
    ]
    slug_param = {"name": "slug", "in": "path", "required": True,
                  "schema": {"type": "string"},
                  "description": "Stable article id."}
    json_body = lambda example: {  # noqa: E731
        "required": True,
        "content": {"application/json": {"schema": {"type": "object"},
                                         "example": example}},
    }
    return jsonify({
        "openapi": "3.1.0",
        "info": {"title": "SMN Publishing Dashboard API", "version": VERSION,
                 "description": "List, filter, pin, add, edit and delete SMN "
                                "articles. Plain-language guide at /llms.txt."},
        "servers": [{"url": base}],
        "paths": {
            "/api/articles": {
                "get": {"operationId": "listArticles",
                        "summary": "List and filter articles.",
                        "parameters": filter_params,
                        "responses": {"200": {"description": "Matching articles."}}},
                "post": {"operationId": "createArticle",
                         "summary": "Add a new HTML article.",
                         "requestBody": json_body({
                             "symbol": "AAPL", "title": "Apple enters its window",
                             "html": "<article>…</article>", "dek": "One line.",
                             "market_family": "US", "direction": "long",
                             "pattern_start_date": "2026-10-01",
                             "pattern_days": 30, "lookback_years": "20"}),
                         "responses": {"201": {"description": "Created."},
                                       "409": {"description": "Slug exists."}}},
            },
            "/api/articles/{slug}": {
                "get": {"operationId": "getArticle", "parameters": [
                    slug_param,
                    {"name": "include", "in": "query", "schema": {"type": "string"},
                     "description": "html, text, or html,text."}],
                    "responses": {"200": {"description": "One article."},
                                  "404": {"description": "Unknown slug."}}},
                "patch": {"operationId": "updateArticle", "parameters": [slug_param],
                          "requestBody": json_body({"title": "New headline",
                                                    "html": "<article>…</article>"}),
                          "responses": {"200": {"description": "Updated."}}},
                "delete": {"operationId": "deleteArticle", "parameters": [slug_param],
                           "responses": {"200": {"description": "Deleted to trash."}}},
            },
            "/api/audit": {"get": {"operationId": "listAudit",
                                   "summary": "Recent changes, newest first.",
                                   "responses": {"200": {"description": "Audit."}}}},
            "/api/pins": {"get": {"operationId": "listPins",
                                  "responses": {"200": {"description": "Pins."}}}},
            "/api/pins/{slug}": {
                "put": {"operationId": "pinArticle", "parameters": [slug_param],
                        "requestBody": json_body({"position": 1, "days": 7,
                                                  "on_expiry": "natural",
                                                  "note": "100-year pattern"}),
                        "responses": {"200": {"description": "Pinned."}}},
                "delete": {"operationId": "unpinArticle", "parameters": [slug_param],
                           "responses": {"200": {"description": "Unpinned."}}},
            },
            "/api/order": {
                "get": {"operationId": "getHomeOrder",
                        "summary": "Exact home-page order. Position 1 = headline.",
                        "responses": {"200": {"description": "Order."}}},
                "put": {"operationId": "setHomeOrder",
                        "summary": "Hold the listed slugs at positions 1..n.",
                        "requestBody": json_body({"slugs": ["slug-a", "slug-b"]}),
                        "responses": {"200": {"description": "Held."}}}},
            "/api/schedule": {
                "get": {"operationId": "listSchedules",
                        "summary": "Pending schedules, soonest first.",
                        "responses": {"200": {"description": "Schedules."}}},
                "post": {"operationId": "addSchedule",
                         "summary": "Schedule a publish or unpublish. Needs X-Actor.",
                         "requestBody": json_body({"slug": "slug-a", "action": "publish",
                                                   "at": "2026-09-25T07:00:00-04:00",
                                                   "as_new": False}),
                         "responses": {"201": {"description": "Scheduled."}}}},
            "/api/schedule/{id}": {"delete": {
                "operationId": "cancelSchedule",
                "parameters": [{"name": "id", "in": "path", "required": True,
                                "schema": {"type": "string"}}],
                "responses": {"200": {"description": "Cancelled."}}}},
            "/api/order/move": {"post": {
                "operationId": "moveArticle",
                "summary": "Move one article to a position and hold it there.",
                "requestBody": json_body({"slug": "slug-a", "position": 3}),
                "responses": {"200": {"description": "Moved."}}}},
            "/api/articles/{slug}/unpublish": {"post": {
                "operationId": "unpublishArticle", "parameters": [slug_param],
                "summary": "Take it off the site; keep it for re-publishing.",
                "responses": {"200": {"description": "Unpublished."}}}},
            "/api/articles/{slug}/publish": {"post": {
                "operationId": "publishArticle", "parameters": [slug_param],
                "summary": "Put an unpublished article back at its URL.",
                "requestBody": json_body({"as_new": False}),
                "responses": {"200": {"description": "Published."}}}},
            "/api/articles/{slug}/hero/recreate": {"post": {
                "operationId": "recreateHero", "parameters": [slug_param],
                "summary": "Recreate the hero image via blog_queue hero mode.",
                "responses": {"200": {"description": "Recreated."}}}},
            "/api/rebuild": {"post": {"operationId": "rebuildHome",
                                      "responses": {"200": {"description": "Rebuilt."}}}},
            "/api/schema": {"get": {"operationId": "describeSchema",
                                    "responses": {"200": {"description": "Schema."}}}},
            "/api/health": {"get": {"operationId": "health",
                                    "responses": {"200": {"description": "Healthy."}}}},
        },
    })


@app.errorhandler(404)
def handle_404(_):
    return fail("no_such_route", f"{request.path} is not a route here", 404,
                hint="see /llms.txt or /openapi.json")


@app.errorhandler(500)
def handle_500(exc):  # pragma: no cover
    app.logger.exception("dashboard error")
    return fail("internal_error", str(exc), 500,
                hint="check: journalctl -u pub_dashboard -n 50")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 7172)), debug=True)
