#!/usr/bin/env python3
"""
m_daily_ai_pick_social.py
==========================
Posts today's AI pick to X (via Publer) and Facebook (direct Graph API).
Reads from featured_history.json, generates a price chart with 60-day projection,
and posts to both platforms.

De-duplication: logs each post to logs/publer/daily-ai-pick-social.json.
Won't post the same featured_date twice per platform.

Usage:
    python m_daily_ai_pick_social.py           # post to all platforms
    python m_daily_ai_pick_social.py --test    # print message, don't post
    python m_daily_ai_pick_social.py --force   # bypass already-posted check
    python m_daily_ai_pick_social.py --x-only  # post to X only
    python m_daily_ai_pick_social.py --fb-only # post to Facebook only

Crontab:
    15 7 * * 1-5 cd /home/flask/blog && python m_daily_ai_pick_social.py >> /home/flask/blog/logs/daily_ai_pick_social.log 2>&1
"""

import os
import json
import sys
import time
import argparse
from datetime import date, datetime

sys.path.insert(0, '/home/flask')
sys.path.insert(0, '/home/flask/blog')
import config
import requests

# =============================================================================
# Configuration
# =============================================================================

FEATURED_HISTORY_FILE = "/home/flask/blog/featured_history.json"
LOG_DIR = "/home/flask/blog/logs/publer"
LOG_FILE = os.path.join(LOG_DIR, "daily-ai-pick-social.json")

# X (via Publer)
PUBLER_API_BASE = "https://app.publer.com/api/v1"
PUBLER_API_KEY = getattr(config, "PUBLER_API_KEY", "")
PUBLER_WORKSPACE_ID = getattr(config, "PUBLER_WORKSPACE_ID", "")
PUBLER_X_ACCOUNT_ID = getattr(config, "PUBLER_X_ACCOUNT_ID", "")

# Facebook (direct Graph API)
FACEBOOK_ACCESS_TOKEN = getattr(config, "FACEBOOK_ACCESS_TOKEN", "")
FACEBOOK_PAGE_ID = getattr(config, "FACEBOOK_PAGE_ID", "")

DOMAIN_ROOT = config.domain_root.rstrip("/")
SCORECARD_URL = "%s/scorecard" % DOMAIN_ROOT

PE_YEAR_LABELS = {
    'pe0': 'election',
    'pe1': 'post-election',
    'pe2': 'midterm',
    'pe3': 'pre-election',
}


# =============================================================================
# Publer helpers (X posting)
# =============================================================================

def publer_headers(json_ct=True):
    h = {
        "Authorization": "Bearer-API %s" % PUBLER_API_KEY,
        "Publer-Workspace-Id": str(PUBLER_WORKSPACE_ID),
    }
    if json_ct:
        h["Content-Type"] = "application/json"
    return h


def publer_upload_image(image_path):
    """Upload a local image to Publer. Returns media_id."""
    with open(image_path, 'rb') as f:
        content = f.read()
    ext = image_path.lower().split('.')[-1]
    ctype = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png'}.get(ext, 'image/jpeg')
    fname = os.path.basename(image_path)

    files = {"file": (fname, content, ctype)}
    data = {"in_library": "false"}
    r = requests.post(
        "%s/media" % PUBLER_API_BASE,
        headers=publer_headers(json_ct=False),
        files=files,
        data=data,
        timeout=60,
    )
    if r.status_code >= 400:
        raise RuntimeError("Publer media upload failed: %s %s" % (r.status_code, r.text))
    media_id = r.json().get("id")
    if not media_id:
        raise RuntimeError("Publer media upload returned no id: %s" % r.json())
    return media_id


def publer_publish_photo(text, media_id, x_account_id):
    """Publish a photo post to X via Publer. Returns job_id."""
    body = {
        "bulk": {
            "state": "scheduled",
            "posts": [
                {
                    "networks": {
                        "twitter": {
                            "type": "photo",
                            "text": text,
                            "media": [{"id": media_id, "type": "photo"}],
                        }
                    },
                    "accounts": [
                        {"id": str(x_account_id)}
                    ],
                }
            ]
        }
    }
    r = requests.post(
        "%s/posts/schedule/publish" % PUBLER_API_BASE,
        headers=publer_headers(json_ct=True),
        json=body,
        timeout=60,
    )
    if r.status_code >= 400:
        raise RuntimeError("Publer create post failed: %s %s" % (r.status_code, r.text))
    data = r.json()
    job_id = (data.get("data") or {}).get("job_id") or data.get("job_id")
    if not job_id:
        raise RuntimeError("Publer create post returned no job_id: %s" % data)
    return job_id


def publer_poll_job(job_id, timeout_sec=120, interval=3):
    """Poll job until complete."""
    url = "%s/job_status/%s" % (PUBLER_API_BASE, job_id)
    deadline = time.time() + timeout_sec
    while True:
        r = requests.get(url, headers=publer_headers(json_ct=False), timeout=30)
        if r.status_code >= 400:
            raise RuntimeError("Publer job_status failed: %s %s" % (r.status_code, r.text))
        data = r.json()
        status = None
        if isinstance(data, dict):
            status = (data.get("data") or {}).get("status") or data.get("status")
        if (status or "").lower() in ("complete", "completed", "success"):
            return data
        if (status or "").lower() in ("failed", "error"):
            raise RuntimeError("Publer job failed: %s" % str(data)[:400])
        if time.time() > deadline:
            raise TimeoutError("Publer job %s did not complete in %ds" % (job_id, timeout_sec))
        time.sleep(interval)


# =============================================================================
# Facebook helpers (direct Graph API)
# =============================================================================

def facebook_post_photo(message, image_path, page_id, access_token):
    """Post a photo with message to Facebook page via direct file upload. Returns post_id."""
    url = "https://graph.facebook.com/%s/photos" % page_id
    with open(image_path, 'rb') as f:
        files = {"source": (os.path.basename(image_path), f, "image/jpeg")}
        data = {
            "message": message,
            "access_token": access_token,
        }
        r = requests.post(url, data=data, files=files, timeout=60)
    if r.status_code != 200:
        raise RuntimeError("Facebook post failed: %s %s" % (r.status_code, r.text))
    return r.json().get("id", "")


# =============================================================================
# Data & de-dupe
# =============================================================================

def load_history():
    try:
        with open(FEATURED_HISTORY_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return []


def get_todays_pick(history):
    today_str = date.today().isoformat()
    for entry in history:
        if entry.get('featured_date') == today_str:
            return entry
    return None


def load_post_log():
    os.makedirs(LOG_DIR, exist_ok=True)
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_post_log(log):
    with open(LOG_FILE, 'w') as f:
        json.dump(log, f, indent=2)


def already_posted_today(log, platform):
    today_str = date.today().isoformat()
    return any(
        entry.get('featured_date') == today_str and entry.get('platform') == platform
        for entry in log
    )


# =============================================================================
# Build post text
# =============================================================================

def build_post_text(pick):
    symbol = pick['symbol']
    direction = 'Long' if pick['direction'] == 'l' else 'Short'
    win_prob = '%.1f' % (pick['win_prob'] * 100)
    pred_return = '%.1f' % pick['pred_return']
    days = pick['daysOut']

    years_str = pick.get('years', '10')
    if pick.get('mode') == 'pe' and '-' in years_str:
        pe_code = years_str.split('-')[0].lower()
        cycle_label = PE_YEAR_LABELS.get(pe_code, 'cycle')
        years_display = "%s %s" % (years_str.split('-')[-1], cycle_label)
    else:
        years_display = years_str

    text = (
        "Today's AI Pick: $%s %s\n"
        "\n"
        "%s%% win probability | +%s%% projected in %d days\n"
        "Based on %s years of seasonal data\n"
        "\n"
        "Full track record: %s\n"
        "\n"
        "#SeasonalTrading #TradeWave #Stocks #MarketAnalysis"
    ) % (symbol, direction, win_prob, pred_return, days, years_display, SCORECARD_URL)

    return text


# =============================================================================
# Chart generation
# =============================================================================

def generate_price_chart(pick):
    """Generate dark price chart with 60-day projection. Returns file path."""
    from article_images import create_article_images
    results = create_article_images(
        "facebook",
        pick['resource_id'],
        pick['date'],
        pick['symbol'],
        str(pick['daysOut']),
        pick.get('years', '10'),
        theme="dark",
        mode="social",
    )
    if results:
        return results[0]['path']
    return None


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test', action='store_true', help='Print message, do not post')
    parser.add_argument('--force', action='store_true', help='Bypass already-posted check')
    parser.add_argument('--x-only', action='store_true', help='Post to X only')
    parser.add_argument('--fb-only', action='store_true', help='Post to Facebook only')
    args = parser.parse_args()

    do_x = not args.fb_only
    do_fb = not args.x_only

    today_str = date.today().isoformat()
    print("[SOCIAL] %s" % today_str)

    # Load pick
    history = load_history()
    pick = get_todays_pick(history)
    if not pick:
        print("  No pick for today. Run generate_home_page.py first.")
        return

    post_log = load_post_log()
    text = build_post_text(pick)
    print("  Pick: $%s %s" % (pick['symbol'], 'Long' if pick['direction'] == 'l' else 'Short'))
    print("  Message:\n%s" % text)

    if args.test:
        print("\n  [TEST MODE] Not posting.")
        return

    # Generate chart (shared by both platforms)
    print("  Generating price chart...")
    chart_path = generate_price_chart(pick)
    if not chart_path:
        print("  ERROR: Failed to generate chart.")
        return
    print("  Chart: %s" % chart_path)

    # ── Post to X via Publer ──
    if do_x:
        if not args.force and already_posted_today(post_log, 'x'):
            print("  [X] Already posted today, skipping.")
        elif not PUBLER_API_KEY or not PUBLER_X_ACCOUNT_ID:
            print("  [X] Publer credentials not configured, skipping.")
        else:
            print("  [X] Uploading chart...")
            try:
                media_id = publer_upload_image(chart_path)
                job_id = publer_publish_photo(text, media_id, PUBLER_X_ACCOUNT_ID)
                print("  [X] Job ID: %s" % job_id)
                job_json = publer_poll_job(job_id, timeout_sec=120, interval=3)
                print("  [X] Job response: %s" % str(job_json)[:500])
                # Check for errors in payload
                for item in job_json.get('payload', []):
                    if item.get('type') == 'error':
                        raise RuntimeError(item.get('failure', {}).get('message', 'unknown'))
                # Verify the post actually landed - check for post_id in payload
                payload = job_json.get('payload', [])
                post_ids = [p.get('post_id') for p in payload if p.get('post_id')] if payload else []
                if not post_ids:
                    print("  [X] WARNING: Publer returned 'complete' but no post_id. Post may not have landed.")
                    print("  [X] Full response: %s" % json.dumps(job_json, indent=2)[:1000])
                else:
                    print("  [X] Post ID: %s" % post_ids[0])
                print("  [X] Posted successfully.")
                post_log.append({
                    "featured_date": today_str,
                    "platform": "x",
                    "symbol": pick['symbol'],
                    "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "publer_job_id": job_id,
                })
                save_post_log(post_log)
            except Exception as e:
                print("  [X] ERROR: %s" % e)

    # ── Post to Facebook (direct Graph API) ──
    if do_fb:
        if not args.force and already_posted_today(post_log, 'facebook'):
            print("  [FB] Already posted today, skipping.")
        elif not FACEBOOK_ACCESS_TOKEN or not FACEBOOK_PAGE_ID:
            print("  [FB] Facebook credentials not configured, skipping.")
        else:
            print("  [FB] Posting...")
            try:
                post_id = facebook_post_photo(text, chart_path, FACEBOOK_PAGE_ID, FACEBOOK_ACCESS_TOKEN)
                print("  [FB] Posted successfully. Post ID: %s" % post_id)
                post_log.append({
                    "featured_date": today_str,
                    "platform": "facebook",
                    "symbol": pick['symbol'],
                    "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "fb_post_id": post_id,
                })
                save_post_log(post_log)
            except Exception as e:
                print("  [FB] ERROR: %s" % e)

    print("  Done!")


if __name__ == "__main__":
    main()
