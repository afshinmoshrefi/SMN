# m_dlvr.py
# Simple, readable utilities for posting TradeWave opportunities to social networks via dlvr.it RSS.
# - Per-network RSS feeds (X to start)
# - JSON logs for de-dupe and audit
# - Delete requests tracked in posts_to_delete.json (for manual cleanup on platforms that can't delete)
# - No classes. Plain functions. Easy to modify.

import os
import re
import json
import random
import datetime
from datetime import timezone
import xml.etree.ElementTree as ET
import sys

# Make sure we can import your app modules (adjust if needed)
sys.path.insert(0, '/home/flask')

# TradeWave app imports
from thumbnail_renderer import create_socialmedia_thumbnail
from get_top10_data import load_top10
import config

# -------------------------------------------------------------------------------------
# Paths / Network Config
# -------------------------------------------------------------------------------------

# Root folder for RSS feeds (public)
RSS_ROOT = "/var/www/html/wp-content/uploads/p/RSS"
# Root folder for JSON logs (private)
LOG_ROOT = "/home/flask/blog/logs/dlvr"

# Add more networks later by extending this dict.
NETWORKS = {
    "x": {
        "rss_filename": "tradewave-x.xml",
        "json_log":     "tradewave-x.json",
        # thumbnail_renderer size key for this network:
        "tn_key":       "x",        # we pass this to create_socialmedia_thumbnail(sm=...)
        # enclosure MIME (dlvr.it/X expects an image they can fetch)
        "enclosure_type": "image/jpeg"
    },
    # "facebook": { ... }, etc.
}

# -------------------------------------------------------------------------------------
# File helpers
# -------------------------------------------------------------------------------------

def ensure_dirs():
    """Ensure RSS and LOG directories exist."""
    os.makedirs(RSS_ROOT, exist_ok=True)
    os.makedirs(LOG_ROOT, exist_ok=True)

def get_rss_path(network):
    return os.path.join(RSS_ROOT, NETWORKS[network]["rss_filename"])

def get_json_log_path(network):
    return os.path.join(LOG_ROOT, NETWORKS[network]["json_log"])

def get_delete_log_path(network):
    return os.path.join(LOG_ROOT, f"{network}_posts_to_delete.json")

# -------------------------------------------------------------------------------------
# RSS helpers
# -------------------------------------------------------------------------------------

def ensure_rss_feed_exists(network):
    """Create or repair a bare RSS 2.0 channel if missing, empty, or invalid."""
    rss_path = get_rss_path(network)

    # If file missing or empty → initialize
    if not os.path.exists(rss_path) or os.path.getsize(rss_path) == 0:
        tree = _new_rss_tree(network)
        save_rss_feed(tree, rss_path)
        return

    # If file exists, verify it parses and has a <channel>
    try:
        tree = ET.parse(rss_path)
        root = tree.getroot()
        if root is None or root.find("channel") is None:
            raise ET.ParseError("RSS missing <channel>")
    except ET.ParseError:
        tree = _new_rss_tree(network)
        save_rss_feed(tree, rss_path)

def _new_rss_tree(network: str) -> ET.ElementTree:
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"TradeWave {network.upper()} Feed"
    ET.SubElement(channel, "link").text = "https://tradewave.ai/"
    ET.SubElement(channel, "description").text = f"Automated {network} posts for TradeWave"
    return ET.ElementTree(rss)

def parse_rss_feed(rss_path):
    """Open RSS and return (tree, channel_element). If invalid, rebuild fresh."""
    print('rss_path=', rss_path)
    try:
        tree = ET.parse(rss_path)
        channel = tree.getroot().find("channel")
        if channel is None:
            raise ET.ParseError("No <channel> in RSS")
        return tree, channel
    except ET.ParseError:
        # Infer network from filename and rebuild
        fname = os.path.basename(rss_path)
        network = next((k for k, v in NETWORKS.items() if v["rss_filename"] == fname), "x")
        tree = _new_rss_tree(network)
        save_rss_feed(tree, rss_path)
        return tree, tree.getroot().find("channel")

def save_rss_feed(tree, rss_path):
    tmp_path = rss_path + ".tmp"
    tree.write(tmp_path, encoding="utf-8", xml_declaration=True)
    os.replace(tmp_path, rss_path)  # atomic on POSIX

def rfc2822_now():
    # Proper RFC 2822 with timezone for RSS pubDate
    return datetime.datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")

# -------------------------------------------------------------------------------------
# JSON log helpers
# -------------------------------------------------------------------------------------

def load_json(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return []
    return []

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# -------------------------------------------------------------------------------------
# De-dupe helpers (by slug and by symbol)
# -------------------------------------------------------------------------------------

def extract_symbol_from_slug(slug):
    """
    Best-effort fallback if 'symbol' wasn't logged.
    Works with slugs like:
      '10-year-custom-tradewave-report-linde-plc-lin-2025-09-08-to-2026-06-05'
    Returns uppercase symbol or None.
    """
    m = re.search(r"-([A-Za-z0-9\.]+)-\d{4}-\d{2}-\d{2}-to-\d{4}-\d{2}-\d{2}$", slug or "")
    if m:
        return m.group(1).upper()
    return None

def get_last_symbols_posted(network, limit=10):
    """
    Return up to `limit` most recent UNIQUE symbols for this network, newest first.
    Prefers the explicit 'symbol' in log; falls back to parsing the slug.
    """
    log_path = get_json_log_path(network)
    log = load_json(log_path)
    if not log:
        return []

    symbols = []
    seen = set()
    # newest to oldest (log appended chronologically)
    for entry in reversed(log):
        sym = (entry.get("symbol") or extract_symbol_from_slug(entry.get("slug", "")) or "").upper().strip()
        if not sym:
            continue
        if sym in seen:
            continue
        seen.add(sym)
        symbols.append(sym)
        if len(symbols) >= limit:
            break
    return symbols

def is_already_posted_by_slug(network, slug):
    """Check JSON log for exact slug match."""
    log = load_json(get_json_log_path(network))
    return any(e.get("slug") == slug for e in log)

def is_already_posted_by_symbol(network, symbol):
    """Check JSON log for symbol (or parse-able symbol from slug)."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return False
    log = load_json(get_json_log_path(network))
    for e in log:
        if (e.get("symbol") or "").upper().strip() == symbol:
            return True
        s2 = (extract_symbol_from_slug(e.get("slug", "")) or "").upper().strip()
        if s2 == symbol:
            return True
    return False

# -------------------------------------------------------------------------------------
# Posting / Deleting
# -------------------------------------------------------------------------------------

def add_post_to_social_networks(action_dict, networks):
    """
    Add a post (one slug) to one or more social network RSS feeds.
    Prevents duplicates by checking the JSON log for the network (by slug).
    action_dict keys expected:
      - slug (str)            unique id for the opportunity (used as RSS guid)
      - symbol (str)          ticker (logged for de-dupe)
      - img_url (str)         absolute URL to thumbnail (network-optimized)
      - message (str)         caption / body
      - link (str)            report URL
    """
    ensure_dirs()
    for network in networks:
        if network not in NETWORKS:
            print(f"[WARN] Unsupported network: {network}")
            continue

        slug     = action_dict["slug"]
        symbol   = (action_dict.get("symbol") or "").upper().strip()
        img_url  = action_dict["img_url"]
        message  = action_dict["message"]
        post_link= action_dict.get("link", "https://tradewave.ai/")

        # De-dupe: if slug already posted to this network, skip
        if is_already_posted_by_slug(network, slug):
            print(f"[SKIP] Already posted to {network}: {slug}")
            continue

        # Prepare/parse RSS
        ensure_rss_feed_exists(network)
        rss_path = get_rss_path(network)
        tree, channel = parse_rss_feed(rss_path)

        # Remove any existing item with same GUID just in case
        for item in list(channel.findall("item")):
            guid_el = item.find("guid")
            guid = guid_el.text if guid_el is not None else ""
            if guid == slug:
                channel.remove(item)

        # Create RSS item
        item = ET.Element("item")
        ET.SubElement(item, "title").text = f"TradeWave Opportunity: {slug}"
        ET.SubElement(item, "description").text = message
        ET.SubElement(item, "link").text = post_link
        guid_el = ET.SubElement(item, "guid")
        guid_el.text = slug
        guid_el.set("isPermaLink", "false")
        ET.SubElement(item, "pubDate").text = rfc2822_now()

        enc = ET.SubElement(item, "enclosure")
        enc.set("url", img_url)
        enc.set("type", NETWORKS[network]["enclosure_type"])

        channel.append(item)
        save_rss_feed(tree, rss_path)

        # Append to JSON log
        log_path = get_json_log_path(network)
        log = load_json(log_path)
        log.append({
            "network":  network,
            "slug":     slug,
            "symbol":   symbol,
            "datetime": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "message":  message,
            "img_url":  img_url,
            "link":     post_link
        })
        save_json(log_path, log)

        print(f"[OK] Added post to {network} RSS & log: {slug}")

def delete_post_from_social_networks(action_dict, networks):
    """
    Remove a post (by slug) from one or more RSS feeds and logs.
    Also record the deletion request in posts_to_delete.json for manual cleanup.
    action_dict keys expected:
      - slug (str)
    """
    ensure_dirs()
    slug = action_dict["slug"]

    for network in networks:
        if network not in NETWORKS:
            print(f"[WARN] Unsupported network: {network}")
            continue

        rss_path = get_rss_path(network)
        log_path = get_json_log_path(network)
        del_path = get_delete_log_path(network)

        # Remove from RSS
        if os.path.exists(rss_path):
            tree, channel = parse_rss_feed(rss_path)
            removed = False
            for item in list(channel.findall("item")):
                guid_el = item.find("guid")
                guid = guid_el.text if guid_el is not None else ""
                if guid == slug:
                    channel.remove(item)
                    removed = True
            if removed:
                save_rss_feed(tree, rss_path)
                print(f"[OK] Removed from {network} RSS: {slug}")
            else:
                print(f"[INFO] Not found in {network} RSS: {slug}")

        # Remove from JSON log
        log = load_json(log_path)
        new_log = [e for e in log if e.get("slug") != slug]
        if len(new_log) != len(log):
            save_json(log_path, new_log)
            print(f"[OK] Removed from {network} log: {slug}")

        # Record deletion request
        del_log = load_json(del_path)
        if not any(e.get("slug") == slug for e in del_log):
            del_log.append({
                "network":  network,
                "slug":     slug,
                "datetime": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            save_json(del_path, del_log)
            print(f"[OK] Added to {network} posts_to_delete: {slug}")

# -------------------------------------------------------------------------------------
# Selection helper for X (example) — avoids recently posted symbols
# -------------------------------------------------------------------------------------

def pick_opportunity_for_x(dfd, recent_syms, top_k_per_group=5):
    """
    From a dict of DataFrames dfd[fg], pick a row whose symbol NOT in recent_syms.
    Scans groups, considers up to top_k_per_group rows in each; returns (fg, r) or None.
    """
    # simple pass over groups in the provided order
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
# __main__ — Match your m_facebook.py "driver" style
# -------------------------------------------------------------------------------------

if __name__ == '__main__':
    # Hashtags or any extra text you want
    hashtags = '#FinancialAnalyst #MarketAnalysis #traders #investors #Stocks'

    # Load today's Top 10 (dfd is dict: fg -> DataFrame of opps)
    filename = config.today_top10_data
    action, dfd = load_top10(filename)

    # Avoid duplicates by symbol: read recent X posts
    recent_syms = set(get_last_symbols_posted("x", limit=10))

    # Pick a candidate not in recent symbols; if none found, fallback to random
    picked = pick_opportunity_for_x(dfd, recent_syms, top_k_per_group=5)
    if not picked:
        # Fallback: pick a random fg, random row (still fine; add any extra filters you like)
        fg = random.choice(list(dfd.keys()))
        r  = random.randint(0, max(0, dfd[fg].shape[0]-1))
    else:
        fg, r = picked

    # Pull row fields (adjust names if your DF differs)
    row       = dfd[fg].iloc[r]
    symbol    = str(row['Symbol']).upper()
    date1     = row['Date']
    date2     = row['Date2']
    days_hold = int(row['DaysOut'])
    years     = str(row['post_title']).split('-')[0]  # e.g., "10"
    trade_dir = row['Direction']                       # "Long"/"Short" or "L/S" etc.
    avg_gain  = row['Avg Profit']                      # text like "12.3%"
    slug      = row['opp_slug']                        # canonical slug for this opp


    # Create X-optimized thumbnail BEFORE posting
    # title_pre is what you used on FB (e.g. "10-Year ")
    title_pre = f"{years}-Year "
    category  = getattr(config, "category_date_range_report", "date_range_report")

    # This returns: (local_path, public_url)
    tn_path, tn_url = create_socialmedia_thumbnail(
        NETWORKS["x"]["tn_key"],  # 'x'
        int(fg),
        date1,
        symbol,
        days_hold,
        trade_dir,
        avg_gain,
        years,
        title_pre,
        category
    )

    # print(tn_path)
    # print(tn_url)
    # print(slug)
    # exit()

    # Compose message (keep it simple and short for X)
    message = f"""
    {symbol} {date1}→{date2} | {years}Y window • {days_hold} days
    Full Report: {slug}
    {hashtags}
    """.strip()

    # Build action dict (include symbol for robust de-dupe logging)
    action_dict = {
        "slug":    slug,
        "symbol":  symbol,
        "img_url": tn_url,  # use returned public URL
        "message": message,
        "link":    slug,
    }

    print(action_dict)

    # Post to X (you can pass more networks later, e.g. ["x","facebook"])
    add_post_to_social_networks(action_dict, ["x"])

    # If you want the exact Facebook-style "recreate if exists" flow:
    # (Note: add_post_to_social_networks already skips duplicates by slug)
    # log_path = get_json_log_path("x")
    # posted = load_json(log_path)
    # if any(e.get("slug") == slug for e in posted):
    #     print("[INFO] Already existed; delete & repost logic could go here if desired.")
    #     # delete_post_from_social_networks(action_dict, ["x"])
    #     # (then pick a new opp, rebuild image, and repost)
