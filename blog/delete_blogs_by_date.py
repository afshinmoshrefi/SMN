#!/usr/bin/env python3
# Delete WordPress posts in a given date range if they belong to allowed categories

import requests
import time
import subprocess
from datetime import datetime, timezone
import sys
import os
import json
import hashlib

# ---------------- config ----------------
sys.path.insert(0, '/home/flask')
import config

WP_SITE_URL = config.wordpress_url
USERNAME = config.username
PASSWORD = config.password
WP_API_URL = f"{WP_SITE_URL}wp-json/wp/v2/posts"

BATCH_SIZE = 20
SLEEP_BETWEEN_DELETES = 2
PHP_IDLE_THRESHOLD = 2
CHECK_INTERVAL = 5
MAX_RETRIES = 5
AUTH = (USERNAME, PASSWORD)

LOCK_FILE = "/tmp/delete_posts_range.lock"
STATE_FILE = "/tmp/delete_posts_range_state.json"

ALLOWED_CATEGORIES = {
    config.category_sr_tn,
    config.category_report,
    config.category_top10,
    config.category_opp_top10,
    config.category_opp_top10t,
    config.category_top10_archive,
    config.category_date_range_report
}

DRY_RUN = False  # set True to test without deleting

# --------------- helpers ----------------
def php_processes():
    try:
        res = subprocess.run(['systemctl', 'status', 'php7.4-fpm'], capture_output=True)
        txt = res.stdout.decode('utf-8', errors='ignore')
        for line in txt.splitlines():
            if 'Status' in line and ',' in line and ':' in line:
                parts = line.split(',')
                try:
                    active = int(parts[1].split(':')[1].strip())
                    idle = int(parts[2].split(':')[1].strip())
                    return active, idle
                except Exception:
                    return 6, 0
    except Exception:
        pass
    return 6, 0

def acquire_lock():
    if os.path.exists(LOCK_FILE):
        with open(LOCK_FILE, "r") as f:
            pid = f.read().strip()
        if pid.isdigit() and os.path.exists(f"/proc/{pid}"):
            print(f"Another instance is running (PID {pid}). Exiting.")
            return False
        print("Stale lock found. Removing it.")
        os.remove(LOCK_FILE)
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True

def release_lock():
    try:
        if os.path.exists(LOCK_FILE):
            with open(LOCK_FILE, "r") as f:
                pid = f.read().strip()
            if pid == str(os.getpid()):
                os.remove(LOCK_FILE)
    except Exception as e:
        print(f"Error releasing lock: {e}")

def state_key(date1, date2):
    s = f"{WP_API_URL}|{date1}|{date2}|{sorted(ALLOWED_CATEGORIES)}"
    return hashlib.sha256(s.encode()).hexdigest()

def save_state(page, deleted, skipped, key):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"page": page, "deleted": deleted, "skipped": skipped, "key": key}, f)
    except Exception as e:
        print(f"Error saving state: {e}")

def load_state(expected_key):
    if not os.path.exists(STATE_FILE):
        return 1, 0, 0
    try:
        with open(STATE_FILE, "r") as f:
            s = json.load(f)
        if s.get("key") != expected_key:
            # different range or env; reset
            return 1, 0, 0
        return s.get("page", 1), s.get("deleted", 0), s.get("skipped", 0)
    except Exception as e:
        print(f"Error loading state: {e}")
        return 1, 0, 0

def get_posts_in_range(date1, date2, page):
    after_iso = f"{date1}T00:00:00Z"
    before_iso = f"{date2}T23:59:59Z"
    params = {
        "per_page": BATCH_SIZE,
        "page": page,
        "after": after_iso,
        "before": before_iso,
        "status": "publish",
        "orderby": "date",
        "order": "asc",
        # Optional server-side filter to reduce volume:
        # "categories": ",".join(str(c) for c in ALLOWED_CATEGORIES),
    }
    try:
        r = requests.get(WP_API_URL, auth=AUTH, params=params)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 400 and "rest_post_invalid_page_number" in r.text:
            return []
        print(f"Error fetching posts: {r.status_code} - {r.text}")
        return []
    except Exception as e:
        print(f"Exception fetching posts: {e}")
        return []

def parse_wp_date(s):
    # WP returns like "2025-08-06T11:22:33"
    # Treat as naive UTC
    try:
        dt = datetime.fromisoformat(s.replace("Z", ""))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def within_range(post_date_str, date1, date2):
    dt = parse_wp_date(post_date_str)
    if not dt:
        return False
    start = datetime.fromisoformat(f"{date1}T00:00:00+00:00")
    end   = datetime.fromisoformat(f"{date2}T23:59:59+00:00")
    return start <= dt <= end

def delete_post_if_allowed(post_id, date1, date2, retry_count=0):
    _, idle = php_processes()
    if idle < PHP_IDLE_THRESHOLD:
        if retry_count >= MAX_RETRIES:
            print(f"Max retries reached for post {post_id}. Skipping.")
            return False
        print(f"Low idle PHP processes ({idle}). Waiting to retry post {post_id}...")
        time.sleep(CHECK_INTERVAL)
        return delete_post_if_allowed(post_id, date1, date2, retry_count + 1)

    url = f"{WP_API_URL}/{post_id}"
    try:
        resp = requests.get(url, auth=AUTH)
        if resp.status_code != 200:
            print(f"Error fetching post {post_id}: {resp.status_code}")
            return False

        post = resp.json()
        post_date = post.get("date", "")
        post_title = post.get("title", {}).get("rendered", "")
        cat_ids = post.get("categories", [])

        # HARD GUARD: do not delete if date is outside range
        if not within_range(post_date, date1, date2):
            print(f"Skip {post_id}: date {post_date} outside {date1}..{date2}")
            return False

        # Category check
        if not any(cid in ALLOWED_CATEGORIES for cid in cat_ids):
            print(f"Skip {post_id}: not in allowed categories {cat_ids}")
            return False

        if DRY_RUN:
            print(f"[DRY RUN] Would delete {post_id} | {post_title} | {post_date}")
            return True

        del_url = f"{WP_API_URL}/{post_id}?force=true"
        d = requests.delete(del_url, auth=AUTH)
        if d.status_code == 200:
            print(f"Deleted {post_id} | {post_title} | {post_date}")
            return True
        print(f"Failed to delete {post_id}: {d.status_code} - {d.text}")
        return False

    except Exception as e:
        print(f"Exception deleting post {post_id}: {e}")
        return False

# --------------- run ----------------
if __name__ == "__main__":
    # Set your range here (inclusive)
    date1 = "2025-08-06"
    date2 = "2025-08-10"
    # Optional first run with DRY_RUN = True to verify

    if not acquire_lock():
        sys.exit(1)

    key = state_key(date1, date2)

    try:
        print(f"Deleting posts from {date1} to {date2} in allowed categories. Dry run: {DRY_RUN}")
        page, deleted, skipped = load_state(key)
        print(f"Resuming at page {page}. Deleted {deleted}, skipped {skipped}")

        while True:
            posts = get_posts_in_range(date1, date2, page)
            if not posts:
                print(f"Done. Total deleted {deleted}, skipped {skipped}")
                break

            print(f"Page {page} | Fetched {len(posts)} posts")
            for p in posts:
                pid = p["id"]
                if delete_post_if_allowed(pid, date1, date2):
                    deleted += 1
                else:
                    skipped += 1
                save_state(page, deleted, skipped, key)
                time.sleep(SLEEP_BETWEEN_DELETES)

            page += 1
            save_state(page, deleted, skipped, key)
            print(f"Completed page {page - 1}. Deleted {deleted}, skipped {skipped}\n")

        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)

    except KeyboardInterrupt:
        print("Interrupted. Progress saved.")
    except Exception as e:
        print(f"Unexpected error: {e}")
    finally:
        release_lock()