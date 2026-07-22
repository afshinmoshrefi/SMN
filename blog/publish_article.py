import base64
import datetime
import json
import logging
import os
from pathlib import Path
import pprint
import re
import sys
import time
from typing import Any, Dict, List, Optional

import fcntl  # Linux-only (matches your deployment)

# --- Third-party ---
import redis
import requests
from slugify import slugify

# --- Project / internal ---
sys.path.insert(0, '/home/flask')
import config

from article_post_process import article_post_process
from article_tools import compute_article_paths_and_url
from blog_tools import get_company_name
from create_report import get_or_create_tag
from rebuild_news_home import build_home

# Default tone and website-id for now
DEFAULT_TONE = "neutral"  # for future use in article generation so that mutliple flavors of the same article can be written
DEFAULT_WEBSITE_ID = 0
DEFAULT_WEBSITE_URL = config.domain_root

RECENT_TITLES_FILENAME = "recent_titles.json"
RECENT_TITLES_MAX_KEEP = 1000

DEFAULT_PUBLISH_STATUS = 'true' # if true then as soon as an article is created its published otherwise set to false


redis_client3 = redis.Redis(host=config.webserver_ip, port=6379, db=3)  # news article publishing db
logging.basicConfig(filename="debug.log",level=logging.DEBUG,format="%(asctime)s %(message)s")




def _make_search_index_record(p: Dict[str, Any]) -> Dict[str, Any]:
    month = (p.get("published_date", "")[:7]) if p.get("published_date") else (p.get("pattern_start_date", "")[:7])
    return {
        "title": p.get("title", ""),
        "url": p.get("url", ""),
        "symbol": p.get("symbol", ""),
        "market_family": p.get("market_family", ""),
        "dek": p.get("dek", ""),
        "published_date": p.get("published_date", ""),
        "month": month,
        "tags": p.get("tags", []),
        # Optional but useful for identity/debugging
        "slug": p.get("slug", ""),
        "resource_id": p.get("resource_id", ""),
        "pattern_start_date": p.get("pattern_start_date", ""),
        "pattern_days": p.get("pattern_days", ""),
        "lookback_years": p.get("lookback_years", ""),
    }


def upsert_search_index_entry(news_root: Path, post_entry: Dict[str, Any]) -> Path:
    """
    Incrementally upsert ONE record into search_index.json.
    This replaces the expensive full rebuild loop.
    """
    search_index_path = news_root / "search_index.json"
    lock_path = str(search_index_path) + ".lock"

    rec = _make_search_index_record(post_entry)
    if not rec["url"]:
        raise ValueError("search_index upsert requires entry['url'] to be present")

    with open(lock_path, "w", encoding="utf-8", newline="\n") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)

        items = _load_json_list(str(search_index_path))

        # Remove any prior record with same URL (unique identity)
        items = [it for it in items if (it.get("url", "") != rec["url"])]

        # Put newest first
        items.insert(0, rec)

        _write_atomic(search_index_path, json.dumps(items, ensure_ascii=False, indent=2))

        fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)

    return search_index_path


def delete_search_index_entry(news_root: Path, url: str) -> Path:
    """
    Incrementally delete ONE record from search_index.json.
    """
    search_index_path = news_root / "search_index.json"
    lock_path = str(search_index_path) + ".lock"

    if not url:
        return search_index_path

    with open(lock_path, "w", encoding="utf-8", newline="\n") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)

        items = _load_json_list(str(search_index_path))
        new_items = [it for it in items if (it.get("url", "") != url)]

        # Only write if it changed
        if len(new_items) != len(items):
            _write_atomic(search_index_path, json.dumps(new_items, ensure_ascii=False, indent=2))

        fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)

    return search_index_path


def _utc_now_iso() -> str:
    # Example: 2025-12-20T19:12:33Z
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _atomic_write_json(path: str, obj: Any) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _load_json_list(path: str) -> List:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data is None:
        return []
    if not isinstance(data, list):
        raise TypeError(f"Expected list JSON at {path}, got {type(data).__name__}")
    return data


def _coerce_recent_title_entry(post: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create the small record we keep in recent_titles.json.

    Required:
      - title

    Optional (but recommended):
      - published_at
      - symbol
      - slug
      - url
    """
    title = str(post.get("title", "")).strip()
    if not title:
        raise ValueError("Cannot update recent_titles.json: post.title is missing or empty")

    # Best-effort mapping for common field names
    published_at = (
        post.get("published_at")
        or post.get("published_date")
        or post.get("updated_date")
        or post.get("date_published")
        or post.get("date")
        or post.get("published")
        or _utc_now_iso()
    )

    symbol = post.get("symbol") or post.get("ticker") or ""
    slug = post.get("slug") or ""
    url = post.get("url") or post.get("canonical_url") or ""

    return {
        "title": title,
        "published_at": str(published_at),
        "symbol": str(symbol),
        "slug": str(slug),
        "url": str(url),
    }


def update_recent_titles_cache(posts_json_path: str, post: Dict[str, Any], max_keep: int = RECENT_TITLES_MAX_KEEP) -> str:
    """
    Writes/updates recent_titles.json next to posts.json.
    recent_titles.json is a JSON list of strings ONLY.
    Returns the path to recent_titles.json.
    """
    base_dir = os.path.dirname(os.path.abspath(posts_json_path))
    recent_titles_path = os.path.join(base_dir, RECENT_TITLES_FILENAME)

    title = (post.get("title") or "").strip()
    if not title:
        raise ValueError("post['title'] is missing or empty")

    os.makedirs(base_dir, exist_ok=True)

    lock_path = recent_titles_path + ".lock"
    with open(lock_path, "w", encoding="utf-8", newline="\n") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)

        # Load existing list (or create empty)
        if not os.path.exists(recent_titles_path):
            items = []
        else:
            items = _load_json_list(recent_titles_path)  # should hard-crash on invalid JSON

        if not isinstance(items, list):
            raise TypeError(f"{RECENT_TITLES_FILENAME} must be a JSON list, got {type(items)}")

        # Migrate dict-format -> string-format (strict)
        migrated: list[str] = []
        for i, it in enumerate(items):
            if isinstance(it, str):
                t = it.strip()
                if t:
                    migrated.append(t)
                continue

            if isinstance(it, dict):
                t = it.get("title")
                if not isinstance(t, str) or not t.strip():
                    raise TypeError(f"{RECENT_TITLES_FILENAME}[{i}].title must be a non-empty str, got {type(t)}")
                migrated.append(t.strip())
                continue

            raise TypeError(f"{RECENT_TITLES_FILENAME}[{i}] must be str or dict, got {type(it)}")

        items = migrated

        # Avoid duplicates (last 50)
        tail = items[-50:] if len(items) > 50 else items
        if title in tail:
            return recent_titles_path

        items.append(title)

        if len(items) > max_keep:
            items = items[-max_keep:]

        _atomic_write_json(recent_titles_path, items)

        fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)

    return recent_titles_path

def make_redis_key(resource_id, symbol, pattern_start_date, days, years,tone=DEFAULT_TONE, website_id=DEFAULT_WEBSITE_ID):
    """
    Build the redis key using the agreed deterministic format.
    """
    return f"{resource_id}_{symbol.upper()}_{pattern_start_date}_{days}_{years}_{tone}_{website_id}"

def save_article_to_redis(redis_key, entry,
                          tone=DEFAULT_TONE, website_id=DEFAULT_WEBSITE_ID):
    """
    Store the full article payload in Redis as JSON.
    Includes entry metadata + raw html + tone + website_id.
    """
    payload = {
        "entry": entry,
        # "article_html": article_html,
        "tone": tone,
        "website_id": website_id,
        "last_updated": _now_iso_utc()
    }
    print(payload)
    redis_client3.set(redis_key, json.dumps(payload, ensure_ascii=False))

def delete_article_from_redis(redis_key):
    redis_client3.delete(redis_key)

def make_request(url, header, post, retries=3, backoff_factor=20):
    response = None  # avoid UnboundLocalError if the POST itself fails
    for attempt in range(retries):
        try:
            response = requests.post(url, headers=header, json=post, timeout=60)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            logging.error("Error making API request: {}".format(e))
            if response is not None:
                logging.error("Status code: {}".format(response.status_code))
            if attempt == retries - 1:
                raise
            # your helper; keep if you rely on PHP-FPM cooldown
            try:
                wait_for_php()
            except NameError:
                time.sleep(backoff_factor * (2 ** attempt))
#-----------------------------------------------------------------------------------------------

#-----------------------------------------------------------------------------------------------
# make sure html ends with </html> tag
# this is only used when posting a full html file - not posting to wordpress
#-----------------------------------------------------------------------------------------------
def clamp_to_html_end(html_text: str) -> str:
    end_tag = "</html>"
    idx = html_text.rfind(end_tag)
    if idx == -1:
        # no </html> found, just return original (or raise, your choice)
        return html_text
    return html_text[: idx + len(end_tag)]
#-----------------------------------------------------------------------------------------------
# the generated article is a stand alone html page - it needs to be properly filtered for 
# wordpress publishing 
#-----------------------------------------------------------------------------------------------
def prepare_for_wordpress(full_html: str) -> str:
    m = re.search(r"<article\b[\s\S]*?</article>", full_html, flags=re.IGNORECASE)
    if not m:
        return ""
    article_block = m.group(0)

    # remove <style> blocks
    article_block = re.sub(r"<style[\s\S]*?</style>", "", article_block, flags=re.IGNORECASE)

    # remove any stray ``` fences and contentReference junk
    article_block = re.sub(r"```", "", article_block)
    article_block = re.sub(r"::contentReference[\s\S]*$", "", article_block)

    # 🔑 strip <title>…</title> if present
    article_block = re.sub(r"(?is)<title>.*?</title>", "", article_block)

    # 🔑 strip the first <h1> (often duplicates WP title)
    page_title = extract_title_from_html(full_html) or ""
    article_block = re.sub(rf"(?is)<h1[^>]*>\s*{re.escape(page_title)}\s*</h1>", "", article_block, count=1)
    # fallback: if an <h1> remains, drop the first one
    article_block = re.sub(r"(?is)<h1[^>]*>.*?</h1>", "", article_block, count=1)

    # wrap entire article to limit total width to 80%
    article_block = f'''
    <div style="max-width:90%;margin:0 auto;">
      {article_block}
    </div>
    '''

    return article_block.strip()
#-----------------------------------------------------------------------------------------------
# fixes issues of extra <p> placed by wordpress and captions not horizantally centered
#-----------------------------------------------------------------------------------------------
def postprocess_for_wordpress(article_html: str) -> str:
    html = article_html

    # fix metadata header block
    # detect the block that starts with "Name:" and ends before first chart or first <figure>
    # replace it with a table version

    # (you know that block structure better than me, so you're in best position
    # to either wrap it in a <div class="tw-meta-block"> ... </div> at generation time instead.)

    # fix figure captions
    html = re.sub(
        r'<figure[^>]*>([\s\S]*?)<figcaption>([\s\S]*?)</figcaption>\s*</figure>',
        r'<figure style="text-align:center;">\1<figcaption style="font-size:0.8rem;color:#666;text-align:center;margin-top:4px;">\2</figcaption></figure>',
        html,
        flags=re.IGNORECASE
    )

    # add vertical spacing before the Sources section (handles with/without existing style)
    html = re.sub(
        r'(<section\b[^>]*class="[^"]*\bsources\b[^"]*"[^>]*\sstyle=")([^"]*)(")',
        r'\1\2;margin-top:24px;padding-top:12px;border-top:1px solid #eee\3',
        html, flags=re.IGNORECASE
    )
    html = re.sub(
        r'(<section\b[^>]*class="[^"]*\bsources\b[^"]*")(>)',
        r'\1 style="margin-top:24px;padding-top:12px;border-top:1px solid #eee"\2',
        html, flags=re.IGNORECASE
    )

    return html
#-----------------------------------------------------------------------------------------------
# 2 methods of publishing
# 1 to wordpress publish as a post
# 2 save as html in the defined publishing folder 
#-----------------------------------------------------------------------------------------------
# publish_to = 'wordpress' # this variable is either wordpress or html_folder
publish_to = 'html_folder'

def publish_article_web(resource_id, symbol, date, days, years, direction, userid, article_html, hero_image=""):
    if publish_to == 'wordpress':
        return publish_article_wordpress(resource_id, symbol, date, days, years, direction, userid, article_html)
    else:
        return publish_article_to_folder(resource_id, symbol, date, days, years, direction, userid, article_html, hero_image=hero_image)

def load_article_web (resource_id, symbol, date, days, years,userid,article_html):
    if publish_to == 'wordpress':
        pass # haven't worked on wordpress publishing - only folder works
    else:
        return load_article_from_folder(resource_id, symbol, date, days, years,userid)
#-----------------------------------------------------------------------------------------------
def load_article_from_folder(resource_id, symbol, date, days, years, userid):
    """
    Load a published article's HTML (for editing) based on:
      (resource_id, symbol, pattern_start_date, days, years).

    Resolution order:
      1) Redis (canonical): entry["path"] + entry["url"]
      2) posts.json: same identity, then entry["path"] + entry["url"]

    Assumes:
      - entry["url"] is a full browser URL
      - entry["path"] is a full filesystem path
    """

    pattern_start_date = date
    days_int           = int(days)
    years_str          = str(years)
    symbol_upper       = symbol.upper()

    # ---------- 1) Try Redis ----------
    redis_key = make_redis_key(
        resource_id=resource_id,
        symbol=symbol_upper,
        pattern_start_date=pattern_start_date,
        days=days_int,
        years=years_str,
        tone=DEFAULT_TONE,
        website_id=DEFAULT_WEBSITE_ID,
    )

    payload_raw = redis_client3.get(redis_key)
    if payload_raw:
        try:
            payload = json.loads(payload_raw.decode("utf-8"))
            entry   = payload.get("entry") or {}
        except Exception as e:
            logging.error(f"Failed to decode Redis payload for key {redis_key}: {e}")
            entry = {}

        path_str = entry.get("path")
        url      = entry.get("url")

        if path_str and url:
            html_path = Path(path_str)
            if not html_path.exists():
                logging.error(f"HTML missing on disk for redis key {redis_key}: {html_path}")
                return {
                    "found": False,
                    "reason": "html file referenced in redis not found on disk",
                    "html": None,
                    "entry": entry,
                    "file_path": str(html_path),
                    "url": url,
                    "source": "redis",
                }

            try:
                html = html_path.read_text(encoding="utf-8")
                html = clamp_to_html_end(html)
            except Exception as e:
                logging.error(f"Failed to read HTML file {html_path}: {e}")
                return {
                    "found": False,
                    "reason": "failed to read html from disk",
                    "html": None,
                    "entry": entry,
                    "file_path": str(html_path),
                    "url": url,
                    "source": "redis",
                }

            return {
                "found": True,
                "reason": "",
                "html": html,
                "entry": entry,
                "file_path": str(html_path),
                "url": url,
                "source": "redis",
            }

    # ---------- 2) Fallback: posts.json ----------
    news_root  = Path(config.news_root_folder).resolve()
    posts_json = news_root / "posts.json"

    if not posts_json.exists():
        return {
            "found": False,
            "reason": "article not indexed in redis or posts.json",
            "html": None,
            "entry": None,
            "file_path": None,
            "url": None,
            "source": None,
        }

    try:
        posts = json.loads(posts_json.read_text(encoding="utf-8"))
    except Exception as e:
        logging.error(f"Failed to read posts.json: {e}")
        return {
            "found": False,
            "reason": "failed to read posts.json",
            "html": None,
            "entry": None,
            "file_path": None,
            "url": None,
            "source": None,
        }

    match_idx = next(
        (
            i for i, p in enumerate(posts)
            if p.get("symbol", "").upper() == symbol_upper
            and p.get("pattern_start_date", "") == pattern_start_date
            and str(p.get("pattern_days", "")) == str(days_int)
            and str(p.get("lookback_years", "")) == years_str
        ),
        None
    )

    if match_idx is None:
        return {
            "found": False,
            "reason": "article metadata not found in posts.json (and not in redis)",
            "html": None,
            "entry": None,
            "file_path": None,
            "url": None,
            "source": None,
        }

    entry    = posts[match_idx]
    path_str = entry.get("path")
    url      = entry.get("url")

    if not path_str or not url:
        return {
            "found": False,
            "reason": "entry in posts.json missing path or url",
            "html": None,
            "entry": entry,
            "file_path": None,
            "url": url,
            "source": "posts.json",
        }

    html_path = Path(path_str)
    if not html_path.exists():
        logging.error(f"HTML file not found for posts.json entry at {html_path}")
        return {
            "found": False,
            "reason": "html file referenced in posts.json not found on disk",
            "html": None,
            "entry": entry,
            "file_path": str(html_path),
            "url": url,
            "source": "posts.json",
        }

    try:
        html = html_path.read_text(encoding="utf-8")
        html = clamp_to_html_end(html)
    except Exception as e:
        logging.error(f"Failed to read HTML file {html_path}: {e}")
        return {
            "found": False,
            "reason": "failed to read html from disk",
            "html": None,
            "entry": entry,
            "file_path": str(html_path),
            "url": url,
            "source": "posts.json",
        }

    # Heal Redis: article was on disk/posts.json but missing from Redis.
    # Re-indexing here means the next portfolio load will find it in Redis
    # and correctly show the blue icon + delete button.
    try:
        heal_key = make_redis_key(
            resource_id=resource_id,
            symbol=symbol_upper,
            pattern_start_date=pattern_start_date,
            days=days_int,
            years=years_str,
        )
        save_article_to_redis(heal_key, entry)
        logging.info(f"[HEAL] Re-indexed article to Redis from posts.json: {heal_key}")
    except Exception as e:
        logging.warning(f"[HEAL] Failed to re-index article to Redis: {e}")

    return {
        "found": True,
        "reason": "",
        "html": html,
        "entry": entry,
        "file_path": str(html_path),
        "url": url,
        "source": "posts.json",
    }

#-----------------------------------------------------------------------------------------------
# this function creates a html structure for seasonal market news articles and save them 


def _ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def _write_atomic(path: Path, content: str, mode="w", encoding="utf-8"):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open(mode, encoding=encoding) as f:
        f.write(content)
    tmp.replace(path)

def _extract_dek_from_html(html: str) -> str:
    m = re.search(r'<p[^>]*class=["\']dek["\'][^>]*>(.*?)</p>', html, flags=re.I|re.S)
    if not m:
        return _extract_text_excerpt(html, limit=280)
    dek = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return re.sub(r"\s+", " ", dek)[:280]

def _extract_text_excerpt(html: str, limit=220) -> str:
    body = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<header.*?</header>", "", html)
    text = re.sub(r"(?s)<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", text).strip()[:limit]

def _now_iso_utc():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"



def _safe_unlink(p: Path): # used for remove
    try:
        if p.exists():
            p.unlink()
    except Exception as e:
        logging.error(f"Failed to delete file {p}: {e}")


def _delete_article_images(symbol: str, pattern_start_date: str, days: str, years: str):
    img_root = getattr(config, "article_images_root", None)
    if not img_root:
        logging.warning("config.article_images_root not set; skipping image deletion.")
        return
    root = Path(img_root)
    if not root.exists():
        logging.warning(f"article_images_root not found at {root}; skipping image deletion.")
        return

    pat = f"{symbol.upper()}_{pattern_start_date}_{int(days)}_{years}_*.jpg"
    for f in root.rglob(pat):
        _safe_unlink(f)





def write_article_and_register(info, resource_id, symbol, pattern_start_date, days, years, direction, userid, hero_image=""):
    """
    Side-effect helper: given the computed paths/URL (info),
    actually write the HTML to disk, update Redis, posts.json, search_index.json,
    rebuild home, and return the same structure as the old publish_article_to_folder.
    """

    # Rehydrate paths
    news_root  = Path(info["news_root"])
    out_path   = Path(info["out_path"])
    article_html = info["article_html"]
    full_url   = info["full_url"]
    slug       = info["slug"]
    market_family = info["market_family"]

    # Ensure directory exists
    _ensure_dir(out_path.parent)

    # POST-PROCESS: Add all SEO enhancements
    article_html = article_post_process(
        resource_id=resource_id,
        symbol=symbol,
        date=pattern_start_date,
        days=days,
        years=years,
        zero_last_year=True,
        article_html=article_html
    )

    # Write HTML atomically
    _write_atomic(out_path, article_html)
    

    # Build entry metadata
    published_date = _now_iso_utc()
    updated_date   = published_date
    dek            = _extract_dek_from_html(article_html)

    entry = {
        "title": info["title"],
        "slug": slug,
        "url": full_url,                    # full browser URL
        "path": str(out_path),              # full filesystem path
        "resource_id": resource_id,
        "symbol": symbol.upper(),
        "tickers": [symbol.upper()],
        "market_family": market_family,
        "pattern_start_date": pattern_start_date,
        "pattern_days": int(days),
        "lookback_years": years,
        "published_date": published_date,
        "updated_date": updated_date,
        "tags": [],
        "dek": dek,
        "hero_image": hero_image,
        "seo_title": info["title"],
        "meta_description": dek[:160],
        "author_id": userid,
        "direction": direction,
        "publish_status": DEFAULT_PUBLISH_STATUS,
    }

    # 1) Redis write
    redis_key = make_redis_key(
        resource_id=resource_id,
        symbol=symbol,
        pattern_start_date=pattern_start_date,
        days=int(days),
        years=years,
        tone=DEFAULT_TONE,
        website_id=DEFAULT_WEBSITE_ID
    )

    save_article_to_redis(
        redis_key=redis_key,
        entry=entry,
        tone=DEFAULT_TONE,
        website_id=DEFAULT_WEBSITE_ID
    )

    # 2) posts.json upsert
    posts_json = news_root / "posts.json"
    try:
        posts = json.loads(posts_json.read_text(encoding="utf-8")) if posts_json.exists() else []
    except Exception:
        posts = []

    idx = next((i for i, p in enumerate(posts) if p.get("slug") == slug), None)
    if idx is None:
        posts.append(entry)
    else:
        # preserve original published_date if existing
        entry["published_date"] = posts[idx].get("published_date", published_date)
        posts[idx] = entry

    _write_atomic(posts_json, json.dumps(posts, ensure_ascii=False, indent=2))

    # recent_titles.json is managed by article_title.py during SEO title generation

    generate_sitemap()
    generate_news_sitemap()
    generate_rss_feed()
    generate_robots_txt()
    generate_llms_txt()
    notify_indexnow(entry.get("url", ""))

    # 3) search_index.json incremental upsert (fast)
    search_index_path = upsert_search_index_entry(news_root, entry)

    # 4) rebuild home
    build_home()

    return {
        "file_path": str(out_path),
        "url": full_url,
        "posts_json": str(posts_json),
        "search_index_json": str(search_index_path),
    }



def publish_article_to_folder(resource_id, symbol, pattern_start_date, days, years, direction, userid, article_html, hero_image=""):
    # Keep this for now even though it's unused (no behavior change)
    company = get_company_name(resource_id, symbol)

    # Strip stray fences first (same logic as before)
    article_html = re.sub(r'^\s*(```|\'\'\'|""")\s*$', '', article_html, flags=re.MULTILINE)

    news_root = Path(config.news_root_folder).resolve()

    ################################ remove function ##################################
    if isinstance(article_html, str) and article_html.strip().lower() == "remove":
        posts_json  = news_root / "posts.json"
        posts       = json.loads(posts_json.read_text(encoding="utf-8")) if posts_json.exists() else []

        match_idx = next(
            (i for i, p in enumerate(posts)
             if p.get("symbol","").upper() == symbol.upper()
             and p.get("pattern_start_date","") == pattern_start_date
             and str(p.get("pattern_days","")) == str(int(days))
             and str(p.get("lookback_years","")) == years),
            None
        )

        if match_idx is None:
            logging.warning(f"Remove requested but not found: {symbol} {pattern_start_date} {days} {years}")
            return {"removed": False, "reason": "not found", "symbol": symbol, "date": pattern_start_date,
                    "days": int(days), "years": years}

        entry = posts[match_idx]

        path_str = entry.get("path")
        if not path_str:
            logging.error(f"Remove requested but entry has no 'path': {entry}")
            return {"removed": False, "reason": "entry missing path", "symbol": symbol,
                    "date": pattern_start_date, "days": int(days), "years": years}

        html_path = Path(path_str)
        _safe_unlink(html_path)

        redis_key = make_redis_key(
            resource_id=resource_id,
            symbol=symbol,
            pattern_start_date=pattern_start_date,
            days=int(days),
            years=years,
            tone=DEFAULT_TONE,
            website_id=DEFAULT_WEBSITE_ID
        )
        delete_article_from_redis(redis_key)

        del posts[match_idx]
        _write_atomic(posts_json, json.dumps(posts, ensure_ascii=False, indent=2))


        generate_sitemap()  # site map gets recreated after every article
        generate_news_sitemap()
        generate_rss_feed()
        generate_robots_txt()
        generate_llms_txt()

        # Remove from search index (fast)
        delete_search_index_entry(news_root, entry.get("url", ""))

        _delete_article_images(symbol, pattern_start_date, days, years)
        build_home()

        return {"removed": True, "symbol": symbol, "date": pattern_start_date,
                "days": int(days), "years": years}

    ############################ end of remove function ###############################

    # Non-remove path: compute + write + register in two helpers

    # 1) Compute all paths/URLs/slug from the cleaned article_html
    info = compute_article_paths_and_url(
        resource_id=resource_id,
        symbol=symbol,
        pattern_start_date=pattern_start_date,
        days=days,
        years=years,
        article_html=article_html,
    )

    # 2) Do the actual IO + Redis + posts.json + search_index.json
    return write_article_and_register(
        info=info,
        resource_id=resource_id,
        symbol=symbol,
        pattern_start_date=pattern_start_date,
        days=days,
        years=years,
        direction=direction,
        userid=userid,
        hero_image=hero_image,
    )
#-----------------------------------------------------------------------------------------------

def delete_article_web(resource_id,symbol,date,days,years,uid):

    """
    Delete a published article and all associated artifacts for a given pattern.

    Key identity:
        (resource_id, symbol, pattern_start_date=date, pattern_days=days, lookback_years=years)

    This mirrors the old "remove" branch inside publish_article_to_folder, but
    is now reusable from other callers (e.g., delete_article_bq).
    """

    # uid is intentionally NOT part of the primary key; kept only for possible logging
    # logging.info(f"delete_article_web called by user {uid} for {symbol} {date} {days} {years}")

    news_root = Path(config.news_root_folder).resolve()

    posts_json = news_root / "posts.json"
    posts = json.loads(posts_json.read_text(encoding="utf-8")) if posts_json.exists() else []

    # Find matching entry in posts.json
    match_idx = next(
        (
            i for i, p in enumerate(posts)
            if p.get("symbol", "").upper() == symbol.upper()
            and p.get("pattern_start_date", "") == date
            and str(p.get("pattern_days", "")) == str(int(days))
            and str(p.get("lookback_years", "")) == str(years)
        ),
        None,
    )

    if match_idx is None:
        logging.warning(
            f"delete_article_web: not found: resource_id={resource_id} "
            f"symbol={symbol} date={date} days={days} years={years}"
        )
        return {
            "removed": False,
            "reason": "not found",
            "symbol": symbol,
            "date": date,
            "days": int(days),
            "years": years,
        }

    entry = posts[match_idx]

    path_str = entry.get("path")
    if not path_str:
        logging.error(
            f"delete_article_web: entry has no 'path': {entry}"
        )
        return {
            "removed": False,
            "reason": "entry missing path",
            "symbol": symbol,
            "date": date,
            "days": int(days),
            "years": years,
        }

    # Delete HTML file
    html_path = Path(path_str)
    _safe_unlink(html_path)

    # Delete from Redis
    redis_key = make_redis_key(
        resource_id=resource_id,
        symbol=symbol,
        pattern_start_date=date,
        days=int(days),
        years=years,
        tone=DEFAULT_TONE,
        website_id=DEFAULT_WEBSITE_ID,
    )
    delete_article_from_redis(redis_key)

    # Remove entry from posts.json and rewrite it
    del posts[match_idx]
    _write_atomic(posts_json, json.dumps(posts, ensure_ascii=False, indent=2))

    # Remove from search index (fast)
    delete_search_index_entry(news_root, entry.get("url", ""))

    # Delete hero/images and rebuild home page
    _delete_article_images(symbol, date, days, years)
    build_home()

    return {
        "removed": True,
        "symbol": symbol,
        "date": date,
        "days": int(days),
        "years": years,
    }
#-----------------------------------------------------------------------------------------------
def sync_all_articles_to_redis():
    """
    Load every article from posts.json and repopulate Redis.
    posts.json now contains BOTH:
        - url  (full, browser-usable URL)
        - path (absolute filesystem path)
    No guessing. No string operations. Zero conditions.
    """

    news_root  = Path(config.news_root_folder)
    posts_json = news_root / "posts.json"

    if not posts_json.exists():
        print("posts.json not found")
        return

    try:
        posts = json.loads(posts_json.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Failed to read posts.json: {e}")
        return

    count = 0

    for p in posts:
        # Debug only
        # pprint.pprint(p)

        symbol        = p["symbol"]
        resource_id   = p["resource_id"]
        pattern_start = p["pattern_start_date"]
        days          = p["pattern_days"]
        years         = p["lookback_years"]

        # We DO NOT reconstruct paths anymore.
        # We trust p["path"] because we just fixed it in your migration.
        html_path = Path(p["path"])

        # Validate file exists
        if not html_path.exists():
            print(f"HTML missing: {html_path}")
            continue

        # Validate readable
        try:
            _ = html_path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"Unreadable HTML: {html_path} ({e})")
            continue

        # Generate deterministic Redis key
        redis_key = make_redis_key(
            resource_id=resource_id,
            symbol=symbol,
            pattern_start_date=pattern_start,
            days=days,
            years=years,
            tone=DEFAULT_TONE,
            website_id=DEFAULT_WEBSITE_ID
        )

        # Write ONLY metadata (as before)
        save_article_to_redis(
            redis_key=redis_key,
            entry=p,
            tone=DEFAULT_TONE,
            website_id=DEFAULT_WEBSITE_ID
        )

        count += 1

    print(f"Synced {count} articles to Redis.")



# -----------------------------------------------------------------------------
# ADD THIS FUNCTION TO publish_article.py
# Call it after every posts.json update
# -----------------------------------------------------------------------------

def generate_sitemap():
    """
    Generate sitemap.xml from posts.json.

    Creates an XML sitemap at /var/www/html/wordpress/sitemap.xml
    listing homepage, security/market pages, and all published articles
    for search engines.

    Call this after every posts.json update to keep sitemap current.
    Only generates when config.seo_enabled is True.
    """
    if not config.seo_enabled:
        return

    import xml.etree.ElementTree as ET
    from xml.dom import minidom
    from datetime import date

    news_root = Path(config.news_root_folder).resolve()
    posts_json = news_root / "posts.json"
    sitemap_path = Path(config.news_root_folder) / "sitemap.xml"
    site_url = config.news_website_url.rstrip('/')
    today = date.today().isoformat()

    # Load articles
    if not posts_json.exists():
        print(f"[SITEMAP] posts.json not found at {posts_json}")
        return

    try:
        posts = json.loads(posts_json.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[SITEMAP] Failed to read posts.json: {e}")
        return

    # Build XML structure
    urlset = ET.Element("urlset")
    urlset.set("xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9")

    # --- Homepage entry (priority 1.0) ---
    url_elem = ET.SubElement(urlset, "url")
    ET.SubElement(url_elem, "loc").text = site_url + '/'
    ET.SubElement(url_elem, "lastmod").text = today
    ET.SubElement(url_elem, "changefreq").text = "daily"
    ET.SubElement(url_elem, "priority").text = "1.0"

    # --- Static evergreen pages (priority 0.9) ---
    url_elem = ET.SubElement(urlset, "url")
    ET.SubElement(url_elem, "loc").text = f"{site_url}/about.html"
    ET.SubElement(url_elem, "lastmod").text = today
    ET.SubElement(url_elem, "changefreq").text = "monthly"
    ET.SubElement(url_elem, "priority").text = "0.9"

    # --- Security / market pages (priority 0.9) ---
    slugs = ['sp500', 'dow', 'nasdaq', 'vix', 'crude-oil', 'natural-gas', 'gold']
    for slug in slugs:
        url_elem = ET.SubElement(urlset, "url")
        ET.SubElement(url_elem, "loc").text = f"{site_url}/markets/{slug}.html"
        ET.SubElement(url_elem, "lastmod").text = today
        ET.SubElement(url_elem, "changefreq").text = "daily"
        ET.SubElement(url_elem, "priority").text = "0.9"

    # --- Article entries (priority 0.8) ---
    for post in posts:
        url_elem = ET.SubElement(urlset, "url")

        # loc (required)
        loc = ET.SubElement(url_elem, "loc")
        loc.text = post.get("url", "")

        # lastmod (use updated_date or published_date)
        lastmod = ET.SubElement(url_elem, "lastmod")
        date_str = post.get("updated_date") or post.get("published_date", "")
        if date_str:
            # Convert from ISO format to YYYY-MM-DD
            try:
                if "T" in date_str:
                    lastmod.text = date_str.split("T")[0]
                else:
                    lastmod.text = date_str[:10]
            except:
                lastmod.text = date_str[:10]

        # changefreq (how often page changes)
        changefreq = ET.SubElement(url_elem, "changefreq")
        changefreq.text = "weekly"

        # priority (0.0 to 1.0, articles are 0.8)
        priority = ET.SubElement(url_elem, "priority")
        priority.text = "0.8"

    # Pretty print XML
    xml_str = minidom.parseString(ET.tostring(urlset)).toprettyxml(indent="  ")

    # Remove extra blank lines
    xml_lines = [line for line in xml_str.split('\n') if line.strip()]
    xml_str = '\n'.join(xml_lines)

    # Write sitemap
    total_urls = 1 + 1 + len(slugs) + len(posts)  # homepage + about + markets + articles
    try:
        sitemap_path.parent.mkdir(parents=True, exist_ok=True)
        with sitemap_path.open('w', encoding='utf-8') as f:
            f.write(xml_str)
        print(f"[SITEMAP] Generated sitemap with {total_urls} URLs "
              f"(1 homepage + 1 about + {len(slugs)} markets + {len(posts)} articles): {sitemap_path}")
    except Exception as e:
        print(f"[SITEMAP] Failed to write sitemap: {e}")


def generate_news_sitemap():
    """
    Generate sitemap-news.xml containing articles published in the last 48 hours.

    Uses the Google News sitemap format (xmlns:news namespace) required for the
    Google News crawler. Only articles from the last 48 hours are included per
    Google's spec. Max 1000 URLs.

    Only generates when config.seo_enabled is True.
    """
    if not config.seo_enabled:
        return

    import xml.etree.ElementTree as ET
    from xml.dom import minidom
    from datetime import datetime, timezone, timedelta

    news_root = Path(config.news_root_folder).resolve()
    posts_json = news_root / "posts.json"
    sitemap_path = Path(config.news_root_folder) / "sitemap-news.xml"
    site_url = config.news_website_url.rstrip('/')

    if not posts_json.exists():
        print(f"[NEWS-SITEMAP] posts.json not found at {posts_json}")
        return

    try:
        posts = json.loads(posts_json.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[NEWS-SITEMAP] Failed to read posts.json: {e}")
        return

    # Only include articles published in the last 48 hours (Google News requirement)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    recent_posts = []
    for post in posts:
        date_str = post.get("published_date", "")
        if not date_str:
            continue
        try:
            if date_str.endswith("Z"):
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                recent_posts.append((post, dt))
        except Exception:
            continue

    # Build XML with news namespace
    urlset = ET.Element("urlset")
    urlset.set("xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9")
    urlset.set("xmlns:news", "http://www.google.com/schemas/sitemap-news/0.9")

    pub_name = getattr(config, 'smn_from_name', 'Seasonal Market News')

    for post, pub_dt in recent_posts[:1000]:
        url_elem = ET.SubElement(urlset, "url")
        ET.SubElement(url_elem, "loc").text = post.get("url", "")

        news_elem = ET.SubElement(url_elem, "news:news")

        pub_elem = ET.SubElement(news_elem, "news:publication")
        ET.SubElement(pub_elem, "news:name").text = pub_name
        ET.SubElement(pub_elem, "news:language").text = "en"

        ET.SubElement(news_elem, "news:publication_date").text = \
            pub_dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        ET.SubElement(news_elem, "news:title").text = post.get("title", "")

    xml_str = minidom.parseString(ET.tostring(urlset)).toprettyxml(indent="  ")
    xml_lines = [line for line in xml_str.split('\n') if line.strip()]
    xml_str = '\n'.join(xml_lines)

    try:
        sitemap_path.parent.mkdir(parents=True, exist_ok=True)
        with sitemap_path.open('w', encoding='utf-8') as f:
            f.write(xml_str)
        print(f"[NEWS-SITEMAP] Generated news sitemap with {len(recent_posts)} recent "
              f"articles (last 48h): {sitemap_path}")
    except Exception as e:
        print(f"[NEWS-SITEMAP] Failed to write news sitemap: {e}")


def generate_rss_feed():
    """
    Generate rss.xml containing the 50 most recent published articles.

    RSS 2.0 format with atom:link self-reference, media namespace for hero images,
    and full per-item metadata. Compatible with Bing News, Apple News, Feedly,
    Flipboard, and all standard feed readers.

    Only generates when config.seo_enabled is True.
    """
    if not config.seo_enabled:
        return

    import xml.etree.ElementTree as ET
    from xml.dom import minidom
    from datetime import datetime, timezone
    from email.utils import format_datetime

    news_root  = Path(config.news_root_folder).resolve()
    posts_json = news_root / "posts.json"
    feed_path  = news_root / "rss.xml"
    site_url   = config.news_website_url.rstrip('/')

    if not posts_json.exists():
        print(f"[RSS] posts.json not found at {posts_json}")
        return

    try:
        posts = json.loads(posts_json.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[RSS] Failed to read posts.json: {e}")
        return

    # Most recent 50 articles
    recent = sorted(
        [p for p in posts if p.get("published_date")],
        key=lambda p: p["published_date"],
        reverse=True,
    )[:50]

    def to_rfc822(date_str: str) -> str:
        try:
            if date_str.endswith("Z"):
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return format_datetime(dt, usegmt=True)
        except Exception:
            return ""

    pub_name = getattr(config, 'smn_from_name', 'Seasonal Market News')
    feed_url  = f"{site_url}/rss.xml"
    now_rfc   = format_datetime(datetime.now(timezone.utc), usegmt=True)

    # Register namespaces
    ET.register_namespace("atom",  "http://www.w3.org/2005/Atom")
    ET.register_namespace("media", "http://search.yahoo.com/mrss/")

    rss = ET.Element("rss", version="2.0")
    rss.set("xmlns:atom",  "http://www.w3.org/2005/Atom")
    rss.set("xmlns:media", "http://search.yahoo.com/mrss/")

    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text        = pub_name
    ET.SubElement(channel, "link").text         = site_url + "/"
    ET.SubElement(channel, "description").text  = (
        "Seasonal stock market analysis and AI-powered financial news from TradeWave.ai"
    )
    ET.SubElement(channel, "language").text     = "en-us"
    ET.SubElement(channel, "lastBuildDate").text = now_rfc
    ET.SubElement(channel, "ttl").text          = "60"

    # atom:link self-reference (required by RSS best practices)
    atom_link = ET.SubElement(channel, "atom:link")
    atom_link.set("href", feed_url)
    atom_link.set("rel",  "self")
    atom_link.set("type", "application/rss+xml")

    # Channel image
    image_elem = ET.SubElement(channel, "image")
    ET.SubElement(image_elem, "url").text   = f"{site_url}/smnfav.png"
    ET.SubElement(image_elem, "title").text = pub_name
    ET.SubElement(image_elem, "link").text  = site_url + "/"

    for post in recent:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text       = post.get("title", "")
        ET.SubElement(item, "link").text        = post.get("url", "")
        ET.SubElement(item, "guid").text        = post.get("url", "")
        desc = post.get("meta_description") or post.get("dek", "")
        ET.SubElement(item, "description").text = desc
        pub_date = to_rfc822(post.get("published_date", ""))
        if pub_date:
            ET.SubElement(item, "pubDate").text = pub_date
        if post.get("symbol"):
            ET.SubElement(item, "category").text = post["symbol"]
        # Hero image as media:content enclosure
        hero = post.get("hero_image", "")
        if hero:
            mc = ET.SubElement(item, "media:content")
            mc.set("url",    hero)
            mc.set("medium", "image")

    xml_str   = minidom.parseString(ET.tostring(rss, encoding="unicode")).toprettyxml(indent="  ")
    xml_lines = [line for line in xml_str.split('\n') if line.strip()]
    xml_str   = '\n'.join(xml_lines)

    try:
        feed_path.parent.mkdir(parents=True, exist_ok=True)
        with feed_path.open('w', encoding='utf-8') as f:
            f.write(xml_str)
        print(f"[RSS] Generated feed with {len(recent)} articles: {feed_path}")
    except Exception as e:
        print(f"[RSS] Failed to write feed: {e}")


def notify_indexnow(article_url: str) -> None:
    """
    Notify Bing, Yandex, Naver and other IndexNow-enabled engines of a new article.
    Makes a single POST to api.indexnow.org which distributes to all participating engines.
    No-op when seo_enabled is False or indexnow_key is not set.
    """
    if not getattr(config, 'seo_enabled', False):
        return
    key = getattr(config, 'indexnow_key', '')
    if not key:
        return

    site_url = config.news_website_url.rstrip('/')
    host = site_url.split('//')[-1].split('/')[0]  # e.g. seasonalmarketnews.com

    payload = {
        "host": host,
        "key": key,
        "keyLocation": f"{site_url}/{key}.txt",
        "urlList": [article_url],
    }

    try:
        import requests as _req
        resp = _req.post(
            "https://api.indexnow.org/indexnow",
            json=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=10,
        )
        if resp.status_code in (200, 202):
            print(f"[INDEXNOW] Notified: {article_url} (HTTP {resp.status_code})")
        else:
            print(f"[INDEXNOW] Unexpected response {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"[INDEXNOW] Failed (non-fatal): {e}")


def generate_robots_txt():
    """Generate robots.txt with sitemap references and explicit AI crawler rules."""
    if not config.seo_enabled:
        return
    site_url = config.news_website_url.rstrip('/')
    content = f"""User-agent: *
Allow: /

# AI crawlers - explicitly welcome for indexing and training
User-agent: GPTBot
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: anthropic-ai
Allow: /

User-agent: Google-Extended
Allow: /

User-agent: Bytespider
Allow: /

User-agent: cohere-ai
Allow: /

User-agent: Meta-ExternalAgent
Allow: /

Sitemap: {site_url}/sitemap.xml
Sitemap: {site_url}/sitemap-news.xml
"""
    robots_path = Path(config.news_root_folder) / "robots.txt"
    robots_path.write_text(content, encoding='utf-8')
    print(f"[ROBOTS] Generated {robots_path}")

    # Ensure IndexNow verification file exists at web root
    key = getattr(config, 'indexnow_key', '')
    if key:
        key_file = Path(config.news_root_folder) / f"{key}.txt"
        if not key_file.exists():
            key_file.write_text(key, encoding='utf-8')
            print(f"[INDEXNOW] Created verification file: {key_file}")


def generate_llms_txt():
    """
    Generate /llms.txt — the emerging standard that tells AI systems (ChatGPT,
    Perplexity, Claude, Gemini, etc.) what this site is, what it covers, and
    which pages are most worth reading.  Spec: https://llmstxt.org
    """
    if not config.seo_enabled:
        return

    site_url = config.news_website_url.rstrip('/')
    news_root = Path(config.news_root_folder).resolve()
    posts_json = news_root / "posts.json"

    posts = []
    if posts_json.exists():
        try:
            posts = json.loads(posts_json.read_text(encoding='utf-8'))
        except Exception:
            pass

    # Build article list: 300 most recent + evergreen articles
    #
    # Evergreen = manually labelled OR auto-detected:
    #   auto: INDX or COMM market family with lookback >= 20 years, or any PE-cycle pattern
    #   manual: slugs listed one-per-line in evergreen_slugs.txt (next to posts.json)

    def _is_auto_evergreen(p: dict) -> bool:
        family = (p.get('market_family') or '').upper()
        ly = str(p.get('lookback_years') or '').strip().lower()
        if ly.startswith('pe'):
            return True
        if family in ('INDX', 'COMM'):
            try:
                return int(ly) >= 20
            except ValueError:
                pass
        return False

    # Load manual evergreen slugs
    manual_slugs: set = set()
    evergreen_file = news_root / "evergreen_slugs.txt"
    if evergreen_file.exists():
        for line in evergreen_file.read_text(encoding='utf-8').splitlines():
            slug = line.strip()
            if slug and not slug.startswith('#'):
                manual_slugs.add(slug)

    valid = [p for p in posts if p.get('url') and p.get('title')]
    by_date = sorted(valid, key=lambda p: p.get('published_date') or p.get('pattern_start_date') or '', reverse=True)

    recent_300 = by_date[:300]
    recent_slugs = {p.get('slug') for p in recent_300}

    # Evergreens from the older articles only (already in recent_300 if recent enough)
    evergreen = [
        p for p in by_date[300:]
        if p.get('slug') in manual_slugs or _is_auto_evergreen(p)
    ]

    recent = recent_300 + evergreen

    article_lines = "\n".join(
        f"- [{p['title']}]({p['url']})"
        for p in recent
    )

    content = f"""# Seasonal Market News

> AI-powered seasonal stock pattern analysis and data-driven financial news for investors and traders.

Seasonal Market News publishes daily articles that combine over 100 years of seasonal market data \
from TradeWave.ai with current financial news. Each article analyzes a specific seasonal pattern for \
a stock, ETF, index, or futures contract, including historical win rates, average returns, and \
current market context. The site targets retail and institutional investors looking for \
quantitative, data-backed seasonal trading insights.

## About

- [Methodology]({site_url}/methodology.html) — How seasonal patterns are sourced, calculated, and validated using TradeWave.ai data
- [Home]({site_url}/) — Latest articles, featured pattern of the day, and market overview
- [About]({site_url}/about.html) — About Afshin Moshrefi, founder and quantitative researcher
- [Search]({site_url}/search.html) — Search all published seasonal pattern articles

## Data Source

All seasonal pattern data is sourced from [TradeWave.ai](https://tradewave.ai/), a quantitative \
seasonal analysis platform covering US stocks, ETFs, indices, futures, and forex with up to 25 years \
of lookback. The underlying research methodology is detailed in the book \
[The 100-Year Pattern]({config.book_amazon_url}).

## Recent Articles

{article_lines}
"""

    llms_path = news_root / "llms.txt"
    llms_path.write_text(content, encoding='utf-8')
    print(f"[LLMS] Generated {llms_path} ({len(recent)} recent articles listed)")


def rebuild_search_index_from_posts():
    import html as _html

    def _clean(s: str) -> str:
        s = _html.unescape(s or "")
        s = re.sub(r"\[\d+\]", "", s)          # strip [1][2][3]
        s = re.sub(r"\s+", " ", s).strip()
        return s

    news_root = Path(config.news_root_folder).resolve()
    posts_json = news_root / "posts.json"
    search_index_path = news_root / "search_index.json"

    posts = json.loads(posts_json.read_text(encoding="utf-8")) if posts_json.exists() else []

    # Dedup by URL, latest wins
    by_url = {}

    for p in posts:
        url = (p.get("url") or "").strip()
        if not url:
            continue

        title = _clean(p.get("title", ""))
        dek   = _clean(p.get("dek", ""))

        published_date = (p.get("published_date") or "").strip()
        month = (published_date[:7] if published_date else (p.get("pattern_start_date", "")[:7]))

        rec = {
            "title": title,
            "url": url,
            "symbol": (p.get("symbol", "") or "").strip(),
            "market_family": (p.get("market_family", "") or "").strip(),
            "dek": dek,
            "published_date": published_date,
            "month": month,
            "tags": p.get("tags", []),
            # Precomputed search string so the UI can do simple contains() fast
            "q": _clean(f"{p.get('symbol','')} {title} {dek} {p.get('market_family','')}").lower(),
        }

        by_url[url] = rec

    search_index = list(by_url.values())
    search_index.sort(key=lambda r: r.get("published_date", ""), reverse=True)

    _write_atomic(search_index_path, json.dumps(search_index, ensure_ascii=False))
    print(f"[search_index] rebuilt {len(search_index)} records -> {search_index_path}")

# -----------------------------------------------------------------------------
# INTEGRATION INSTRUCTIONS:
# -----------------------------------------------------------------------------
# 
# 1. Add the generate_sitemap() function above to publish_article.py
#    (put it near the other helper functions like _ensure_dir, _write_atomic, etc.)
#
# 2. Call it in two places:
#
#    A) In write_article_and_register() - after updating posts.json:
#       
#       _write_atomic(posts_json, json.dumps(posts, ensure_ascii=False, indent=2))
#       
#       # Generate sitemap
#       generate_sitemap()  # <-- ADD THIS LINE
#       
#       # rebuild home
#       build_home()
#
#    B) In publish_article_to_folder() - after the remove operation updates posts.json:
#       
#       _write_atomic(posts_json, json.dumps(posts, ensure_ascii=False, indent=2))
#       
#       # Generate sitemap
#       generate_sitemap()  # <-- ADD THIS LINE
#       
#       _delete_article_images(symbol, pattern_start_date, days, years)
#       build_home()
#
# 3. After deploying, submit sitemap to Google Search Console:
#    - Go to https://search.google.com/search-console
#    - Add property: http://192.168.1.151/
#    - Submit sitemap: http://192.168.1.151/sitemap.xml
#
# -----------------------------------------------------------------------------

#############################################################################################################


if __name__ == "__main__":

    # sync_all_articles_to_redis()
    # exit()

    # rebuild_search_index_from_posts()
    # exit()

    print('publish article 1.0')


    # sync_all_articles_to_redis()

    # exit()


    test_article_path = '/var/www/html/wordpress/news/US/2025/12/07/church-dwight-chd-trades-inside-a-historically-strong-seasonal-window.html'
    test_article_path = 'CHD_test_article.html'
    with open(test_article_path, 'r', encoding='utf-8') as f: article_html = f.read()


    resource_id  = '2'
    symbol       = 'CHD'
    date         = '2025-12-07'
    days         = '132'
    years        = '10'
    userid       = '22'
    # article_html = 'remove'
    


    # Compute paths/URLs (same as publish_article_to_folder does)
    info = compute_article_paths_and_url(
        resource_id=resource_id,
        symbol=symbol,
        pattern_start_date=date,
        days=days,
        years=years,
        article_html=article_html,
    )
    
    # Call write_article_and_register directly (includes post-processing)
    return_dict = write_article_and_register(
        info=info,
        resource_id=resource_id,
        symbol=symbol,
        pattern_start_date=date,
        days=days,
        years=years,
        userid=userid,
    )

    print(return_dict)