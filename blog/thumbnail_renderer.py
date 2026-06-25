# -*- coding: utf-8 -*-
"""
thumbnail_renderer.py — renders FIVE TradeWave thumbnails:

1) Stats-only (ticker → avg gain → seasonal edge + stacked stats row)
2) Bar chart (year-by-year window returns; negatives red; % axis)
3) Trend chart (windowed seasonal trend; shaded trade window)
4) Price chart (recent lookback; smart date ticks)
5) Cumulative chart (seasonal window cumulative %; turns red if <0)

All layout is percentage-based and width-aware (titles/disclaimer shrink-to-fit).
"""

import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MaxNLocator
import matplotlib.dates as mdates
from dateutil.parser import parse as dtparse
from matplotlib.offsetbox import AnchoredOffsetbox, TextArea, HPacker
from thumbnail_tools import build_thumbnail_payload
from blog_tools import inc_date_day,create_title_slug,get_company_name
import random
import sys
sys.path.insert(0, '/home/flask')
import config


lookback_days = 340  # number or days prior to date0 to create price chart

#---------------------- CTA Lines for bottom of thumbnails --------------------
CTA_LINES = [
    "Act Before the Window Opens!",
    "Unlock This Edge Now!",
    "Catch the Move Before It Starts",
    "History Points to This Pattern",
    "Don’t Miss the Seasonal Edge",
    "Timing Is Everything—Be Early",
    "Ride the Pattern, Not the Noise",
    "Profit from History’s Repeats",
    "Your Edge Is Waiting Here",
    "Seasonality Doesn’t Lie",
    "Get In Before It Moves",
    "This Window Won’t Stay Open",
    "Institutions Know - Now You Do Too",
    "The Pattern Says It All",
    "Seize the Opportunity Before It’s Gone"
]

# CTA layout
CTA_GAP_ABOVE_DISC = -0.010   # distance above disclaimer (fig fraction)
CTA_SIZE_H         = 0.032   # base size (height fraction) before width-fit
# Move brand tag up on stats_only for IG/Pinterest (figure fraction)
BRAND_STATS_UP_TALL = 0.055  # tweak 0.03–0.06 to taste
# ---------------- Size presets (pixels) ----------------
THUMB_SIZES = {
    'tn': (1080, 600),
    'fb': (1200, 630),
    'facebook': (1280, 720),
    'twitter': (1600, 900),
    'twitter_post': (1600, 900),
    'twitter_recommended1': (1080, 1080),
    'twitter_recommended2': (1080, 1350),
    'instagram': (1080, 1080),
    'linkedin': (1200, 627),
    'x': (1600, 900),

    'pinterest': (1000, 1500),
    'youtube': (1280, 720),
}
DEF_DPI = 100

# ---------------- Brand palette ----------------
BG_DARK       = "#0f1216"
TEXT_MAIN     = "#e8ecf1"
ACCENT_GREEN  = "#41d14a"
ACCENT_RED    = "#ef4444"
ACCENT_MID    = "#9aa5b1"
ACCENT_LINE   = "#b8c2cc"
BORDER_SUBTLE = "#2a2f35"
DISCLAIMER_BG = "#ffffff"
DISCLAIMER_TX = "#1f2937"
PRICE_LINE    = "#60a5fa"
PRICE_FILL    = "#60a5fa"

# ---------------- Global layout ----------------
SAFE_TOP      = 0.06
SIDE_PAD      = 0.06

TITLE_SIZE_H  = 0.050   # start sizes (height-based); all titles shrink-to-fit width
SUB_SIZE_H    = 0.030
HEADER_GAP    = 0.110

DISCLAIMER_H  = 0.10
CHART_TOP_PAD = 0.140

CHART_TITLE_Y   = 1.03
CHART_TITLE_PAD = 18

SHOW_STATS = False

# ---------------- Stats header block ----------------
STATS_HDR_ANCHOR_Y = 0.82
STATS_HDR_GAP1     = 0.18
STATS_HDR_GAP2     = 0.10

STATS_TICKER_SIZE  = 0.16
STATS_GAIN_SIZE    = 0.060
STATS_EDGE_SIZE    = 0.038

# ---------------- Bottom stacked stats ----------------
STATS_ANCHOR_Y   = DISCLAIMER_H + 0.245
STATS_GAP        = 0.10
STATS_TITLE_Y    = STATS_ANCHOR_Y
STATS_VALUE_Y    = STATS_ANCHOR_Y - STATS_GAP

STATS_TITLE_SIZE = 0.032
STATS_VALUE_SIZE = 0.060
STATS_COL_X      = [0.17, 0.50, 0.83]

PAD_FOR_STACKED  = 0.22
PAD_FOR_INLINE   = 0.14
PAD_FOR_NONE     = 0.10

# ---------------- Brand tag defaults ----------------
BRAND_DEFAULTS = {
    "stats":      {"show": True, "text": "TradeWave.AI", "loc": "br", "fg": "#cdd6e1", "bg": "#11161b", "alpha": 0.45, "size_h": 0.020, "pad": 0.028},
    "bars":       {"show": True, "text": "TradeWave.AI", "loc": "br", "fg": "#cdd6e1", "bg": "#11161b", "alpha": 0.55, "size_h": 0.022, "pad": 0.015},
    "trend":      {"show": True, "text": "TradeWave.AI", "loc": "br", "fg": "#cdd6e1", "bg": "#11161b", "alpha": 0.55, "size_h": 0.022, "pad": 0.015},
    "price":      {"show": True, "text": "TradeWave.AI", "loc": "br", "fg": "#cdd6e1", "bg": "#11161b", "alpha": 0.55, "size_h": 0.022, "pad": 0.015},
    "cumulative": {"show": True, "text": "TradeWave.AI", "loc": "br", "fg": "#cdd6e1", "bg": "#11161b", "alpha": 0.55, "size_h": 0.022, "pad": 0.015},
}
# ---------------- Bottom stacked stats ----------------
# added to adjust some of the lines in instagram and pinterest versions
STATS_ANCHOR_Y   = DISCLAIMER_H + 0.245
STATS_GAP        = 0.10
STATS_TITLE_Y    = STATS_ANCHOR_Y
STATS_VALUE_Y    = STATS_ANCHOR_Y - STATS_GAP

STATS_TITLE_SIZE = 0.032
STATS_VALUE_SIZE = 0.060
STATS_COL_X      = [0.17, 0.50, 0.83]

# Adjustable offsets for Instagram and Pinterest stats positioning
INSTAGRAM_STATS_OFFSET = 0.05   # much to move stats up for Instagram (1080x1080)
PINTEREST_STATS_OFFSET = 0.05   # How much to move stats up for Pinterest (1000x1500)

# ----- new tunables for portrait safety -----
YLABEL_PAD = 0.065          # extra left pad when y-axis labels are visible
TITLE_Y_LAND = 1.02         # title y inside axes (landscape)
TITLE_Y_PORT = 1.01         # title y inside axes (portrait)
CHAR_W_EST = 0.62           # approx character width (em) for width fit
MAX_HEADLINE_W_FRAC = 1.0 - 2*SIDE_PAD  # usable width for centered headlines

# ----- pushes down the top title line of landscape thumbnails -----
# How far to push ONLY the top line down on landscape (fraction of figure height)
TOPLINE_DOWN_LAND = 0.036   # try 0.015–0.025 to taste   

# ---------------- Utilities ----------------_portrait_extra_left_pad

def _save_exact(fig, out_path, W, H, dpi=DEF_DPI):
    fig.set_size_inches(W / dpi, H / dpi, forward=True)
    fig.savefig(out_path, dpi=dpi, facecolor=fig.get_facecolor(),
                edgecolor="none", bbox_inches=None, pad_inches=0, transparent=False)

def _font_px(W, H, frac_h=None, frac_w=None, min_px=8):
    sizes = []
    if frac_h is not None: sizes.append(H * frac_h)
    if frac_w is not None: sizes.append(W * frac_w)
    return max(min_px, min(sizes) if sizes else min_px)

def _init_figure(W, H):
    fig = plt.figure(figsize=(W/DEF_DPI, H/DEF_DPI), dpi=DEF_DPI)
    fig.patch.set_facecolor(BG_DARK)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_axis_off()
    return fig

def _measure_text_px(fig, text, size_px, weight=900, color=TEXT_MAIN):
    t = fig.text(0, 0, text, fontsize=size_px, weight=weight, color=color)
    fig.canvas.draw()
    w = t.get_window_extent(renderer=fig.canvas.get_renderer()).width
    t.remove()
    return w

def _fit_two_parts(fig, W, left_txt, right_txt, base_size_px,
                   left_weight=900, right_weight=900, margin_frac=SIDE_PAD, steps=12):
    avail = W * (1 - 2*margin_frac)
    size = base_size_px
    for _ in range(steps):
        if _measure_text_px(fig, left_txt, size, left_weight) + \
           _measure_text_px(fig, right_txt, size, right_weight) <= avail:
            break
        size *= 0.92
    return int(size)

def _fit_single(fig, W, text, base_size_px, weight=600, margin_frac=SIDE_PAD, steps=12):
    avail = W * (1 - 2*margin_frac)
    size = base_size_px
    for _ in range(steps):
        if _measure_text_px(fig, text, size, weight) <= avail:
            break
        size *= 0.92
    return int(size)

def _add_disclaimer(fig, W, H, text="Historical Analysis.  Not Investment Advice."):
    ax = fig.add_axes([0, 0, 1, DISCLAIMER_H]); ax.set_facecolor(DISCLAIMER_BG); ax.set_axis_off()
    base = _font_px(W, H, frac_h=0.045)
    fs   = _fit_single(fig, W, text, base, weight=800, margin_frac=SIDE_PAD)
    fig.text(0.5, DISCLAIMER_H/2.0, text, ha="center", va="center",
             fontsize=fs, color=DISCLAIMER_TX, weight=800)

def _resolve_brand_config(variant, override=None):
    cfg = dict(BRAND_DEFAULTS.get(variant, BRAND_DEFAULTS["bars"]))
    if override: cfg.update(override)
    return cfg

def _add_brand_tag(fig, W, H, variant, ax=None, override=None):
    cfg = _resolve_brand_config(variant, override)
    if not cfg.get("show", True): return
    size  = _font_px(W, H, frac_h=cfg.get("size_h", 0.022))
    text  = cfg.get("text", "TradeWave.AI")
    fg    = cfg.get("fg", "#cdd6e1")
    bg    = cfg.get("bg", "#11161b")
    alpha = cfg.get("alpha", 0.55)
    pad   = cfg.get("pad", 0.015)

    if ax is None:  # stats-only
        # Only bump up on Instagram and Pinterest
        is_ig  = (W == 1080 and H == 1080)
        is_pin = (W == 1000 and H == 1500)
        y = DISCLAIMER_H + pad + (BRAND_STATS_UP_TALL if (is_ig or is_pin) else 0.0)

        fig.text(1.0 - SIDE_PAD, y, text, ha="right", va="bottom",
                 color=fg, fontsize=size, weight=800,
                 bbox=dict(boxstyle="round,pad=0.28", fc=bg, ec="none", alpha=alpha))
        return
    else:
        anchors = {"br": (0.995, 0.02, "right", "bottom"),
                   "tr": (0.995, 0.98, "right", "top"),
                   "bl": (0.005, 0.02, "left",  "bottom"),
                   "tl": (0.005, 0.98, "left",  "top")}
        x, y, ha, va = anchors.get(cfg.get("loc", "br"), anchors["br"])
        ax.text(x, y, text, transform=ax.transAxes, ha=ha, va=va,
                color=fg, fontsize=size, weight=800,
                bbox=dict(boxstyle="round,pad=0.28", fc=bg, ec="none", alpha=alpha), zorder=10)

def _add_colored_ticker_title(fig, ticker, rest_text, y, W, H, trade_dir):
    color = ACCENT_GREEN if str(trade_dir).lower().startswith("l") else ACCENT_RED

    is_landscape = W > H  # NEW: only bump size on landscape
    base  = int(H * TITLE_SIZE_H * (1.22 if is_landscape else 1.0))  # NEW: slight boost
    size  = _fit_two_parts(
        fig, W, f"${ticker}", f" {rest_text}", base,
        margin_frac=(SIDE_PAD * 0.40 if is_landscape else SIDE_PAD)  # NEW: use more width
    )

    t_tkr = TextArea(f"${ticker}", textprops=dict(color=color, weight=900, size=size))
    t_rst = TextArea(f" {rest_text}", textprops=dict(color=TEXT_MAIN, weight=900, size=size))
    packed = HPacker(children=[t_tkr, t_rst], align="center", pad=0, sep=6)
    box = AnchoredOffsetbox(loc="center", child=packed, frameon=False,
                            bbox_to_anchor=(0.5, y), bbox_transform=fig.transFigure, borderpad=0)
    fig.add_artist(box)

 

def _add_header(fig, ticker, rest_after_ticker, subtitle, W, H, trade_dir=None):
    base_y   = 1.0 - SAFE_TOP                 # original anchor for lines 2 & 3
    title_y  = base_y - (TOPLINE_DOWN_LAND if (W > H) else 0.0)  # move only line 1 on landscape

    if trade_dir is not None:
        _add_colored_ticker_title(fig, ticker, rest_after_ticker, title_y, W, H, trade_dir)
    else:
        is_landscape = W > H
        base = int(H * TITLE_SIZE_H * (1.22 if is_landscape else 1.0))
        fs   = _fit_single(
            fig, W, f"${ticker} {rest_after_ticker}", base, weight=900,
            margin_frac=(SIDE_PAD * 0.40 if is_landscape else SIDE_PAD)
        )
        fig.text(0.5, title_y, f"${ticker} {rest_after_ticker}",
                 ha="center", va="top", fontsize=fs, color=TEXT_MAIN, weight=900)

    if subtitle:
        # NOTE: subtitle stays tied to the original base_y — unchanged
        base = int(H * SUB_SIZE_H)
        fs   = _fit_single(fig, W, subtitle, base, weight=600)
        fig.text(0.5, base_y - HEADER_GAP, subtitle,  # <-- uses base_y, not title_y
                 ha="center", va="top", fontsize=fs, color=ACCENT_MID, weight=600)

# --- Chart title (width-aware) drawn at the top of the axes, not with set_title ---
def _draw_chart_title(fig, ax, text, W, H):
    # axis bbox in figure coords
    bbox = ax.get_position()
    x_center = (bbox.x0 + bbox.x1) / 2.0
    y_top    = bbox.y1 + (CHART_TITLE_PAD / H)
    base = _font_px(W, H, frac_h=0.042)
    # available width: the axes width in figure coords turned into pixels
    avail_w = (bbox.x1 - bbox.x0) * W * 0.98
    # shrink-to-fit against axis width
    size = int(base)
    for _ in range(12):
        if _measure_text_px(fig, text, size, weight=900) <= avail_w: break
        size *= 0.92
    fig.text(x_center, y_top, text, ha="center", va="bottom",
             fontsize=size, color=TEXT_MAIN, weight=900)

def _add_stats_header_block(fig, ticker, avg_gain, success_text, window_text,
                            trade_dir, W, H):
    color = ACCENT_RED if str(trade_dir).lower().startswith('s') else ACCENT_GREEN
    
    # Width-aware ticker sizing
    ticker_text = f"${ticker}"
    base_ticker_size = _font_px(W, H, frac_h=STATS_TICKER_SIZE)
    ticker_size = _fit_single(fig, W, ticker_text, base_ticker_size, weight=900)
    fig.text(0.5, STATS_HDR_ANCHOR_Y, ticker_text, ha="center", va="center",
             fontsize=ticker_size, color=color, weight=900)
    
    # Width-aware gain text sizing
    gain_text = f"{avg_gain} Avg Gain | {success_text}"
    base_gain_size = _font_px(W, H, frac_h=STATS_GAIN_SIZE)
    gain_size = _fit_single(fig, W, gain_text, base_gain_size, weight=800)
    fig.text(0.5, STATS_HDR_ANCHOR_Y - STATS_HDR_GAP1, gain_text, ha="center", va="center",
             fontsize=gain_size, color=TEXT_MAIN, weight=800)
    
    # Width-aware window text sizing
    base_window_size = _font_px(W, H, frac_h=STATS_EDGE_SIZE)
    window_size = _fit_single(fig, W, window_text, base_window_size, weight=700)
    fig.text(0.5, STATS_HDR_ANCHOR_Y - STATS_HDR_GAP1 - STATS_HDR_GAP2, window_text, 
             ha="center", va="center", fontsize=window_size, color=ACCENT_MID, weight=700)

def _add_footer_stats_stacked(fig, stats_triplet, W, H):
    titles = ["Sharpe", "% Profitable", "Cumulative"]
    values = [stats_triplet.get("sharpe", ""), stats_triplet.get("winrate", ""), stats_triplet.get("cumulative", "")]
    
    # Calculate width-aware font sizes
    base_title_size = _font_px(W, H, frac_h=STATS_TITLE_SIZE)
    base_value_size = _font_px(W, H, frac_h=STATS_VALUE_SIZE)
    
    # Find the longest title and value to size appropriately
    longest_title = max(titles, key=len)
    longest_value = max([str(v) for v in values], key=len)
    
    # Size for individual column width (roughly 1/3 of usable width)
    col_margin = 0.12  # margin for each column
    title_size = _fit_single(fig, W, longest_title, base_title_size, weight=800, margin_frac=col_margin)
    value_size = _fit_single(fig, W, longest_value, base_value_size, weight=900, margin_frac=col_margin)
    
    for x, t, v in zip(STATS_COL_X, titles, values):
        fig.text(x, STATS_TITLE_Y, t, ha="center", va="center",
                 fontsize=title_size, color=ACCENT_MID, weight=800)
        fig.text(x, STATS_VALUE_Y, v, ha="center", va="center",
                 fontsize=value_size, color=TEXT_MAIN, weight=900)

def _add_cta(fig, W, H, text=None):
    """Centered CTA above the disclaimer, auto width-fit."""
    msg = text or random.choice(CTA_LINES)
    base_px = _font_px(W, H, frac_h=CTA_SIZE_H)
    fs = _fit_single(fig, W, msg, base_px, weight=900, margin_frac=SIDE_PAD)
    fig.text(
        0.5, DISCLAIMER_H + CTA_GAP_ABOVE_DISC,  # just above the disclaimer bar
        msg, ha="center", va="bottom",
        fontsize=fs, color=TEXT_MAIN, weight=900
    )

def _portrait_extra_left_pad(W, H):
    ar = W / H
    if ar < 0.75:  # very tall (e.g., Pinterest 2:3)
        return 0.03
    elif ar <= 1.0:  # square or nearly square (Instagram 1:1)
        return 0.04  # Add extra padding for square formats
    return 0.0

def _setup_axes(fig, rect):
    ax = fig.add_axes(rect); ax.set_facecolor(BG_DARK); return ax

def _format_chart_axes(ax, W, H, grid=True, spine_color=ACCENT_LINE, y_percent=False):
    if grid: ax.grid(True, color="#2a2f35", linewidth=max(0.8, H*0.0012), alpha=0.9, linestyle="--")
    else:    ax.grid(False)
    lbl = max(8, min(H*0.028, W*0.030))
    ax.tick_params(colors=ACCENT_MID, labelsize=lbl)
    if y_percent:
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x, pos: f"{x:.0f}%"))
    for s in ax.spines.values():
        s.set_color(spine_color); s.set_linewidth(max(0.8, H*0.0016))

def _chart_rect(W, H, extra_bottom=0.0):
    left  = SIDE_PAD + _portrait_extra_left_pad(W, H)
    right = SIDE_PAD
    top   = 1.0 - SAFE_TOP - HEADER_GAP - SUB_SIZE_H - CHART_TOP_PAD
    base_pad = PAD_FOR_NONE if not SHOW_STATS else PAD_FOR_STACKED
    bottom = DISCLAIMER_H + base_pad + extra_bottom
    width  = 1.0 - left - right
    height = max(0.42, top - bottom)
    return [left, bottom, width, height]

# --- Plot primitives ---
def _bars(ax, years, returns, W, H):
    years  = [int(y) for y in years]
    x      = np.arange(len(years))
    colors = [ACCENT_GREEN if v >= 0 else ACCENT_RED for v in returns]
    ax.bar(x, returns, width=0.7, color=colors, edgecolor=colors, alpha=0.95)

    ymin = min(0, min(returns) * 1.15)
    ymax = max(0, max(returns) * 1.15)
    if ymin == ymax: ymin, ymax = -1, 1
    ax.set_ylim(ymin, ymax)
    ax.axhline(0, color=BORDER_SUBTLE, linewidth=max(1.0, H*0.0016))

    # Determine if this is Instagram or Pinterest format
    ar = W / H
    is_instagram_or_pinterest = (W == 1080 and H == 1080) or (W == 1000 and H == 1500)
    
    if is_instagram_or_pinterest:
        # Show half the number of labels for Instagram and Pinterest
        base_step = max(1, len(years)//6)
        step = base_step * 2  # Double the step to show half the labels
    else:
        # Keep original logic for all other platforms
        step = max(1, len(years)//6)
    
    ax.set_xticks(x[::step])
    ax.set_xticklabels([str(y) for y in years][::step],
                       color=ACCENT_MID, fontsize=max(8, min(H*0.028, W*0.030)))
    _format_chart_axes(ax, W, H, grid=False, spine_color=BORDER_SUBTLE, y_percent=True)

def _trend_plot(ax, yvals, W, H):
    ax.plot(yvals, linewidth=max(1.6, H*0.003), color=TEXT_MAIN)
    ax.fill_between(range(len(yvals)), yvals, [min(yvals)]*len(yvals),
                    color=TEXT_MAIN, alpha=0.08)
    _format_chart_axes(ax, W, H, grid=False, y_percent=False)

def _cum(ax, yvals, W, H):
    color = ACCENT_GREEN if (len(yvals)==0 or yvals[-1] >= 0) else ACCENT_RED
    ax.plot(yvals, linewidth=max(1.8, H*0.003), color=color)
    ax.fill_between(range(len(yvals)), yvals, color=color, alpha=0.15)
    _format_chart_axes(ax, W, H, grid=True, y_percent=True)

def _price(ax, dates, prices, W, H):
    dnum = mdates.date2num(dates)
    ax.plot(dnum, prices, linewidth=max(1.8, H*0.003), color=PRICE_LINE)
    ax.fill_between(dnum, prices, [min(prices)] * len(prices), color=PRICE_FILL, alpha=0.15)
    loc = mdates.AutoDateLocator(minticks=3, maxticks=6)
    fmt = mdates.ConciseDateFormatter(loc)
    ax.xaxis.set_major_locator(loc); ax.xaxis.set_major_formatter(fmt)
    ax.margins(x=0); ax.set_xlim(dnum[0], dnum[-1])
    ax.yaxis.set_major_locator(MaxNLocator(nbins=5, prune="both"))
    _format_chart_axes(ax, W, H, grid=True, y_percent=False)

# ---------------- Public renderers ----------------
def render_stats_only(out_path, size_key, ticker, avg_gain, success_text,
                      window_text, stats_triplet, trade_dir, brand_override=None):
    W, H = THUMB_SIZES[size_key]
    fig = _init_figure(W, H)
    _add_disclaimer(fig, W, H)
    _add_cta(fig, W, H)
    _add_stats_header_block(fig, ticker, avg_gain, success_text, window_text, trade_dir, W, H)
    titles = ["Sharpe", "% Profitable", "Cumulative"]
    
    values = [stats_triplet.get("sharpe", ""), stats_triplet.get("winrate", ""), stats_triplet.get("cumulative", "")]

    # Responsive sizing based on image dimensions
    if H >= 1200:  # Tall formats like Pinterest
        title_size = max(8, H * 0.018)
        value_size = max(10, H * 0.035)
    else:  # Landscape formats like Twitter, FB, YouTube
        title_size = max(10, H * 0.028)
        value_size = max(12, H * 0.050)

    # Determine if this is Instagram or Pinterest and adjust stats position
    stats_offset = 0.0
    if W == 1080 and H == 1080:  # Instagram
        stats_offset = INSTAGRAM_STATS_OFFSET
    elif W == 1000 and H == 1500:  # Pinterest
        stats_offset = PINTEREST_STATS_OFFSET
    
    # Calculate adjusted positions
    adjusted_title_y = STATS_TITLE_Y + stats_offset
    adjusted_value_y = STATS_VALUE_Y + stats_offset

    for x, t, v in zip(STATS_COL_X, titles, values):
        fig.text(x, adjusted_title_y, t, ha="center", va="center",
                fontsize=title_size, color=ACCENT_MID, weight=800)
        fig.text(x, adjusted_value_y, v, ha="center", va="center",
                fontsize=value_size, color=TEXT_MAIN, weight=900)
    
    _add_brand_tag(fig, W, H, variant="stats", ax=None, override=brand_override)
    _save_exact(fig, out_path, W, H); plt.close(fig)

def render_with_bars(out_path, size_key, ticker, avg_gain, success_text,
                     window_text, bar_years, bar_returns, stats_triplet, years_int, trade_dir,
                     brand_override=None):
    W, H = THUMB_SIZES[size_key]
    fig = _init_figure(W, H); 
    _add_disclaimer(fig, W, H)
    _add_cta(fig, W, H)
    _add_header(fig, ticker, f"{avg_gain} Avg Gain | {success_text}", window_text, W, H, trade_dir=trade_dir)
    ax = _setup_axes(fig, _chart_rect(W, H))
    _bars(ax, bar_years, bar_returns, W, H)
    _draw_chart_title(fig, ax, f"{years_int}-Year Seasonal Window Returns", W, H)
    if SHOW_STATS: _add_footer_stats_stacked(fig, stats_triplet, W, H)
    _add_brand_tag(fig, W, H, variant="bars", ax=ax, override=brand_override)
    _save_exact(fig, out_path, W, H); plt.close(fig)

def render_with_trend(out_path, size_key, ticker, avg_gain, success_text,
                      window_text, trend_segment_labels, trend_segment_values,
                      trend_segment_hl, years_int, trade_dir, stats_triplet,
                      brand_override=None):
    W, H = THUMB_SIZES[size_key]
    fig = _init_figure(W, H); 
    _add_disclaimer(fig, W, H)
    _add_cta(fig, W, H)
    _add_header(fig, ticker, f"{avg_gain} Avg Gain | {success_text}", window_text, W, H, trade_dir=trade_dir)

    y = [float(v) for v in trend_segment_values]
    x = list(range(len(y)))
    hl_start, hl_end = trend_segment_hl

    ax = _setup_axes(fig, _chart_rect(W, H))
    shade_color = ACCENT_GREEN if str(trade_dir).lower().startswith('l') else ACCENT_RED
    if 0 <= hl_start <= hl_end < len(x):
        ax.axvspan(hl_start, hl_end, facecolor=shade_color, alpha=0.18, zorder=0)

    _trend_plot(ax, y, W, H)

    nticks = 6
    pos = (np.linspace(0, len(x)-1, nticks).round().astype(int)) if len(x) > 1 else np.array([0], dtype=int)
    labels = [trend_segment_labels[i][5:10] for i in pos]
    for i in range(1, len(labels)):
        if labels[i] == labels[i-1]: labels[i] = ''
    ax.set_xticks(pos.tolist()); ax.set_xticklabels(labels, color=ACCENT_MID,
                                                    fontsize=max(8, min(H*0.028, W*0.030)))
    ax.yaxis.set_ticks([]); ax.tick_params(left=False, labelleft=False)

    ymin, ymax = (min(y), max(y)) if y else (0, 1)
    pad = (ymax - ymin) * 0.08 if ymax > ymin else 1.0
    ax.set_ylim(ymin - pad, ymax + pad); ax.set_xlim(0, max(1, len(x)-1))

    _draw_chart_title(fig, ax, f"{years_int}-Year Historical Trend Chart", W, H)
    if SHOW_STATS: _add_footer_stats_stacked(fig, stats_triplet, W, H)
    _add_brand_tag(fig, W, H, variant="trend", ax=ax, override=brand_override)
    _save_exact(fig, out_path, W, H); plt.close(fig)

def render_with_price(out_path, size_key, ticker, avg_gain, success_text,
                      window_text, price_points, stats_triplet, trade_dir, brand_override=None):
    W, H = THUMB_SIZES[size_key]
    fig = _init_figure(W, H); 
    _add_disclaimer(fig, W, H)
    _add_cta(fig, W, H)
    _add_header(fig, ticker, f"{avg_gain} Avg Gain | {success_text}", window_text, W, H, trade_dir=trade_dir)

    dates, prices = [], []
    if price_points:
        dates  = [dtparse(p[0]).date() for p in price_points]
        prices = [float(p[1]) for p in price_points]

    ax = _setup_axes(fig, _chart_rect(W, H, extra_bottom=0.02))
    if dates: _price(ax, dates, prices, W, H)
    _draw_chart_title(fig, ax, f"{ticker} Price Chart", W, H)

    if SHOW_STATS: _add_footer_stats_stacked(fig, stats_triplet, W, H)
    _add_brand_tag(fig, W, H, variant="price", ax=ax, override=brand_override)
    _save_exact(fig, out_path, W, H); plt.close(fig)

def render_with_cumulative(out_path, size_key, ticker, avg_gain, success_text,
                           window_text, cum_vals, years_int, stats_triplet, trade_dir, brand_override=None):
    W, H = THUMB_SIZES[size_key]
    fig = _init_figure(W, H); 
    _add_disclaimer(fig, W, H)
    _add_cta(fig, W, H)
    _add_header(fig, ticker, f"{avg_gain} Avg Gain | {success_text}", window_text, W, H, trade_dir=trade_dir)

    ax = _setup_axes(fig, _chart_rect(W, H))
    _cum(ax, cum_vals, W, H)
    _draw_chart_title(fig, ax, f"{years_int}-Year Seasonal Cumulative (Window)", W, H)

    if SHOW_STATS: _add_footer_stats_stacked(fig, stats_triplet, W, H)
    _add_brand_tag(fig, W, H, variant="cumulative", ax=ax, override=brand_override)
    _save_exact(fig, out_path, W, H); plt.close(fig)
#------------------------------------------------------------------------------------------------------------------------------
# this function is called to generate a thumbnail - it will create one of 5 versions by random
#------------------------------------------------------------------------------------------------------------------------------
def create_socialmedia_thumbnail(sm,resource_id,date,symbol,days,dir,avg_gain,years,title_pre,
                                 category = config.category_date_range_report,ttype='report'): # ttype could be report or video - changes background image
    brand_ovr = None # used to place a logo if needed
    date1 = date
    date2 = inc_date_day(date1,int(days)-1) # -1 is to created corrected_date - problem was from my mistake in the past 
    company = get_company_name(resource_id,symbol)
    
    p = build_thumbnail_payload(resource_id, symbol, date, days, years,price_lookback_days=lookback_days, zero_last_year=True)
    
    title,slug=create_title_slug(company, symbol, date1, date2, years,category)
    sm_tn_folder = config.socialmedia_thumbnail_folder

    base_year = date[:4]
    filepath  = f'{sm_tn_folder}{sm}/{base_year}/{date}/{slug}.jpg'
    url       = f'{config.img_folder}thumbnails/{sm}/{base_year}/{date}/{slug}.jpg'
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    print(filepath)
    print(url)

    tm_variation = random.randint(0, 4)

    print(tm_variation)

    if tm_variation   == 0:
        render_stats_only(filepath, sm,
                      p["ticker"], p["avg_gain_txt"], p["success_text"],
                      p["window_text"], p["stats_triplet"], p["trade_dir"],
                      brand_override=brand_ovr)
    elif tm_variation == 1:
        render_with_bars(filepath, sm,
                     p["ticker"], p["avg_gain_txt"], p["success_text"], p["window_text"],
                     p["bar_years"], p["bar_returns"], p["stats_triplet"], p["years_int"], p["trade_dir"],
                     brand_override=brand_ovr)

    elif tm_variation == 2:
        render_with_trend(filepath, sm,
                      p["ticker"], p["avg_gain_txt"], p["success_text"], p["window_text"],
                      p["trend_segment_labels"], p["trend_segment_values"], p["trend_segment_hl"],
                      p["years_int"], p["trade_dir"], p["stats_triplet"],
                      brand_override=brand_ovr)

    elif tm_variation == 3:
        render_with_price(filepath, sm,
                      p["ticker"], p["avg_gain_txt"], p["success_text"], p["window_text"],
                      p["price_points"], p["stats_triplet"], p["trade_dir"],
                      brand_override=brand_ovr)

    elif tm_variation == 4:
        render_with_cumulative(filepath, sm,
                           p["ticker"], p["avg_gain_txt"], p["success_text"], p["window_text"],
                           p["cum_data"], p["years_int"], p["stats_triplet"], p["trade_dir"],
                           brand_override=brand_ovr)

    return filepath,url
# ---------------- Test runner ----------------
if __name__ == "__main__":

    
    financial_group_id = 0
    date1   = '2025-10-21'
    days_hold = '30'
    symbol  = 'AAPL'
    years   = '10'
    lookback_days = 340
    size_key = "twitter"  # try "instagram" / "twitter_recommended2" too


    create_socialmedia_thumbnail(size_key,financial_group_id,date1,symbol,days_hold,'','',years,'',
                                 category = config.category_date_range_report,ttype='report')

    exit()


    tn_folder = '/var/www/html/wp-content/uploads/p/1/'

    p = build_thumbnail_payload(financial_group_id, symbol, date1, days_hold, years,
                                price_lookback_days=lookback_days, zero_last_year=True)

    os.makedirs("thumbnails_out", exist_ok=True)
    subfolder=''
    base = os.path.join(subfolder, f"{symbol}_{date1}_{days_hold}_{years}")
    brand_ovr = None

    render_stats_only(f"{tn_folder}{base}_stats.png", size_key,
                      p["ticker"], p["avg_gain_txt"], p["success_text"],
                      p["window_text"], p["stats_triplet"], p["trade_dir"],
                      brand_override=brand_ovr)

    render_with_bars(f"{tn_folder}{base}_bars.png", size_key,
                     p["ticker"], p["avg_gain_txt"], p["success_text"], p["window_text"],
                     p["bar_years"], p["bar_returns"], p["stats_triplet"], p["years_int"], p["trade_dir"],
                     brand_override=brand_ovr)

    render_with_trend(f"{tn_folder}{base}_trend.png", size_key,
                      p["ticker"], p["avg_gain_txt"], p["success_text"], p["window_text"],
                      p["trend_segment_labels"], p["trend_segment_values"], p["trend_segment_hl"],
                      p["years_int"], p["trade_dir"], p["stats_triplet"],
                      brand_override=brand_ovr)

    render_with_price(f"{tn_folder}{base}_price.png", size_key,
                      p["ticker"], p["avg_gain_txt"], p["success_text"], p["window_text"],
                      p["price_points"], p["stats_triplet"], p["trade_dir"],
                      brand_override=brand_ovr)

    render_with_cumulative(f"{tn_folder}{base}_cumulative.png", size_key,
                           p["ticker"], p["avg_gain_txt"], p["success_text"], p["window_text"],
                           p["cum_data"], p["years_int"], p["stats_triplet"], p["trade_dir"],
                           brand_override=brand_ovr)

    print("✅ Wrote 5 thumbnails to:", os.path.abspath("thumbnails_out"))
