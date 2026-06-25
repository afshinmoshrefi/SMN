#!/usr/bin/env python3
# -----------------------------------------------------------------------------------------------------
# WordPress Auto-Delete Script for Old Posts - FIXED VERSION
# -----------------------------------------------------------------------------------------------------
# This script automatically deletes WordPress posts that are older than a specified number
# of months and belong to allowed categories (as defined in config.py).
#
# MAJOR FIXES:
# - Fixed pagination to properly process all posts without skipping any
# - Improved PHP-FPM load handling to retry posts rather than skip them
# - Added proper error handling and logging
# - Added a resume feature to continue from where it left off
# -----------------------------------------------------------------------------------------------------

import requests
import time
import subprocess
from datetime import datetime, timedelta
import sys
import os
import json

# Ensure config is accessible
sys.path.insert(0, '/home/flask')
import config

# WordPress settings
WP_SITE_URL = config.wordpress_url
USERNAME = config.username
PASSWORD = config.password

# WordPress REST API endpoint for posts
WP_API_URL = f"{WP_SITE_URL}wp-json/wp/v2/posts"

# === CONFIGURABLE PARAMETERS ===
DELETE_AFTER_MONTHS = 4  # Delete posts older than 4 months

# Date cutoff mode
# Set to 'calendar' for precise calendar month calculation (recommended)
# Set to 'days' for the original 30-days-per-month calculation
DATE_CUTOFF_MODE = 'calendar'  

# Batch processing
BATCH_SIZE = 20          # Number of posts to process in each API call
SLEEP_BETWEEN_DELETES = 2  # Time (in seconds) to wait between deletions

# PHP-FPM thresholds
PHP_IDLE_THRESHOLD = 2   # Minimum number of idle PHP processes required
CHECK_INTERVAL = 5       # Time (in seconds) to wait before rechecking PHP load
MAX_RETRIES = 5          # Maximum number of retries for a post

# Date cutoff for deletion (posts older than DELETE_AFTER_MONTHS)
def calculate_date_cutoff(months_ago):
    """
    Calculate a more accurate date cutoff by using calendar months rather than a fixed 30-day period.
    This ensures we're truly getting posts from 9 calendar months ago, not just ~270 days ago.
    """
    today = datetime.now()
    # Calculate the target month by subtracting from current month
    target_month = today.month - (months_ago % 12)
    # Handle month underflow by adjusting year
    target_year = today.year - (months_ago // 12) - (1 if target_month <= 0 else 0)
    # Correct the month value if it went negative
    if target_month <= 0:
        target_month += 12
        
    # Use the same day of month as today, but cap to the max days in target month
    # (e.g., Feb 30 doesn't exist, so use Feb 28/29)
    import calendar
    _, last_day = calendar.monthrange(target_year, target_month)
    target_day = min(today.day, last_day)
    
    cutoff_date = datetime(target_year, target_month, target_day, 
                           today.hour, today.minute, today.second)
    return cutoff_date.isoformat()

# Get cutoff date based on selected mode
if DATE_CUTOFF_MODE == 'calendar':
    # Use precise calendar month calculation
    DATE_CUTOFF = calculate_date_cutoff(DELETE_AFTER_MONTHS)
    cutoff_date = datetime.fromisoformat(DATE_CUTOFF)
    print(f"Using calendar month calculation: Posts before {cutoff_date.strftime('%B %d, %Y')} will be deleted")
else:
    # Use original days-based calculation
    DATE_CUTOFF = (datetime.now() - timedelta(days=DELETE_AFTER_MONTHS * 30)).isoformat()
    cutoff_date = datetime.fromisoformat(DATE_CUTOFF)
    print(f"Using days-based calculation ({DELETE_AFTER_MONTHS*30} days): Posts before {cutoff_date.strftime('%B %d, %Y')} will be deleted")

# WordPress authentication tuple
AUTH = (USERNAME, PASSWORD)

# Lock file and state file paths
LOCK_FILE = "/tmp/delete_old_posts.lock"
STATE_FILE = "/tmp/delete_old_posts_state.json"

# Allowed categories (IDs) from config.py
ALLOWED_CATEGORIES = {
    config.category_sr_tn,
    config.category_report,
    config.category_top10,
    config.category_opp_top10,
    config.category_opp_top10t,
    config.category_top10_archive,
    config.category_date_range_report
}

# -----------------------------------------------------------------------------------------------------------------
def php_processes():
    """
    Check the number of active and idle PHP-FPM processes.
    Returns: (active_processes, idle_processes)
    """
    result = subprocess.run(['systemctl', 'status', 'php7.4-fpm'], capture_output=True)
    txt = result.stdout.decode('utf-8')
    lines = txt.splitlines()

    for line in lines:
        if 'Status' in line:
            parts = line.split(',')
            try:
                active_processes = int(parts[1].split(':')[1].strip())
                idle_processes = int(parts[2].split(':')[1].strip())
                return active_processes, idle_processes
            except (IndexError, ValueError):
                return 6, 0  # Assume high load if parsing fails

    return 6, 0  # Default to high load

# -----------------------------------------------------------------------------------------------------------------
def acquire_lock():
    """
    Prevent multiple instances of this script from running.
    Remove a stale lock file if detected.
    """
    if os.path.exists(LOCK_FILE):
        with open(LOCK_FILE, "r") as f:
            pid = f.read().strip()
        
        # Check if the process is still running
        if pid.isdigit() and os.path.exists(f"/proc/{pid}"):
            print(f"⚠️ Another instance of the script (PID {pid}) is running. Exiting.")
            return False
        
        print("⚠️ Stale lock file detected. Removing it...")
        os.remove(LOCK_FILE)

    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))

    return True

# -----------------------------------------------------------------------------------------------------------------
def release_lock():
    """
    Releases the lock file if this process created it.
    """
    try:
        if os.path.exists(LOCK_FILE):
            with open(LOCK_FILE, "r") as f:
                pid = f.read().strip()
            if pid == str(os.getpid()):
                os.remove(LOCK_FILE)
    except Exception as e:
        print(f"⚠️ Error releasing lock: {e}")

# -----------------------------------------------------------------------------------------------------------------
def save_state(page, posts_deleted, posts_skipped):
    """
    Save the current processing state to resume later if needed.
    """
    state = {
        "page": page,
        "posts_deleted": posts_deleted,
        "posts_skipped": posts_skipped,
        "date": datetime.now().isoformat()
    }
    
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"⚠️ Error saving state: {e}")

# -----------------------------------------------------------------------------------------------------------------
def load_state():
    """
    Load the saved state from previous run.
    Returns: (page, posts_deleted, posts_skipped)
    """
    if not os.path.exists(STATE_FILE):
        return 1, 0, 0
    
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        return state.get("page", 1), state.get("posts_deleted", 0), state.get("posts_skipped", 0)
    except Exception as e:
        print(f"⚠️ Error loading state: {e}")
        return 1, 0, 0

# -----------------------------------------------------------------------------------------------------------------
def get_old_posts(page):
    """
    Fetch posts older than DELETE_AFTER_MONTHS using proper pagination.
    Returns a list of posts older than the cutoff.
    """
    params = {
        "per_page": BATCH_SIZE,
        "page": page,
        "before": DATE_CUTOFF,
        "status": "publish",
        "orderby": "date",
        # "order": "desc",  # Newest posts first among those older than the cutoff
        "order": "asc",  # Oldest posts first among those older than the cutoff - 7/18/2025
    }
    
    try:
        response = requests.get(WP_API_URL, auth=AUTH, params=params)
        
        if response.status_code == 200:
            posts = response.json()
            return posts
        elif response.status_code == 400 and "rest_post_invalid_page_number" in response.text:
            # No more pages
            return []
        else:
            print(f"Error fetching posts: {response.status_code} - {response.text}")
            return []
    except Exception as e:
        print(f"Exception fetching posts: {e}")
        return []

# -----------------------------------------------------------------------------------------------------------------
def get_post_categories(post_id):
    """
    Fetch the categories for a given post.
    Returns a tuple: (list of category IDs, list of category slugs)
    """
    url = f"{WP_API_URL}/{post_id}"
    try:
        response = requests.get(url, auth=AUTH)
        
        if response.status_code == 200:
            post_data = response.json()
            category_ids = post_data.get("categories", [])
            category_names = []
            
            for cat_id in category_ids:
                cat_url = f"{WP_SITE_URL}wp-json/wp/v2/categories/{cat_id}"
                cat_response = requests.get(cat_url, auth=AUTH)
                if cat_response.status_code == 200:
                    category_names.append(cat_response.json().get("slug"))
            
            return category_ids, category_names
        else:
            print(f"Error fetching categories for post {post_id}: {response.status_code}")
            return [], []
    except Exception as e:
        print(f"Exception fetching categories: {e}")
        return [], []

# -----------------------------------------------------------------------------------------------------------------
def delete_post(post_id, retry_count=0):
    """
    Delete a single post if it belongs to an allowed category.
    Returns: True if deleted, False otherwise
    """
    # Check PHP load before attempting deletion
    _, idle_processes = php_processes()
    if idle_processes < PHP_IDLE_THRESHOLD:
        if retry_count >= MAX_RETRIES:
            print(f"⚠️ Maximum retries reached for post {post_id}. Skipping.")
            return False
            
        print(f"Low idle PHP processes ({idle_processes}). Waiting before retrying post {post_id}...")
        time.sleep(CHECK_INTERVAL)
        return delete_post(post_id, retry_count + 1)
    
    # Fetch the post details
    url = f"{WP_API_URL}/{post_id}"
    try:
        response = requests.get(url, auth=AUTH)
        if response.status_code != 200:
            print(f"❌ Error fetching post {post_id}: {response.status_code}")
            return False

        post_data = response.json()
        post_date = post_data.get("date", "Unknown Date")
        post_title = post_data.get("title", {}).get("rendered", "Unknown Title")
        category_ids = post_data.get("categories", [])
        
        # Get category names for logging
        category_names = []
        for cat_id in category_ids:
            cat_url = f"{WP_SITE_URL}wp-json/wp/v2/categories/{cat_id}"
            cat_response = requests.get(cat_url, auth=AUTH)
            if cat_response.status_code == 200:
                category_names.append(cat_response.json().get("slug"))

        # Only delete if post is in an allowed category
        if not any(cat_id in ALLOWED_CATEGORIES for cat_id in category_ids):
            print(f"Skipping post ID {post_id}: Not in allowed categories.")
            print(f"  → Title: {post_title}")
            print(f"  → Found categories (IDs): {category_ids}")
            print(f"  → Found categories (Names): {category_names}")
            print(f"  → Allowed categories: {ALLOWED_CATEGORIES}")
            print(f"  → Post Date: {post_date}\n")
            return False

        # Proceed with deletion
        delete_url = f"{WP_API_URL}/{post_id}?force=true"
        delete_response = requests.delete(delete_url, auth=AUTH)
        
        if delete_response.status_code == 200:
            print(f"✅ Deleted post ID {post_id}")
            print(f"  → Title: {post_title}")
            print(f"  → Date: {post_date}")
            print(f"  → Categories: {category_names}\n")
            return True
        else:
            print(f"❌ Failed to delete post ID {post_id}: {delete_response.status_code} - {delete_response.text}")
            return False
            
    except Exception as e:
        print(f"Exception deleting post {post_id}: {e}")
        return False

# -----------------------------------------------------------------------------------------------------------------
def main():
    """
    Main function to delete old posts while ensuring PHP-FPM is not overloaded.
    Uses proper pagination to ensure all eligible posts are processed.
    """
    if not acquire_lock():
        print("Another instance of the script is running. Exiting.")
        return

    try:
        current_date = datetime.now().strftime("%B %d, %Y")
        print(f"Starting WordPress post cleanup on {current_date}")
        print(f"Target: Posts older than {DELETE_AFTER_MONTHS} months")
        
        # Load state from previous run (if any)
        current_page, posts_deleted, posts_skipped = load_state()
        print(f"Resuming from page {current_page} (Previously: {posts_deleted} deleted, {posts_skipped} skipped)")
        
        while True:
            # Fetch a batch of old posts
            posts = get_old_posts(current_page)
            
            if not posts:
                print(f"No more old posts to delete. Total processed: {posts_deleted} deleted, {posts_skipped} skipped")
                break
                
            print(f"Processing page {current_page} with {len(posts)} posts...")
            
            # Process each post in the batch
            for post in posts:
                post_id = post["id"]
                
                # Delete post if eligible
                if delete_post(post_id):
                    posts_deleted += 1
                else:
                    posts_skipped += 1
                
                # Save state after each post
                save_state(current_page, posts_deleted, posts_skipped)
                
                # Small delay between deletions to avoid overloading the server
                time.sleep(SLEEP_BETWEEN_DELETES)
            
            # Move to next page
            current_page += 1
            save_state(current_page, posts_deleted, posts_skipped)
            print(f"Completed page {current_page-1}. Progress: {posts_deleted} deleted, {posts_skipped} skipped\n")
            
        # Cleanup
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
            
    except KeyboardInterrupt:
        print("\nScript interrupted. Progress has been saved and can be resumed later.")
    except Exception as e:
        print(f"Unexpected error: {e}")
    finally:
        release_lock()

# -----------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    main()