#!/usr/bin/env python3
"""
m_daily_ai_pick_to_x.py
========================
Posts today's AI pick to X (@TradeWaveHQ) via Publer.
Reads from featured_history.json, same data source as the homepage and email.

De-duplication: logs each post to logs/publer/daily-ai-pick-x.json.
Won't post the same featured_date twice.

Usage:
    python m_daily_ai_pick_to_x.py           # post today's pick
    python m_daily_ai_pick_to_x.py --test    # print message, don't post
    python m_daily_ai_pick_to_x.py --force   # bypass already-posted check

Crontab (runs after generate_home_page.py picks the daily AI pick):
    15 7 * * 1-5 cd /home/flask/blog && python m_daily_ai_pick_to_x.py >> /home/flask/blog/logs/daily_ai_pick_x.log 2>&1
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
LOG_FILE = os.path.join(LOG_DIR, "daily-ai-pick-x.json")

PUBLER_API_BASE = "https://app.publer.com/api/v1"
PUBLER_API_KEY = getattr(config, "PUBLER_API_KEY", "")
PUBLER_WORKSPACE_ID = getattr(config, "PUBLER_WORKSPACE_ID", "")
PUBLER_X_ACCOUNT_ID = getattr(config, "PUBLER_X_ACCOUNT_ID", "")

DOMAIN_ROOT = config.domain_root.rstrip("/")
SCORECARD_URL = "%s/scorecard" % DOMAIN_ROOT

PE_YEAR_LABELS = {
    'pe0': 'election',
    'pe1': 'post-election',
    'pe2': 'midterm',
    'pe3': 'pre-election',
}


# =============================================================================
# Publer helpers (reused from m_x.py pattern)
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
    # Determine content type
    ext = image_path.lower().split('.')[-1]
    ctype = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'svg': 'image/svg+xml'}.get(ext, 'image/jpeg')
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


def already_posted_today(log):
    today_str = date.today().isoformat()
    return any(entry.get('featured_date') == today_str for entry in log)


# =============================================================================
# Build post text
# =============================================================================

def build_post_text(pick):
    symbol = pick['symbol']
    direction = 'Long' if pick['direction'] == 'l' else 'Short'
    win_prob = '%.1f' % (pick['win_prob'] * 100)
    pred_return = '%.1f' % pick['pred_return']
    days = pick['daysOut']

    # Build wave viewer URL with production domain
    pattern_param = pick.get('pattern_param', '')
    wave_url = '%s/wave-viewer?o=%s' % (DOMAIN_ROOT, pattern_param)

    # Years label
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
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test', action='store_true', help='Print message, do not post')
    parser.add_argument('--force', action='store_true', help='Bypass already-posted check')
    args = parser.parse_args()

    today_str = date.today().isoformat()
    print("[X POST] %s" % today_str)

    # Check credentials
    if not PUBLER_API_KEY or not PUBLER_X_ACCOUNT_ID:
        print("  ERROR: Publer credentials not configured.")
        return

    # Load pick
    history = load_history()
    pick = get_todays_pick(history)
    if not pick:
        print("  No pick for today. Run generate_home_page.py first.")
        return

    # De-dupe check
    post_log = load_post_log()
    if not args.force and not args.test and already_posted_today(post_log):
        print("  Already posted today, skipping. Use --force to repost.")
        return

    # Build post
    text = build_post_text(pick)
    print("  Pick: $%s %s" % (pick['symbol'], 'Long' if pick['direction'] == 'l' else 'Short'))
    print("  Message:\n%s" % text)

    if args.test:
        print("\n  [TEST MODE] Not posting.")
        return

    # Generate price chart with 60-day projection
    print("  Generating price chart...")
    try:
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
        chart_path = results[0]['path'] if results else None
        if not chart_path or not os.path.exists(chart_path):
            print("  ERROR: Price chart not generated")
            return
        print("  Chart: %s" % chart_path)
    except Exception as e:
        print("  ERROR generating chart: %s" % e)
        return

    # Upload chart image
    print("  Uploading chart image...")
    try:
        media_id = publer_upload_image(chart_path)
        print("  Media ID: %s" % media_id)
    except Exception as e:
        print("  ERROR uploading image: %s" % e)
        return

    # Post via Publer
    print("  Publishing to X via Publer...")
    try:
        job_id = publer_publish_photo(text, media_id, PUBLER_X_ACCOUNT_ID)
        print("  Job ID: %s" % job_id)
        job_json = publer_poll_job(job_id, timeout_sec=120, interval=3)
        # Check for errors in payload
        payload = job_json.get('payload', [])
        for item in payload:
            if item.get('type') == 'error':
                raise RuntimeError("Post failed: %s" % item.get('failure', {}).get('message', 'unknown'))
        print("  Posted successfully.")
    except Exception as e:
        print("  ERROR posting: %s" % e)
        return

    # Log
    post_log.append({
        "featured_date": today_str,
        "symbol": pick['symbol'],
        "direction": pick['direction'],
        "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "message": text,
        "publer_job_id": job_id,
    })
    save_post_log(post_log)
    print("  Done!")


if __name__ == "__main__":
    main()
