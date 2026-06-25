"""
backfill_direction.py
=====================
One-time tool to backfill the 'direction' field for existing articles.

Reads posts.json, finds articles missing 'direction', calls get_opp_data
to retrieve it, then updates both posts.json and Redis.

Usage:
    python3 backfill_direction.py          # dry run (shows what would change)
    python3 backfill_direction.py --apply  # actually apply changes
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, '/home/flask')
import config
import redis
from article_prompt import get_opp_data

# --- Paths ---
NEWS_ROOT = Path(config.news_root_folder)
POSTS_JSON = NEWS_ROOT / "posts.json"

# --- Redis ---
DEFAULT_TONE = "neutral"
DEFAULT_WEBSITE_ID = 0
redis_client3 = redis.Redis(host=config.webserver_ip, port=6379, db=3)


def make_redis_key(resource_id, symbol, pattern_start_date, days, years, tone=DEFAULT_TONE, website_id=DEFAULT_WEBSITE_ID):
    return f"{resource_id}_{symbol.upper()}_{pattern_start_date}_{days}_{years}_{tone}_{website_id}"


def get_direction(resource_id, symbol, date, days, years):
    """Call get_opp_data and extract trade direction."""
    try:
        cdata = get_opp_data(resource_id, date, symbol, days, years, True)
        stats = cdata.get("stats", {})
        direction = stats.get("Trade Dir", "").strip().lower()
        if direction in ("long", "short"):
            return direction
        return None
    except Exception as e:
        print(f"  [ERROR] get_opp_data failed: {e}")
        return None


def backfill(apply=False):
    """Backfill direction for articles missing it."""
    
    if not POSTS_JSON.exists():
        print(f"posts.json not found at {POSTS_JSON}")
        return
    
    posts = json.loads(POSTS_JSON.read_text("utf-8"))
    print(f"Loaded {len(posts)} articles from posts.json\n")
    
    updated_count = 0
    skipped_count = 0
    error_count = 0
    
    for i, p in enumerate(posts):
        symbol = p.get("symbol", "")
        date = p.get("pattern_start_date", "")
        
        # Skip if direction already set
        if p.get("direction"):
            skipped_count += 1
            continue
        
        resource_id = p.get("resource_id", "")
        days = p.get("pattern_days", "")
        years = p.get("lookback_years", "")
        
        print(f"[{i+1}/{len(posts)}] {symbol} {date} {days}d {years}y ... ", end="")
        
        # Get direction from chartData4
        direction = get_direction(resource_id, symbol, date, days, years)
        
        if not direction:
            print("SKIPPED (no direction returned)")
            error_count += 1
            continue
        
        print(f"{direction.upper()}")
        
        if apply:
            # Update posts.json entry
            p["direction"] = direction
            
            # Update Redis
            redis_key = make_redis_key(
                resource_id=resource_id,
                symbol=symbol,
                pattern_start_date=date,
                days=int(days),
                years=str(years)
            )
            
            raw = redis_client3.get(redis_key)
            if raw:
                try:
                    payload = json.loads(raw)
                    if "entry" in payload:
                        payload["entry"]["direction"] = direction
                        redis_client3.set(redis_key, json.dumps(payload, ensure_ascii=False))
                        print(f"       Redis updated: {redis_key}")
                except Exception as e:
                    print(f"       [ERROR] Redis update failed: {e}")
        
        updated_count += 1
    
    # Write updated posts.json
    if apply and updated_count > 0:
        POSTS_JSON.write_text(json.dumps(posts, ensure_ascii=False, indent=2), "utf-8")
        print(f"\nWrote updated posts.json")
    
    print(f"\n{'='*50}")
    print(f"Summary:")
    print(f"  Already had direction: {skipped_count}")
    print(f"  Updated: {updated_count}")
    print(f"  Errors/skipped: {error_count}")
    
    if not apply and updated_count > 0:
        print(f"\nDRY RUN - no changes made. Run with --apply to update.")


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    backfill(apply=apply)
