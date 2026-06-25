#!/usr/bin/env python3
"""
reindex_articles_to_redis.py

One-off script to heal articles that exist in posts.json (on disk) but are
missing from Redis db=3.  Missing Redis keys cause the portfolio blue icon
and delete button to stay off even though the article was published.

Run on the webserver:
  python3 /home/flask/blog/reindex_articles_to_redis.py

Safe to run multiple times — skips articles already in Redis.
"""

import json
import sys
import logging
from pathlib import Path

sys.path.insert(0, '/home/flask')
sys.path.insert(0, '/home/flask/blog')

import config
import redis
from publish_article import make_redis_key, save_article_to_redis

logging.basicConfig(level=logging.INFO, format='%(message)s')

DEFAULT_TONE       = "neutral"
DEFAULT_WEBSITE_ID = 0

redis_client3 = redis.Redis(host=config.webserver_ip, port=6379, db=3)

posts_json_path = Path(config.news_root_folder) / "posts.json"

if not posts_json_path.exists():
    print(f"[ERROR] posts.json not found at {posts_json_path}")
    sys.exit(1)

try:
    posts = json.loads(posts_json_path.read_text(encoding="utf-8"))
except Exception as e:
    print(f"[ERROR] Failed to read posts.json: {e}")
    sys.exit(1)

print(f"Found {len(posts)} articles in posts.json\n")

healed   = 0
already  = 0
skipped  = 0

for p in posts:
    resource_id        = str(p.get("resource_id", ""))
    symbol             = str(p.get("symbol", "")).upper()
    pattern_start_date = str(p.get("pattern_start_date", ""))
    days               = p.get("pattern_days", "")
    years              = str(p.get("lookback_years", ""))

    if not all([resource_id, symbol, pattern_start_date, days, years]):
        print(f"  [SKIP] Missing fields — {p.get('symbol','?')} {pattern_start_date}: "
              f"resource_id={resource_id} days={days} years={years}")
        skipped += 1
        continue

    try:
        days_int = int(days)
    except (ValueError, TypeError):
        print(f"  [SKIP] Non-integer days={days!r} for {symbol}")
        skipped += 1
        continue

    redis_key = make_redis_key(
        resource_id=resource_id,
        symbol=symbol,
        pattern_start_date=pattern_start_date,
        days=days_int,
        years=years,
        tone=DEFAULT_TONE,
        website_id=DEFAULT_WEBSITE_ID,
    )

    if redis_client3.exists(redis_key):
        print(f"  [OK]   {symbol:6s} {pattern_start_date}  days={days_int}  years={years}  — already in Redis")
        already += 1
        continue

    # Missing from Redis — heal it
    try:
        save_article_to_redis(redis_key, p)
        print(f"  [HEAL] {symbol:6s} {pattern_start_date}  days={days_int}  years={years}  → written to Redis")
        healed += 1
    except Exception as e:
        print(f"  [ERR]  {symbol:6s} {pattern_start_date} — failed to write Redis: {e}")
        skipped += 1

print(f"\n{'='*50}")
print(f"Done.  Healed: {healed}  Already OK: {already}  Skipped: {skipped}")
