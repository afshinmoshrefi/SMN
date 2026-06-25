#!/usr/bin/env python3
"""
force_delete_article.py

Directly delete a published article by symbol, bypassing the UI.
Works even when the Redis key is missing/mismatched (blue icon broken).

Usage:
    python3 /home/flask/blog/force_delete_article.py XLF
    python3 /home/flask/blog/force_delete_article.py XLF --confirm
"""

import sys
import json
import redis
from pathlib import Path

sys.path.insert(0, '/home/flask')
sys.path.insert(0, '/home/flask/blog')
import config

SYMBOL = sys.argv[1].upper() if len(sys.argv) > 1 else None
CONFIRM = '--confirm' in sys.argv

if not SYMBOL:
    print("Usage: python3 force_delete_article.py <SYMBOL> [--confirm]")
    sys.exit(1)

# --- 1. Load posts.json ---
news_root = Path(config.news_root_folder).resolve()
posts_json = news_root / "posts.json"

if not posts_json.exists():
    print(f"ERROR: posts.json not found at {posts_json}")
    sys.exit(1)

posts = json.loads(posts_json.read_text(encoding="utf-8"))

# Find all entries for this symbol
matches = [
    (i, p) for i, p in enumerate(posts)
    if p.get("symbol", "").upper() == SYMBOL
]

if not matches:
    print(f"No articles found for {SYMBOL} in posts.json")
    sys.exit(0)

print(f"Found {len(matches)} article(s) for {SYMBOL}:")
for i, (idx, p) in enumerate(matches):
    print(f"  [{i}] date={p.get('pattern_start_date')}  days={p.get('pattern_days')}  "
          f"years={p.get('lookback_years')}  resource_id={p.get('resource_id')}")
    print(f"       title: {p.get('title', '(no title)')[:80]}")
    print(f"       path:  {p.get('path', '(no path)')}")

if len(matches) > 1:
    print("\nMultiple articles found. Run with a more specific symbol or edit script to target index.")
    print("All will be deleted with --confirm. Press Ctrl+C to abort.")

if not CONFIRM:
    print(f"\nDry run — add --confirm to actually delete.")
    sys.exit(0)

# --- 2. Delete each match ---
r3 = redis.Redis(host=config.webserver_ip, port=6379, db=3)

# Import the delete helper from publish_article
from publish_article import (
    make_redis_key, delete_article_from_redis,
    delete_search_index_entry, _safe_unlink, _delete_article_images,
    _write_atomic, DEFAULT_TONE, DEFAULT_WEBSITE_ID
)
from rebuild_news_home import build_home

# Work from the end so indices stay valid
deleted = 0
posts_remaining = list(posts)

for _, (orig_idx, p) in enumerate(matches):
    symbol      = p.get("symbol", SYMBOL)
    resource_id = p.get("resource_id", "")
    date        = p.get("pattern_start_date", "")
    days        = p.get("pattern_days", "")
    years       = p.get("lookback_years", "")
    path_str    = p.get("path", "")
    url         = p.get("url", "")

    print(f"\nDeleting: {symbol} date={date} days={days} years={years}")

    # Delete HTML
    if path_str:
        html_path = Path(path_str)
        if html_path.exists():
            html_path.unlink()
            print(f"  [OK] Deleted HTML: {html_path}")
        else:
            print(f"  [WARN] HTML not found: {html_path}")
    else:
        print("  [WARN] No path in posts.json entry")

    # Delete from Redis — try both plain years AND pe-prefixed variants
    keys_to_try = set()
    # Standard key
    try:
        k = make_redis_key(resource_id=resource_id, symbol=symbol,
                           pattern_start_date=date, days=int(days),
                           years=years, tone=DEFAULT_TONE,
                           website_id=DEFAULT_WEBSITE_ID)
        keys_to_try.add(k)
    except Exception:
        pass

    # Also nuke any matching keys from db=3 via wildcard scan
    pattern = f"*_{symbol.upper()}_*"
    for k in r3.keys(pattern):
        keys_to_try.add(k.decode() if isinstance(k, bytes) else k)

    for k in keys_to_try:
        if r3.exists(k):
            r3.delete(k)
            print(f"  [OK] Deleted Redis key: {k}")

    # Remove from search index
    delete_search_index_entry(news_root, url)
    print(f"  [OK] Removed from search index")

    # Delete images
    _delete_article_images(symbol, date, str(days), years)
    print(f"  [OK] Cleaned images")

    # Mark for removal from posts
    posts_remaining = [p2 for p2 in posts_remaining
                       if not (p2.get("symbol", "").upper() == symbol.upper()
                               and p2.get("pattern_start_date") == date
                               and str(p2.get("pattern_days", "")) == str(days)
                               and str(p2.get("lookback_years", "")) == str(years))]
    deleted += 1

# Rewrite posts.json
_write_atomic(posts_json, json.dumps(posts_remaining, ensure_ascii=False, indent=2))
print(f"\n[OK] Rewrote posts.json ({len(posts)} → {len(posts_remaining)} entries)")

# Rebuild home page
print("Rebuilding home page...")
build_home()
print("Done.")
print(f"\nDeleted {deleted} article(s) for {SYMBOL}.")
