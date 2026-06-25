"""
fix_staging_urls.py
===================
Replaces all site URLs in article HTML files, posts.json, datasets, and
sitemap to match the current server's config.news_website_url.

Auto-detects what URL is currently in the files and replaces it with
the value from config.news_website_url on this server.

Usage:
  python fix_staging_urls.py                # dry run
  python fix_staging_urls.py --confirm      # apply changes
"""

import sys, re, os, json
sys.path.insert(0, '/home/flask')
import config
import redis
from pathlib import Path
from collections import Counter


def _detect_current_url(articles_dir):
    """Scan a few articles to detect what site URL they currently contain."""
    patterns = [
        # Match canonical link or og:url which contain the site base URL
        re.compile(r'<link rel="canonical" href="(https?://[^/]+)'),
        re.compile(r'<meta property="og:url" content="(https?://[^/]+)'),
        # Match article URLs in related articles / JSON-LD
        re.compile(r'"url":\s*"(https?://[^/]+)/articles/'),
    ]

    found = Counter()
    html_files = list(articles_dir.rglob("*.html"))[:20]  # sample first 20

    for fpath in html_files:
        try:
            html = fpath.read_text("utf-8")
        except Exception:
            continue
        for pat in patterns:
            for m in pat.finditer(html):
                url = m.group(1).rstrip("/")
                # Skip schema.org, fonts, etc.
                if "schema.org" in url or "fonts" in url or "mailerlite" in url:
                    continue
                found[url] += 1

    if not found:
        return None

    # Return the most common one
    most_common = found.most_common(1)[0][0]
    return most_common


def _sync_redis(news_root, from_url, to_url, confirm):
    """Flush old article entries from Redis db=3 and re-import from posts.json
    with URLs updated to the target server."""
    posts_path = news_root / "posts.json"
    if not posts_path.exists():
        print("\n  [REDIS] No posts.json found, skipping Redis sync")
        return

    try:
        r = redis.Redis(host=config.webserver_ip, port=6379, db=3)
        r.ping()
    except Exception as e:
        print(f"\n  [REDIS] Cannot connect to Redis: {e}")
        return

    posts = json.loads(posts_path.read_text("utf-8"))

    # Count existing article keys
    old_keys = list(r.scan_iter(match="*_neutral_0"))

    if not confirm:
        print(f"\n  [REDIS] Would flush {len(old_keys)} old entries and import {len(posts)} from posts.json")
        return

    # Flush old article entries
    for k in old_keys:
        r.delete(k)

    # Import from posts.json (URLs already fixed in the file by this point)
    imported = 0
    for p in posts:
        symbol = p.get("symbol", "")
        date = p.get("pattern_start_date", "")
        days = p.get("pattern_days", "")
        years = p.get("lookback_years", "")

        key = f"0_{symbol}_{date}_{days}_{years}_neutral_0"
        payload = {"entry": p, "tone": "neutral", "website_id": 0}
        r.set(key, json.dumps(payload))
        imported += 1

    new_count = len(list(r.scan_iter(match="*_neutral_0")))
    print(f"\n  [REDIS] Flushed {len(old_keys)} old entries, imported {imported} articles ({new_count} unique keys)")


def fix_urls(to_url=None, confirm=False):
    news_root = Path(config.news_root_folder)
    articles_dir = news_root / "articles"

    if to_url is None:
        to_url = config.news_website_url.rstrip("/")

    if not articles_dir.exists():
        print(f"No articles directory at {articles_dir}")
        return

    # Auto-detect what URL is currently in the files
    from_url = _detect_current_url(articles_dir)
    if not from_url:
        print("Could not detect current site URL in articles. Nothing to fix.")
        return

    from_url = from_url.rstrip("/")
    to_url = to_url.rstrip("/")

    if from_url == to_url:
        print(f"Articles already use {to_url}. URLs already correct.")
        # Still sync Redis if needed
        _sync_redis(news_root, from_url, to_url, confirm)
        return

    print(f"{'DRY RUN' if not confirm else 'APPLYING'}")
    print(f"  Detected in files: {from_url}")
    print(f"  Target (config):   {to_url}")
    print(f"  Root: {news_root}")
    print()

    total_files = 0
    total_replacements = 0

    # Process all HTML, JSON, and XML files
    file_groups = [
        ("articles", list(articles_dir.rglob("*.html"))),
        ("posts.json", [news_root / "posts.json"] if (news_root / "posts.json").exists() else []),
        ("datasets", list((news_root / "datasets").glob("*.json")) if (news_root / "datasets").exists() else []),
        ("sitemap", [news_root / "sitemap.xml"] if (news_root / "sitemap.xml").exists() else []),
        ("search", [news_root / "search.html"] if (news_root / "search.html").exists() else []),
        ("robots", [news_root / "robots.txt"] if (news_root / "robots.txt").exists() else []),
    ]

    for group_name, files in file_groups:
        group_count = 0
        for fpath in files:
            try:
                text = fpath.read_text("utf-8")
            except Exception:
                continue

            n = text.count(from_url)
            if n == 0:
                continue

            text = text.replace(from_url, to_url)
            group_count += n
            total_files += 1
            total_replacements += n

            if confirm:
                fpath.write_text(text, "utf-8")

        if group_count > 0:
            file_count = sum(1 for f in files if f.exists())
            print(f"  {group_name}: {group_count} replacements")

    # Sync Redis db=3 with posts.json
    _sync_redis(news_root, from_url, to_url, confirm)

    print(f"\n{'Applied' if confirm else 'Would apply'}: {total_replacements} replacements across {total_files} files")
    if not confirm:
        print("\nRun with --confirm to apply changes.")


if __name__ == "__main__":
    to_url = None
    confirm = "--confirm" in sys.argv

    for arg in sys.argv[1:]:
        if arg.startswith("--to="):
            to_url = arg.split("=", 1)[1]

    fix_urls(to_url=to_url, confirm=confirm)
