#!/usr/bin/env python3
"""
TradeWave AI Scorecard Generator
Generates the /scorecard page from featured_history.json + live price data.

Usage:
    python generate_scorecard.py
"""

import json
import requests
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
import sys
sys.path.insert(0, '/home/flask')
import config
from blog_tools import convert_param_base64

# =============================================================================
# CONFIGURATION
# =============================================================================

FEATURED_HISTORY_FILE = "/home/flask/blog/featured_history.json"
OUTPUT_DIR = config.web_root_dir + "/_static/"
OUTPUT_FILENAME = "scorecard.html"
TEMPLATES_DIR = "/home/flask/blog/templates"
DOMAIN_ROOT = config.domain_root
APPSERVER_URL = 'https://app1pp.trxstat.com'
REALTIME_SERVICE_URL = config.realtime_service_url
X_PROFILE_URL = config.x_profile_url

# =============================================================================
# APPSERVER AUTH
# =============================================================================

def appserver_login():
    """Login to appserver and return token."""
    url = APPSERVER_URL + '/login/16/7/4/5/6'
    result = requests.get(url).json()
    kp_token = result['message'].split(' ')[4]

    url = APPSERVER_URL + '/login/16/7/4/5/' + kp_token
    result = requests.get(url).json()
    if 'message' in result:
        time.sleep(10)
        result = requests.get(url).json()
        if 'message' in result:
            return None
    return result['token']


# =============================================================================
# PRICE LOOKUPS
# =============================================================================

def fetch_close_price(resource_id, symbol, target_date, token):
    """Fetch the close price for a symbol on a specific date."""
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
        best = None
        for row in rows:
            if row[0] <= target_date:
                best = row
        if best:
            return float(best[4])
    except Exception as e:
        print("   WARNING: Price fetch failed for %s on %s: %s" % (symbol, target_date, e))
    return None


def fetch_realtime_prices_bulk(symbols):
    """Fetch real-time prices for multiple symbols from the realtime service.

    Returns dict: {symbol: {price, high}} for symbols found.
    Symbols not found (non-US stocks, indices, futures) are omitted.
    """
    if not symbols:
        return {}
    symbols_param = ','.join(s.upper() for s in symbols)
    url = REALTIME_SERVICE_URL.rstrip('/') + '/prices/bulk?symbols=' + symbols_param
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        result = {}
        for sym, pdata in data.get('prices', {}).items():
            p = pdata.get('price')
            if p is not None:
                entry = {'price': float(p)}
                h = pdata.get('high')
                if h is not None:
                    entry['high'] = float(h)
                result[sym.upper()] = entry
        return result
    except Exception as e:
        print("   WARNING: Realtime bulk fetch failed: %s" % e)
        return {}


def fetch_current_price(resource_id, symbol, token):
    """Fetch the most recent close price for a symbol (fallback via ChartHistorical2)."""
    today = date.today()
    d1 = today.strftime('%Y-%m-%d')
    d0 = (today - timedelta(days=10)).strftime('%Y-%m-%d')
    url = '%s/ChartHistorical2/%s/%s/%s/%s?token=%s' % (
        APPSERVER_URL, resource_id, symbol, d0, d1, token
    )
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        rows = resp.json().get('ChartHistorical2', [])
        if rows:
            return float(rows[-1][4])
    except Exception as e:
        print("   WARNING: Current price fetch failed for %s: %s" % (symbol, e))
    return None


def fetch_end_price(resource_id, symbol, end_date, token):
    """Fetch the close price on or just after the pattern end date."""
    dt = datetime.strptime(end_date, '%Y-%m-%d')
    d0 = (dt - timedelta(days=2)).strftime('%Y-%m-%d')
    d1 = (dt + timedelta(days=5)).strftime('%Y-%m-%d')
    url = '%s/ChartHistorical2/%s/%s/%s/%s?token=%s' % (
        APPSERVER_URL, resource_id, symbol, d0, d1, token
    )
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        rows = resp.json().get('ChartHistorical2', [])
        # Find the first trading day on or after end_date
        for row in rows:
            if row[0] >= end_date:
                return float(row[4])
        # If none on/after, use the last available
        if rows:
            return float(rows[-1][4])
    except Exception as e:
        print("   WARNING: End price fetch failed for %s on %s: %s" % (symbol, end_date, e))
    return None


def fetch_peak_price(resource_id, symbol, start_date, end_date, direction, token):
    """Fetch the peak price during the trade window.

    For longs: highest high during the window.
    For shorts: lowest low during the window.
    Returns the peak price or None.

    ChartHistorical2 row format: [date, open, high, low, close, volume]
    """
    # Use the day after start (entry is at close of start_date) through end_date
    dt_start = datetime.strptime(start_date, '%Y-%m-%d')
    d0 = (dt_start + timedelta(days=1)).strftime('%Y-%m-%d')
    # For open positions, use today as the end
    today_str = date.today().strftime('%Y-%m-%d')
    d1 = min(end_date, today_str)
    # Add a few days buffer for weekends
    dt_end = datetime.strptime(d1, '%Y-%m-%d')
    d1_padded = (dt_end + timedelta(days=3)).strftime('%Y-%m-%d')

    url = '%s/ChartHistorical2/%s/%s/%s/%s?token=%s' % (
        APPSERVER_URL, resource_id, symbol, d0, d1_padded, token
    )
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        rows = resp.json().get('ChartHistorical2', [])
        # Filter to rows within the actual window
        window_rows = [r for r in rows if r[0] >= d0 and r[0] <= d1]
        if not window_rows:
            return None
        if direction == 'l':
            return max(float(r[2]) for r in window_rows)  # highest high
        else:
            return min(float(r[3]) for r in window_rows)  # lowest low
    except Exception as e:
        print("   WARNING: Peak price fetch failed for %s %s-%s: %s" % (symbol, start_date, end_date, e))
    return None


# =============================================================================
# DATA PROCESSING
# =============================================================================

def load_history():
    """Load featured history."""
    try:
        with open(FEATURED_HISTORY_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return []


def save_history(history):
    """Save featured history (with enriched fields)."""
    with open(FEATURED_HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2)


def compute_end_date(start_date, days_out):
    """Compute the pattern end date."""
    dt = datetime.strptime(start_date, '%Y-%m-%d')
    end = dt + timedelta(days=days_out)
    return end.strftime('%Y-%m-%d')


def enrich_positions(history, token):
    """Enrich history entries with current/end prices and returns.

    Classifies each entry as 'open' or 'closed' and computes actual returns.
    Modifies entries in place and saves back to history file.
    """
    today_str = date.today().strftime('%Y-%m-%d')
    changed = False

    # Bulk-fetch real-time prices for all open positions in one call
    open_symbols = [
        e['symbol'] for e in history
        if today_str < compute_end_date(e['date'], e['daysOut'])
    ]
    realtime_prices = fetch_realtime_prices_bulk(open_symbols)
    if realtime_prices:
        print("   Realtime prices fetched for %d/%d open symbols" % (
            len(realtime_prices), len(open_symbols)))

    for entry in history:
        end_date = compute_end_date(entry['date'], entry['daysOut'])
        entry['end_date'] = end_date

        # Ensure start_price exists
        if not entry.get('start_price'):
            price = fetch_close_price(
                entry['resource_id'], entry['symbol'], entry['date'], token
            )
            if price:
                entry['start_price'] = price
                changed = True
            else:
                continue

        if today_str >= end_date:
            # Closed position
            entry['status'] = 'closed'
            if not entry.get('end_price'):
                end_price = fetch_end_price(
                    entry['resource_id'], entry['symbol'], end_date, token
                )
                if end_price:
                    entry['end_price'] = end_price
                    changed = True

            if entry.get('end_price') and entry.get('start_price'):
                if entry['direction'] == 'l':
                    entry['actual_return'] = round(
                        (entry['end_price'] - entry['start_price']) / entry['start_price'] * 100, 2
                    )
                else:
                    entry['actual_return'] = round(
                        (entry['start_price'] - entry['end_price']) / entry['start_price'] * 100, 2
                    )
                entry['win'] = entry['actual_return'] > 0

            # Peak return (MFE) for closed positions - fetch once and persist
            if not entry.get('peak_price') and entry.get('start_price'):
                peak = fetch_peak_price(
                    entry['resource_id'], entry['symbol'],
                    entry['date'], end_date, entry['direction'], token
                )
                if peak:
                    entry['peak_price'] = peak
                    if entry['direction'] == 'l':
                        entry['peak_return'] = round(
                            (peak - entry['start_price']) / entry['start_price'] * 100, 2
                        )
                    else:
                        entry['peak_return'] = round(
                            (entry['start_price'] - peak) / entry['start_price'] * 100, 2
                        )
                    changed = True
        else:
            # Open position
            entry['status'] = 'open'
            sym = entry['symbol'].upper()
            rt_data = realtime_prices.get(sym)
            current_price = None
            today_high = None
            if rt_data:
                current_price = rt_data['price']
                today_high = rt_data.get('high')
                entry['price_source'] = 'realtime'
            else:
                current_price = fetch_current_price(
                    entry['resource_id'], entry['symbol'], token
                )
                entry['price_source'] = 'close'
            if current_price and entry.get('start_price'):
                if entry['direction'] == 'l':
                    entry['current_return'] = round(
                        (current_price - entry['start_price']) / entry['start_price'] * 100, 2
                    )
                else:
                    entry['current_return'] = round(
                        (entry['start_price'] - current_price) / entry['start_price'] * 100, 2
                    )
                entry['current_price'] = current_price

            # Peak return (MFE) for open positions - recalculate each run
            if entry.get('start_price'):
                peak = fetch_peak_price(
                    entry['resource_id'], entry['symbol'],
                    entry['date'], end_date, entry['direction'], token
                )
                if peak:
                    # Historical OHLC may not include today, so incorporate
                    # today's intraday high/low from the realtime service
                    if today_high is not None:
                        if entry['direction'] == 'l':
                            peak = max(peak, today_high)
                        else:
                            peak = min(peak, today_high)
                    entry['peak_price'] = peak
                    if entry['direction'] == 'l':
                        entry['peak_return'] = round(
                            (peak - entry['start_price']) / entry['start_price'] * 100, 2
                        )
                    else:
                        entry['peak_return'] = round(
                            (entry['start_price'] - peak) / entry['start_price'] * 100, 2
                        )
                    changed = True

    if changed:
        save_history(history)

    return history


def compute_stats(history):
    """Compute aggregate stats for the stat boxes."""
    closed = [e for e in history if e.get('status') == 'closed' and e.get('actual_return') is not None]
    total_picks = len(history)
    wins = [e for e in closed if e.get('win')]
    win_rate = (len(wins) / len(closed) * 100) if closed else 0
    avg_return = (sum(e['actual_return'] for e in closed) / len(closed)) if closed else 0

    # Current streak (consecutive wins from most recent)
    streak = 0
    streak_type = 'W'
    sorted_closed = sorted(closed, key=lambda x: x['featured_date'], reverse=True)
    if sorted_closed:
        first_win = sorted_closed[0].get('win', False)
        streak_type = 'W' if first_win else 'L'
        for e in sorted_closed:
            if e.get('win') == first_win:
                streak += 1
            else:
                break

    return {
        'total_picks': total_picks,
        'win_rate': round(win_rate, 1),
        'avg_return': round(avg_return, 1),
        'current_streak': '%d%s' % (streak, streak_type) if streak else '--',
        'closed_count': len(closed),
        'open_count': len([e for e in history if e.get('status') == 'open']),
    }


def build_positions(history):
    """Build open and closed position lists for the template."""
    open_positions = []
    closed_positions = []

    for entry in history:
        row = {
            'featured_date': entry.get('featured_date', ''),
            'symbol': entry.get('symbol', ''),
            'company_name': entry.get('company_name', ''),
            'direction': 'Long' if entry.get('direction') == 'l' else 'Short',
            'days': entry.get('daysOut', 0),
            'win_prob': '%.1f' % (entry.get('win_prob', 0) * 100),
            'pred_return': '%.1f' % entry.get('pred_return', 0),
            'wave_viewer_url': entry.get('wave_viewer_url', ''),
        }

        # Success column: green check if peak >= predicted, red X only for closed failures
        peak_ret = entry.get('peak_return')
        pred_ret = entry.get('pred_return', 0)
        if peak_ret is not None and peak_ret >= pred_ret:
            row['success'] = 'yes'
        elif entry.get('status') == 'closed' and entry.get('actual_return') is not None and entry.get('actual_return') < 0:
            row['success'] = 'no'
        else:
            row['success'] = 'pending'

        if entry.get('status') == 'open':
            row['current_return'] = '%.1f' % entry.get('current_return', 0) if entry.get('current_return') is not None else '--'
            row['current_return_num'] = entry.get('current_return', 0)
            row['peak_return'] = '%.1f' % entry.get('peak_return', 0) if entry.get('peak_return') is not None else '--'
            row['peak_return_num'] = entry.get('peak_return', 0)
            row['price_source'] = entry.get('price_source', 'close')
            open_positions.append(row)
        elif entry.get('status') == 'closed':
            row['actual_return'] = '%.1f' % entry.get('actual_return', 0) if entry.get('actual_return') is not None else '--'
            row['actual_return_num'] = entry.get('actual_return', 0)
            row['peak_return'] = '%.1f' % entry.get('peak_return', 0) if entry.get('peak_return') is not None else '--'
            row['peak_return_num'] = entry.get('peak_return', 0)
            row['win'] = entry.get('win', False)
            row['wl'] = 'W' if entry.get('win') else 'L'
            closed_positions.append(row)

    # Sort: open by date ascending, closed by date descending (newest first)
    open_positions.sort(key=lambda x: x['featured_date'])
    closed_positions.sort(key=lambda x: x['featured_date'], reverse=True)

    return open_positions, closed_positions


# =============================================================================
# HTML GENERATION
# =============================================================================

def generate_scorecard_html(stats, open_positions, closed_positions):
    """Generate the scorecard HTML page."""
    jinja_env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=True,
    )
    template = jinja_env.get_template('scorecard.html')

    content = {
        'stats': stats,
        'open_positions': open_positions,
        'closed_positions': closed_positions,
        'domain_root': DOMAIN_ROOT,
        'x_profile_url': X_PROFILE_URL,
        'favicon': config.tw_favicon,
        'daily_ai_pick_group_id': '182221521780999195',
    }

    return template.render(content=content)


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("TradeWave AI Scorecard Generator")
    print("   History: %s" % FEATURED_HISTORY_FILE)
    print("   Output: %s%s" % (OUTPUT_DIR, OUTPUT_FILENAME))
    print()

    # 1. Load history
    history = load_history()
    if not history:
        print("   No history entries found. Generating empty scorecard.")

    # 2. Login to appserver
    print("   Logging in to appserver...")
    token = appserver_login()
    if token is None:
        print("   WARNING: Appserver login failed. Generating with cached data only.")
    else:
        # 3. Enrich with prices
        print("   Enriching %d positions with price data..." % len(history))
        history = enrich_positions(history, token)

    # 4. Compute stats
    stats = compute_stats(history)
    print("   Stats: %d picks, %.1f%% win rate, %.1f%% avg return, streak: %s" % (
        stats['total_picks'], stats['win_rate'], stats['avg_return'], stats['current_streak']))

    # 5. Build position tables
    open_positions, closed_positions = build_positions(history)
    print("   Open: %d, Closed: %d" % (len(open_positions), len(closed_positions)))

    # 6. Generate HTML
    print("   Generating HTML...")
    html = generate_scorecard_html(stats, open_positions, closed_positions)

    # 7. Save
    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / OUTPUT_FILENAME
    output_path.write_text(html)

    print("   Generated: %s" % output_path)
    print("   Size: %d bytes" % len(html))
    print("   Done!")


if __name__ == "__main__":
    main()
