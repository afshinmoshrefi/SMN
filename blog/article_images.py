# -*- coding: utf-8 -*-
"""
article_images.py — Article-ready charts for TradeWave opportunities.

Produces 11 images:
  1) bars             -> year-by-year seasonal window returns (final return only)
  2) bars_mfe         -> bars + MFE stacked (light favorable extension)
  3) bars_mae         -> bars + MAE from zero (light adverse slab)
  4) bars_mae_mfe     -> bars + BOTH overlays
  5) trend            -> historical trend with shaded trade window
  6) price            -> recent lookback price chart
  7) cumulative       -> seasonal window cumulative % by YEAR
  8) stats            -> article-grade stats table incl. TradeWave Ratio (TWR)
  9) price_proj_30    -> price chart + 30-day seasonal projection
 10) price_proj_60    -> price chart + 60-day seasonal projection
 11) price_proj_90    -> price chart + 90-day seasonal projection

Filenames:
  SYMBOL_DATE_DAYS_YEARS_<variant>.jpg

Return value (list of dicts):
  {
    "variant": "bars" | ... | "price_proj_30" | "price_proj_60" | "price_proj_90",
    "path": "<absolute file path>",
    "rel":  "<web-relative path starting with />",
    "url":  "<https://... absolute URL (root_domain + rel)>"
  }
"""

import os, re, math
from datetime import timedelta
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MaxNLocator
import matplotlib.dates as mdates
from dateutil.parser import parse as dtparse
import socket
from thumbnail_tools import build_thumbnail_payload
from blog_tools import get_company_name
import thumbnail_renderer as TR
from article_tools import get_article_image_paths
from create_report import get_seasonal_chart_data,get_seasonal_chart_data2, get_chart_data
import config

# ---------------- Canvas / sizes ----------------
DEFAULT_SIZE_KEY = "facebook"          # 1280 x 720
THUMB_SIZES = TR.THUMB_SIZES
DEF_DPI = getattr(TR, "DEF_DPI", 100)

# ---------------- Output root -------------------


hostname = socket.gethostname()
if hostname == 'afshin-VirtualBox': #  on dev server
    BASE_OUT_DIR = getattr(config,"article_images_folder",
        os.path.join(getattr(config, "socialmedia_thumbnail_folder", "/var/www/html/wordpress/wp-content/uploads/p/"),"articles"))
else:
    BASE_OUT_DIR = getattr(config,"article_images_folder",
        os.path.join(getattr(config, "socialmedia_thumbnail_folder", "/var/www/html/wp-content/uploads/p/"),"articles"))

# ---------------- Themes ------------------------
def _make_themes(light_bg="#f5f6f7"):
    """Return theme dict with adjustable light background."""
    return {
        "dark": {
            "bg":        "#0f1216",
            "text":      "#e8ecf1",
            "muted":     "#9aa5b1",
            "line":      "#b8c2cc",
            "border":    "#2a2f35",
            "pos":       "#41d14a",
            "neg":       "#ef4444",
            "price":     "#60a5fa",
            "pricefill": "#60a5fa",
        },
        "light": {
            "bg":        light_bg,  # off-white & adjustable
            "text":      "#111827",
            "muted":     "#6b7280",
            "line":      "#9ca3af",
            "border":    "#cbd5e1",
            "pos":       "#16a34a",
            "neg":       "#dc2626",
            "price":     "#2563eb",
            "pricefill": "#93c5fd",
        },
    }

# -------- color helpers for lighter overlays ----
def _hex_to_rgb01(h):
    h = h.lstrip("#")
    return (int(h[0:2],16)/255.0, int(h[2:4],16)/255.0, int(h[4:6],16)/255.0)

def _rgb01_to_hex(rgb):
    r,g,b = [max(0,min(1,x)) for x in rgb]
    return "#{:02x}{:02x}{:02x}".format(int(r*255), int(g*255), int(b*255))

def _lighten(hex_color, frac=0.70):
    """Mix hex color with white by frac (0..1)."""
    r,g,b = _hex_to_rgb01(hex_color)
    return _rgb01_to_hex((r + (1-r)*frac, g + (1-g)*frac, b + (1-b)*frac))

# ---------------- Layout (no top title) ---------
AX_LEFT, AX_RIGHT, AX_TOP, AX_BOTTOM = 0.075, 0.040, 0.92, 0.24
CAPTION_Y = 0.095   # below x-ticks (figure coords)

# --- Label size scales --------------------------
BAR_XLABEL_SCALE = 0.75
CUM_XLABEL_SCALE = 0.75

# ---------------- Utilities ---------------------
def _init_fig(W, H, pal):
    fig = plt.figure(figsize=(W/DEF_DPI, H/DEF_DPI), dpi=DEF_DPI)
    fig.patch.set_facecolor(pal["bg"])
    return fig

def _axes(fig, pal):
    rect = [AX_LEFT, AX_BOTTOM, 1.0 - AX_LEFT - AX_RIGHT, AX_TOP - AX_BOTTOM]
    ax = fig.add_axes(rect)
    ax.set_facecolor(pal["bg"])
    return ax

def _fmt_axes(ax, W, H, pal, *, grid=True, y_percent=False):
    if grid:
        ax.grid(True, color=pal["border"], linewidth=max(1.0, H*0.0016), alpha=0.7, linestyle="--")
    else:
        ax.grid(False)
    lbl = max(11, min(H*0.036, W*0.034))
    ax.tick_params(colors=pal["muted"], labelsize=lbl)
    if y_percent:
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:.0f}%"))
    for s in ax.spines.values():
        s.set_color(pal["line"]); s.set_linewidth(max(1.2, H*0.002))

def _caption(fig, pal, text):
    if not text: return
    W, H = fig.get_size_inches() * fig.dpi
    size = int(H * 0.034)
    avail = W * 0.92
    t = fig.text(0.5, 0, text, fontsize=size, weight=800, color=pal["muted"])
    fig.canvas.draw()
    while t.get_window_extent(renderer=fig.canvas.get_renderer()).width > avail and size > 10:
        size = int(size * 0.92); t.set_fontsize(size); fig.canvas.draw()
    t.remove()
    fig.text(0.5, CAPTION_Y, text, ha="center", va="center",
             fontsize=size, color=pal["muted"], weight=800)

def _footer_labels(ax, pal, company):
    """Company bottom-left & TradeWave.ai bottom-right INSIDE axes."""
    bbox = ax.get_position(); fig = ax.figure
    W, H = fig.get_size_inches() * fig.dpi
    ax_h_px = (bbox.y1 - bbox.y0) * H
    fs = max(12, int(ax_h_px * 0.055 / fig.dpi * 72 / 100) * 2)
    ax.text(0.01, 0.02, company, transform=ax.transAxes, ha="left", va="bottom",
            fontsize=fs, color=pal["muted"], weight=900, zorder=10)
    ax.text(0.99, 0.02, "TradeWave.ai", transform=ax.transAxes, ha="right", va="bottom",
            fontsize=fs, color=pal["muted"], weight=900, zorder=10)

def _save(fig, path, W, H):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.set_size_inches(W / DEF_DPI, H / DEF_DPI, forward=True)
    fig.savefig(path, dpi=DEF_DPI, facecolor=fig.get_facecolor(),
                edgecolor="none", bbox_inches=None, pad_inches=0, transparent=False)
    plt.close(fig)

def _date_range_only(window_text: str) -> str:
    if not window_text: return ""
    parts = window_text.split("|", 1)
    return parts[-1].strip()

def _is_full_year_window(date_str: str, days) -> bool:
    """
    Treat a pattern as a full-year buy-and-hold window when:
      - the start date is Jan 1, and
      - the configured window length is effectively a full year (>= 360 trading days).
    Display-only rule; does not change underlying stats.
    """
    parts = date_str.split("-")
    if len(parts) != 3:
        return False

    month = int(parts[1])
    day   = int(parts[2])
    length = int(days)

    return (month == 1 and day == 1 and length >= 360)

def _clean_date_range_for_caption(date_range: str) -> str:
    """
    Strip noisy trailing parts like '(365 Days...)' from the window text
    for use in figure captions.
    """
    if not date_range:
        return ""
    # Keep everything before the first '(' if present
    return date_range.split("(", 1)[0].strip()

# ----- path helpers -----
def _ensure_root_domain(rd: str) -> str:
    if not rd: return ""
    rd = rd.strip().rstrip("/")
    if not rd.startswith("http://") and not rd.startswith("https://"):
        rd = f"https://{rd}"
    return rd

def _abs_to_rel(abs_path: str) -> str:
    web_root = config.news_root_folder
    if web_root and abs_path.startswith(web_root):
        rel = abs_path[len(web_root):]; return rel if rel.startswith("/") else "/" + rel
    
    
    if hostname == 'afshin-VirtualBox': #  on dev server
        marker = "/var/www/html/wordpress"
    else: 
        marker = "/var/www/html"

    if marker in abs_path:
        rel = abs_path.split(marker, 1)[1]; return rel if rel.startswith("/") else "/" + rel
    idx = abs_path.find("/wp-content/")
    if idx != -1: return abs_path[idx:]
    return "/" + os.path.basename(abs_path)

# def _rel_to_url(rel_path: str) -> str:
#     rd = _ensure_root_domain(getattr(config, "domain_root", "").strip())
#     return f"{rd}{rel_path}" if rd else rel_path

def _rel_to_url(rel_path: str) -> str:
    base = config.news_website_url.rstrip('/')
    if not base.startswith('http'):
        base = 'http://' + base
    return f"{base}{rel_path}"
    
def _set_tick_fontsizes(ax, x=None, y=None):
    if x is not None:
        for lab in ax.get_xticklabels(): lab.set_fontsize(x)
    if y is not None:
        for lab in ax.get_yticklabels(): lab.set_fontsize(y)

# ---------------- Plot primitives ----------------
def _bars(ax, years, returns, W, H, pal, x_scale=BAR_XLABEL_SCALE, extra_values=None):
    """
    Base bar plot (final returns). If extra_values is passed, y-limits expand
    to include them so MFE/MAE overlays are not clipped.
    """
    years = [int(y) for y in years]
    x = np.arange(len(years))
    returns = [0.0 if v in (None, "") else float(v) for v in returns]

    base_colors = [pal["pos"] if v >= 0 else pal["neg"] for v in returns]
    ax.bar(x, returns, width=0.7, color=base_colors, edgecolor=base_colors, alpha=0.95, linewidth=0)

    combo = list(returns)
    if extra_values:
        combo += [float(v) for v in extra_values if v is not None and str(v) != ""]
    combo.append(0.0)
    ymin = min(combo) * 1.15
    ymax = max(combo) * 1.15
    if ymin == ymax:
        ymin, ymax = -1, 1
    ax.set_ylim(ymin, ymax)

    ax.axhline(0, color=pal["line"], linewidth=max(1.2, H*0.002))

    step = max(1, len(years)//6)
    ax.set_xticks(x[::step]); ax.set_xticklabels([str(y) for y in years][::step], color=pal["muted"])

    _fmt_axes(ax, W, H, pal, grid=False, y_percent=True)
    base_fs = max(12, min(H*0.040, W*0.038)); xlab_fs = int(base_fs * float(x_scale))
    _set_tick_fontsizes(ax, x=xlab_fs)

def _trend(ax, labels, yvals, hl_span, trade_dir, W, H, pal):
    y = [float(v) for v in yvals]
    x = list(range(len(y)))
    if y:
        ymin, ymax = min(y), max(y); pad = (ymax - ymin) * 0.10 if ymax > ymin else 1.0
        ax.set_ylim(ymin - pad, ymax + pad); ax.set_xlim(0, max(1, len(x)-1))
    shade = pal["pos"] if str(trade_dir).lower().startswith("l") else pal["neg"]
    s, e = hl_span
    if 0 <= s <= e < len(x): ax.axvspan(s, e, facecolor=shade, alpha=0.20, zorder=0)
    ax.plot(y, linewidth=max(2.0, H*0.0034), color=pal["text"])
    nticks = 6
    pos = (np.linspace(0, len(x)-1, nticks).round().astype(int)) if len(x) > 1 else np.array([0], dtype=int)
    lbls = [labels[i][5:10] for i in pos]
    for i in range(1, len(lbls)):
        if lbls[i] == lbls[i-1]: lbls[i] = ""
    ax.set_xticks(pos.tolist()); ax.set_xticklabels(lbls, color=pal["muted"])
    _fmt_axes(ax, W, H, pal, grid=False, y_percent=False)

def _cumulative(ax, yvals, years_labels, W, H, pal, x_scale=CUM_XLABEL_SCALE):
    color = pal["pos"] if (len(yvals) == 0 or (yvals and yvals[-1] >= 0)) else pal["neg"]
    x = np.arange(len(yvals))
    ax.plot(x, yvals, linewidth=max(2.2, H*0.0036), color=color)
    ax.fill_between(x, yvals, color=color, alpha=0.15)
    years_labels = [str(y) for y in years_labels[:len(x)]]
    step = max(1, len(x)//6)
    ax.set_xticks(x[::step]); ax.set_xticklabels(years_labels[::step], color=pal["muted"])
    _fmt_axes(ax, W, H, pal, grid=True, y_percent=True)
    base_fs = max(12, min(H*0.040, W*0.038)); xlab_fs = int(base_fs * float(x_scale))
    _set_tick_fontsizes(ax, x=xlab_fs)

def _price(ax, dates, prices, W, H, pal):
    dnum = mdates.date2num(dates)
    ax.plot(dnum, prices, linewidth=max(2.2, H*0.0036), color=pal["price"])
    ax.fill_between(dnum, prices, [min(prices)] * len(prices), color=pal["pricefill"], alpha=0.18)
    loc = mdates.AutoDateLocator(minticks=3, maxticks=6); fmt = mdates.ConciseDateFormatter(loc)
    ax.xaxis.set_major_locator(loc); ax.xaxis.set_major_formatter(fmt)
    ax.margins(x=0); ax.set_xlim(dnum[0], dnum[-1]); ax.yaxis.set_major_locator(MaxNLocator(nbins=5, prune="both"))
    _fmt_axes(ax, W, H, pal, grid=True, y_percent=False)

def _build_projection(dates, prices, trend_labels, trend_values, proj_days):
    """
    Build seasonal price projection from the last known price point.

    Walks the consolidated seasonal cycle by ARRAY INDEX (not by MM-DD) so the
    cycle boundary stays continuous instead of producing a wrap-around cliff for
    trending stocks. cumulative_offset carries the cycle's annual drift across
    each wrap.

    Returns (proj_dates, proj_prices) - lists including the connection point.
    """
    if not dates or not prices or not trend_labels or not trend_values:
        return [], []

    cycle_len = len(trend_values)
    if cycle_len == 0:
        return [], []

    # Build MM-DD -> first cycle index lookup
    mmdd_to_idx = {}
    for i, lbl in enumerate(trend_labels):
        mmdd = lbl[5:10] if len(lbl) >= 10 else lbl
        if mmdd not in mmdd_to_idx:
            mmdd_to_idx[mmdd] = i

    last_date = dates[-1]
    last_close = prices[-1]

    # Find todayIdx for the last price date (anchor of the projection)
    today_mmdd = last_date.strftime("%m-%d")
    today_idx = mmdd_to_idx.get(today_mmdd)
    # Fallback for Feb 29 or any MM-DD missing from cycle: closest prior MM-DD
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

    today_return = float(trend_values[today_idx])

    # Cycle drift = implied annual normalized appreciation. Carries across wraps
    # so the projection stays continuous past the cycle boundary.
    cycle_drift = float(trend_values[cycle_len - 1]) - float(trend_values[0])

    # Generate future trading dates (skip weekends)
    future_dates = []
    d = last_date
    while len(future_dates) < proj_days:
        d = d + timedelta(days=1)
        if d.weekday() < 5:  # Mon-Fri
            future_dates.append(d)

    # Build projection prices by walking the cycle linearly
    proj_dates = [last_date]  # connection point
    proj_prices = [last_close]
    cycle_idx = today_idx
    cumulative_offset = 0.0
    prev_date = last_date
    wrapped = False
    for fd in future_dates:
        days_diff = (fd - prev_date).days
        cycle_idx += days_diff
        while cycle_idx >= cycle_len:
            cycle_idx -= cycle_len
            cumulative_offset += cycle_drift
            wrapped = True
        future_return = float(trend_values[cycle_idx]) + cumulative_offset
        projected_price = last_close * (1 + (future_return - today_return) / 100)
        proj_dates.append(fd)
        proj_prices.append(projected_price)
        prev_date = fd

    # Stale-feed guard: if the walk wrapped the seasonal cycle, the price anchor sits in the
    # final stretch of the cycle -- i.e. the recent-price feed is stale and anchored *before*
    # the article's window (e.g. an unrolled futures contract like LBR, whose appserver feed
    # dead-ends mid-roll). That wrap injects a discontinuity that can flip the projection's
    # direction (bullish on a bearish window), so suppress it rather than publish a misleading
    # chart. A fresh anchor sits near the cycle start and never wraps within the horizon.
    if wrapped:
        print(f"[PROJECTION] suppressed: stale/pre-window anchor "
              f"(last_price={last_date}, idx={today_idx}/{cycle_len}) would wrap the seasonal cycle")
        return [], []

    return proj_dates, proj_prices


def _human_years_label(years_str):
    """
    Convert the internal years parameter to a human-readable label.
    Examples:
      "10"      -> "10 Years of Historical Data"
      "20"      -> "20 Years of Historical Data"
      "pe2-20"  -> "20 Midterm Election Years"
      "pe0-15"  -> "15 Pre-Election Years"
      "pe1-25"  -> "25 Election Years"
      "pe3-12"  -> "12 Post-Election Years"
    """
    s = str(years_str).strip().lower()
    pe_map = {
        "pe0": "Pre-Election",
        "pe1": "Election",
        "pe2": "Midterm Election",
        "pe3": "Post-Election",
    }
    m = re.match(r"(pe\d)-(\d+)", s)
    if m:
        cycle_key, n = m.group(1), m.group(2)
        cycle_name = pe_map.get(cycle_key, "Presidential Cycle")
        return f"{n} {cycle_name} Years"
    # PE without count (e.g., "pe2" = all available)
    if s in pe_map:
        cycle_name = pe_map[s]
        return f"{cycle_name} Years"
    # Plain number
    if s.isdigit():
        return f"{s} Years of Historical Data"
    return f"{s} Years"


def _price_with_projection(ax, dates, prices, proj_dates, proj_prices,
                           proj_days, years_label, W, H, pal):
    """Draw price chart with a solid golden seasonal projection line and legend."""
    PROJ_COLOR = "#e8a838"

    # Draw the base price line
    dnum = mdates.date2num(dates)
    ax.plot(dnum, prices, linewidth=max(2.2, H * 0.0036), color=pal["price"],
            label="Price")
    ax.fill_between(dnum, prices, [min(prices)] * len(prices), color=pal["pricefill"], alpha=0.18)

    # Draw projection line (solid golden)
    if proj_dates and proj_prices:
        pdnum = mdates.date2num(proj_dates)
        ax.plot(pdnum, proj_prices, linewidth=max(2.0, H * 0.003), color=PROJ_COLOR,
                solid_capstyle="round", zorder=5,
                label=f"{proj_days}-Day Seasonal Projection")
        ax.fill_between(pdnum, proj_prices, [min(prices)] * len(proj_prices),
                        color=PROJ_COLOR, alpha=0.08)

    # Legend with "Based on ..." as a third entry (invisible line)
    legend_fs = max(11, int(H * 0.026))
    basis_fs = max(9, int(H * 0.020))
    if years_label:
        # Add an invisible dummy line for the basis text
        dummy, = ax.plot([], [], color="none", label=f"Based on {years_label}")
    leg = ax.legend(loc="upper left", fontsize=legend_fs, frameon=True,
                    fancybox=True, framealpha=0.85,
                    edgecolor=pal["border"], facecolor=pal["bg"],
                    labelcolor=pal["text"])
    # Style the basis text entry smaller and muted
    if years_label and leg.get_texts():
        basis_text = leg.get_texts()[-1]  # last entry is the basis
        basis_text.set_fontsize(basis_fs)
        basis_text.set_color(pal["muted"])
        basis_text.set_fontstyle("italic")

    # Set x-axis to span both historical + projection
    all_dnum = list(dnum)
    if proj_dates:
        all_dnum += list(mdates.date2num(proj_dates))
    loc = mdates.AutoDateLocator(minticks=3, maxticks=7)
    fmt = mdates.ConciseDateFormatter(loc)
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(fmt)
    ax.margins(x=0)
    ax.set_xlim(min(all_dnum), max(all_dnum))

    # Set y-axis to encompass both price and projection
    all_prices = list(prices) + (proj_prices if proj_prices else [])
    ymin, ymax = min(all_prices), max(all_prices)
    ypad = (ymax - ymin) * 0.08
    ax.set_ylim(ymin - ypad, ymax + ypad)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=5, prune="both"))

    _fmt_axes(ax, W, H, pal, grid=True, y_percent=False)


# ---------------- Bar overlays -------------------
def _align_series(seq, n, fill=None):
    if seq is None: return [fill]*n
    seq = [None if v in (None, "") else float(v) for v in list(seq)]
    if len(seq) < n: seq = seq + [fill]*(n-len(seq))
    elif len(seq) > n: seq = seq[:n]
    return seq

def _draw_mfe_mae_overlays_levels(ax, years, returns, mfe_levels, mae_levels, trade_dir, pal):
    """
    LEVELS (relative to 0%):
      MFE (favorable):
        Long  (bullish): stack from max(ret, 0) -> mfe, only if mfe > max(ret, 0)  (light green).
        Short (bearish): stack from mfe -> min(ret, 0), only if mfe < min(ret, 0)  (light red).
      MAE (adverse from zero):
        Long  (bullish): if mae < 0, draw mae -> 0  (light red).
        Short (bearish): if mae > 0, draw 0  -> mae (light green).
    """
    import numpy as np
    x = np.arange(len(years))
    width = 0.7
    is_long = str(trade_dir).lower().startswith("l")

    # distinct light overlay colors
    pos_light = _lighten(pal["pos"], 0.70)  # light green
    neg_light = _lighten(pal["neg"], 0.70)  # light red/pink

    returns    = [0.0 if r in (None, "") else float(r) for r in returns]
    mfe_levels = _align_series(mfe_levels, len(returns), fill=None)
    mae_levels = _align_series(mae_levels, len(returns), fill=None)

    # ----- MFE stacked extension (favorable) -----
    for i, ret in enumerate(returns):
        mfe = mfe_levels[i]
        if mfe is None: 
            continue
        if is_long:
            low = max(ret, 0.0)     # Option A rule
            if mfe > low:
                ax.bar(x[i], mfe - low, width=width, bottom=low,
                       color=pos_light, alpha=1.0, edgecolor="none", linewidth=0)
        else:
            high = min(ret, 0.0)
            if mfe < high:
                ax.bar(x[i], high - mfe, width=width, bottom=mfe,
                       color=neg_light, alpha=1.0, edgecolor="none", linewidth=0)

    # ----- MAE from zero (adverse) -----
    for i, ret in enumerate(returns):
        mae = mae_levels[i]
        if mae is None:
            continue
        if is_long and mae < 0.0:
            ax.bar(x[i], -mae, width=width, bottom=mae,
                   color=neg_light, alpha=1.0, edgecolor="none", linewidth=0)
        elif (not is_long) and mae > 0.0:
            ax.bar(x[i], mae, width=width, bottom=0.0,
                   color=pos_light, alpha=1.0, edgecolor="none", linewidth=0)

# ---------------- Stats ----------------
def _stats_pairs_from_payload(p, symbol, date, days, years):
    """
    Build (label, value) pairs for the article table.
    Date Range is omitted (caption shows it). Includes TradeWave Ratio (TWR).
    """
    pairs = []
    def _val(x, default="—"):
        return default if x in (None, "") else x

    # Identification
    pairs.append(("Symbol", _val(symbol)))
    if "trade_dir" in p:
        pairs.append(("Trade Direction", "Long" if str(p["trade_dir"]).lower().startswith("l") else "Short"))
    if "years_int" in p:
        pairs.append(("History Years", _val(str(p["years_int"]))))

    # Days Hold
    days_hold = None
    m = re.search(r"\((\d+)\s*Days\)", p.get("window_text", "") or "")
    if m: days_hold = m.group(1)
    if not days_hold and days not in (None, ""): days_hold = str(days)
    if days_hold: pairs.append(("Days Hold", days_hold))

    # Core performance
    if p.get("avg_gain_txt"):
        pairs.append(("Avg Gain", _val(p["avg_gain_txt"])))
    st = p.get("stats_triplet", {}) or {}
    if st.get("cumulative"):
        pairs.append(("Cumulative Return", _val(st["cumulative"])))
    if st.get("sharpe"):
        pairs.append(("Sharpe Ratio", _val(st["sharpe"])))

    # TradeWave Ratio (TWR)
    twr = _val(st.get("sharpe2"))
    pairs.append(("TradeWave Ratio (TWR)", _val(twr)))

    # Success (W|L) with percent appended
    success_txt = p.get("success_text", "")
    winrate = st.get("winrate", "")
    if success_txt and winrate:
        success_val = f"{success_txt} ({winrate})"
    elif success_txt:
        success_val = success_txt
    elif winrate:
        success_val = winrate
    else:
        success_val = "—"
    pairs.append(("Success (W|L)", success_val))

    return pairs

def _draw_stats_table(ax, pal, pairs):
    """Render (label, value) pairs in a clean 3-column grid inside the axes."""
    ax.set_axis_off()
    inner = ax.inset_axes([0.02, 0.08, 0.96, 0.84]); inner.set_axis_off()

    n = len(pairs); cols = 3; rows = int(math.ceil(n / cols))
    bbox = ax.get_position(); fig = ax.figure
    W, H = fig.get_size_inches() * fig.dpi; ax_h_px = (bbox.y1 - bbox.y0) * H
    label_fs = max(16, int(ax_h_px * 0.085 / fig.dpi * 72 / 100) * 2)
    value_fs = max(18, int(ax_h_px * 0.125 / fig.dpi * 72 / 100) * 2)
    line_h   = 1.0 / rows

    for idx, (label, value) in enumerate(pairs):
        r = idx // cols; c = idx % cols
        x0 = c * (1.0 / cols); y0 = 1.0 - (r + 1) * line_h
        inner.add_patch(plt.Rectangle((x0 + 0.015, y0 + 0.10*line_h),
                                      (1.0/cols) - 0.030, line_h*0.80,
                                      fill=False, edgecolor=pal["line"], linewidth=2))
        inner.text(x0 + 0.03, y0 + 0.70*line_h, label, ha="left", va="center",
                   color=pal["muted"], fontsize=label_fs, weight=800)
        inner.text(x0 + (1.0/cols)/2.0, y0 + 0.34*line_h, str(value),
                   ha="center", va="center", color=pal["text"],
                   fontsize=value_fs, weight=900, linespacing=1.15)

def _create_price_chart_only(p, symbol, date, days, years, company, W, H, pal, _fname):
    """Generate only the price chart with 60-day projection. Used for social posts."""
    from dateutil.parser import parse as dtparse

    f0 = AX_TOP - AX_BOTTOM
    P_BOTTOM, P_TOP = 0.12, 0.94
    f1 = P_TOP - P_BOTTOM
    H_price = int(round(H * (f0 / f1)))

    fig = _init_fig(W, H_price, pal)
    rect = [AX_LEFT, P_BOTTOM, 1.0 - AX_LEFT - AX_RIGHT, P_TOP - P_BOTTOM]
    ax = fig.add_axes(rect); ax.set_facecolor(pal["bg"])

    dates, prices = [], []
    if p.get("price_points"):
        dates  = [dtparse(pt[0]).date() for pt in p["price_points"]]
        prices = [float(pt[1]) for pt in p["price_points"]]

    years_label = _human_years_label(years)
    proj_dates, proj_prices = _build_projection(
        dates, prices,
        p.get("trend_labels", []), p.get("trend_values", []),
        60
    )

    if dates:
        if proj_dates:
            _price_with_projection(ax, dates, prices, proj_dates, proj_prices,
                                   60, years_label, W, H_price, pal)
        else:
            _price(ax, dates, prices, W, H_price, pal)

    _footer_labels(ax, pal, company)
    fpath = _fname("price")
    _save(fig, fpath, W, H_price)
    rel = _abs_to_rel(fpath)
    url = _rel_to_url(rel)
    return [{"variant": "price", "path": fpath, "rel": rel, "url": url}]


# ---------------- Main API -----------------------
def create_article_images(size_key,
                          resource_id,
                          date,           # YYYY-MM-DD
                          symbol,
                          days,           # str or int
                          years,          # str or int
                          theme="dark",
                          price_lookback_days=365,
                          light_bg="#f5f6f7",
                          bar_xlabel_scale=BAR_XLABEL_SCALE,
                          cum_xlabel_scale=CUM_XLABEL_SCALE,
                          mode="all"):
    """
    Creates and saves images. Returns list of dicts with:
      variant, path (abs), rel (web-relative), url (absolute https).

    mode='all'    - generate all chart variants (default, for articles)
    mode='social' - generate only the price chart with 60-day projection (for X posts)
    """
    THEMES = _make_themes(light_bg=light_bg)
    if size_key not in THUMB_SIZES: size_key = DEFAULT_SIZE_KEY
    W, H = THUMB_SIZES[size_key]; pal = THEMES.get(theme, THEMES["dark"])

    # Expect payload to include:
    # bar_years, bar_returns, bar_returns_mfe (levels), bar_returns_mae (levels)

    # this one had errors in pe2 so I rewrote it in this script called build_article_images_payload
    # p = build_thumbnail_payload(resource_id, symbol, date, days, years,price_lookback_days=price_lookback_days, zero_last_year=True)
    p = build_article_images_payload(resource_id, symbol, date, days, years,price_lookback_days=price_lookback_days, zero_last_year=True)

    # print(p['trend_labels'])
    # print('')
    # print(p['trend_values'])
    # exit()


    try:
        company = get_company_name(resource_id, symbol) or symbol
    except Exception:
        company = symbol
    ticker = p.get("ticker", symbol)

    out_dir, _, _, _ = get_article_image_paths(resource_id, date, symbol, "")

    def _fname(variant): # variant is like bars or trend or ... defines the chart type
        return os.path.join(out_dir, f"{symbol}_{date}_{days}_{years}_{variant}.jpg")

    results = []

    # Social mode: skip bars/trend, generate only price chart with projection
    if mode == "social":
        return _create_price_chart_only(p, symbol, date, days, years, company, W, H, pal, _fname)

    raw_date_range = _date_range_only(p.get("window_text", ""))

    # Normalize caption text so full-year windows read correctly
    if _is_full_year_window(date, days):
        core = _clean_date_range_for_caption(raw_date_range)
        # Caption emphasizes the full-year concept, not the raw day count
        if core:
            date_range = f"{core} · full-year buy-and-hold period"
        else:
            date_range = "full-year buy-and-hold period"
    else:
        date_range = raw_date_range

 # ---------- Bars (plain) ----------
    # Resize height because caption was removed (match trend/price pattern)
    f0 = AX_TOP - AX_BOTTOM        # original axes height fraction
    B_BOTTOM, B_TOP = 0.12, 0.94   # tuned margins for bars without caption
    f1 = B_TOP - B_BOTTOM
    H_bars = int(round(H * (f0 / f1)))
    fig = _init_fig(W, H_bars, pal)
    rect = [AX_LEFT, B_BOTTOM, 1.0 - AX_LEFT - AX_RIGHT, B_TOP - B_BOTTOM]
    ax = fig.add_axes(rect); ax.set_facecolor(pal["bg"])
    _bars(ax, p["bar_years"], p["bar_returns"], W, H_bars, pal, x_scale=bar_xlabel_scale)
    _footer_labels(ax, pal, company)   # caption intentionally removed
    fpath = _fname("bars")
    _save(fig, fpath, W, H_bars)
    rel = _abs_to_rel(fpath); url = _rel_to_url(rel)
    results.append({"variant": "bars", "path": fpath, "rel": rel, "url": url})

    # ---------- Bars with overlays ----------
    years_list = p["bar_years"]
    rets_list  = p["bar_returns"]

    mfe_lvl    = _align_series(p.get("bar_returns_mfe"), len(rets_list))
    mae_lvl    = _align_series(p.get("bar_returns_mae"), len(rets_list))

    trade_dir = str(p.get("trade_dir", "long")).lower()
    if trade_dir == 'short':
        mfe_lvl, mae_lvl = mae_lvl, mfe_lvl

    extra_vals_all = [v for v in (mfe_lvl or []) if v is not None] + [v for v in (mae_lvl or []) if v is not None]


    

    # Bars + MFE (draw overlays FIRST, base bars SECOND)
    fig = _init_fig(W, H, pal); ax = _axes(fig, pal)
    _draw_mfe_mae_overlays_levels(ax, years_list, rets_list, mfe_lvl, None, p.get("trade_dir","long"), pal)
    _bars(ax, years_list, rets_list, W, H, pal, x_scale=bar_xlabel_scale, extra_values=extra_vals_all)
    _footer_labels(ax, pal, company); _caption(fig, pal, f"{ticker} Seasonal Pattern + MFE | {date_range}")
    fpath = _fname("bars_mfe"); _save(fig, fpath, W, H)
    rel = _abs_to_rel(fpath); url = _rel_to_url(rel)
    results.append({"variant":"bars_mfe","path":fpath,"rel":rel,"url":url})

    # Bars + MAE
    fig = _init_fig(W, H, pal); ax = _axes(fig, pal)
    _draw_mfe_mae_overlays_levels(ax, years_list, rets_list, None, mae_lvl, p.get("trade_dir","long"), pal)
    _bars(ax, years_list, rets_list, W, H, pal, x_scale=bar_xlabel_scale, extra_values=extra_vals_all)
    _footer_labels(ax, pal, company); _caption(fig, pal, f"{ticker} Seasonal Pattern + MAE | {date_range}")
    fpath = _fname("bars_mae"); _save(fig, fpath, W, H)
    rel = _abs_to_rel(fpath); url = _rel_to_url(rel)
    results.append({"variant":"bars_mae","path":fpath,"rel":rel,"url":url})

    # Bars + MFE + MAE
    f0 = AX_TOP - AX_BOTTOM        # original axes height fraction (space reserved for caption)
    B2_BOTTOM, B2_TOP = 0.12, 0.94 # tuned margins for bars without caption
    f1 = B2_TOP - B2_BOTTOM
    H_bars_mae_mfe = int(round(H * (f0 / f1)))  # shrink canvas so plot area stays same size
    fig = _init_fig(W, H_bars_mae_mfe, pal)
    rect = [AX_LEFT, B2_BOTTOM, 1.0 - AX_LEFT - AX_RIGHT, B2_TOP - B2_BOTTOM]
    ax = fig.add_axes(rect); ax.set_facecolor(pal["bg"])
    _draw_mfe_mae_overlays_levels(ax, years_list, rets_list, mfe_lvl, mae_lvl, p.get("trade_dir","long"), pal)
    _bars(ax, years_list, rets_list, W, H_bars_mae_mfe, pal, x_scale=bar_xlabel_scale, extra_values=extra_vals_all)
    _footer_labels(ax, pal, company)   # caption intentionally removed
    fpath = _fname("bars_mae_mfe")
    _save(fig, fpath, W, H_bars_mae_mfe)
    rel = _abs_to_rel(fpath); url = _rel_to_url(rel)
    results.append({"variant":"bars_mae_mfe","path":fpath,"rel":rel,"url":url})


    # ---------- Trend ----------
    # Keep plot pixel height constant, reduce canvas height (no caption now)
    f0 = AX_TOP - AX_BOTTOM          # old axes height fraction
    T_BOTTOM, T_TOP = 0.12, 0.94     # tighter margins for trend (no caption)
    f1 = T_TOP - T_BOTTOM

    H_trend = int(round(H * (f0 / f1)))  # shrink canvas so plot area stays same size

    fig = _init_fig(W, H_trend, pal)
    rect = [AX_LEFT, T_BOTTOM, 1.0 - AX_LEFT - AX_RIGHT, T_TOP - T_BOTTOM]
    ax = fig.add_axes(rect); ax.set_facecolor(pal["bg"])

    seg_labels = p.get("trend_labels", [])
    seg_values = p.get("trend_values", [])
    # seg_labels = p.get("trend_segment_labels", [])
    # seg_values = p.get("trend_segment_values", [])

    # print('seg_values=',seg_values)
    # print('seg_labels=',seg_labels)

    # exit()

    seg_hl     = p.get("trend_segment_hl", (0, 0))
    _trend(ax, seg_labels, seg_values, seg_hl, p.get("trade_dir","long"), W, H_trend, pal)
    _footer_labels(ax, pal, company)
    fpath = _fname("trend")
    _save(fig, fpath, W, H_trend)    # save with the new shorter height
    rel = _abs_to_rel(fpath); url = _rel_to_url(rel)
    results.append({"variant":"trend","path":fpath,"rel":rel,"url":url})

    # ---------- Price ----------
    # Keep plot pixel height constant, reduce canvas height (no caption now)
    f0 = AX_TOP - AX_BOTTOM          # old axes height fraction
    P_BOTTOM, P_TOP = 0.12, 0.94     # tighter margins for price (no caption)
    f1 = P_TOP - P_BOTTOM

    H_price = int(round(H * (f0 / f1)))  # shrink canvas so plot area stays same size

    fig = _init_fig(W, H_price, pal)
    rect = [AX_LEFT, P_BOTTOM, 1.0 - AX_LEFT - AX_RIGHT, P_TOP - P_BOTTOM]
    ax = fig.add_axes(rect); ax.set_facecolor(pal["bg"])

    dates, prices = [], []
    if p.get("price_points"):
        dates  = [dtparse(pt[0]).date() for pt in p["price_points"]]
        prices = [float(pt[1]) for pt in p["price_points"]]

    # ---------- Price chart = Price + 60-day projection ----------
    years_label = _human_years_label(years)
    proj_dates_60, proj_prices_60 = _build_projection(
        dates, prices,
        p.get("trend_labels", []), p.get("trend_values", []),
        60
    )

    if dates:
        if proj_dates_60:
            _price_with_projection(ax, dates, prices, proj_dates_60, proj_prices_60,
                                   60, years_label, W, H_price, pal)
        else:
            _price(ax, dates, prices, W, H_price, pal)

    _footer_labels(ax, pal, company)
    fpath = _fname("price")
    _save(fig, fpath, W, H_price)
    rel = _abs_to_rel(fpath); url = _rel_to_url(rel)
    results.append({"variant":"price","path":fpath,"rel":rel,"url":url})

    # Social mode: only need the price chart with projection, done
    if mode == "social":
        return results

    # ---------- Price + Projection (30d, 60d, 90d) ----------
    for proj_days in (30, 60, 90):
        variant = f"price_proj_{proj_days}"
        proj_dates, proj_prices = _build_projection(
            dates, prices,
            p.get("trend_labels", []), p.get("trend_values", []),
            proj_days
        )

        fig = _init_fig(W, H_price, pal)
        rect = [AX_LEFT, P_BOTTOM, 1.0 - AX_LEFT - AX_RIGHT, P_TOP - P_BOTTOM]
        ax = fig.add_axes(rect); ax.set_facecolor(pal["bg"])

        if dates:
            _price_with_projection(ax, dates, prices, proj_dates, proj_prices,
                                   proj_days, years_label, W, H_price, pal)

        _footer_labels(ax, pal, company)

        fpath = _fname(variant)
        _save(fig, fpath, W, H_price)
        rel = _abs_to_rel(fpath); url = _rel_to_url(rel)
        results.append({"variant": variant, "path": fpath, "rel": rel, "url": url})

    # ---------- Cumulative ----------
    fig = _init_fig(W, H, pal); ax = _axes(fig, pal)
    cum_vals  = p.get("cum_data", []); year_lbls = p.get("cum_years") or p.get("bar_years") or []
    _cumulative(ax, cum_vals, year_lbls, W, H, pal, x_scale=cum_xlabel_scale)
    _footer_labels(ax, pal, company); _caption(fig, pal, f"{ticker} Cumulative Chart | {date_range}")
    fpath = _fname("cumulative"); _save(fig, fpath, W, H)
    rel = _abs_to_rel(fpath); url = _rel_to_url(rel)
    results.append({"variant":"cumulative","path":fpath,"rel":rel,"url":url})

    # ---------- Stats ----------
    # Keep plot pixel height constant, reduce canvas height (no caption)
    f0 = AX_TOP - AX_BOTTOM # old axes height fraction
    S_BOTTOM, S_TOP = 0.12, 0.94 # tighter margins for stats (no caption)
    f1 = S_TOP - S_BOTTOM
    H_stats = int(round(H * (f0 / f1)))


    fig = _init_fig(W, H_stats, pal)
    rect = [AX_LEFT, S_BOTTOM, 1.0 - AX_LEFT - AX_RIGHT, S_TOP - S_BOTTOM]
    ax = fig.add_axes(rect); ax.set_facecolor(pal["bg"])


    pairs = _stats_pairs_from_payload(p, symbol, date, days, years)
    _draw_stats_table(ax, pal, pairs)
    _footer_labels(ax, pal, company) # caption intentionally removed
    fpath = _fname("stats"); _save(fig, fpath, W, H_stats)
    rel = _abs_to_rel(fpath); url = _rel_to_url(rel)
    results.append({"variant":"stats","path":fpath,"rel":rel,"url":url})

    return results
#-------------------------------------------------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# build_article_images_payload
# -----------------------------------------------------------------------------
# Similar to build_thumbnail_payload in thumbnail_tools.py but with corrections:
# - Uses get_seasonal_chart_data2() to properly pass chart_start_date and 
#   opp_start_date separately (required for pe0/pe1/pe2/pe3 filtering)
# -----------------------------------------------------------------------------

TREND_MARGIN_DAYS = 14


def _maybe_refresh_stale_prices(financial_group_id, symbol, date1, price_points,
                                max_stale_days=10):
    """
    If the appserver price history ends well before the article date (e.g. an unrolled
    futures contract whose continuous series dead-ends mid-roll, like CME lumber LBR),
    splice in fresh EODHD EOD closes -- scaled for continuity at the splice point -- so the
    price chart and seasonal projection anchor at the CURRENT price, not a weeks-old one.

    Non-destructive: returns price_points unchanged if it isn't stale, if EODHD has nothing
    newer, or if the implied scale looks wrong (symbol mismatch). Fully non-fatal.
    """
    if not price_points:
        return price_points
    try:
        import datetime as _dt
        import config as _cfg
        from get_price_eod import get_eod_series
        def _d(s):
            return _dt.datetime.strptime(str(s)[:10], "%Y-%m-%d").date()

        last_dt = _d(price_points[-1][0])
        try:
            target = _d(date1)
        except Exception:
            target = _dt.date.today()
        ref = min(target, _dt.date.today())
        stale_days = (ref - last_dt).days
        if stale_days <= max_stale_days:
            return price_points  # fresh enough -- normal path

        exch = _cfg.exchange_mapping.get(str(financial_group_id), "US")
        series = get_eod_series(symbol, exch, last_dt.isoformat(), ref.isoformat())
        if not series or len(series) < 2:
            print(f"[PRICE-FALLBACK] {symbol}: appserver stale {stale_days}d but no EODHD series; left as-is")
            return price_points

        # scale EODHD to the appserver's last price at the splice date (continuity)
        appserver_last = float(price_points[-1][1])
        splice_val = None
        for d, c in series:
            if _d(d) <= last_dt:
                splice_val = c
        if not splice_val or splice_val <= 0:
            splice_val = series[0][1]
        scale = appserver_last / float(splice_val)
        if not (0.2 <= scale <= 5.0):
            print(f"[PRICE-FALLBACK] {symbol}: EODHD scale {scale:.3f} out of range (symbol mismatch?); left as-is")
            return price_points

        fresh = [(d, round(float(c) * scale, 4)) for d, c in series if _d(d) > last_dt]
        if not fresh:
            return price_points
        print(f"[PRICE-FALLBACK] {symbol}: appserver stale {stale_days}d (ends {last_dt}); "
              f"spliced {len(fresh)} EODHD pt(s) x{scale:.4f} -> {fresh[-1][0]} @ {fresh[-1][1]}")
        return list(price_points) + fresh
    except Exception as e:
        print(f"[PRICE-FALLBACK] {symbol}: skipped (non-fatal): {e}")
        return price_points


def build_article_images_payload(financial_group_id, symbol, date1, days_hold, years,
                                  price_lookback_days=60, zero_last_year=True):
    from create_report import get_seasonal_chart_data2, get_chart_data
    from create_report import get_keyprovider_token, login_appserver
    from thumbnail_tools import get_chart_historical_prices, get_cumulative_chart_data
    from thumbnail_tools import build_stats_triplet, build_trend_segment, inc_date_day, f2

    keyprovider_token = get_keyprovider_token()
    appserver_token = login_appserver(keyprovider_token)

    # get TREND_MARGIN_DAYS before date1 as the starting date of the trendchart
    trend_start_date = inc_date_day(date1, -TREND_MARGIN_DAYS)

    # Seasonal curve (full year) - uses corrected function with both dates
    trend_labels, trend_values = get_seasonal_chart_data2(
        financial_group_id, symbol, years,
        trend_start_date,  # chart visual start
        date1,             # opportunity date (for pe filtering)
        appserver_token
    )

    # NEW: seasonal trend segment (raw, all positive)
    seg_labels, seg_values, seg_hl = build_trend_segment(
        trend_labels, trend_values, date1, days_hold, TREND_MARGIN_DAYS
    )

    # Bar chart (year-by-year window returns)
    days_hold_corrected = str(int(days_hold) - 1)
    cdata = get_chart_data(financial_group_id, date1, symbol, days_hold_corrected, years, zero_last_year, appserver_token)

    # Success text + avg gain text
    w = int(cdata['stats']['Num Winners'])
    l = int(cdata['stats']['Num Losers'])
    success_text = f"{w} of {w+l}"

    avg_raw = cdata['stats'].get('Avg Profit') or "0%"
    avg_val = f2(avg_raw, 0.0)
    avg_gain_txt = f"{avg_val:.1f}%"

    # Cumulative curve over the window built from barData
    cum_data = get_cumulative_chart_data(cdata)

    # Recent price data (~60d)
    d1 = date1
    d0 = inc_date_day(date1, -int(price_lookback_days))
    price_points = get_chart_historical_prices(financial_group_id, symbol, d0, d1, appserver_token)
    # Splice in fresh EODHD prices if the appserver feed is stale (e.g. unrolled futures).
    price_points = _maybe_refresh_stale_prices(financial_group_id, symbol, date1, price_points)

    # Bar arrays
    bar_years, bar_returns, bar_returns_mfe, bar_returns_mae = [], [], [], []
    for row in cdata['ChartData4']:
        yr = int(row['year'])
        pct = float(row['pct'].split(',')[0])
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
    end_date = inc_date_day(date1, int(days_hold) - 1)
    window_text = f"Seasonal Edge | {date1} ➝ {end_date} ({int(days_hold)} Days)"

    return {
        "ticker": symbol,
        "years": years,
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
        "price_points": price_points,
        "stats_triplet": build_stats_triplet(cdata),
        "trade_dir": trade_dir,
        # "trend_segment_labels": seg_labels,
        # "trend_segment_values": seg_values,
        "trend_segment_hl": seg_hl,
        "trend_margin_days": TREND_MARGIN_DAYS,
    }
# ---------------- CLI smoke test -----------------
if __name__ == "__main__":
    info = create_article_images(
        size_key="x",
        resource_id="2",
        date="2026-05-23",
        symbol="AMZN",
        days="61",
        years="15",
        theme="light",
        cum_xlabel_scale=0.75,
    )

    # info = create_article_images(
    #     size_key="x",
    #     resource_id=0,
    #     date="2025-08-30",
    #     symbol="DIS",
    #     days="23",
    #     years="10",
    #     theme="light",
    #     cum_xlabel_scale=0.75,
    # )


    for r in info:
        print(f"{r['variant']:>13}  path={r['path']}  rel={r['rel']}  url={r['url']}")
