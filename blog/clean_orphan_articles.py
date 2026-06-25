#!/usr/bin/env python3
"""
clean_orphan_articles.py

Finds articles that exist in Redis db=3 (so they appear on the home page)
but whose HTML file is missing from disk (causing 404 when clicked).

Optionally removes the orphaned Redis keys and rebuilds the home page.

Usage:
    python3 /home/flask/blog/clean_orphan_articles.py          # report only
    python3 /home/flask/blog/clean_orphan_articles.py ADP      # report for one symbol
    python3 /home/flask/blog/clean_orphan_articles.py --confirm          # fix all
    python3 /home/flask/blog/clean_orphan_articles.py ADP --confirm      # fix one symbol
"""

import sys
import json
import redis
from pathlib import Path

sys.path.insert(0, '/home/flask')
sys.path.insert(0, '/home/flask/blog')
import config

FILTER_SYMBOL = None
CONFIRM = '--confirm' in sys.argv
for arg in sys.argv[1:]:
    if not arg.startswith('--'):
        FILTER_SYMBOL = arg.upper()

r3 = redis.Redis(host=config.webserver_ip, port=6379, db=3)

orphans = []  # (key, symbol, url, path)

print("Scanning Redis db=3 for orphaned articles (Redis entry exists, HTML missing)...\n")

for raw_key in r3.scan_iter(match='*_neutral_0'):
    key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
    raw = r3.get(raw_key)
    if not raw:
        continue
    try:
        payload = json.loads(raw)
    except Exception:
        continue

    entry = payload.get('entry', {})
    if not entry:
        continue

    symbol = entry.get('symbol', '')
    url    = entry.get('url', '')
    path   = entry.get('path', '')

    if FILTER_SYMBOL and symbol.upper() != FILTER_SYMBOL:
        continue

    # Check if HTML file exists
    if path:
        html_exists = Path(path).exists()
    elif url:
        # Derive path from URL as fallback
        from article_tools import compute_article_paths_and_url
        html_exists = True  # can't check without path, skip
    else:
        html_exists = True  # no path info, skip

    if not html_exists:
        orphans.append((key, symbol, url, path))
        print(f"  [ORPHAN] {key}")
        print(f"           symbol={symbol}  url={url}")
        print(f"           path={path}")
        print()

if not orphans:
    print("No orphaned articles found. All Redis entries have matching HTML files.")
    sys.exit(0)

print(f"Found {len(orphans)} orphaned article(s).")

if not CONFIRM:
    print("\nDry run — add --confirm to remove orphaned Redis keys and rebuild home page.")
    sys.exit(0)

# --- Remove orphans ---
import json as _json

news_root  = Path(config.news_root_folder).resolve()
posts_json = news_root / 'posts.json'

# Also remove from posts.json if present
posts = []
if posts_json.exists():
    try:
        posts = _json.loads(posts_json.read_text('utf-8'))
    except Exception:
        pass

removed_keys = 0
posts_before = len(posts)

for key, symbol, url, path in orphans:
    r3.delete(key)
    print(f"[OK] Deleted Redis key: {key}")
    removed_keys += 1

    # Remove from posts.json if matched by path or url
    posts = [p for p in posts
             if not (p.get('path') == path or (url and p.get('url') == url))]

if len(posts) < posts_before:
    from publish_article import _write_atomic
    _write_atomic(posts_json, _json.dumps(posts, ensure_ascii=False, indent=2))
    print(f"[OK] Removed {posts_before - len(posts)} entry(ies) from posts.json")

# Rebuild home
print("\nRebuilding home page...")
from rebuild_news_home import build_home
build_home()
print(f"\nDone. Removed {removed_keys} orphaned Redis key(s). Article(s) no longer appear on home page.")
