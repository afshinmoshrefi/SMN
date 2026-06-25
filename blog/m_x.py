# m_x.py
# Post TradeWave opportunities to X (Twitter) via Publer API (no RSS).
# - Upload thumbnail -> get media_id
# - Publish photo post to X via Publer
# - Poll job -> log post metadata for de-dupe/audit
# - Plain functions; easy to modify.

import os
import re
import json
import time
import random
import datetime
from datetime import timezone
import requests
import sys
from urllib.parse import urljoin, urlparse, unquote

# Make sure we can import your app modules (adjust if needed)
sys.path.insert(0, '/home/flask')

# TradeWave app imports
from thumbnail_renderer import create_socialmedia_thumbnail
from get_top10_data import load_top10
import config

# -------------------------------------------------------------------------------------
# Publer API config
# -------------------------------------------------------------------------------------

PUBLER_API_BASE      = "https://app.publer.com/api/v1"
PUBLER_API_KEY       = getattr(config, "PUBLER_API_KEY", "")
PUBLER_WORKSPACE_ID  = getattr(config, "PUBLER_WORKSPACE_ID", "")
PUBLER_X_ACCOUNT_ID  = getattr(config, "PUBLER_X_ACCOUNT_ID", "")  # target account to post to

# Log folder (private)
LOG_ROOT = "/home/flask/blog/logs/publer"
os.makedirs(LOG_ROOT, exist_ok=True)

# -------------------------------------------------------------------------------------
# Utilities
# -------------------------------------------------------------------------------------

SITE_ROOT = getattr(config, "domain_root", "https://tradewave.ai/").rstrip("/") + "/"

def to_absolute(url_or_path: str) -> str:
    u = (url_or_path or "").strip()
    return u if u.startswith("http") else urljoin(SITE_ROOT, u)

def rfc2822_now():
    return datetime.datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")

def json_load(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def json_save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def get_json_log_path(network="x"):
    return os.path.join(LOG_ROOT, f"tradewave-{network}.json")

def publer_headers(json_ct=True):
    h = {
        "Authorization": f"Bearer-API {PUBLER_API_KEY}",
        "Publer-Workspace-Id": str(PUBLER_WORKSPACE_ID),
    }
    if json_ct:
        h["Content-Type"] = "application/json"
    return h

def filename_from_url(u: str, fallback="image.jpg"):
    try:
        name = os.path.basename(unquote(urlparse(u).path)) or fallback
        if "." not in name:
            return fallback
        return name
    except Exception:
        return fallback

# -------------------------------------------------------------------------------------
# De-dupe helpers (by slug and by symbol)
# -------------------------------------------------------------------------------------

def extract_symbol_from_slug(slug):
    """
    Works with slugs like:
      '10-year-custom-tradewave-report-linde-plc-lin-2025-09-08-to-2026-06-05'
    Returns uppercase symbol or None.
    """
    m = re.search(r"-([A-Za-z0-9\.]+)-\d{4}-\d{2}-\d{2}-to-\d{4}-\d{2}-\d{2}$", slug or "")
    if m:
        return m.group(1).upper()
    return None

def get_last_symbols_posted(network="x", limit=10):
    log = json_load(get_json_log_path(network), [])
    if not log:
        return []
    symbols = []
    seen = set()
    for entry in reversed(log):
        sym = (entry.get("symbol") or extract_symbol_from_slug(entry.get("slug", "")) or "").upper().strip()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        symbols.append(sym)
        if len(symbols) >= limit:
            break
    return symbols

def is_already_posted_by_slug(network, slug_abs):
    log = json_load(get_json_log_path(network), [])
    return any((entry.get("slug") or "").strip() == slug_abs for entry in log)

def is_already_posted_by_symbol(network, symbol):
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return False
    log = json_load(get_json_log_path(network), [])
    for e in log:
        if (e.get("symbol") or "").upper().strip() == symbol:
            return True
        s2 = (extract_symbol_from_slug(e.get("slug", "")) or "").upper().strip()
        if s2 == symbol:
            return True
    return False

# -------------------------------------------------------------------------------------
# Publer API helpers
# -------------------------------------------------------------------------------------

def publer_list_accounts():
    """Return list of accounts in the workspace (useful to fetch the X account id)."""
    r = requests.get(f"{PUBLER_API_BASE}/accounts", headers=publer_headers(json_ct=False), timeout=30)
    r.raise_for_status()
    return r.json()

def publer_upload_image_from_url(image_url: str, in_library=True):
    """
    Download the image and upload to Publer /media (direct file upload).
    Returns media_id.
    """
    # 1) fetch image
    resp = requests.get(image_url, timeout=60, stream=True)
    resp.raise_for_status()
    fname = filename_from_url(image_url, "image.jpg")
    # guess content-type if missing
    ctype = resp.headers.get("Content-Type", "image/jpeg")
    content = resp.content

    # 2) upload to /media
    files = {"file": (fname, content, ctype)}
    data = {"in_library": "true" if in_library else "false"}
    # Note: /media expects multipart/form-data, do NOT send application/json
    r = requests.post(
        f"{PUBLER_API_BASE}/media",
        headers=publer_headers(json_ct=False),
        files=files,
        data=data,
        timeout=60,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Publer /media upload failed: {r.status_code} {r.text}")
    payload = r.json()
    media_id = payload.get("id")
    if not media_id:
        raise RuntimeError(f"Publer /media upload returned no media id: {payload}")
    return media_id

def publer_publish_x_photo(text: str, media_ids: list, x_account_id: str):
    """
    Immediately publish a PHOTO post to X via Publer.
    Returns job_id for polling. We also try to read created post ids after polling.
    """
    body = {
        "bulk": {
            "state": "scheduled",  # immediate publish when scheduled_at omitted
            "posts": [
                {
                    "networks": {
                        "twitter": {
                            "type": "photo",
                            "text": text,
                            "media": [{"id": mid, "type": "photo"} for mid in media_ids],
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
        f"{PUBLER_API_BASE}/posts/schedule/publish",
        headers=publer_headers(json_ct=True),
        json=body,
        timeout=60,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Publer create post failed: {r.status_code} {r.text}")
    data = r.json()
    job_id = (data.get("data") or {}).get("job_id") or data.get("job_id")
    if not job_id:
        raise RuntimeError(f"Publer create post returned no job_id: {data}")
    return job_id

def publer_poll_job(job_id: str, timeout_sec=120, interval=2):
    """
    Poll /job_status/{job_id} until complete or timeout.
    Returns the final JSON (including payload/results).
    """
    url = f"{PUBLER_API_BASE}/job_status/{job_id}"
    deadline = time.time() + timeout_sec
    while True:
        r = requests.get(url, headers=publer_headers(json_ct=False), timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"Publer job_status failed: {r.status_code} {r.text}")
        data = r.json()
        # normalize a bit
        status = None
        if isinstance(data, dict):
            status = (data.get("data") or {}).get("status") or data.get("status")
        if (status or "").lower() in ("complete", "completed", "success"):
            return data
        if (status or "").lower() in ("failed", "error"):
            raise RuntimeError(f"Publer job failed: {json.dumps(data)[:400]}")
        if time.time() > deadline:
            raise TimeoutError(f"Publer job {job_id} did not complete in {timeout_sec}s")
        time.sleep(interval)

def extract_created_post_ids_from_job(job_json):
    """
    Publer job responses vary by endpoint and batch size.
    This walks the whole structure (dicts/lists) and collects any
    post-like objects that expose an 'id'. We de-dupe and return strings.
    """
    ids = set()

    def visit(node):
        # Recurse through dicts/lists; pick up post ids where present.
        if isinstance(node, dict):
            # If this looks like a post-like object, capture its id.
            pid = node.get("id") or node.get("_id")
            # Heuristic: avoid grabbing job ids by requiring some post-ish keys.
            if pid and any(k in node for k in ("provider", "network", "account_id", "social", "created_at", "scheduled_at", "status")):
                ids.add(str(pid))

            # Recurse into common containers
            for k in ("posts", "post", "result", "results", "payload", "data", "items"):
                if k in node:
                    visit(node[k])

            # Also recurse into any other dict values (safety)
            for v in node.values():
                if isinstance(v, (dict, list)):
                    visit(v)

        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(job_json)
    return list(ids)
    

def publer_delete_posts(post_ids):
    """
    Delete posts by publer post id(s).
    Returns list of deleted ids (best-effort).
    """
    if not post_ids:
        return []
    params = [("post_ids", pid) for pid in post_ids]
    r = requests.delete(
        f"{PUBLER_API_BASE}/posts",
        headers=publer_headers(json_ct=False),
        params=params,
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Publer delete failed: {r.status_code} {r.text}")
    data = r.json()
    return data.get("deleted_ids", [])

# -------------------------------------------------------------------------------------
# Selection helper (avoid recently posted symbols)
# -------------------------------------------------------------------------------------

def pick_opportunity_for_x(dfd, recent_syms, top_k_per_group=5):
    for fg in list(dfd.keys()):
        df = dfd[fg]
        if df is None or df.shape[0] == 0:
            continue
        upto = min(top_k_per_group, df.shape[0])
        for r in range(upto):
            sym = str(df.iloc[r]['Symbol']).upper().strip()
            if sym not in recent_syms:
                return fg, r
    return None

# -------------------------------------------------------------------------------------
# Main driver
# -------------------------------------------------------------------------------------

if __name__ == '__main__':
    # Hashtags or any extra text you want
    hashtags = '#FinancialAnalyst #MarketAnalysis #traders #investors #Stocks'

    # Load today's Top 10 (dfd is dict: fg -> DataFrame of opps)
    filename = config.today_top10_data
    action, dfd = load_top10(filename)

    # Guard: no opportunities available
    valid_groups = [g for g in dfd.keys() if dfd[g] is not None and dfd[g].shape[0] > 0]
    if not valid_groups:
        raise RuntimeError("No opportunities found in today's Top 10.")

    # Avoid duplicates by symbol (read recent X posts we logged)
    recent_syms = set(get_last_symbols_posted("x", limit=10))

    # Pick a candidate not in recent symbols; if none found, fallback to random
    picked = pick_opportunity_for_x(dfd, recent_syms, top_k_per_group=5)
    if not picked:
        fg = random.choice(valid_groups)
        r  = random.randint(0, dfd[fg].shape[0]-1)
    else:
        fg, r = picked

    # Pull row fields
    row       = dfd[fg].iloc[r]
    symbol    = str(row['Symbol']).upper()
    date1     = row['Date']
    date2     = row['Date2']
    days_hold = int(row['DaysOut'])
    _pt       = str(row['post_title'])
    _m        = re.match(r"(\d+)", _pt)
    years     = _m.group(1) if _m else "10"
    trade_dir = row['Direction']
    avg_gain  = row['Avg Profit']
    slug_path = row['opp_slug']          # canonical slug/path or absolute URL
    slug_abs  = to_absolute(slug_path)

    # De-dupe by slug (don’t post same opp twice)
    if is_already_posted_by_slug("x", slug_abs):
        print(f"[SKIP] Already posted to X: {slug_abs}")
        sys.exit(0)

    # Create X-optimized thumbnail (returns local_path, public_url)
    title_pre = f"{years}-Year "
    category  = getattr(config, "category_date_range_report", "date_range_report")
    tn_path, tn_url = create_socialmedia_thumbnail(
        "x", int(fg), date1, symbol, days_hold, trade_dir, avg_gain, years, title_pre, category
    )

    # Compose short message (X caption)
    message = f"{symbol} {date1}→{date2} | {years}Y • {days_hold}d\n{slug_abs}\n{hashtags}".strip()

    # 1) Upload thumbnail to Publer -> media_id
    media_id = publer_upload_image_from_url(tn_url, in_library=False)

    # 2) Publish a PHOTO post to X
    job_id = publer_publish_x_photo(message, [media_id], PUBLER_X_ACCOUNT_ID)

    # 3) Poll until done; try to grab created post IDs
    job_json = publer_poll_job(job_id, timeout_sec=180, interval=3)
    created_ids = extract_created_post_ids_from_job(job_json)

    # 4) Log to JSON
    log_path = get_json_log_path("x")
    log = json_load(log_path, [])
    log.append({
        "network":  "x",
        "slug":     slug_abs,
        "symbol":   symbol,
        "datetime": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "message":  message,
        "img_url":  tn_url,
        "link":     slug_abs,
        "publer_job_id": job_id,
        "publer_post_ids": created_ids,
    })
    json_save(log_path, log)

    print(f"[OK] Posted to X via Publer. Job: {job_id}  Post IDs: {created_ids}")
