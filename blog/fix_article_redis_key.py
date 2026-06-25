#!/usr/bin/env python3
"""
fix_article_redis_key.py

Diagnoses and fixes the Redis key mismatch that causes the blue icon
and delete button to disappear for a specific article.

Run on the webserver:
  python3 /home/flask/blog/fix_article_redis_key.py XLE
  python3 /home/flask/blog/fix_article_redis_key.py NVDA
"""

import sys
import json
import redis

sys.path.insert(0, '/home/flask')
import config

SYMBOL = sys.argv[1].upper() if len(sys.argv) > 1 else None
if not SYMBOL:
    print("Usage: python3 fix_article_redis_key.py <SYMBOL>")
    sys.exit(1)

r2 = redis.Redis(host=config.webserver_ip, port=6379, db=2)
r3 = redis.Redis(host=config.webserver_ip, port=6379, db=3)

# --- 1. Find portfolio rows for this symbol ---
portfolio_years_values = set()
for k in r2.keys('user_reports_*'):
    raw = r2.get(k)
    if not raw:
        continue
    try:
        items = json.loads(raw)
        for item in items:
            if item.get('symbol', '').upper() == SYMBOL:
                y = item.get('years', '')
                portfolio_years_values.add(y)
                print(f"  [portfolio] resourceID={item.get('resourceID')}  date={item.get('date')}  "
                      f"days={item.get('days_hold')}  years={y!r}")
    except Exception:
        continue

if not portfolio_years_values:
    print(f"  [WARN] No portfolio rows found for {SYMBOL} in Redis db=2")

# --- 2. Find all existing db=3 keys for this symbol ---
print(f"\nExisting Redis db=3 keys for {SYMBOL}:")
existing_keys = [k.decode() for k in r3.keys(f'*_{SYMBOL}_*')]
for k in sorted(existing_keys):
    print(f"  {k}")

if not existing_keys:
    print(f"  [WARN] No Redis article keys found for {SYMBOL} — article may not be published")
    sys.exit(1)

# --- 3. For each existing key, check if portfolio_years match and heal if not ---
print(f"\nHealing mismatches:")
healed = 0
for src_key in existing_keys:
    parts = src_key.split('_')
    # key format: {resource_id}_{SYMBOL}_{date}_{days}_{years}_neutral_0
    # SYMBOL may contain underscores? No — symbols are simple. Split carefully.
    # Format: resourceID _ SYMBOL _ YYYY-MM-DD _ days _ years _ neutral _ 0
    if len(parts) < 7:
        continue
    key_years = parts[4]   # the years embedded in the existing key

    src_val = r3.get(src_key)
    if not src_val:
        continue

    for portfolio_y in portfolio_years_values:
        if portfolio_y == key_years:
            print(f"  [OK]  {src_key}  (portfolio years={portfolio_y!r} already matches)")
            continue
        # Build the key the portfolio would look for
        target_key = src_key.replace(f'_{key_years}_neutral_0', f'_{portfolio_y}_neutral_0')
        if r3.exists(target_key):
            print(f"  [OK]  target key already exists: {target_key}")
        else:
            r3.set(target_key, src_val)
            print(f"  [HEAL] wrote {target_key}")
            healed += 1

print(f"\nDone. Healed {healed} key(s).")
print("Reload the portfolio page — blue icon and delete button should now appear.")
