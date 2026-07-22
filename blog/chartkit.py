# -*- coding: utf-8 -*-
"""
chartkit.py - SMN production chart visual system.

Port of the approved prototype (chart_proto_samples.py) into a clean, reusable
module. Every renderer is a pure, deterministic function of its inputs: no
network calls, no external fonts/CDN, headless (Agg). Each renderer draws its
figure AND returns a semantics dict describing exactly what the chart says,
with every number computed from the same arrays being drawn - the renderer is
the single source of truth for the chart's claim.

Palettes: "light" (the prototype's PAL, default) and "dark" (the visual system
mapped onto article_images' existing dark theme). Palette is a parameter; the
article pipeline uses light today.

Semantics dict (returned by every renderer):
    {"variant", "title", "spec", "source", "alt", "n", "direction",
     "window_start", "window_end"}
where alt = f"{title}. {spec}. {source}".
"""

import os
import glob
import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
import matplotlib.dates as mdates        # noqa: E402
import matplotlib.font_manager as fm     # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

# --------------------------------------------------------------------------- #
# Font registration (Roboto TTFs, headless Agg backend)
# --------------------------------------------------------------------------- #
FONTS_DIR = "/home/flask/blog/fonts"

def _register_fonts():
    for f in glob.glob(os.path.join(FONTS_DIR, "Roboto-*.ttf")):
        try:
            fm.fontManager.addfont(f)
        except Exception:
            pass
    plt.rcParams["font.family"] = "Roboto"

_register_fonts()

# --------------------------------------------------------------------------- #
# Palettes
# --------------------------------------------------------------------------- #
# Light: the prototype's PAL, verbatim.
PAL = {
    "bg":    "#FFFFFF",
    "ink":   "#101828",   # headline / primary line
    "muted": "#667085",   # spec line, tick labels
    "faint": "#98A2B3",   # source line
    "grid":  "#E7EAEE",
    "axis":  "#D0D5DD",   # bottom hairline
    "pos":   "#0E7B54",   # deep green (gain)
    "neg":   "#D92D20",   # signal red (loss)
    "accent":"#1D5BD6",   # price / data line blue
    "amber": "#DC6803",   # projection (hypothetical path)
    "whisk": "#475467",   # excursion range needle
}

# Dark: the same visual system mapped onto article_images' existing dark theme
# (bg #0f1216, ink #e8ecf1, muted #9aa5b1); pos/neg/accent/amber brightened for
# a dark background.
PAL_DARK = {
    "bg":    "#0f1216",
    "ink":   "#e8ecf1",   # headline / primary line
    "muted": "#9aa5b1",   # spec line, tick labels
    "faint": "#6b7580",   # source line
    "grid":  "#232a31",
    "axis":  "#3a424b",   # bottom hairline
    "pos":   "#41d14a",   # bright green (gain)
    "neg":   "#ef4444",   # bright red (loss)
    "accent":"#60a5fa",   # price / data line blue
    "amber": "#f5b942",   # projection (hypothetical path)
    "whisk": "#8a97a6",   # excursion range needle
}

PALETTES = {"light": PAL, "dark": PAL_DARK}

def _pal(palette):
    """Accept a palette name ('light'/'dark') or a palette dict."""
    if isinstance(palette, dict):
        return palette
    return PALETTES.get(palette or "light", PAL)

W, H, DPI = 1280, 720, 100

# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
# Roboto lacks glyphs like "➝" (U+279D). Replace arrows/dashes it can't render
# with an en-dash so nothing turns into a tofu box.
_ARROW_MAP = {
    "➝": "–",  # ➝ heavy round-tipped rightwards arrow
    "→": "–",  # →
    "⟶": "–",  # ⟶
    "➔": "–",  # ➔
    "➞": "–",  # ➞
    "➜": "–",  # ➜
    "➙": "–",  # ➙
    "↦": "–",  # ↦
    "⮕": "–",  # ⮕
}

def _sanitize(s):
    """Replace glyphs Roboto can't render (e.g. ➝) with an en-dash."""
    if s is None:
        return ""
    s = str(s)
    for bad, good in _ARROW_MAP.items():
        s = s.replace(bad, good)
    return s

def _track(s, sp="  "):
    return sp.join(list(s))

def _fmt_pct_signed(v, _=None):
    if abs(v) < 1e-9:
        return "0"
    return f"{v:+.0f}%"

def _fmt_mmm_d(datestr):
    """'2026-07-21' -> 'Jul 21'."""
    d = datetime.datetime.strptime(str(datestr)[:10], "%Y-%m-%d")
    return d.strftime("%b %d").replace(" 0", " ")

def _median(vals):
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return 0.0
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0

def _drop_zeroed(years, nets, mfe=None, mae=None):
    """Drop placeholder '0,0,0' rows (net==0 and mfe/mae absent-or-0).

    Returns (years, nets, mfe, mae) with those indices removed. mfe/mae stay
    None when they came in as None.
    """
    yk, nk, fk, ak = [], [], [], []
    for i in range(len(nets)):
        net = float(nets[i])
        m = None if mfe is None else (None if mfe[i] in (None, "") else float(mfe[i]))
        a = None if mae is None else (None if mae[i] in (None, "") else float(mae[i]))
        is_zero_row = (abs(net) < 1e-12
                       and (m is None or abs(m) < 1e-12)
                       and (a is None or abs(a) < 1e-12))
        if is_zero_row:
            continue
        yk.append(years[i]); nk.append(net)
        if mfe is not None:
            fk.append(m)
        if mae is not None:
            ak.append(a)
    return yk, nk, (fk if mfe is not None else None), (ak if mae is not None else None)

def _semantics(variant, title, spec, source, n, direction,
               window_start, window_end):
    title = _sanitize(title); spec = _sanitize(spec); source = _sanitize(source)
    return {
        "variant": variant,
        "title": title,
        "spec": spec,
        "source": source,
        "alt": f"{title}. {spec}. {source}",
        "n": n,
        "direction": direction,
        "window_start": window_start,
        "window_end": window_end,
    }

# --------------------------------------------------------------------------- #
# Frame / header / footer builders
# --------------------------------------------------------------------------- #
def _fit_text(fig, x, y, text, *, max_frac, fontsize, fontweight, color,
              ha="left", va="top", min_fontsize=8.0):
    """Draw header text, shrinking until it fits `max_frac` of the canvas width.

    The header used a fixed size with no width bound, so a long spec line ran
    past the chart's right margin (and nearly off the canvas). How long the
    string is depends on the data, so this must be measured, not guessed.
    """
    t = fig.text(x, y, text, fontsize=fontsize, fontweight=fontweight,
                 color=color, ha=ha, va=va)
    W = fig.get_size_inches()[0] * fig.dpi
    avail = max_frac * W
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    size = fontsize
    while t.get_window_extent(renderer=r).width > avail and size > min_fontsize:
        size = max(min_fontsize, size * 0.96)
        t.set_fontsize(size)
        fig.canvas.draw()
    return t


def new_frame(kicker, title, spec, source, *, palette="light", w=W, h=H,
              ax_rect=(0.065, 0.145, 0.905, 0.60), axes_style=True):
    """Standard SMN chart frame: header block, plot area, source footer."""
    pal = _pal(palette)
    fig = plt.figure(figsize=(w / DPI, h / DPI), dpi=DPI)
    fig.patch.set_facecolor(pal["bg"])
    left = ax_rect[0]
    # Header lines are width-fitted to the SAME right margin the plot, source
    # line and TRADEWAVE.AI already respect (left + ax_rect[2]).
    _hdr_frac = ax_rect[2]
    _fit_text(fig, left, 0.952, _track(_sanitize(kicker).upper()),
              max_frac=_hdr_frac, fontsize=11, fontweight=500,
              color=pal["muted"])
    _fit_text(fig, left, 0.905, _sanitize(title),
              max_frac=_hdr_frac, fontsize=21.5, fontweight=700,
              color=pal["ink"], min_fontsize=13.0)
    _fit_text(fig, left, 0.842, _sanitize(spec),
              max_frac=_hdr_frac, fontsize=12.5, fontweight=400,
              color=pal["muted"], min_fontsize=9.0)
    fig.text(left, 0.040, _sanitize(source), fontsize=10.5, fontweight=400,
             color=pal["faint"], ha="left", va="center")
    fig.text(left + ax_rect[2], 0.040, _track("TRADEWAVE.AI"), fontsize=10.5,
             fontweight=700, color=pal["faint"], ha="right", va="center")
    ax = fig.add_axes(ax_rect)
    ax.set_facecolor(pal["bg"])
    if axes_style:
        style_axes(ax, palette=pal)
    return fig, ax

def place_label(ax, text, *, anchor, avoid=(), color=None, fontsize=11,
                fontweight=600, pad_px=4.0, candidates=None):
    """Draw `text` near `anchor` (data coords), choosing a position that does
    not overlap the plotted data.

    anchor  : (x, y) in DATA coords - the point the label refers to.
    avoid   : iterable of (x, y) data-coord sequences to keep clear (the series).
    candidates : ranked (dx, dy, ha, va) offsets in POINTS. First one that is
                 collision-free and inside the axes wins; otherwise the one with
                 the fewest collisions.

    Placement is CHOSEN per render, not hardcoded, because the collision depends
    on the data. Hand-nudging an offset fixes one chart and breaks the next.
    """
    if candidates is None:
        candidates = [
            (0,   14, "center", "bottom"),   # directly above
            (10,  14, "left",   "bottom"),   # above-right
            (-10, 14, "right",  "bottom"),   # above-left
            (14,   0, "left",   "center"),   # right
            (-14,  0, "right",  "center"),   # left
            (0,  -14, "center", "top"),      # below
            (10, -14, "left",   "top"),      # below-right
            (-10,-14, "right",  "top"),      # below-left
            (0,   26, "center", "bottom"),   # higher above
        ]

    fig = ax.figure
    fig.canvas.draw()
    rend = fig.canvas.get_renderer()

    # Series points in display space, once.
    pts = []
    for seq in avoid:
        seq = [p for p in seq if p is not None]
        if not seq:
            continue
        try:
            pts.extend(ax.transData.transform(seq))
        except Exception:
            pass

    ax_box = ax.get_window_extent(renderer=rend)

    best = None  # (score, artist)
    for dx, dy, ha, va in candidates:
        t = ax.annotate(text, xy=anchor, xytext=(dx, dy),
                        textcoords="offset points", fontsize=fontsize,
                        fontweight=fontweight, color=color, ha=ha, va=va)
        fig.canvas.draw()
        bb = t.get_window_extent(renderer=rend).expanded(1.0, 1.0)
        x0, x1 = bb.x0 - pad_px, bb.x1 + pad_px
        y0, y1 = bb.y0 - pad_px, bb.y1 + pad_px

        hits = sum(1 for px, py in pts if x0 <= px <= x1 and y0 <= py <= y1)
        # Leaving the plot area is worse than brushing a point.
        outside = 0
        if bb.x0 < ax_box.x0 or bb.x1 > ax_box.x1:
            outside += 50
        if bb.y0 < ax_box.y0 or bb.y1 > ax_box.y1:
            outside += 50
        score = hits + outside

        if score == 0:
            # A previously-kept candidate is still on the axes; drop it or the
            # label renders twice.
            if best is not None:
                best[1].remove()
            return t
        if best is None or score < best[0]:
            if best is not None:
                best[1].remove()
            best = (score, t)
        else:
            t.remove()

    return best[1] if best else None


def style_axes(ax, *, palette="light", bottom_spine=True):
    pal = _pal(palette)
    for s in ax.spines.values():
        s.set_visible(False)
    if bottom_spine:
        ax.spines["bottom"].set_visible(True)
        ax.spines["bottom"].set_color(pal["axis"])
        ax.spines["bottom"].set_linewidth(1.0)
    ax.set_axisbelow(True)
    ax.grid(axis="y", color=pal["grid"], linewidth=1.0)
    ax.tick_params(colors=pal["muted"], labelsize=11, length=0)

def _save(fig, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fig.savefig(path, dpi=DPI, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path

# --------------------------------------------------------------------------- #
# Title / spec / source composers (renderer = single source of truth)
# --------------------------------------------------------------------------- #
def _lookback_phrase(meta, n):
    """Compact lookback phrase for kickers: '20-year' for plain lookbacks,
    the cycle name ('15 midterm election years') for PE slices. The verbose
    '20 Years of Historical Data' form never belongs in a kicker."""
    lbl = str(meta.get("lookback_label") or "")
    if lbl and "Historical Data" not in lbl:
        return lbl.lower()          # PE cycle labels, e.g. '15 midterm election years'
    if str(n).strip():
        return f"{n}-year"
    return lbl or "seasonal"

def _bars_title(symbol, direction, wins, n, win_lbl):
    word = "higher" if str(direction).lower().startswith("l") else "lower"
    k = wins if word == "higher" else (n - wins)
    base = f"{symbol} has closed {word} in {k} of the past {n} years"
    return f"{base} ({win_lbl})" if win_lbl else base

def _bars_source(n, y0, y1, direction):
    conv = "long" if str(direction).lower().startswith("l") else "short"
    return (f"Source: TradeWave seasonal database · n={n} completed years "
            f"({y0}–{y1}) · {conv} convention: positive = price rose")

# --------------------------------------------------------------------------- #
# Renderer: record_bars
# --------------------------------------------------------------------------- #
def record_bars(years, nets, meta, path, *, mfe=None, mae=None,
                w=W, h=H, show_median=True, palette="light"):
    """One bar per completed year, net % over the window.

    Optional excursion "needles" (thin whisker lines drawn over the bars):
      - both mfe and mae  -> needle spans mae -> mfe (full intra-window range)
      - mfe only          -> needle spans 0  -> mfe (best gain reached)
      - mae only          -> needle spans mae -> 0  (worst drawdown reached)

    meta keys used: symbol, direction ('long'/'short'), window_start,
      window_end (YYYY-MM-DD), variant (optional; controls spec text),
      lookback_label (optional), kicker (optional; else derived).
    """
    pal = _pal(palette)
    years, nets, mfe, mae = _drop_zeroed(years, nets, mfe, mae)
    n = len(nets)
    wins = sum(1 for v in nets if v > 0)
    direction = meta.get("direction", "long")
    med = _median(nets)
    y0 = years[0] if years else ""
    y1 = years[-1] if years else ""
    d1 = meta.get("window_start", "")
    d2 = meta.get("window_end", "")
    win_lbl = f"{_fmt_mmm_d(d1)} – {_fmt_mmm_d(d2)}" if d1 and d2 else ""

    symbol = meta.get("symbol", "")
    company = meta.get("company", "")
    days = meta.get("days", "")
    variant = meta.get("variant", "record_bars")

    kicker = meta.get("kicker") or (
        f"{symbol}"
        + (f" · {company}" if company else "")
        + (f" · {days}-day seasonal window" if days else " · seasonal window"))
    title = _bars_title(symbol, direction, wins, n, win_lbl)

    mmm1, mmm2 = (_fmt_mmm_d(d1) if d1 else ""), (_fmt_mmm_d(d2) if d2 else "")
    if mfe is not None and mae is not None:
        spec = ("Bars: net % change over the window. Needles: the full "
                "intra-window range each year (worst drawdown to best gain)")
    elif mfe is not None:
        spec = ("Bars: net % change over the window. Needles: the best gain "
                "reached within the window each year")
    elif mae is not None:
        spec = ("Bars: net % change over the window. Needles: the worst "
                "drawdown reached within the window each year")
    else:
        spec = (f"Net % change from the {mmm1} close to the {mmm2} close, "
                f"each year - one bar per year")
    source = _bars_source(n, y0, y1, direction)

    fig, ax = new_frame(kicker, title, spec, source, palette=pal, w=w, h=h)
    x = list(range(n))
    colors = [pal["pos"] if v >= 0 else pal["neg"] for v in nets]
    ax.bar(x, nets, width=0.62, color=colors, zorder=3)
    ax.axhline(0, color=pal["ink"], linewidth=1.1, zorder=4)

    # needles
    lo_extra, hi_extra = [], []
    if mfe is not None or mae is not None:
        for i in range(n):
            top = mfe[i] if mfe is not None else 0.0
            bot = mae[i] if mae is not None else 0.0
            if top is None:
                top = 0.0
            if bot is None:
                bot = 0.0
            lo_extra.append(bot); hi_extra.append(top)
            ax.vlines(x[i], min(bot, top), max(bot, top), color=pal["whisk"],
                      linewidth=1.4, alpha=0.65, zorder=5)

    lo = min([min(nets)] + lo_extra + [0.0]) if nets else -1.0
    hi = max([max(nets)] + hi_extra + [0.0]) if nets else 1.0
    pad = (hi - lo) * 0.12 if hi > lo else 1.0
    ax.set_ylim(lo - pad, hi + pad)

    if show_median and n:
        ax.axhline(med, color=pal["ink"], linewidth=1.1, alpha=0.55,
                   linestyle=(0, (4, 3)), zorder=4)
        ax.annotate(f"median {med:+.1f}%", xy=(x[-1] + 0.55, med),
                    fontsize=11, fontweight=600, color=pal["ink"],
                    alpha=0.75, va="bottom", ha="right",
                    xytext=(x[-1] + 0.55, med + (hi - lo) * 0.015))

    step = 1 if n <= 20 else 2
    ax.set_xticks(x[::step])
    ax.set_xticklabels([str(y) for y in years][::step], fontsize=10)
    ax.set_xlim(-0.7, n - 0.3 + 0.6)
    ax.yaxis.set_major_formatter(FuncFormatter(_fmt_pct_signed))
    _save(fig, path)
    return _semantics(variant, title, spec, source, n, direction, d1, d2)

# --------------------------------------------------------------------------- #
# Renderer: trend_window
# --------------------------------------------------------------------------- #
def trend_window(labels, vals, d1, d2, direction, meta, path, *, palette="light"):
    """Seasonal path: average cumulative % through the year with the trade
    window as a shaded band. Replaces the Pillow-patched legacy chart.

    vals are rebased to 0 at index 0 (the raw consolidated curve inherits a
    Jan-1 index).

    meta keys used: symbol, company, n (completed years), year_first,
      year_last, days, lookback_label (optional), kicker/title/spec/source
      overrides (optional).
    """
    pal = _pal(palette)
    labels = list(labels)
    vals = [float(v) for v in vals]
    if vals:
        base = vals[0]
        vals = [v - base for v in vals]

    symbol = meta.get("symbol", "")
    company = meta.get("company", "")
    n = meta.get("n", "")
    y0 = meta.get("year_first", "")
    y1 = meta.get("year_last", "")
    days = meta.get("days", "")
    tstart = labels[0] if labels else d1
    win_lbl = f"{_fmt_mmm_d(d1)} – {_fmt_mmm_d(d2)}"

    kicker = meta.get("kicker") or (
        f"{symbol}" + (f" · {company}" if company else "")
        + f" · {_lookback_phrase(meta, n)} seasonal path")
    title = meta.get("title") or f"Where {win_lbl} sits in {symbol}'s average year"
    spec = meta.get("spec") or (
        f"{symbol}'s average path over the past {n} years, rebased to 0 at "
        f"{_fmt_mmm_d(tstart)} · shaded: the {days}-day window")
    source = meta.get("source") or (
        f"Source: TradeWave seasonal database · {n}-year average "
        f"({y0}–{y1}) · not a forecast")

    fig, ax = new_frame(kicker, title, spec, source, palette=pal)
    x = list(range(len(vals)))
    band = pal["pos"] if str(direction).lower().startswith("l") else pal["neg"]

    def idx_of(datestr):
        for i, l in enumerate(labels):
            if l >= datestr:
                return i
        return len(labels) - 1
    i1, i2 = idx_of(d1), idx_of(d2)
    ax.axvspan(i1, i2, facecolor=band, alpha=0.08, zorder=1)
    for i in (i1, i2):
        ax.axvline(i, color=band, alpha=0.45, linewidth=1.0, zorder=2)
    ax.plot(x, vals, color=pal["ink"], linewidth=2.4, zorder=3,
            solid_capstyle="round")
    for i in (i1, i2):
        ax.plot([i], [vals[i]], "o", ms=5.5, color=band, zorder=5)

    if vals:
        ytop = max(vals) + (max(vals) - min(vals)) * 0.06
        ax.text((i1 + i2) / 2, ytop, win_lbl, ha="center", va="bottom",
                fontsize=11.5, fontweight=600, color=band)
        pad = (max(vals) - min(vals)) * 0.16
        ax.set_ylim(min(vals) - pad * 0.5, max(vals) + pad)

    # month ticks derived from the label strings
    ticks, tlabels, seen = [], [], set()
    for i, l in enumerate(labels):
        m = l[5:7]
        if m not in seen:
            seen.add(m)
            ticks.append(i)
            tlabels.append(datetime.datetime.strptime(l, "%Y-%m-%d").strftime("%b"))
    if ticks and ticks[0] == 0 and len(ticks) > 1 and (ticks[1] - ticks[0]) < 12:
        ticks, tlabels = ticks[1:], tlabels[1:]   # partial first month
    ax.set_xticks(ticks)
    ax.set_xticklabels(tlabels, fontsize=10.5)
    ax.set_xlim(0, max(1, len(x) - 1))
    ax.yaxis.set_major_formatter(FuncFormatter(_fmt_pct_signed))
    _save(fig, path)
    return _semantics("trend_window", title, spec, source, n, direction, d1, d2)

# --------------------------------------------------------------------------- #
# Renderer: price_projection
# --------------------------------------------------------------------------- #
def price_projection(dates, prices, proj_dates, proj_prices, meta, path, *,
                     palette="light"):
    """Recent price (blue) with the median seasonal path ahead (dashed amber).

    meta keys used: symbol, company, n, proj_days, one_month (1M return string,
      optional), kicker/title/spec/source overrides (optional).
    """
    pal = _pal(palette)
    prices = [float(p) for p in prices]
    proj_prices = [float(p) for p in (proj_prices or [])]

    symbol = meta.get("symbol", "")
    company = meta.get("company", "")
    n = meta.get("n", "")
    proj_days = meta.get("proj_days", "")
    mo1 = meta.get("one_month", "")

    kicker = meta.get("kicker") or (
        f"{symbol}" + (f" · {company}" if company else "")
        + " · price and seasonal path")
    title = meta.get("title") or (
        f"{symbol} enters the window at {prices[-1]:,.2f}"
        + (f", {mo1} over the past month" if mo1 else ""))
    spec = meta.get("spec") or (
        f"Daily closes, past 12 months · dashed amber: the median "
        f"{n}-year seasonal path over the next {proj_days} days, anchored to "
        f"the last close - indicative, not a forecast")
    source = meta.get("source") or (
        f"Source: TradeWave price history + seasonal database · n={n} years")

    fig, ax = new_frame(kicker, title, spec, source, palette=pal,
                        ax_rect=(0.065, 0.145, 0.865, 0.60))
    dn = mdates.date2num(dates)
    ax.plot(dn, prices, color=pal["accent"], linewidth=2.2, zorder=3,
            solid_capstyle="round")
    ax.fill_between(dn, prices, min(prices), color=pal["accent"],
                    alpha=0.05, zorder=1)
    allp = list(prices)
    if proj_dates:
        pn = mdates.date2num(proj_dates)
        ax.plot(pn, proj_prices, color=pal["amber"], linewidth=2.0,
                linestyle=(0, (3, 2)), zorder=4)
        allp += list(proj_prices)
        chg = (proj_prices[-1] / prices[-1] - 1) * 100
        # Anchor on the projection's ACTUAL peak (x and y from the same point -
        # the old code took x from the last date and y from the back-half max,
        # so the label sat above a value belonging to a point far to its left,
        # landing on the line). Position is then chosen by collision test.
        pk = max(range(len(proj_prices)), key=lambda i: proj_prices[i])
        place_label(ax, f"seasonal path {chg:+.1f}%",
                    anchor=(pn[pk], proj_prices[pk]),
                    avoid=(list(zip(pn, proj_prices)), list(zip(dn, prices))),
                    color=pal["amber"], fontsize=11, fontweight=600)
    ax.plot([dn[-1]], [prices[-1]], "o", ms=5, color=pal["accent"], zorder=5)
    # last-price label BELOW the min of the last ~10 closes so it never sits on
    # the line (fix over the prototype, which anchored on the line itself).
    tail = prices[-10:] if len(prices) >= 10 else prices
    span = (max(allp) - min(allp)) or 1.0
    label_y = min(tail) - span * 0.02
    ax.annotate(f"{prices[-1]:,.2f}", xy=(dn[-1], label_y), xytext=(-6, -4),
                textcoords="offset points", fontsize=11, fontweight=600,
                color=pal["accent"], va="top", ha="right")

    loc = mdates.AutoDateLocator(minticks=4, maxticks=7)
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    lo, hi = min(allp), max(allp)
    pad = (hi - lo) * 0.08 if hi > lo else 1.0
    # extra bottom room for the last-price label; top pad x1.9 when projecting
    ax.set_ylim(lo - pad * 1.6, hi + pad * (1.9 if proj_dates else 1.0))
    xmax = mdates.date2num(proj_dates[-1]) if proj_dates else dn[-1]
    ax.set_xlim(dn[0], xmax)
    _save(fig, path)
    d1 = meta.get("window_start", "")
    d2 = meta.get("window_end", "")
    return _semantics("price_projection", title, spec, source, n,
                      meta.get("direction", "long"), d1, d2)

# --------------------------------------------------------------------------- #
# Renderer: cumulative
# --------------------------------------------------------------------------- #
def cumulative(years, cum_vals, meta, path, *, palette="light"):
    """Cumulative % of the window compounded year over year: line + soft fill,
    colored pos/neg by the end value.

    meta keys used: symbol, company, direction, window_start, window_end,
      days, lookback_label (optional), kicker/title/spec/source overrides.
    """
    pal = _pal(palette)
    cum_vals = [float(v) for v in cum_vals]
    years = list(years)[:len(cum_vals)]
    n = len(cum_vals)
    end_val = cum_vals[-1] if cum_vals else 0.0
    color = pal["pos"] if end_val >= 0 else pal["neg"]
    direction = meta.get("direction", "long")

    symbol = meta.get("symbol", "")
    company = meta.get("company", "")
    days = meta.get("days", "")
    d1 = meta.get("window_start", "")
    d2 = meta.get("window_end", "")
    win_lbl = f"{_fmt_mmm_d(d1)} – {_fmt_mmm_d(d2)}" if d1 and d2 else ""
    y0 = years[0] if years else ""
    y1 = years[-1] if years else ""

    kicker = meta.get("kicker") or (
        f"{symbol}" + (f" · {company}" if company else "")
        + " · cumulative window return")
    title = meta.get("title") or (
        f"Stacking the {win_lbl} window compounds to {end_val:+.1f}% over "
        f"{n} years" if win_lbl else
        f"The window compounds to {end_val:+.1f}% over {n} years")
    spec = meta.get("spec") or (
        f"Cumulative return of the {days}-day window, compounded year over "
        f"year - one point per year" if days else
        "Cumulative return of the window, compounded year over year")
    source = meta.get("source") or (
        f"Source: TradeWave seasonal database · n={n} completed years "
        f"({y0}–{y1})")

    fig, ax = new_frame(kicker, title, spec, source, palette=pal)
    x = list(range(n))
    ax.plot(x, cum_vals, color=color, linewidth=2.4, zorder=3,
            solid_capstyle="round")
    ax.fill_between(x, cum_vals, 0, color=color, alpha=0.12, zorder=1)
    ax.axhline(0, color=pal["axis"], linewidth=1.0, zorder=2)
    step = 1 if n <= 20 else 2
    ax.set_xticks(x[::step])
    ax.set_xticklabels([str(y) for y in years][::step], fontsize=10)
    ax.set_xlim(-0.5, max(1, n - 1) + 0.5)
    ax.yaxis.set_major_formatter(FuncFormatter(_fmt_pct_signed))
    _save(fig, path)
    return _semantics("cumulative", title, spec, source, n, direction, d1, d2)

# --------------------------------------------------------------------------- #
# Renderer: stats_table
# --------------------------------------------------------------------------- #
def stats_table(pairs, meta, path, *, palette="light"):
    """Article stats grid (3-column boxed grid) inside the new frame.

    meta keys used: symbol, company, direction, window_start, window_end,
      kicker/title/spec/source overrides (optional).
    """
    pal = _pal(palette)
    symbol = meta.get("symbol", "")
    company = meta.get("company", "")
    direction = meta.get("direction", "long")
    d1 = meta.get("window_start", "")
    d2 = meta.get("window_end", "")
    win_lbl = f"{_fmt_mmm_d(d1)} – {_fmt_mmm_d(d2)}" if d1 and d2 else ""

    kicker = meta.get("kicker") or (
        f"{symbol}" + (f" · {company}" if company else "")
        + " · pattern statistics")
    title = meta.get("title") or (
        f"{symbol} {win_lbl} - the numbers behind the pattern"
        if win_lbl else f"{symbol} - the numbers behind the pattern")
    spec = meta.get("spec") or (
        "Key seasonal statistics for the window, computed across all "
        "completed years")
    source = meta.get("source") or "Source: TradeWave seasonal database"

    # Header/footer only; the grid gets its own full-width inset axes.
    fig, ax = new_frame(kicker, title, spec, source, palette=pal,
                        ax_rect=(0.065, 0.08, 0.905, 0.66), axes_style=False)
    ax.set_axis_off()
    _draw_stats_grid(ax, pal, list(pairs))
    _save(fig, path)
    return _semantics("stats", title, spec, source, meta.get("n", ""),
                      direction, d1, d2)

def _draw_stats_grid(ax, pal, pairs):
    """Render (label, value) pairs in a 3-column boxed grid inside the axes,
    styled to the chartkit visual system."""
    import math
    ax.set_axis_off()
    inner = ax.inset_axes([0.0, 0.0, 1.0, 1.0]); inner.set_axis_off()
    inner.set_xlim(0, 1); inner.set_ylim(0, 1)

    n = len(pairs)
    cols = 3
    rows = int(math.ceil(n / cols)) if n else 1
    bbox = ax.get_position(); fig = ax.figure
    fw, fh = fig.get_size_inches() * fig.dpi
    ax_h_px = (bbox.y1 - bbox.y0) * fh
    label_fs = max(11, int(ax_h_px * 0.052 / fig.dpi * 72 / 100) * 2)
    value_fs = max(14, int(ax_h_px * 0.080 / fig.dpi * 72 / 100) * 2)
    line_h = 1.0 / rows

    for idx, (label, value) in enumerate(pairs):
        r = idx // cols; c = idx % cols
        x0 = c * (1.0 / cols)
        y0 = 1.0 - (r + 1) * line_h
        inner.add_patch(plt.Rectangle((x0 + 0.012, y0 + 0.12 * line_h),
                                      (1.0 / cols) - 0.024, line_h * 0.76,
                                      fill=False, edgecolor=pal["axis"],
                                      linewidth=1.4))
        inner.text(x0 + 0.028, y0 + 0.70 * line_h, _sanitize(label),
                   ha="left", va="center", color=pal["muted"],
                   fontsize=label_fs, fontweight=600)
        inner.text(x0 + (1.0 / cols) / 2.0, y0 + 0.34 * line_h,
                   _sanitize(str(value)), ha="center", va="center",
                   color=pal["ink"], fontsize=value_fs, fontweight=700,
                   linespacing=1.15)

# --------------------------------------------------------------------------- #
# Renderer: fork_panels (built + tested, NOT wired into production yet)
# --------------------------------------------------------------------------- #
def fork_panels(panels, meta, path, *, palette="light"):
    """FORK: two record-bar panels side by side in a shared frame.

    panels: list of (years, nets, subtitle) - exactly two.
    meta keys used: kicker, title, spec, source (all optional overrides).
    """
    pal = _pal(palette)
    kicker = meta.get("kicker", "")
    title = meta.get("title", "")
    spec = meta.get("spec", "")
    source = meta.get("source", "")

    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI)
    fig.patch.set_facecolor(pal["bg"])
    left = 0.065
    fig.text(left, 0.952, _track(_sanitize(kicker).upper()), fontsize=11,
             fontweight=500, color=pal["muted"], ha="left", va="top")
    fig.text(left, 0.905, _sanitize(title), fontsize=21.5, fontweight=700,
             color=pal["ink"], ha="left", va="top")
    fig.text(left, 0.842, _sanitize(spec), fontsize=12.5, color=pal["muted"],
             ha="left", va="top")
    fig.text(left, 0.040, _sanitize(source), fontsize=10.5, color=pal["faint"],
             ha="left", va="center")
    fig.text(0.97, 0.040, _track("TRADEWAVE.AI"), fontsize=10.5,
             fontweight=700, color=pal["faint"], ha="right", va="center")
    rects = [(0.065, 0.145, 0.42, 0.545), (0.55, 0.145, 0.42, 0.545)]
    total_n = 0
    for (years, nets, subtitle), rect in zip(panels, rects):
        years = list(years); nets = [float(v) for v in nets]
        total_n += len(nets)
        ax = fig.add_axes(rect)
        ax.set_facecolor(pal["bg"])
        style_axes(ax, palette=pal)
        x = list(range(len(years)))
        colors = [pal["pos"] if v >= 0 else pal["neg"] for v in nets]
        ax.bar(x, nets, width=0.62, color=colors, zorder=3)
        ax.axhline(0, color=pal["ink"], linewidth=1.1, zorder=4)
        step = 1 if len(years) <= 12 else 2
        ax.set_xticks(x[::step])
        ax.set_xticklabels([str(y) for y in years][::step], fontsize=9.5)
        ax.yaxis.set_major_formatter(FuncFormatter(_fmt_pct_signed))
        ax.set_title(_sanitize(subtitle), fontsize=12.5, fontweight=600,
                     color=pal["ink"], loc="left", pad=10)
    _save(fig, path)
    return _semantics("fork", title, spec, source, total_n,
                      meta.get("direction", ""), meta.get("window_start", ""),
                      meta.get("window_end", ""))
