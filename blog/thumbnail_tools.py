# -*- coding: utf-8 -*-
"""
thumbnail_tools.py
Builds a payload of real data for thumbnail_renderer.py.

Includes:
- Windowed seasonal trend (%), normalized to 0 at day 0
- Window cumulative curve from year-by-year window returns
- Price points (recent ~60d)
- Trade direction (long/short) for ticker color
"""
# get_seasonal_chart_data2 uses both chart_start_date and opp_start_date, while get_seasonal_chart_data uses 1 date for both
from create_report import get_seasonal_chart_data, get_chart_data
from create_report import get_keyprovider_token, login_appserver
import datetime
from datetime import timedelta
import requests
import sys
sys.path.insert(0, '/home/flask')
import config


# ---- seasonal trend window margin (days) ----
TREND_MARGIN_DAYS = 55 # ← adjust 3w default here

def build_trend_segment(labels, values, start_date, days_hold, margin_days):
    """
    Slice the seasonal series to [start - margin_days, end + margin_days],
    wrapping around the year if needed. Returns:
      seg_labels, seg_values, (hl_start_idx, hl_end_idx)
    where hl_* are indexes INSIDE the segment for shading the trade window.
    """
    if not labels or not values:
        return [], [], (0, -1)

    n = len(labels)
    try:
        idx0 = labels.index(start_date)
    except ValueError:
        idx0 = 0

    left = int(margin_days)
    right = int(margin_days)
    hold = int(days_hold)
    total_len = left + hold + right

    seg_labels, seg_values = [], []
    for i in range(-left, hold + right):
        idx = (idx0 + i) % n
        seg_labels.append(labels[idx])
        seg_values.append(float(values[idx] or 0.0))

    # highlight window is the middle chunk
    hl_start = left
    hl_end   = left + hold - 1
    return seg_labels, seg_values, (hl_start, hl_end)

# ---------------------------------------------------------------------
def inc_date_day(d, i):
    return (datetime.datetime.strptime(d, '%Y-%m-%d') + timedelta(days=i)).strftime('%Y-%m-%d')

# ---------------------------------------------------------------------
def get_cumulative_chart_data(barData):
    """Builds cumulative curve over the window using year-by-year pct for the window."""
    opp_dir = barData['stats']['Trade Dir']
    out = []
    cr = 1.0
    for pp in barData['ChartData4']:
        p = float(pp['pct'].split(',')[0])
        if opp_dir == 'short':
            cr *= (1 + (-p / 100.0))
        else:
            cr *= (1 + (p / 100.0))
        out.append((cr * 100.0) - 100.0)
    return out

# ---------------------------------------------------------------------
def get_chart_historical_prices(fid, symbol, d0, d1, appserver_token):
    url = f"{config.appserver_url}/ChartHistorical2/{fid}/{symbol}/{d0}/{d1}?token={appserver_token}"
    r = requests.get(url)
    j = r.json()
    rows = j.get('ChartHistorical2') or []
    out = []
    for row in rows:
        # expected: ['YYYY-MM-DD', price, 0]
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            out.append((str(row[0]), float(row[1])))
    return out

# ---------------------------------------------------------------------
def f2(x, default=0.0):
    try:
        s = str(x).replace('%', '').replace('+', '').replace(',', '').strip()
        return float(s)
    except Exception:
        return default

def build_stats_triplet(cdata):
    sharpe  = cdata['stats'].get('Sharpe') or cdata['stats'].get('Sharpe Ratio') or 0.0
    sharpe2 = cdata['stats'].get('Sharpe Ratio2') or 0.0

    w = int(cdata['stats'].get('Num Winners', 0))
    l = int(cdata['stats'].get('Num Losers', 0))
    winrate = f"{round(100.0*w/(w+l))}%" if (w+l) else "—"

    cum_raw = cdata['stats'].get('Cumulative') or cdata['stats'].get('Cumulative Return')
    cum_val = f2(cum_raw, 0.0)
    cum_txt = f"{cum_val:+.0f}%"

    return {"sharpe": f"{f2(sharpe):.2f}", "sharpe2": f"{f2(sharpe2):.2f}","winrate": winrate, "cumulative": cum_txt}

# ---------------------------------------------------------------------
# unncecessary function created by AI that caused all trend chart issues discovered on 11/4/2025

# def build_windowed_seasonal_trend(labels, values, date1, days_hold):
#     """
#     Take the seasonal series (full year), align to date1, slice for window length,
#     and normalize to % change from the first point (starts at ~0%).
#     """
#     if not labels or not values:
#         return []

#     try:
#         idx0 = labels.index(date1)
#     except ValueError:
#         idx0 = 0

#     n = int(days_hold)
#     seg = [float(v) for v in values[idx0:idx0+n]]
#     if len(seg) < n:
#         seg += [float(v) for v in values[:n-len(seg)]]  # wrap into next year if needed

#     base = seg[0] if seg else 1.0
#     if base == 0:
#         base = 1.0
#     return [ (v/base - 1.0)*100.0 for v in seg ]

# ---------------------------------------------------------------------
def build_thumbnail_payload(financial_group_id, symbol, date1, days_hold, years,price_lookback_days=60, zero_last_year=True):

    # print(financial_group_id, symbol, date1, days_hold, years,price_lookback_days, zero_last_year)
    # exit()

    # years_int = int(years)

    keyprovider_token = get_keyprovider_token()
    appserver_token = login_appserver(keyprovider_token)

    # get TREND_MARGIN_DAYS before date1 as the starting date of the trendchart
    trend_start_date = inc_date_day(date1,-TREND_MARGIN_DAYS)

    # Seasonal curve (full year)
    trend_labels, trend_values = get_seasonal_chart_data(financial_group_id, symbol, years, trend_start_date, appserver_token)

    # Windowed seasonal trend (normalized %)
    # trend_window_pct = build_windowed_seasonal_trend(trend_labels, trend_values, date1, days_hold)

    # NEW: seasonal trend segment (raw, all positive)
    seg_labels, seg_values, seg_hl = build_trend_segment(
        trend_labels, trend_values, date1, days_hold, TREND_MARGIN_DAYS
    )

    # Bar chart (year-by-year window returns)
    days_hold_corrected = str(int(days_hold) - 1)
    # print(type(years))
    cdata = get_chart_data(financial_group_id, date1, symbol, days_hold_corrected, years, zero_last_year, appserver_token)

    # print(cdata['stats'])

    # Success text + avg gain text
    w = int(cdata['stats']['Num Winners'])
    l = int(cdata['stats']['Num Losers'])
    success_text = f"{w} of {w+l}"

    # avg_raw = cdata['stats'].get('Avg Profit - All') or cdata['stats'].get('Avg Profit') or "0%"
    avg_raw = cdata['stats'].get('Avg Profit') or "0%"

    avg_val = f2(avg_raw, 0.0)
    avg_gain_txt = f"{avg_val:.1f}%"

    # print('avg_gain_txt=',avg_gain_txt,avg_val)

    # Cumulative curve over the window built from barData
    cum_data = get_cumulative_chart_data(cdata)

    # Recent price data (~60d)
    d1 = date1
    d0 = inc_date_day(date1, -int(price_lookback_days))
    price_points = get_chart_historical_prices(financial_group_id, symbol, d0, d1, appserver_token)

    # Bar arrays
    bar_years, bar_returns,bar_returns_mfe,bar_returns_mae = [], [], [], []
    for row in cdata['ChartData4']:
        yr      = int(row['year'])
        pct     = float(row['pct'].split(',')[0])
        pct_mfe = float(row['pct'].split(',')[1])
        pct_mae = float(row['pct'].split(',')[2])
        bar_years.append(yr)
        bar_returns.append(pct)
        bar_returns_mfe.append(pct_mfe)
        bar_returns_mae.append(pct_mae)

    # Direction
    trade_dir = (cdata['stats'].get('Trade Dir', 'long') or 'long').lower()
    trade_dir = 'short' if trade_dir.startswith('s') else 'long'

    # Window label
    end_date = inc_date_day(date1, int(days_hold)-1)
    window_text = f"Seasonal Edge | {date1} ➝ {end_date} ({int(days_hold)} Days)"


    return {
        "ticker": symbol,
        # "years_int": years_int, # AI mistake - its blocking pe0,pe1,pe2,pe3
        "years":years,          # this is what it should be - remove above when all code is updated with years instead of years_int
        "avg_gain_txt": avg_gain_txt,
        "success_text": success_text,
        "window_text": window_text,
        "bar_years": bar_years,
        "bar_returns": bar_returns,
        "bar_returns_mfe": bar_returns_mfe,
        "bar_returns_mae": bar_returns_mae,
        "cum_data": cum_data,
        "trend_labels": trend_labels,
        "trend_values": trend_values,
        # "trend_window_pct": trend_window_pct,
        "price_points": price_points,
        "stats_triplet": build_stats_triplet(cdata),
        "trade_dir": trade_dir,
        "trend_segment_labels": seg_labels,
        "trend_segment_values": seg_values,
        "trend_segment_hl": seg_hl,          # (start_idx, end_idx) within the segment
        "trend_margin_days": TREND_MARGIN_DAYS,
    }

    
# ---------------------------------------------------------------------
if __name__ == "__main__":
    # Example opportunity (hardcoded quick test)
    financial_group_id = 5
    date1 = '2026-01-01'
    days_hold = '90'
    symbol = 'SPX'
    years  = 'pe2'

    p = build_thumbnail_payload(financial_group_id, symbol, date1, days_hold, years)
    # print(p['bar_returns'])
    # exit()
    print("ticker:", p["ticker"])
    # print("years_int:", p["years_int"])
    print("years:", p["years"])
    print("avg_gain_txt:", p["avg_gain_txt"])
    print("success_text:", p["success_text"])
    print("window_text:", p["window_text"])
    print("trade_dir:", p["trade_dir"])
    print("bar years:", p["bar_years"])
    print("bar returns:", p["bar_returns"])
    print("cum_data len:", len(p["cum_data"]))
    # print("trend_window_pct len:", len(p["trend_window_pct"]))
    print("price sample:", p["price_points"][:5])
    print("stats:", p["stats_triplet"])
