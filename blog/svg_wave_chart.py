#!/usr/bin/env python3
"""
svg_wave_chart.py

Generate an SVG rendering of the TradeWave Wave Viewer for homepage use.
Two panels with title bars: seasonal bar chart (top) + candlestick price
chart with SMA, projection, volume, and earnings markers (bottom).
Pure SVG string generation — no matplotlib.
"""

import sys
import math
import time
import datetime
from datetime import timedelta
from xml.sax.saxutils import escape as xml_escape
import requests

sys.path.insert(0, '/home/flask')
import config

# ============================================================
# LAYOUT CONSTANTS
# ============================================================

SVG_W       = 1200
SVG_H       = 700
MARGIN      = 10
PANEL_GAP   = 6
TITLE_H     = 30       # height of each title bar
TOP_H       = 288      # top chart area (below title bar)
BOT_H       = 318      # bottom chart area (below title bar)
PANEL_W     = SVG_W - 2 * MARGIN

# Plot area margins inside each chart panel
CHART_LEFT  = 60
CHART_RIGHT = 20
CHART_TOP   = 10
CHART_BOT   = 35

# Volume sub-panel takes bottom 18% of candle chart area
VOL_RATIO   = 0.18

# ============================================================
# COLORS  (React DarkTheme from Common.js)
# ============================================================

PAGE_BG        = "rgb(15,10,21)"
CHART_BG       = "rgb(25,22,35)"
TITLE_BAR_BG   = "rgb(30,50,80)"
GRID_COLOR     = "rgb(50,47,62)"
TEXT_COLOR      = "rgb(180,180,190)"
TEXT_PRIMARY    = "rgb(220,220,225)"
TEXT_HIGHLIGHT  = "rgb(100,220,140)"   # green accent for title text

BAR_GREEN      = "rgb(0,140,0)"
BAR_RED        = "rgb(200,0,0)"
BAR_MFE        = "rgb(100,220,100)"

CANDLE_UP      = "#26a69a"
CANDLE_DOWN    = "#ef5350"
WICK_COLOR     = "rgba(220,220,220,0.85)"

SMA_COLOR      = "#c850c8"
PROJ_COLOR     = "#e8a838"
PROJ_DASH      = "8,4"

VOL_UP_COLOR   = "rgba(38,166,154,0.35)"
VOL_DOWN_COLOR = "rgba(239,83,80,0.35)"

EARN_COLOR     = "#e040e0"     # magenta for earnings badges
EARN_EST_COLOR = "#e8a838"     # orange for estimated earnings

# ============================================================
# AUTHENTICATION  (same pattern as create_report.py)
# ============================================================

def get_keyprovider_token():
    url = config.appserver_url + '/login/2/3/4/5/6'
    result = requests.get(url).json()
    return result['message'].split(' ')[4]


def login_appserver(keyprovider_token):
    url = config.appserver_url + '/login/28/3/4/5/' + keyprovider_token
    result = requests.get(url).json()
    if 'message' in result:
        time.sleep(10)
        result = requests.get(url).json()
        if 'message' in result:
            print('login failed:', result['message'])
            return None
    return result['token']


def get_appserver_token():
    kp = get_keyprovider_token()
    return login_appserver(kp)

# ============================================================
# DATA FETCHING
# ============================================================

def fetch_bar_data(resource_id, date, symbol, days_out, years, token):
    """Fetch ChartData4 — year-by-year bar chart with MFE/MAE."""
    url = (f"{config.appserver_url}/ChartData4/{resource_id}/{date}"
           f"/{symbol}/{days_out}/{years}?token={token}")
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json()


def fetch_ohlc_data(resource_id, symbol, d0, d1, token):
    """Fetch ChartHistorical2 — OHLC candle data."""
    url = (f"{config.appserver_url}/ChartHistorical2/{resource_id}"
           f"/{symbol}/{d0}/{d1}?token={token}")
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json().get('ChartHistorical2', [])


def fetch_seasonal_data(resource_id, symbol, years, chart_start, opp_start, token):
    """Fetch consolidated_seasonal_chart2 — seasonal projection data."""
    url = (f"{config.appserver_url}/consolidated_seasonal_chart2/{resource_id}"
           f"/{symbol}/{years}/{chart_start}/{opp_start}?token={token}")
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json().get('cons_seas_chart', [])

# ============================================================
# DATA PROCESSING
# ============================================================

def compute_sma(closes, period=50):
    """Simple Moving Average.  Returns list same length as closes; early entries are None."""
    result = []
    for i in range(len(closes)):
        if i < period - 1:
            result.append(None)
        else:
            window = closes[i - period + 1: i + 1]
            result.append(sum(window) / period)
    return result


def compute_projection(last_close, last_date, seasonal_data, period_days=30):
    """Seasonal projection from last close price.

    Walks the seasonal cycle by ARRAY INDEX (not by MM-DD) so the cycle
    boundary stays continuous; cumulative_offset carries the cycle's annual
    drift across each wrap.
    """
    if not seasonal_data:
        return []

    cycle_len = len(seasonal_data)
    mmdd_to_idx = {}
    for i, row in enumerate(seasonal_data):
        mmdd = row[0][5:]
        if mmdd not in mmdd_to_idx:
            mmdd_to_idx[mmdd] = i

    today_mmdd = last_date[5:]
    today_idx = mmdd_to_idx.get(today_mmdd)
    if today_idx is None:
        sorted_mmdds = sorted(mmdd_to_idx.keys())
        closest = None
        for k in sorted_mmdds:
            if k <= today_mmdd:
                closest = k
            else:
                break
        if closest is None:
            closest = sorted_mmdds[-1]
        today_idx = mmdd_to_idx[closest]

    today_return = float(seasonal_data[today_idx][1])
    cycle_drift = float(seasonal_data[cycle_len - 1][1]) - float(seasonal_data[0][1])

    last_dt = datetime.datetime.strptime(last_date, "%Y-%m-%d")
    future_dts = []
    d = last_dt
    while len(future_dts) < period_days:
        d += timedelta(days=1)
        if d.weekday() >= 5:
            continue
        future_dts.append(d)

    points = []
    cycle_idx = today_idx
    cumulative_offset = 0.0
    prev_dt = last_dt
    for fdt in future_dts:
        days_diff = (fdt - prev_dt).days
        cycle_idx += days_diff
        while cycle_idx >= cycle_len:
            cycle_idx -= cycle_len
            cumulative_offset += cycle_drift
        future_return = float(seasonal_data[cycle_idx][1]) + cumulative_offset
        proj_price = last_close * (1 + (future_return - today_return) / 100)
        points.append((fdt.strftime("%Y-%m-%d"), proj_price))
        prev_dt = fdt

    return points


def format_years_label(years_str):
    """Format years param for title bar display."""
    if years_str.isdigit():
        return f"{years_str} Years", "Consecutive"
    if '-' in years_str:
        parts = years_str.split('-')
        pe_code = parts[0].upper().replace('PE', 'PE+')  # pe2 → PE+2
        count = parts[1]
        return f"{count} Years", f"{pe_code} Years"
    # Legacy: just "pe2"
    pe_code = years_str.upper().replace('PE', 'PE+')
    return "All Years", f"{pe_code} Years"


def compute_end_date(start_date, days):
    """Compute pattern end date (calendar days forward)."""
    dt = datetime.datetime.strptime(start_date, "%Y-%m-%d")
    return (dt + timedelta(days=int(days))).strftime("%m-%d")


def scale_y(value, data_min, data_max, pixel_top, pixel_bottom):
    """Map data value to SVG y-coordinate (inverted: higher value → lower y)."""
    if data_max == data_min:
        return (pixel_top + pixel_bottom) / 2
    ratio = (value - data_min) / (data_max - data_min)
    return pixel_bottom - ratio * (pixel_bottom - pixel_top)


def scale_x(index, count, pixel_left, pixel_right):
    """Map index to SVG x-coordinate (center of slot)."""
    if count <= 1:
        return (pixel_left + pixel_right) / 2
    return pixel_left + index * (pixel_right - pixel_left) / (count - 1)

# ============================================================
# SVG PRIMITIVES
# ============================================================

def svg_rect(x, y, w, h, fill, rx=0, opacity=1.0, stroke="none", stroke_width=0):
    parts = [f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}"']
    if rx:
        parts.append(f' rx="{rx}"')
    if opacity < 1.0:
        parts.append(f' opacity="{opacity:.2f}"')
    if stroke != "none":
        parts.append(f' stroke="{stroke}" stroke-width="{stroke_width}"')
    parts.append('/>')
    return ''.join(parts)


def svg_line(x1, y1, x2, y2, stroke, stroke_width=1, dash=""):
    d = f' stroke-dasharray="{dash}"' if dash else ''
    return (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{stroke}" stroke-width="{stroke_width}"{d}/>')


def svg_polyline(points, stroke, stroke_width=1.5, fill="none", dash=""):
    if not points:
        return ''
    pts = ' '.join(f'{x:.1f},{y:.1f}' for x, y in points)
    d = f' stroke-dasharray="{dash}"' if dash else ''
    return (f'<polyline points="{pts}" stroke="{stroke}" '
            f'stroke-width="{stroke_width}" fill="{fill}"{d} stroke-linejoin="round"/>')


def svg_text(x, y, text, fill=TEXT_COLOR, font_size=11, anchor="middle",
             font_weight="normal"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" fill="{fill}" font-size="{font_size}" '
            f'text-anchor="{anchor}" font-weight="{font_weight}">{xml_escape(str(text))}</text>')

# ============================================================
# TITLE BARS
# ============================================================

def render_top_title_bar(symbol, start_date, days_out, years_str,
                         bar_x, bar_y, bar_w, bar_h):
    """Top title bar: centered pattern summary + right-side params."""
    elements = []
    elements.append(svg_rect(bar_x, bar_y, bar_w, bar_h, TITLE_BAR_BG, rx=8))

    cy = bar_y + bar_h / 2 + 4
    cx = bar_x + bar_w / 2       # horizontal center

    years_label, mode_label = format_years_label(years_str)
    start_mmdd = start_date[5:]
    end_mmdd = compute_end_date(start_date, days_out)
    num_years = years_label.split()[0]

    # Centered pattern summary: "10-Year  TAP  03-03 to 03-16    ■ MFE"
    # Build as one centered group offset from center
    full_text = f"{num_years}-Year   {symbol}   {start_mmdd} to {end_mmdd}"
    text_w = len(full_text) * 6.8  # approximate width
    x = cx - text_w / 2 - 30      # shift left a bit to make room for MFE legend

    elements.append(svg_text(x, cy, f"{num_years}-Year", TEXT_HIGHLIGHT, 14, "start", "bold"))
    x += len(f"{num_years}-Year") * 8 + 12
    elements.append(svg_text(x, cy, symbol, TEXT_PRIMARY, 15, "start", "bold"))
    x += len(symbol) * 9.5 + 12
    elements.append(svg_text(x, cy, f"{start_mmdd} to {end_mmdd}",
                             TEXT_HIGHLIGHT, 14, "start"))
    x += len(f"{start_mmdd} to {end_mmdd}") * 7.5 + 20

    # MFE legend
    elements.append(svg_rect(x, cy - 8, 10, 10, BAR_MFE))
    elements.append(svg_text(x + 15, cy, "MFE", TEXT_PRIMARY, 12, "start"))

    # Right side: "10 PE+2 Years | 14 Day Pattern" separated by vertical bar
    rx = bar_x + bar_w - 20
    params = [f"{num_years} {mode_label}", f"{days_out} Day Pattern"]
    for i, p in enumerate(reversed(params)):
        elements.append(svg_text(rx, cy, p, TEXT_COLOR, 12, "end"))
        rx -= len(p) * 6 + 20
        if i < len(params) - 1:
            elements.append(svg_line(rx + 8, bar_y + 7, rx + 8, bar_y + bar_h - 7,
                                     GRID_COLOR, 1))

    return '\n  '.join(elements)


def render_bottom_title_bar(company_name, bar_x, bar_y, bar_w, bar_h):
    """Bottom title bar: centered company name + pills + right-side legend."""
    elements = []
    elements.append(svg_rect(bar_x, bar_y, bar_w, bar_h, TITLE_BAR_BG, rx=8))

    cy = bar_y + bar_h / 2 + 4
    cx = bar_x + bar_w / 2

    # --- Centered: company name + "Current Price Chart" + pills ---
    # Estimate total width of the centered group
    name_w = len(company_name) * 7.8
    cpc_w = len("Current Price Chart") * 7.2
    pills_data = [("3M", False), ("6M", False), ("1Y", True), ("2Y", False),
                  ("D", False), ("E", False), ("Proj", False)]
    pills_w = sum(len(p[0]) * 8 + 14 for p in pills_data)
    total_group_w = name_w + 10 + cpc_w + 16 + pills_w
    x = cx - total_group_w / 2

    # Company name
    elements.append(svg_text(x, cy, company_name, TEXT_HIGHLIGHT, 13, "start", "bold"))
    x += name_w + 10

    # "Current Price Chart"
    elements.append(svg_text(x, cy, "Current Price Chart", TEXT_PRIMARY, 13, "start"))
    x += cpc_w + 16

    # Timeframe pills
    for label, active in pills_data[:-1]:  # all except Proj
        pw = len(label) * 8 + 10
        ph = 16
        fill = "rgba(100,220,140,0.2)" if active else "rgba(255,255,255,0.08)"
        text_fill = TEXT_HIGHLIGHT if active else TEXT_COLOR
        elements.append(svg_rect(x, cy - 11, pw, ph, fill, rx=3))
        elements.append(svg_text(x + pw / 2, cy, label, text_fill, 11))
        x += pw + 4

    # Proj pill (orange)
    pw = 32
    elements.append(svg_rect(x, cy - 11, pw, 16, "rgba(232,168,56,0.2)", rx=3))
    elements.append(svg_text(x + pw / 2, cy, "Proj", PROJ_COLOR, 11))

    # --- Right side legend: SMA + Projection lines ---
    rx = bar_x + bar_w - 20
    elements.append(svg_text(rx, cy, "Seasonal Projection", TEXT_COLOR, 11, "end"))
    rx -= 135
    elements.append(svg_line(rx, cy - 3, rx + 20, cy - 3, PROJ_COLOR, 2, PROJ_DASH))
    rx -= 20
    elements.append(svg_text(rx, cy, "50 SMA", TEXT_COLOR, 11, "end"))
    rx -= 52
    elements.append(svg_line(rx, cy - 3, rx + 20, cy - 3, SMA_COLOR, 2))

    return '\n  '.join(elements)

# ============================================================
# TOP PANEL — SEASONAL BAR CHART (MFE only, no MAE)
# ============================================================

def _nice_ticks(data_min, data_max, target_count=5):
    """Generate nice round tick values for an axis."""
    data_range = data_max - data_min
    if data_range <= 0:
        return [0]
    raw_step = data_range / target_count
    mag = 10 ** math.floor(math.log10(raw_step))
    residual = raw_step / mag
    if residual <= 1.5:
        nice_step = 1 * mag
    elif residual <= 3:
        nice_step = 2 * mag
    elif residual <= 7:
        nice_step = 5 * mag
    else:
        nice_step = 10 * mag

    start = math.floor(data_min / nice_step) * nice_step
    ticks = []
    v = start
    while v <= data_max + nice_step * 0.01:
        ticks.append(round(v, 6))
        v += nice_step
    return ticks


def render_bar_chart(bar_data, panel_x, panel_y, panel_w, panel_h):
    """Render the seasonal bar chart with MFE overlay (no MAE)."""
    elements = []
    n = len(bar_data)
    if n == 0:
        return ''

    # Plot area
    px_left   = panel_x + CHART_LEFT
    px_right  = panel_x + panel_w - CHART_RIGHT
    px_top    = panel_y + CHART_TOP
    px_bottom = panel_y + panel_h - CHART_BOT
    plot_w    = px_right - px_left

    # Parse data
    parsed = []
    for row in bar_data:
        parts = row['pct'].split(',')
        close_pct = float(parts[0])
        mfe_pct   = float(parts[1])
        parsed.append((int(row['year']), close_pct, mfe_pct))

    # Y-axis range (only close and MFE — no MAE)
    all_vals = [0.0]
    for _, c, mfe in parsed:
        all_vals.extend([c, mfe])
    y_min = min(all_vals) * 1.15 if min(all_vals) < 0 else min(all_vals) - 0.5
    y_max = max(all_vals) * 1.15 if max(all_vals) > 0 else max(all_vals) + 0.5
    if y_min > 0:
        y_min = 0

    # Gridlines and Y-axis labels
    ticks = _nice_ticks(y_min, y_max, 5)
    for t in ticks:
        yy = scale_y(t, y_min, y_max, px_top, px_bottom)
        elements.append(svg_line(px_left, yy, px_right, yy, GRID_COLOR, 0.5))
        label = f"{t:.0f}%" if t == int(t) else f"{t:.1f}%"
        elements.append(svg_text(px_left - 6, yy + 4, label, TEXT_COLOR, 11, "end"))

    # Zero line
    y_zero = scale_y(0, y_min, y_max, px_top, px_bottom)
    elements.append(svg_line(px_left, y_zero, px_right, y_zero, "rgb(70,67,82)", 0.8))

    # Bars
    bar_spacing = plot_w / n
    bar_width   = bar_spacing * 0.7

    for i, (year, close_pct, mfe_pct) in enumerate(parsed):
        cx = px_left + i * bar_spacing + bar_spacing / 2
        y_close = scale_y(close_pct, y_min, y_max, px_top, px_bottom)
        y_mfe   = scale_y(mfe_pct, y_min, y_max, px_top, px_bottom)

        # Main bar (close return: 0 → close%)
        bar_color = BAR_GREEN if close_pct >= 0 else BAR_RED
        bar_top = min(y_zero, y_close)
        bar_h   = abs(y_close - y_zero)
        if bar_h < 0.5:
            bar_h = 0.5
        elements.append(svg_rect(cx - bar_width / 2, bar_top, bar_width, bar_h, bar_color))

        # MFE overlay (favorable excursion above close)
        if close_pct >= 0 and mfe_pct > close_pct:
            mfe_top = y_mfe
            mfe_h   = y_close - y_mfe
            if mfe_h > 0.5:
                elements.append(svg_rect(cx - bar_width / 2, mfe_top, bar_width, mfe_h, BAR_MFE))
        elif close_pct < 0 and mfe_pct > 0:
            mfe_top = y_mfe
            mfe_h   = y_zero - y_mfe
            if mfe_h > 0.5:
                elements.append(svg_rect(cx - bar_width / 2, mfe_top, bar_width, mfe_h, BAR_MFE))

    # X-axis year labels
    max_labels = 12
    step = max(1, math.ceil(n / max_labels))
    for i, (year, _, _) in enumerate(parsed):
        if i % step == 0:
            cx = px_left + i * bar_spacing + bar_spacing / 2
            elements.append(svg_text(cx, px_bottom + 16, str(year), TEXT_COLOR, 11))

    return '\n  '.join(elements)

# ============================================================
# BOTTOM PANEL — CANDLESTICK + VOLUME + EARNINGS
# ============================================================

def render_candle_chart(ohlc_data, sma_values, projection_points,
                        earnings_dates, next_earnings_est,
                        panel_x, panel_y, panel_w, panel_h):
    """Render candlestick chart with SMA, projection, volume, and earnings."""
    elements = []
    n_candles = len(ohlc_data)
    n_proj    = len(projection_points)
    total_x   = n_candles + n_proj
    if n_candles == 0:
        return ''

    # Plot area — split into price zone (top) and volume zone (bottom)
    px_left   = panel_x + CHART_LEFT
    px_right  = panel_x + panel_w - CHART_RIGHT
    px_top    = panel_y + CHART_TOP
    px_bottom = panel_y + panel_h - CHART_BOT
    chart_h   = px_bottom - px_top

    vol_top    = px_bottom - chart_h * VOL_RATIO
    price_bot  = vol_top - 4   # small gap between price and volume

    # Build date→index lookup for earnings markers
    date_to_idx = {}
    for i, row in enumerate(ohlc_data):
        date_to_idx[row[0]] = i

    # --- Y-axis range for prices ---
    all_prices = []
    for row in ohlc_data:
        all_prices.append(float(row[2]))  # high
        all_prices.append(float(row[3]))  # low
    for v in sma_values:
        if v is not None:
            all_prices.append(v)
    for _, price in projection_points:
        all_prices.append(price)

    price_min = min(all_prices) * 0.997
    price_max = max(all_prices) * 1.003

    # --- Volume range ---
    volumes = [float(row[5]) if len(row) > 5 else 0 for row in ohlc_data]
    vol_max = max(volumes) if volumes else 1

    # --- Gridlines and Y-axis price labels ---
    ticks = _nice_ticks(price_min, price_max, 5)
    for t in ticks:
        yy = scale_y(t, price_min, price_max, px_top, price_bot)
        elements.append(svg_line(px_left, yy, px_right, yy, GRID_COLOR, 0.5))
        if t >= 1000:
            label = f"{t:,.0f}"
        elif t >= 10:
            label = f"{t:.1f}"
        else:
            label = f"{t:.2f}"
        elements.append(svg_text(px_left - 6, yy + 4, label, TEXT_COLOR, 11, "end"))

    # --- Volume bars ---
    candle_width = max(1, (px_right - px_left) / total_x * 0.65)
    vol_h_area = px_bottom - vol_top

    for i, row in enumerate(ohlc_data):
        vol = float(row[5]) if len(row) > 5 else 0
        if vol <= 0:
            continue
        cx = scale_x(i, total_x, px_left, px_right)
        o, c = float(row[1]), float(row[4])
        is_up = c >= o
        vh = (vol / vol_max) * vol_h_area
        vy = px_bottom - vh
        color = VOL_UP_COLOR if is_up else VOL_DOWN_COLOR
        elements.append(svg_rect(cx - candle_width / 2, vy, candle_width, vh, color))

    # --- Candlesticks ---
    for i, row in enumerate(ohlc_data):
        date_str = row[0]
        o, h, l, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
        cx = scale_x(i, total_x, px_left, px_right)

        y_high  = scale_y(h, price_min, price_max, px_top, price_bot)
        y_low   = scale_y(l, price_min, price_max, px_top, price_bot)
        y_open  = scale_y(o, price_min, price_max, px_top, price_bot)
        y_close = scale_y(c, price_min, price_max, px_top, price_bot)

        is_up = c >= o
        color = CANDLE_UP if is_up else CANDLE_DOWN

        # Wick
        elements.append(svg_line(cx, y_high, cx, y_low, WICK_COLOR, 1))
        # Body
        body_top = min(y_open, y_close)
        body_h   = max(1, abs(y_close - y_open))
        elements.append(svg_rect(cx - candle_width / 2, body_top,
                                 candle_width, body_h, color))

    # --- 50 SMA line ---
    sma_points = []
    for i, val in enumerate(sma_values):
        if val is not None:
            sx = scale_x(i, total_x, px_left, px_right)
            sy = scale_y(val, price_min, price_max, px_top, price_bot)
            sma_points.append((sx, sy))
    if sma_points:
        elements.append(svg_polyline(sma_points, SMA_COLOR, 1.5))

    # --- Projection line ---
    if projection_points and n_candles > 0:
        last_close = float(ohlc_data[-1][4])
        last_x = scale_x(n_candles - 1, total_x, px_left, px_right)
        last_y = scale_y(last_close, price_min, price_max, px_top, price_bot)

        proj_pts = [(last_x, last_y)]
        for j, (pdate, pprice) in enumerate(projection_points):
            px = scale_x(n_candles + j, total_x, px_left, px_right)
            py = scale_y(pprice, price_min, price_max, px_top, price_bot)
            proj_pts.append((px, py))
        elements.append(svg_polyline(proj_pts, PROJ_COLOR, 2, dash=PROJ_DASH))

    # --- Earnings markers (vertical line + magenta "E" badge) ---
    for ed in (earnings_dates or []):
        edate = ed.get('date', '')[:10]
        idx = date_to_idx.get(edate)
        if idx is not None:
            ex = scale_x(idx, total_x, px_left, px_right)
            # Dashed vertical line full chart height
            elements.append(svg_line(ex, px_top + 16, ex, px_bottom,
                                     EARN_COLOR, 0.8, "4,3"))
            # Badge at top of chart
            elements.append(svg_rect(ex - 8, px_top - 2, 16, 16, EARN_COLOR, rx=3))
            elements.append(svg_text(ex, px_top + 11, "E", "white", 10, "middle", "bold"))

    # Estimated next earnings (vertical line + "Est" badge)
    if next_earnings_est:
        est_date = next_earnings_est[:10]
        idx = date_to_idx.get(est_date)
        if idx is not None:
            ex = scale_x(idx, total_x, px_left, px_right)
            elements.append(svg_line(ex, px_top + 16, ex, px_bottom,
                                     EARN_EST_COLOR, 0.8, "4,3"))
            elements.append(svg_rect(ex - 12, px_top - 2, 24, 16, EARN_EST_COLOR, rx=3))
            elements.append(svg_text(ex, px_top + 11, "Est", "white", 9, "middle", "bold"))
        else:
            # Est date is in projection zone
            est_dt = datetime.datetime.strptime(est_date, "%Y-%m-%d").date()
            last_dt = datetime.datetime.strptime(ohlc_data[-1][0], "%Y-%m-%d").date()
            if est_dt > last_dt:
                days_ahead = (est_dt - last_dt).days
                proj_idx = min(days_ahead, n_proj - 1) if n_proj > 0 else 0
                if proj_idx >= 0:
                    ex = scale_x(n_candles + proj_idx, total_x, px_left, px_right)
                    elements.append(svg_line(ex, px_top + 16, ex, px_bottom,
                                             EARN_EST_COLOR, 0.8, "4,3"))
                    elements.append(svg_rect(ex - 12, px_top - 2, 24, 16,
                                             EARN_EST_COLOR, rx=3))
                    elements.append(svg_text(ex, px_top + 11, "Est", "white", 9,
                                             "middle", "bold"))

    # --- X-axis date labels ---
    all_dates = [row[0] for row in ohlc_data]
    all_dates += [d for d, _ in projection_points]
    max_labels = 8
    step = max(1, len(all_dates) // max_labels)
    for i in range(0, len(all_dates), step):
        cx = scale_x(i, total_x, px_left, px_right)
        label = all_dates[i][5:]
        elements.append(svg_text(cx, px_bottom + 16, label, TEXT_COLOR, 11))

    return '\n  '.join(elements)

# ============================================================
# MAIN ASSEMBLY
# ============================================================

def generate_wave_chart_svg(resource_id, symbol, start_date, days_out, years,
                            company_name=None, ohlc_lookback=250,
                            projection_days=45, output_path=None):
    """
    Main entry point.  Fetch data, render two-panel SVG, return string.
    Optionally saves to output_path.
    """
    token = get_appserver_token()
    if not token:
        raise RuntimeError("Failed to authenticate with appserver")

    # 1. Bar chart data + stats (includes earnings)
    bar_json = fetch_bar_data(resource_id, start_date, symbol,
                              str(int(days_out) - 1), years, token)
    bar_data = bar_json.get('ChartData4', [])
    stats = bar_json.get('stats', {})

    earnings_dates = stats.get('earnings_filings', [])
    next_earnings_est = stats.get('next_earnings_est')

    if not company_name:
        company_name = symbol

    # 2. OHLC candle data (extra lookback for SMA warmup)
    today = datetime.date.today()
    lookback_cal = int(ohlc_lookback * 1.6) + 80
    d0 = (today - timedelta(days=lookback_cal)).isoformat()
    d1 = today.isoformat()
    ohlc_raw = fetch_ohlc_data(resource_id, symbol, d0, d1, token)

    # Compute SMA on full data, then trim to display window
    all_closes = [float(row[4]) for row in ohlc_raw]
    sma_full = compute_sma(all_closes, 50)

    display_start = max(0, len(ohlc_raw) - ohlc_lookback)
    ohlc_display = ohlc_raw[display_start:]
    sma_display  = sma_full[display_start:]

    # 3. Seasonal projection
    last_date = ohlc_display[-1][0] if ohlc_display else today.isoformat()
    chart_start = (datetime.datetime.strptime(start_date, "%Y-%m-%d")
                   - timedelta(days=14)).strftime("%Y-%m-%d")
    seasonal_raw = fetch_seasonal_data(resource_id, symbol, years,
                                       chart_start, start_date, token)
    last_close = float(ohlc_display[-1][4]) if ohlc_display else 0
    projection = compute_projection(last_close, last_date, seasonal_raw,
                                    projection_days)

    # Layout positions
    # Top title bar
    ttb_x, ttb_y = MARGIN, MARGIN
    # Top chart panel
    tcp_x = MARGIN
    tcp_y = MARGIN + TITLE_H
    # Bottom title bar
    btb_x = MARGIN
    btb_y = MARGIN + TITLE_H + TOP_H + PANEL_GAP
    # Bottom chart panel
    bcp_x = MARGIN
    bcp_y = btb_y + TITLE_H

    # Build SVG sections
    top_title  = render_top_title_bar(symbol, start_date, days_out, years,
                                      ttb_x, ttb_y, PANEL_W, TITLE_H)
    bar_svg    = render_bar_chart(bar_data, tcp_x, tcp_y, PANEL_W, TOP_H)
    bot_title  = render_bottom_title_bar(company_name,
                                         btb_x, btb_y, PANEL_W, TITLE_H)
    candle_svg = render_candle_chart(ohlc_display, sma_display, projection,
                                     earnings_dates, next_earnings_est,
                                     bcp_x, bcp_y, PANEL_W, BOT_H)

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SVG_W} {SVG_H}"
     preserveAspectRatio="xMidYMid meet"
     style="background:{PAGE_BG}; border-radius:14px;">
  <defs>
    <style>
      text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    </style>
  </defs>

  <!-- Top title bar -->
  {top_title}

  <!-- Top chart background -->
  {svg_rect(tcp_x, tcp_y, PANEL_W, TOP_H, CHART_BG, rx=0)}

  <!-- Seasonal bar chart -->
  {bar_svg}

  <!-- Bottom title bar -->
  {bot_title}

  <!-- Bottom chart background -->
  {svg_rect(bcp_x, bcp_y, PANEL_W, BOT_H, CHART_BG, rx=0)}

  <!-- Candlestick chart -->
  {candle_svg}

  <!-- TradeWave.AI watermark -->
  {svg_text(bcp_x + PANEL_W - 10, bcp_y + BOT_H - 8, "TradeWave.AI", TEXT_COLOR, 10, "end")}

</svg>'''

    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(svg)
        print(f"SVG written to {output_path} ({len(svg):,} bytes)")

    return svg


# ============================================================
# SMOKE TEST — just run: python svg_wave_chart.py
# ============================================================

if __name__ == "__main__":
    out = "/home/flask/blog/wave_chart_test.svg"
    print(f"Smoke test: TAP, resource 2, 2026-03-03, 14 days, pe2-10")
    svg = generate_wave_chart_svg(
        resource_id="2",
        symbol="TAP",
        start_date="2026-03-03",
        days_out=14,
        years="pe2-10",
        company_name="Molson Coors Brewing Co Class B",
        output_path=out,
    )
    print(f"Open in browser: file://{out}")
