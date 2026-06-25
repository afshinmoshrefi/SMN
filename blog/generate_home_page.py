#!/usr/bin/env python3
"""
TradeWave Homepage Generator
Generates a homepage from CSV opportunities data + live stockscore data.

Usage:
    python generate_home_page.py
"""

import csv
import json
import requests
import base64
import shutil
import time
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict
from jinja2 import Environment, FileSystemLoader
import sys
sys.path.insert(0, '/home/flask')
import config

from daily_pattern_picks import get_daily_picks
from blog_tools import get_company_name, convert_param_base64
from get_price_eod import get_quote_details

# =============================================================================
# CONFIGURATION - Edit these settings
# =============================================================================

# Input: CSV file with opportunities
OPPORTUNITIES_CSV = "/home/flask/blog/home_opportunities.csv"

# Output: Where to save the generated HTML
# OUTPUT_DIR = "/var/www/html/_static/"
# OUTPUT_DIR = "/var/www/html/wordpress/_static/"  # for dev
OUTPUT_DIR = config.web_root_dir+"/_static/"

OUTPUT_FILENAME = "home.html"

# Stockscore API for live market data
# STOCKSCORE_URL = "http://10.0.0.30:7771/"
# STOCKSCORE_URL = 'http://104.238.214.253:7771/' # for dev
STOCKSCORE_URL = config.stockscore_url

STOCKSCORE_RESOURCE_ID = 2

# Domain for wave-viewer links
# DOMAIN_ROOT = "https://trxstat.com/"
# DOMAIN_ROOT = "192.168.1.151/"
DOMAIN_ROOT = config.domain_root

# How many opportunities to show per tab
OPPORTUNITIES_PER_TAB = 10

# Show opportunities table on the page (set to False to hide)
SHOW_OPPORTUNITIES = True

# Enable SEO tags (robots, canonical, structured data) - disable for dev/staging
ENABLE_SEO = False

# =============================================================================
# SIGNUP & AUTH URLs
# =============================================================================

SIGNUP_URL = "%sregister/?lid=1" % DOMAIN_ROOT
LOGIN_URL = "%smember-login" % DOMAIN_ROOT
LOGOUT_URL = "%smember-logout/?ihcdologout=true" % DOMAIN_ROOT
DASHBOARD_URL = "%smy-account/" % DOMAIN_ROOT
UPGRADE_URL = "%smy-account/?ihc_ap_menu=subscription" % DOMAIN_ROOT

# =============================================================================
# PRICING URLs - Set these to your actual signup pages for each plan
# =============================================================================

PRICING_EXPLORER_URL = "%sregister/?lid=1" % DOMAIN_ROOT
PRICING_ANALYST_MONTHLY_URL = "%sregister/?lid=4" % DOMAIN_ROOT
PRICING_ANALYST_YEARLY_URL = "%sregister/?lid=5" % DOMAIN_ROOT
PRICING_STRATEGIST_MONTHLY_URL = "%sregister/?lid=6" % DOMAIN_ROOT
PRICING_STRATEGIST_YEARLY_URL = "%sregister/?lid=7" % DOMAIN_ROOT

# =============================================================================
# ANNE-MARIE VIDEO (self-hosted MP4)
# =============================================================================

ANNEMARIE_VIDEO_URL = "/_static/anne-marie_tradewave.mp4"
ERIN_VIDEO_URL = "/_static/erin1.mp4"

# =============================================================================
# CONTACT
# =============================================================================

CONTACT_URL = "%scontact" % DOMAIN_ROOT

# =============================================================================
# MARKET BAR TICKERS
# =============================================================================

MARKET_BAR_TICKERS = [
    {"label": "S&P 500",  "symbol": "GSPC", "exchange": "INDX", "slug": "sp500"},
    {"label": "DOW",      "symbol": "DJI",  "exchange": "INDX", "slug": "dow"},
    {"label": "NASDAQ",   "symbol": "IXIC", "exchange": "INDX", "slug": "nasdaq"},
    {"label": "VIX",      "symbol": "VIX",  "exchange": "INDX", "slug": "vix"},
    {"label": "CRUDE",    "symbol": "CL",   "exchange": "COMM", "slug": "crude-oil"},
    {"label": "NAT GAS",  "symbol": "NG",   "exchange": "COMM", "slug": "natural-gas"},
    {"label": "GOLD",     "symbol": "GC",   "exchange": "COMM", "slug": "gold"},
]


def build_market_bar_data():
    """Fetch quotes and build market bar items for template."""
    items = []
    for t in MARKET_BAR_TICKERS:
        quote = get_quote_details(t["symbol"], t["exchange"])
        try:
            close_val = quote.get("close") if quote else None
            price = float(close_val) if close_val not in (None, "NA", "N/A", "") else None
        except (ValueError, TypeError):
            price = None

        if price is not None:
            try:
                change_p = float(quote.get("change_p") or 0)
            except (ValueError, TypeError):
                change_p = 0
            direction = "up" if change_p >= 0 else "down"
            sign = "+" if change_p >= 0 else ""
            price_fmt = "%s" % format(price, ",.2f")
            chg_fmt = "%s%.2f%%" % (sign, change_p)
        else:
            price_fmt = ""
            chg_fmt = ""
            direction = "flat"

        items.append({
            "label": t["label"],
            "slug": t["slug"],
            "price": price_fmt,
            "change": chg_fmt,
            "direction": direction,
            "url": "%s_static/markets/%s.html" % (DOMAIN_ROOT, t["slug"]),
        })
    return items


# =============================================================================
# FEATURED PATTERN HISTORY
# =============================================================================

FEATURED_HISTORY_FILE = "/home/flask/blog/featured_history.json"
FEATURED_REPEAT_DAYS = 14  # don't repeat same symbol within this many days

# ML scorer params for featured pattern selection
FEATURED_RESOURCE_IDS = ['2']       # S&P 500
FEATURED_DIRECTION = 'both'
FEATURED_DAYS_OUT_MIN = 10
FEATURED_DAYS_OUT_MAX = 30
FEATURED_MIN_AVG_RETURN = 5.0
FEATURED_MIN_WIN_PROB = 0.75

# OppList4 lookup params
FEATURED_YEARS = 10
FEATURED_MIN_PYEARS = 8

# Appserver URL for OppList4 lookups
APPSERVER_URL = 'https://app1pp.trxstat.com'
APPSERVER_USERID = 16


def load_featured_history():
    """Load featured history, return list of entries."""
    if not Path(FEATURED_HISTORY_FILE).exists():
        return []
    try:
        with open(FEATURED_HISTORY_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return []


def save_featured_history(history):
    """Save featured history to JSON file."""
    with open(FEATURED_HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2)


def get_recent_symbols(history, days=FEATURED_REPEAT_DAYS):
    """Get symbols featured in the last N days."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return {
        entry['symbol']
        for entry in history
        if entry.get('featured_date', '') >= cutoff
    }


def appserver_login():
    """Login to appserver and return token. Retries on transient stalls."""
    def _get(url, attempts=3, timeout=30, sleep_s=5):
        last = None
        for i in range(attempts):
            try:
                return requests.get(url, timeout=timeout).json()
            except Exception as e:
                last = e
                print("   WARN appserver call failed (%s), attempt %d/%d" % (e, i + 1, attempts))
                time.sleep(sleep_s)
        raise last

    try:
        result = _get(APPSERVER_URL + '/login/16/7/4/5/6')
        kp_token = result['message'].split(' ')[4]

        url = APPSERVER_URL + '/login/16/7/4/5/' + kp_token
        result = _get(url)
        if 'message' in result:
            time.sleep(10)
            result = _get(url)
            if 'message' in result:
                return None
        return result['token']
    except Exception as e:
        print("   ERROR appserver login failed after retries: %s" % e)
        return None


def fetch_close_price(resource_id, symbol, target_date, token):
    """Fetch the close price for a symbol on a specific date via ChartHistorical2.

    If the exact date isn't a trading day, returns the close from the nearest
    prior trading day.  Returns float or None on failure.
    """
    # Request a small window around the target date
    dt = datetime.strptime(target_date, '%Y-%m-%d')
    d0 = (dt - timedelta(days=5)).strftime('%Y-%m-%d')
    d1 = (dt + timedelta(days=5)).strftime('%Y-%m-%d')
    url = '%s/ChartHistorical2/%s/%s/%s/%s?token=%s' % (
        APPSERVER_URL, resource_id, symbol, d0, d1, token
    )
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        rows = resp.json().get('ChartHistorical2', [])
        # rows: [date, open, high, low, close, volume]
        # Find exact date or nearest prior
        best = None
        for row in rows:
            if row[0] <= target_date:
                best = row
        if best:
            return float(best[4])  # close price
    except Exception as e:
        print("   WARNING: ChartHistorical2 failed for %s: %s" % (symbol, e))
    return None


def lookup_years_via_opplist4(symbol, target_date, days_out, direction, token):
    """Look up the years string for a pattern via OppList4.

    Tries consecutive mode first with years=10 (pyears 10,9,8), then PE mode.
    pyears/years must be > 0.8 to be valid.
    Returns (years_str, mode) or (None, None) if not found.
    """
    dt = datetime.strptime(target_date, '%Y-%m-%d')
    month_name = dt.strftime('%B')
    day_num = str(dt.day)

    # Determine day_range bucket
    if days_out <= 30:
        day_range = '7-30'
    elif days_out <= 60:
        day_range = '31-60'
    else:
        day_range = '61-90'

    # Direction mapping: ML scorer uses 'l'/'s', OppList4 uses 'Long'/'Short'
    dir_match = 'Long' if direction == 'l' else 'Short'

    # Valid pyears for years=10: 10, 9, 8 (all where pyears/years > 0.8)
    valid_pyears = [p for p in range(FEATURED_YEARS, 0, -1)
                    if p / FEATURED_YEARS > 0.8]

    def search_opplist(mode, pyears):
        url = '%s/OppList4/%s/%s/%s/%s/%s/%s/0/0?token=%s&mode=%s' % (
            APPSERVER_URL, FEATURED_RESOURCE_IDS[0], month_name, day_num,
            FEATURED_YEARS, pyears, day_range, token, mode
        )
        try:
            result = requests.get(url, timeout=30).json()
            for opp in result.get('OppList', []):
                # opp: [date, symbol, daysOut, direction, SR, AvgP, median, ...]
                if (isinstance(opp, list) and len(opp) > 3 and
                        opp[1] == symbol and int(opp[2]) == days_out and
                        opp[3] == dir_match):
                    return True
        except Exception as e:
            print("      WARNING: OppList4 lookup failed (%s, pyears=%s): %s" % (mode, pyears, e))
        return False

    # Try consecutive first, walking pyears from highest to lowest valid
    for pyears in valid_pyears:
        if search_opplist('cons', pyears):
            return str(FEATURED_YEARS), 'cons'

    # Try PE mode with same pyears walk
    pe_cycle = dt.year % 4
    for pyears in valid_pyears:
        if search_opplist('pe', pyears):
            return 'pe%d-%s' % (pe_cycle, FEATURED_YEARS), 'pe'

    return None, None


def select_featured_from_ml_scorer():
    """Select featured pattern using ML scorer + history dedup + OppList4 confirmation.

    Walks ML picks top-down. Skips symbols featured in last 14 days and symbols
    that can't be confirmed in OppList4 (so the wave-viewer link is always valid).

    Returns a dict with all featured pattern data, or None.
    """
    history = load_featured_history()

    # If already picked today, reuse that entry
    today_str = date.today().isoformat()
    for entry in history:
        if entry.get('featured_date') == today_str:
            print("   Reusing today's pick: %s (already selected)" % entry['symbol'])
            return entry

    recent_symbols = get_recent_symbols(history)
    if recent_symbols:
        print("   Recent featured symbols (last %d days): %s" % (
            FEATURED_REPEAT_DAYS, ', '.join(sorted(recent_symbols))))

    # Find next weekday
    today = date.today()
    if today.weekday() >= 5:
        today = today + timedelta(days=(7 - today.weekday()))
    target = today.strftime('%Y-%m-%d')

    # Get ML-scored picks
    print("   Fetching ML scorer picks for %s..." % target)
    try:
        result = get_daily_picks(
            date=target,
            resource_ids=FEATURED_RESOURCE_IDS,
            num_picks=0,
            direction=FEATURED_DIRECTION,
            days_out_min=FEATURED_DAYS_OUT_MIN,
            days_out_max=FEATURED_DAYS_OUT_MAX,
            min_avg_return=FEATURED_MIN_AVG_RETURN,
            min_win_prob=FEATURED_MIN_WIN_PROB,
        )
    except Exception as e:
        print("   WARNING: ML scorer call failed: %s" % e)
        return None

    picks = result.get('picks', [])
    if not picks:
        print("   No qualifying picks from ML scorer.")
        return None

    print("   ML scorer returned %d qualifying picks." % len(picks))

    # Login to appserver once for OppList4 lookups
    token = appserver_login()
    if token is None:
        print("   WARNING: Appserver login failed, cannot confirm patterns.")
        return None

    # Walk picks: skip recently featured, skip if OppList4 can't confirm
    resource_id = FEATURED_RESOURCE_IDS[0]
    for p in picks:
        if p['symbol'] in recent_symbols:
            continue

        print("   Trying %s (wp=%.1f%%, ml=%.1f)..." % (
            p['symbol'], p['win_prob'] * 100, p['ml_score']))

        years_str, mode = lookup_years_via_opplist4(
            p['symbol'], target, p['daysOut'], p['direction'], token
        )
        if years_str is not None:
            print("   Confirmed in OppList4: mode=%s, years=%s" % (mode, years_str))
            pick = p
            break
        else:
            print("      Not found in OppList4, skipping.")
    else:
        print("   No picks confirmed by OppList4.")
        return None

    # Get company name
    company_name = get_company_name(int(resource_id), pick['symbol']) or pick['symbol']

    # Get start price (close on pattern start date)
    start_price = fetch_close_price(resource_id, pick['symbol'], target, token)
    if start_price:
        print("   Start price for %s on %s: $%.2f" % (pick['symbol'], target, start_price))
    else:
        print("   WARNING: Could not fetch start price for %s" % pick['symbol'])

    # Build pattern_param
    pattern_param = convert_param_base64(
        resource_id, pick['symbol'], target, str(pick['daysOut']), years_str
    )
    wave_viewer_url = "%swave-viewer?o=%s" % (DOMAIN_ROOT, pattern_param)

    # Build history entry
    history_entry = {
        'featured_date': date.today().isoformat(),
        'symbol': pick['symbol'],
        'company_name': company_name,
        'date': target,
        'daysOut': pick['daysOut'],
        'direction': pick['direction'],
        'resource_id': resource_id,
        'mode': mode,
        'years': years_str,
        'pattern_param': pattern_param,
        'wave_viewer_url': wave_viewer_url,
        'sharpe_ratio': pick['sharpe_ratio'],
        'sharpe_ratio2': pick.get('sharpe_ratio2', pick['sharpe_ratio']),
        'avg_profit': pick['avg_profit'],
        'avg_profit2': pick.get('avg_profit2', pick['avg_profit']),
        'median_profit': pick['median_profit'],
        'ml_score': pick['ml_score'],
        'win_prob': pick['win_prob'],
        'pred_return': pick['pred_return'],
        'pred_mfe': pick['pred_mfe'],
        'p_hit_return': pick.get('p_hit_return'),
        'p_hit_mfe': pick.get('p_hit_mfe'),
        'tier': pick.get('tier'),
        'start_price': start_price,
    }

    # Save to history
    history.append(history_entry)
    save_featured_history(history)
    print("   Saved to featured history (%d total entries)." % len(history))

    return history_entry


def parse_pattern_param(pattern_param):
    """Decode base64 pattern_param -> resource_id, symbol, date, days, years."""
    try:
        decoded = base64.b64decode(pattern_param).decode('utf-8')
        parts = decoded.split('|')
        return {
            'resource_id': parts[0],
            'symbol': parts[1],
            'start_date': parts[2],
            'days': int(parts[3]),
            'years': parts[4].lower(),
        }
    except Exception:
        return None


# =============================================================================
# DISPLAY ALIASES
# =============================================================================

DISPLAY_ALIASES = {
    "pe":   "Election Year",
    "pe+1": "Post-Election",
    "pe+2": "Midterm",
    "pe+3": "Pre-Election",
    "cons": "All Years",
}

# =============================================================================
# PE CYCLE CALCULATION
# =============================================================================

def get_current_pe_cycle():
    """Calculate current Presidential Election cycle year."""
    year = datetime.now().year
    offset = year % 4
    if offset == 0:
        return "pe"
    return "pe+%d" % offset

CURRENT_PE_CYCLE = get_current_pe_cycle()

def get_alias(mode):
    """Get display alias for a pattern mode (pe or cons)."""
    if mode.lower() == "pe":
        return DISPLAY_ALIASES.get(CURRENT_PE_CYCLE, CURRENT_PE_CYCLE.upper())
    return DISPLAY_ALIASES.get(mode.lower(), mode.upper())

CURRENT_PE_DISPLAY = get_alias("pe")

# =============================================================================
# TEMPLATES & THEMES
# =============================================================================

TEMPLATES_DIR = Path(__file__).parent / "templates"

ACTIVE_THEME = "dark-blue"

THEMES = {
    "dark-blue": {
        "name": "Dark Blue",
        "template": "index-dark-blue.html",
        "description": "Dark theme with blue accents (fintech style)",
    },
}

# =============================================================================
# CSV LOADING
# =============================================================================

def load_opportunities_from_csv(csv_path):
    """Load opportunities from CSV file."""
    opportunities = []

    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                mode_raw = row["mode"]
                opp = {
                    "symbol": row["symbol"],
                    "company_name": row["company_name"],
                    "start_date": row["start_date"],
                    "days": int(row["days"]),
                    "direction": row["direction"],
                    "sharpe_ratio": float(row["SR"]),
                    "avg_return": float(row["AvgP"]),
                    "median_return": float(row["median"]),
                    "twa": float(row["TWA"]),
                    "twr": float(row["TWR"]),
                    "day_range": row["day_range"],
                    "mode": mode_raw,
                    "mode_display": get_alias(mode_raw),
                    "pattern_param": row["pattern_param"],
                    "wave_viewer_url": "%swave-viewer?o=%s" % (DOMAIN_ROOT, row['pattern_param']),
                }
                opportunities.append(opp)
        print("   Loaded %d opportunities from CSV" % len(opportunities))
    except FileNotFoundError:
        print("   ERROR: CSV not found at %s" % csv_path)
        return []
    except Exception as e:
        print("   ERROR loading CSV: %s" % e)
        return []

    return opportunities

# =============================================================================
# STOCKSCORE API
# =============================================================================

def fetch_stockscore_data(symbol):
    """Fetch live market data from stockscore API."""
    url = "%sstockta/%d/%s" % (STOCKSCORE_URL, STOCKSCORE_RESOURCE_ID, symbol)

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        return {
            "price": data["price"]["current"],
            "price_change_1d": data["price_changes"].get("1d"),
            "week_52_position": data["week_52"]["range_position_pct"],
            "week_52_high": data["week_52"]["high"],
            "week_52_low": data["week_52"]["low"],
            "sma_50": data["moving_averages"].get("sma50"),
            "sma_200": data["moving_averages"].get("sma200"),
            "rsi": data["momentum"]["rsi_14"],
            "long_score": data["scores"]["long"],
            "short_score": data["scores"]["short"],
        }
    except requests.RequestException as e:
        print("      Warning: Stockscore API failed for %s: %s" % (symbol, e))
        return None
    except (KeyError, TypeError) as e:
        print("      Warning: Invalid stockscore response for %s: %s" % (symbol, e))
        return None

def enrich_with_stockscore(opportunities):
    """Add live market data to each opportunity."""
    print("   Fetching stockscore data for %d symbols..." % len(opportunities))

    for opp in opportunities:
        stockscore = fetch_stockscore_data(opp["symbol"])

        if stockscore:
            opp["price"] = stockscore["price"]
            opp["price_change_1d"] = stockscore["price_change_1d"]
            opp["week_52_position"] = stockscore["week_52_position"]
            opp["has_live_data"] = True

            sma_50 = stockscore.get("sma_50")
            if sma_50 and sma_50.get("value"):
                opp["above_sma_50"] = sma_50.get("above", False)
            else:
                opp["above_sma_50"] = None
        else:
            opp["price"] = 0.0
            opp["price_change_1d"] = None
            opp["week_52_position"] = 50.0
            opp["above_sma_50"] = None
            opp["has_live_data"] = False

    return opportunities

# =============================================================================
# GROUP BY DAY RANGE
# =============================================================================

def group_by_day_range(opportunities, limit_per_tab=10):
    """Group opportunities by day_range for tabs."""
    short_term = []
    medium_term = []

    for opp in opportunities:
        day_range = opp.get("day_range", "")

        try:
            start = datetime.strptime(opp["start_date"], "%Y-%m-%d")
            end = start + timedelta(days=opp["days"])
            opp["start_date_formatted"] = start.strftime("%b %d")
            opp["end_date_formatted"] = end.strftime("%b %d")
            opp["days_until_start"] = (start - datetime.now()).days
        except Exception:
            opp["start_date_formatted"] = opp["start_date"]
            opp["end_date_formatted"] = ""
            opp["days_until_start"] = 0

        if day_range == "7-30":
            short_term.append(opp)
        elif day_range == "31-60":
            medium_term.append(opp)

    short_term.sort(key=lambda x: x["start_date"])
    medium_term.sort(key=lambda x: x["start_date"])

    return {
        "short_term": {
            "label": "1-4 Weeks",
            "sublabel": "Quick Plays",
            "count": len(short_term),
            "opportunities": short_term[:limit_per_tab],
        },
        "medium_term": {
            "label": "1-2 Months",
            "sublabel": "Position Trades",
            "count": len(medium_term),
            "opportunities": medium_term[:limit_per_tab],
        },
    }

# =============================================================================
# HTML GENERATION
# =============================================================================

def compute_homepage_scorecard_stats():
    """Compute scorecard stats for homepage display.

    Counts a pick as a win if:
    - closed with win=True, OR
    - open but peak_return >= pred_return (already hit predicted target)
    """
    history = load_featured_history()
    total_picks = len(history)

    # Count wins: closed wins + open positions that already hit target
    resolved = []
    for e in history:
        if e.get('status') == 'closed':
            resolved.append(e.get('win', False))
        elif e.get('status') == 'open':
            peak = e.get('peak_return', 0) or 0
            pred = e.get('pred_return', 0) or 0
            if peak >= pred and pred > 0:
                resolved.append(True)

    wins = sum(1 for w in resolved if w)
    win_rate = round((wins / len(resolved)) * 100) if resolved else 0

    # Count consecutive winning picks (from most recent backwards)
    consecutive_wins = 0
    for entry in reversed(history):
        if entry.get('status') == 'closed' and entry.get('win'):
            consecutive_wins += 1
        elif entry.get('status') == 'open':
            peak = entry.get('peak_return', 0) or 0
            pred = entry.get('pred_return', 0) or 0
            if peak >= pred and pred > 0:
                consecutive_wins += 1
            # Skip open positions that haven't hit target yet
        elif entry.get('status') == 'closed' and not entry.get('win'):
            break

    # Avg peak return across all picks with peak data
    all_with_peaks = [e for e in history if e.get('peak_return') or e.get('actual_return')]
    peak_returns = [e.get('peak_return', e.get('actual_return', 0)) for e in all_with_peaks]
    avg_peak_return = (sum(peak_returns) / len(peak_returns)) if peak_returns else 0

    return {
        'total_picks': total_picks,
        'win_rate': win_rate,
        'consecutive_wins': consecutive_wins,
        'avg_peak_return': round(avg_peak_return, 1),
    }


def _hero_headline(history):
    """Build dynamic hero headline from the most recent proven pick.

    Walks history newest-first, takes the first pick with peak_return >= 5%.
    Falls back to static headline if none qualifies.
    """
    for entry in reversed(history):
        peak = entry.get('peak_return', 0) or 0
        if peak >= 5.0:
            symbol = entry['symbol']
            days = entry['daysOut']
            if entry['direction'] == 's':
                return "$%s dropped %.1f%% in %d days." % (symbol, peak, days)
            return "$%s rose +%.1f%% in %d days." % (symbol, peak, days)
    return "78% of our AI picks won. Over 8 years."


def generate_html(opportunities_by_tab, featured_data=None, market_bar_items=None):
    """Generate HTML from template and data."""

    jinja_env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=True,
    )

    template = jinja_env.get_template(THEMES[ACTIVE_THEME]["template"])

    default_tab = "short_term"
    if not opportunities_by_tab.get("short_term", {}).get("opportunities"):
        default_tab = "medium_term"

    total_opps = sum(
        tab.get("count", 0)
        for tab in opportunities_by_tab.values()
    )

    # =========================================================================
    # CONTENT DICT
    # =========================================================================
    content = {
        "show_opportunities": SHOW_OPPORTUNITIES,
        "enable_seo": ENABLE_SEO,
        "canonical_url": DOMAIN_ROOT,

        # -- Meta --
        "meta": {
            "title": "TradeWave - AI-Scored Seasonal Stock Patterns | 78-86% Win Rate",
            "description": (
                "TradeWave scores seasonal stock patterns with a 62-feature AI model "
                "trained on 34.7 million data points. 78-86% win rates across 8 years "
                "of out-of-sample testing. Zero losing years."
            ),
            "keywords": (
                "seasonal trading, stock market patterns, AI trading signals, "
                "quantitative trading, pattern scoring, machine learning stocks, "
                "wave viewer, election cycle trading, S&P 500 patterns, market history"
            ),
        },

        # -- Nav (simplified: logo + CTA only) --
        "nav": {
            "links": [
                {"text": "Wave Viewer", "url": "/wave-viewer"},
                {"text": "Tickers", "url": "/patterns/"},
                {"text": "Research", "url": "/_static/research.html"},
            ],
            "signup_url": SIGNUP_URL,
            "login_url": LOGIN_URL,
            "logout_url": LOGOUT_URL,
            "dashboard_url": DASHBOARD_URL,
            "upgrade_url": UPGRADE_URL,
        },

        # -- Hero (dynamic from best proven pick in history) --
        "hero": {
            "headline": _hero_headline(load_featured_history()),
            "headline2": "Our AI called it before market open.",
            "headline3": "See today's pick free.",
            "subheadline": (
                "Every morning before market open, TradeWave's AI scores 475+ stocks "
                "and delivers only the highest-probability seasonal trades. "
                "78-86% accuracy. Zero losing years across 8 years of live testing."
            ),
            "cta_primary": "Get Today's Free Pick",
            "cta_primary_url": SIGNUP_URL,
            "cta_secondary": "Watch How It Works",
            "cta_secondary_url": "#testimonials",
            # Logged-in CTA variants
            "cta_analyst_url":     PRICING_ANALYST_MONTHLY_URL,
            "cta_strategist_url":  PRICING_STRATEGIST_MONTHLY_URL,
            "cta_wave_viewer_url": "%swave-viewer" % DOMAIN_ROOT,
            "cta_webinars_url":    "%swebinars" % DOMAIN_ROOT,
        },

        # -- Anne-Marie Video --
        "video": {
            "src": ANNEMARIE_VIDEO_URL,
            "quote": "I don't go into any trade without looking at it on TradeWave first",
            "author": "Anne-Marie Baiynd",
            "title": "CEO & Founder, TheTradingBook.com",
            "credential": "Bestselling Author & Market Strategist",
        },

        # -- Erin Video --
        "video2": {
            "src": ERIN_VIDEO_URL,
            "quote": "TradeWave has directed me to a few trades that were really lovely, five-figure trades",
            "author": "Erin West",
            "title": "Founder, Humans Who Trade",
            "credential": "Trading Coach & Educator for Women",
        },

        # -- Featured Pattern --
        "featured_pattern": featured_data,
        "scorecard_url": "%sscorecard" % DOMAIN_ROOT,
        "scorecard_stats": compute_homepage_scorecard_stats(),
        "recent_picks": [e['symbol'] for e in reversed(load_featured_history())][:3],
        "market_bar": market_bar_items or [],

        # -- Opportunities Table --
        "opportunities": {
            "headline": "This Week's Top Patterns, Click Any Row to See the Proof",
            "context": "Every opportunity links to its historical proof. Click any row to see the full data behind it.",
            "tabs": opportunities_by_tab,
            "default_tab": default_tab,
            "total_count": total_opps,
            "signup_prompt": "Create a free account to see more patterns across all markets.",
            "signup_url": SIGNUP_URL,
            "show_election_compare": True,
            "election_compare_label": "Compare election cycle",
        },

        # -- Pricing --
        "pricing": {
            "headline": "Simple Pricing for Serious Traders",
            "subheadline": (
                "Start free, upgrade when you're ready. "
                "Lock in launch pricing before April 30."
            ),
            "default_billing": "yearly",
            "tiers": [
                {
                    "name": "Explorer",
                    "monthly_price": "Free",
                    "monthly_period": "",
                    "monthly_original": "",
                    "yearly_price": "Free",
                    "yearly_period": "",
                    "yearly_original": "",
                    "description": "Get a feel for the platform with no credit card required",
                    "trial_badge": "",
                    "features": [
                        "See today's top 5 seasonal patterns",
                        "Track up to 5 opportunities in 1 portfolio",
                        "Real-time prices and Trend Score",
                        "Earnings date estimates (EDGAR)",
                        "Election-cycle overlay on loaded patterns",
                        "~upgrade:Upgrade to unlock AI scoring, all markets, and more",
                    ],
                    "cta": "Sign Up Free",
                    "monthly_url": PRICING_EXPLORER_URL,
                    "yearly_url": PRICING_EXPLORER_URL,
                    "highlighted": False,
                    "after_launch_price": "",
                },
                {
                    "name": "Analyst",
                    "monthly_price": "$47",
                    "monthly_period": "/mo",
                    "monthly_original": "",
                    "yearly_price": "$37",
                    "yearly_period": "/mo, billed yearly",
                    "yearly_original": "",
                    "description": "Deep seasonal insights across all U.S. stocks and ETFs with AI scoring",
                    "trial_badge": "",
                    "features": [
                        "AI Pattern Analyst with scoring and predictions",
                        "All U.S. stocks + ETFs, custom start dates",
                        "25 portfolios, track up to 100 opportunities",
                        "5 watchlists, up to 50 symbols each",
                        "Seasonal Market News articles",
                        "LIVE weekly Q&A webinar",
                        "Email Support",
                    ],
                    "cta": "Try Analyst Free for 7 Days",
                    "monthly_url": PRICING_ANALYST_MONTHLY_URL,
                    "yearly_url": PRICING_ANALYST_YEARLY_URL,
                    "highlighted": False,
                    "after_launch_price": "$58/mo",
                    "yearly_daily": "$1.23/day",
                    "yearly_savings": "Save 21%",
                },
                {
                    "name": "Strategist",
                    "monthly_price": "$149",
                    "monthly_period": "/mo",
                    "monthly_original": "",
                    "yearly_price": "$99",
                    "yearly_period": "/mo, billed yearly",
                    "yearly_original": "",
                    "description": "Everything in Analyst plus election-cycle discovery, all 15 markets, and premium support",
                    "trial_badge": "7 days free, full access",
                    "card_testimonial": "TradeWave has directed me to a few trades that were really lovely, five-figure trades. -- Erin West, Trading Coach",
                    "roi_anchor": "One winning trade on a $10k position pays for a year of Strategist.",
                    "features": [
                        "Everything in Analyst, plus:",
                        "Spot 4-year cycle setups on any pattern",
                        "All 15 markets with full access",
                        "100 portfolios, track up to 500 opportunities",
                        "50 watchlists, up to 500 symbols each",
                        "Weekly strategy Zoom call",
                        "Premium Support",
                    ],
                    "cta": "Try Full Access Free for 7 Days",
                    "monthly_url": PRICING_STRATEGIST_MONTHLY_URL,
                    "yearly_url": PRICING_STRATEGIST_YEARLY_URL,
                    "highlighted": True,
                    "after_launch_price": "$199/mo",
                    "yearly_daily": "$3.30/day",
                    "yearly_savings": "Save 34%",
                },
            ],
        },

        # -- Midterm Warning (removed from homepage, moved to blog) --
        "midterm_warning": None,

        # -- Founder --
        "founder": {
            "name": "Afshin Moshrefi",
            "title": "Founder, TradeWave.ai",
            "bio": (
                "Afshin Moshrefi has spent a lifetime building technology around "
                "one obsession: finding what is hidden in the data before the rest "
                "of the world catches on. TradeWave.ai grew out of that obsession, "
                "turning market seasonality into something traders can measure, "
                "verify, and actually use."
            ),
            "photo": "/_static/afshin-profile-pic.jpg",
            "linkedin": "https://www.linkedin.com/in/afshinmoshrefi/",
            "book_title": "The 100-Year Pattern",
            "book_url": "https://100yearpattern.com",
        },

        # -- Contact --
        "contact_url": CONTACT_URL,

        # -- Footer (minimal) --
        "footer": {
            "legal": {
                "copyright": "%d Tara Data Research LLC. All rights reserved." % datetime.now().year,
                "disclaimer": (
                    "TradeWave is a research platform. It is not a brokerage and "
                    "does not execute trades. All data is based on historical analysis "
                    "and is provided for informational and educational purposes only. "
                    "Past performance does not guarantee future results. Trading and "
                    "investing involve substantial risk of loss. You should consult "
                    "with a qualified financial advisor before making any investment "
                    "decisions. Nothing on this website constitutes a recommendation "
                    "to buy or sell any security."
                ),
            },
        },

        "assets": {
            "logo": "/static/images/logo.png",
            "favicon": config.tw_favicon,
            "og_image": "/static/images/og-image.jpg",
        },
    }

    return template.render(content=content)

# =============================================================================
# MAIN
# =============================================================================

def main():
    print("TradeWave Homepage Generator")
    print("   CSV: %s" % OPPORTUNITIES_CSV)
    print("   Output: %s/%s" % (OUTPUT_DIR, OUTPUT_FILENAME))
    print("   Theme: %s (%s)" % (ACTIVE_THEME, THEMES[ACTIVE_THEME]['name']))
    print("   PE Cycle: %s (%s)" % (CURRENT_PE_CYCLE, CURRENT_PE_DISPLAY))
    print()

    # 0. Fetch market bar quotes
    print("   Fetching market bar quotes...")
    market_bar_items = build_market_bar_data()
    print("   Market bar: %d tickers" % len(market_bar_items))

    # 1. Load opportunities from CSV
    opportunities = load_opportunities_from_csv(OPPORTUNITIES_CSV)
    if not opportunities:
        print("   No opportunities loaded. Exiting.")
        return

    # 2. Enrich with live stockscore data
    opportunities = enrich_with_stockscore(opportunities)

    # 3. Group by day range for tabs
    opportunities_by_tab = group_by_day_range(opportunities, OPPORTUNITIES_PER_TAB)

    # 4. Select featured pattern via ML scorer and generate SVG
    featured_data = None
    featured = select_featured_from_ml_scorer()
    if not featured:
        # Pipeline failed (login/ML/OppList4). Reuse the most recent pick from
        # history so the homepage still renders, but log LOUDLY and mark the
        # entry as stale. The scorecard reads featured_history.json directly,
        # so it will NOT update until select_featured_from_ml_scorer() succeeds.
        history = load_featured_history()
        if history:
            featured = dict(history[-1])
            featured['_stale_reuse'] = True
            print("=" * 70)
            print("   ERROR: No new AI pick produced today.")
            print("   Reusing %s from %s on homepage only." % (
                featured["symbol"], featured["featured_date"]))
            print("   featured_history.json NOT updated -> scorecard will NOT show a new pick.")
            print("=" * 70)
    if featured:
        print("   Featured pattern: %s (ML %.1f, WP %.1f%%)" % (
            featured["symbol"], featured["ml_score"], featured["win_prob"] * 100))
        try:
            from svg_wave_chart import generate_wave_chart_svg
            svg_path = "/home/flask/blog/featured_chart.svg"
            generate_wave_chart_svg(
                resource_id=featured['resource_id'],
                symbol=featured['symbol'],
                start_date=featured['date'],
                days_out=featured['daysOut'],
                years=featured['years'],
                company_name=featured['company_name'],
                output_path=svg_path,
            )
            # Copy SVG to output dir
            output_dir = Path(OUTPUT_DIR)
            output_dir.mkdir(parents=True, exist_ok=True)
            dest_svg = output_dir / "featured_chart.svg"
            shutil.copy2(svg_path, str(dest_svg))
            print("   Featured SVG copied to %s" % dest_svg)

            # Extract years count and label for story
            years_str = featured['years']
            years_count = years_str.split('-')[-1] if '-' in years_str else years_str

            PE_YEAR_LABELS = {
                'pe0': 'election',
                'pe1': 'post-election',
                'pe2': 'midterm',
                'pe3': 'pre-election',
            }
            if featured['mode'] == 'pe' and '-' in years_str:
                pe_code = years_str.split('-')[0].lower()
                cycle_label = PE_YEAR_LABELS.get(pe_code, 'cycle')
                years_display = "%s %s" % (years_count, cycle_label)
            else:
                years_display = years_count

            # Compute end date for urgency display
            start_dt = datetime.strptime(featured["date"], "%Y-%m-%d")
            end_dt = start_dt + timedelta(days=featured["daysOut"])
            days_remaining = (end_dt - datetime.now()).days

            featured_data = {
                "show": True,
                "symbol": featured["symbol"],
                "company_name": featured["company_name"],
                "svg_url": "/_static/featured_chart.svg",
                "wave_viewer_url": featured["wave_viewer_url"],
                "story": (
                    "%s has averaged +%.1f%% in this %d-day window over the "
                    "past %s years (Sharpe %.2f). AI win probability: %.0f%%." % (
                        featured["symbol"],
                        featured["avg_profit"],
                        featured["daysOut"],
                        years_display,
                        featured["sharpe_ratio"],
                        featured["win_prob"] * 100,
                    )
                ),
                "sharpe_ratio": featured["sharpe_ratio"],
                "avg_return": featured["avg_profit"],
                "days": featured["daysOut"],
                "direction": "Long" if featured["direction"] == "l" else "Short",
                "win_prob": "%.1f" % (featured["win_prob"] * 100),
                "pred_return": "%.1f" % featured["pred_return"],
                "pred_mfe": "%.1f" % featured.get("pred_mfe", 0),
                "ml_score": "%.0f" % featured.get("ml_score", 0),
                "start_date_formatted": datetime.strptime(
                    featured["date"], "%Y-%m-%d").strftime("%b %d"),
                "end_date_formatted": end_dt.strftime("%b %d"),
                "days_remaining": max(days_remaining, 0),
                "published_time": "6:30 AM ET",
            }
        except Exception as e:
            print("   WARNING: Featured SVG generation failed: %s" % e)
            featured_data = None

    # 5. Generate HTML
    print("   Generating HTML...")
    html = generate_html(opportunities_by_tab, featured_data=featured_data,
                         market_bar_items=market_bar_items)

    # 6. Save to output file
    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / OUTPUT_FILENAME
    output_path.write_text(html)

    print("   Generated: %s" % output_path)
    print("   Size: %d bytes" % len(html))
    print("   Done!")

    # 7. Generate scorecard page
    print()
    from generate_scorecard import main as generate_scorecard
    generate_scorecard()

    # 8. Sync market bar quotes (handles futures mode)
    print()
    from update_news_quotes import update_quotes
    update_quotes(force=True)

if __name__ == "__main__":
    main()
