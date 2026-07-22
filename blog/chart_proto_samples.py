# -*- coding: utf-8 -*-
"""
chart_proto_samples.py — SMN chart redesign PROTOTYPE (sample renders only).

Renders before/after samples from LIVE TradeWave data so the new visual
system can be judged on real charts. Nothing here touches production
paths, WordPress uploads, or the queue. Output: /home/flask/blog/chart_proto_out/

Run (dev):
  set -a; . /etc/tradewave/secrets.env; . /etc/SMN/secrets.env; set +a
  /home/flask/venv/bin/python3 /home/flask/blog/chart_proto_samples.py
"""

import os, sys, glob, json, math, datetime
from datetime import timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.font_manager as fm
from matplotlib.ticker import FuncFormatter

sys.path.insert(0, '/home/flask/blog')
sys.path.insert(0, '/home/flask')
import config

from create_report import (get_keyprovider_token, login_appserver,
                           get_chart_data, get_seasonal_chart_data2,
                           create_barchart, create_seasonals_chart)
from thumbnail_tools import get_chart_historical_prices, inc_date_day

OUT = "/home/flask/blog/chart_proto_out"
os.makedirs(OUT, exist_ok=True)

# ================= visual system =================
for f in glob.glob("/home/flask/blog/fonts/Roboto-*.ttf"):
    try:
        fm.fontManager.addfont(f)
    except Exception:
        pass
plt.rcParams["font.family"] = "Roboto"

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

W, H, DPI = 1280, 720, 100

def _track(s, sp="  "):
    return sp.join(list(s))

def _fmt_pct_signed(v, _=None):
    if abs(v) < 1e-9:
        return "0"
    return f"{v:+.0f}%"

def new_frame(kicker, title, spec, source, *, w=W, h=H,
              ax_rect=(0.065, 0.145, 0.905, 0.60)):
    """Standard SMN chart frame: header block, plot area, source footer."""
    fig = plt.figure(figsize=(w / DPI, h / DPI), dpi=DPI)
    fig.patch.set_facecolor(PAL["bg"])
    left = ax_rect[0]
    fig.text(left, 0.952, _track(kicker.upper()), fontsize=11, fontweight=500,
             color=PAL["muted"], ha="left", va="top")
    fig.text(left, 0.905, title, fontsize=21.5, fontweight=700,
             color=PAL["ink"], ha="left", va="top")
    fig.text(left, 0.842, spec, fontsize=12.5, fontweight=400,
             color=PAL["muted"], ha="left", va="top")
    fig.text(left, 0.040, source, fontsize=10.5, fontweight=400,
             color=PAL["faint"], ha="left", va="center")
    fig.text(left + ax_rect[2], 0.040, _track("TRADEWAVE.AI"), fontsize=10.5,
             fontweight=700, color=PAL["faint"], ha="right", va="center")
    ax = fig.add_axes(ax_rect)
    ax.set_facecolor(PAL["bg"])
    style_axes(ax)
    return fig, ax

def style_axes(ax, *, bottom_spine=True):
    for s in ax.spines.values():
        s.set_visible(False)
    if bottom_spine:
        ax.spines["bottom"].set_visible(True)
        ax.spines["bottom"].set_color(PAL["axis"])
        ax.spines["bottom"].set_linewidth(1.0)
    ax.set_axisbelow(True)
    ax.grid(axis="y", color=PAL["grid"], linewidth=1.0)
    ax.tick_params(colors=PAL["muted"], labelsize=11, length=0)

def save(fig, name):
    path = os.path.join(OUT, name)
    fig.savefig(path, dpi=DPI, facecolor=fig.get_facecolor())
    plt.close(fig)
    print("wrote", path)
    return path

# ================= renderers =================

def chart_record_bars(years, nets, meta, name, *, mfe=None, mae=None,
                      w=W, h=H, show_median=True):
    """The record chart: one bar per completed year, net % over the window.
    Optional excursion needles (MAE→MFE range within the window)."""
    fig, ax = new_frame(meta["kicker"], meta["title"], meta["spec"],
                        meta["source"], w=w, h=h)
    x = list(range(len(years)))
    colors = [PAL["pos"] if v >= 0 else PAL["neg"] for v in nets]
    ax.bar(x, nets, width=0.62, color=colors, zorder=3)
    ax.axhline(0, color=PAL["ink"], linewidth=1.1, zorder=4)
    if mfe is not None and mae is not None:
        ax.vlines(x, mae, mfe, color=PAL["whisk"], linewidth=1.4,
                  alpha=0.65, zorder=5)
    lo = min(min(nets), min(mae) if mae else 0)
    hi = max(max(nets), max(mfe) if mfe else 0)
    pad = (hi - lo) * 0.12
    ax.set_ylim(lo - pad, hi + pad)
    if show_median:
        med = sorted(nets)[len(nets) // 2] if len(nets) % 2 else \
              sum(sorted(nets)[len(nets)//2 - 1:len(nets)//2 + 1]) / 2
        ax.axhline(med, color=PAL["ink"], linewidth=1.1, alpha=0.55,
                   linestyle=(0, (4, 3)), zorder=4)
        ax.annotate(f"median {med:+.1f}%", xy=(x[-1] + 0.55, med),
                    fontsize=11, fontweight=600, color=PAL["ink"],
                    alpha=0.75, va="bottom", ha="right",
                    xytext=(x[-1] + 0.55, med + (hi - lo) * 0.015))
    step = 1 if len(years) <= 20 else 2
    ax.set_xticks(x[::step])
    ax.set_xticklabels([str(y) for y in years][::step], fontsize=10)
    ax.set_xlim(-0.7, len(years) - 0.3 + 0.6)
    ax.yaxis.set_major_formatter(FuncFormatter(_fmt_pct_signed))
    return save(fig, name)

def chart_trend_window(labels, vals, d1, d2, direction, meta, name):
    """Seasonal path chart: average cumulative % through the year with the
    trade window as a shaded band. Replaces the Pillow-patched legacy chart."""
    fig, ax = new_frame(meta["kicker"], meta["title"], meta["spec"],
                        meta["source"])
    x = list(range(len(vals)))
    band = PAL["pos"] if direction == "long" else PAL["neg"]

    def idx_of(datestr):
        for i, l in enumerate(labels):
            if l >= datestr:
                return i
        return len(labels) - 1
    i1, i2 = idx_of(d1), idx_of(d2)
    ax.axvspan(i1, i2, facecolor=band, alpha=0.08, zorder=1)
    for i in (i1, i2):
        ax.axvline(i, color=band, alpha=0.45, linewidth=1.0, zorder=2)
    ax.plot(x, vals, color=PAL["ink"], linewidth=2.4, zorder=3,
            solid_capstyle="round")
    for i in (i1, i2):
        ax.plot([i], [vals[i]], "o", ms=5.5, color=band, zorder=5)
    # band label centered above the window
    ytop = max(vals) + (max(vals) - min(vals)) * 0.06
    lbl = (f"{_fmt_mmm_d(d1)} – {_fmt_mmm_d(d2)}")
    ax.text((i1 + i2) / 2, ytop, lbl, ha="center", va="bottom",
            fontsize=11.5, fontweight=600, color=band)
    pad = (max(vals) - min(vals)) * 0.16
    ax.set_ylim(min(vals) - pad * 0.5, max(vals) + pad)
    # month ticks
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
    ax.set_xlim(0, len(x) - 1)
    ax.yaxis.set_major_formatter(FuncFormatter(_fmt_pct_signed))
    return save(fig, name)

def chart_price_projection(dates, prices, pdates, pprices, meta, name):
    """Recent price with the median seasonal path ahead (dashed amber)."""
    fig, ax = new_frame(meta["kicker"], meta["title"], meta["spec"],
                        meta["source"], ax_rect=(0.065, 0.145, 0.865, 0.60))
    dn = mdates.date2num(dates)
    ax.plot(dn, prices, color=PAL["accent"], linewidth=2.2, zorder=3,
            solid_capstyle="round")
    ax.fill_between(dn, prices, min(prices), color=PAL["accent"],
                    alpha=0.05, zorder=1)
    allp = list(prices)
    if pdates:
        pn = mdates.date2num(pdates)
        ax.plot(pn, pprices, color=PAL["amber"], linewidth=2.0,
                linestyle=(0, (3, 2)), zorder=4)
        allp += list(pprices)
        chg = (pprices[-1] / prices[-1] - 1) * 100
        # anchor the label to the HIGHEST point of the back half of the
        # projection so it clears the line's wiggles instead of sitting on them
        tail_hi = max(pprices[len(pprices) // 2:])
        ax.annotate(f"seasonal path {chg:+.1f}%",
                    xy=(pn[-1], tail_hi), xytext=(0, 16),
                    textcoords="offset points", fontsize=11, fontweight=600,
                    color=PAL["amber"], va="bottom", ha="right")
    ax.plot([dn[-1]], [prices[-1]], "o", ms=5, color=PAL["accent"], zorder=5)
    ax.annotate(f"{prices[-1]:,.2f}", xy=(dn[-1], prices[-1]), xytext=(-6, -16),
                textcoords="offset points", fontsize=11, fontweight=600,
                color=PAL["accent"], va="center", ha="right")
    loc = mdates.AutoDateLocator(minticks=4, maxticks=7)
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    lo, hi = min(allp), max(allp)
    pad = (hi - lo) * 0.08
    ax.set_ylim(lo - pad, hi + pad * (1.9 if pdates else 1.0))
    xmax = mdates.date2num(pdates[-1]) if pdates else dn[-1]
    ax.set_xlim(dn[0], xmax)
    return save(fig, name)

def chart_fork(panels, meta, name):
    """FORK: two record charts side by side, opposite signs, shared frame."""
    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI)
    fig.patch.set_facecolor(PAL["bg"])
    left = 0.065
    fig.text(left, 0.952, _track(meta["kicker"].upper()), fontsize=11,
             fontweight=500, color=PAL["muted"], ha="left", va="top")
    fig.text(left, 0.905, meta["title"], fontsize=21.5, fontweight=700,
             color=PAL["ink"], ha="left", va="top")
    fig.text(left, 0.842, meta["spec"], fontsize=12.5, color=PAL["muted"],
             ha="left", va="top")
    fig.text(left, 0.040, meta["source"], fontsize=10.5, color=PAL["faint"],
             ha="left", va="center")
    fig.text(0.97, 0.040, _track("TRADEWAVE.AI"), fontsize=10.5,
             fontweight=700, color=PAL["faint"], ha="right", va="center")
    rects = [(0.065, 0.145, 0.42, 0.545), (0.55, 0.145, 0.42, 0.545)]
    for (years, nets, subtitle), rect in zip(panels, rects):
        ax = fig.add_axes(rect)
        ax.set_facecolor(PAL["bg"])
        style_axes(ax)
        x = list(range(len(years)))
        colors = [PAL["pos"] if v >= 0 else PAL["neg"] for v in nets]
        ax.bar(x, nets, width=0.62, color=colors, zorder=3)
        ax.axhline(0, color=PAL["ink"], linewidth=1.1, zorder=4)
        step = 1 if len(years) <= 12 else 2
        ax.set_xticks(x[::step])
        ax.set_xticklabels([str(y) for y in years][::step], fontsize=9.5)
        ax.yaxis.set_major_formatter(FuncFormatter(_fmt_pct_signed))
        ax.set_title(subtitle, fontsize=12.5, fontweight=600,
                     color=PAL["ink"], loc="left", pad=10)
    return save(fig, name)

def _fmt_mmm_d(datestr):
    d = datetime.datetime.strptime(datestr, "%Y-%m-%d")
    return d.strftime("%b %d").replace(" 0", " ")

# ================= data =================

def fetch_cell(token, rid, symbol, date1, days, years):
    cdata = get_chart_data(rid, date1, symbol, str(days - 1), years, True, token)
    rows = [r for r in cdata["ChartData4"] if r["pct"] != "0,0,0"]
    yrs = [int(r["year"]) for r in rows]
    nets = [float(r["pct"].split(",")[0]) for r in rows]
    mfe = [float(r["pct"].split(",")[1]) for r in rows]
    mae = [float(r["pct"].split(",")[2]) for r in rows]
    wins = sum(1 for v in nets if v > 0)
    return {"cdata": cdata, "years": yrs, "nets": nets, "mfe": mfe,
            "mae": mae, "wins": wins, "n": len(nets),
            "date2": inc_date_day(date1, days - 1)}

def main():
    token = login_appserver(get_keyprovider_token())
    anchor = datetime.date.today().isoformat()
    semantics = []

    # ---------- NVDA 60d x 20y (the CLOCKWORK demo) ----------
    rid, sym, days, years = "2", "NVDA", 60, "20"
    c = fetch_cell(token, rid, sym, anchor, days, years)
    d1, d2 = anchor, c["date2"]
    win_lbl = f"{_fmt_mmm_d(d1)} – {_fmt_mmm_d(d2)}"
    direction = "long" if c["wins"] >= c["n"] - c["wins"] else "short"
    word = "higher" if direction == "long" else "lower"
    k = c["wins"] if direction == "long" else c["n"] - c["wins"]

    src = (f"Source: TradeWave seasonal database · n={c['n']} completed years "
           f"({c['years'][0]}–{c['years'][-1]}) · long convention: positive = price rose")

    meta = {
        "kicker": f"{sym} · Nvidia · {days}-day seasonal window",
        "title": f"{sym} has closed {word} in {k} of the past {c['n']} years ({win_lbl})",
        "spec": (f"Net % change from the {_fmt_mmm_d(d1)} close to the "
                 f"{_fmt_mmm_d(d2)} close, each year — one bar per year"),
        "source": src,
    }
    chart_record_bars(c["years"], c["nets"], meta, "after_record_bars.png")
    semantics.append(dict(meta, variant="record_bars"))

    meta2 = dict(meta)
    meta2["spec"] = (f"Bars: net % change over the window. Needles: the full "
                     f"intra-window range each year (worst drawdown to best gain)")
    chart_record_bars(c["years"], c["nets"], meta2, "after_record_bars_excursion.png",
                      mfe=c["mfe"], mae=c["mae"])
    semantics.append(dict(meta2, variant="record_bars_excursion"))

    # email-size render, same grammar
    chart_record_bars(c["years"], c["nets"], meta, "after_email_bars.png",
                      w=1200, h=628)

    # ---------- trend/window chart (kills the Pillow patch) ----------
    tstart = inc_date_day(d1, -14)
    tl, tv = get_seasonal_chart_data2(rid, sym, years, tstart, d1, token)
    tv = [v - tv[0] for v in tv]   # rebase: 0 at chart start, no inherited Jan-1 index
    metaT = {
        "kicker": f"{sym} · Nvidia · 20-year seasonal path",
        "title": f"Where {win_lbl} sits in {sym}'s average year",
        "spec": (f"{sym}'s average path over the past {c['n']} years, "
                 f"rebased to 0 at {_fmt_mmm_d(tstart)} · shaded: the {days}-day window"),
        "source": (f"Source: TradeWave seasonal database · {c['n']}-year average "
                   f"({c['years'][0]}–{c['years'][-1]}) · not a forecast"),
    }
    chart_trend_window(tl, tv, d1, d2, direction, metaT, "after_trend_window.png")
    semantics.append(dict(metaT, variant="trend_window"))

    # ---------- price + projection ----------
    from dateutil.parser import parse as dtparse
    from article_images import _build_projection
    pp = get_chart_historical_prices(rid, sym, inc_date_day(d1, -365), d1, token)
    dts = [dtparse(p[0]).date() for p in pp]
    prc = [float(p[1]) for p in pp]
    pd_, ppr = _build_projection(dts, prc, tl, tv, 60)
    mo1 = c["cdata"]["stats"].get("1M Return", "")
    metaP = {
        "kicker": f"{sym} · Nvidia · price and seasonal path",
        "title": f"{sym} enters the window at {prc[-1]:,.2f}"
                 + (f", {mo1} over the past month" if mo1 else ""),
        "spec": (f"Daily closes, past 12 months · dashed amber: the median "
                 f"{c['n']}-year seasonal path over the next 60 days, "
                 f"anchored to the last close — indicative, not a forecast"),
        "source": f"Source: TradeWave price history + seasonal database · n={c['n']} years",
    }
    chart_price_projection(dts, prc, pd_, ppr, metaP, "after_price_projection.png")
    semantics.append(dict(metaP, variant="price_projection"))

    # ---------- FORK scan: 30d vs 90d, 10y & 20y ----------
    fork = None
    cells_cache = {}
    def cell(fsym, dd, yy):
        key = (fsym, dd, yy)
        if key not in cells_cache:
            cells_cache[key] = fetch_cell(token, "2", fsym, anchor, dd, yy)
        return cells_cache[key]
    for fsym in ("AAPL", "MSFT", "XOM", "JPM", "WMT", "CAT", "DIS", "NKE",
                 "INTC", "BA", "PG", "KO"):
        try:
            combos = [(dd, yy) for dd in (30, 60, 90) for yy in ("10", "20")]
            best = None
            for (da_, ya_) in combos:
                for (db_, yb_) in combos:
                    if (da_, ya_) >= (db_, yb_):
                        continue
                    a = cell(fsym, da_, ya_)
                    b = cell(fsym, db_, yb_)
                    wa, na = a["wins"], a["n"]
                    wb, nb = b["wins"], b["n"]
                    one_a = max(wa, na - wa) / na
                    one_b = max(wb, nb - wb) / nb
                    if one_a >= 0.65 and one_b >= 0.62 and \
                       ((wa / na >= 0.5) != (wb / nb >= 0.5)):
                        score = one_a + one_b
                        if best is None or score > best[0]:
                            best = (score, (da_, ya_, a), (db_, yb_, b))
            if best:
                fork = (fsym, best[1], best[2])
                break
        except Exception as e:
            print("fork scan skip", fsym, e)
            continue
    if fork:
        fsym, (da_, ya_, a), (db_, yb_, b) = fork
        wa, na, wb, nb = a["wins"], a["n"], b["wins"], b["n"]
        side_a = "higher" if wa / na >= 0.5 else "lower"
        side_b = "higher" if wb / nb >= 0.5 else "lower"
        ka = wa if side_a == "higher" else na - wa
        kb = wb if side_b == "higher" else nb - wb
        metaF = {
            "kicker": f"{fsym} · two horizons, two records",
            "title": (f"{fsym}'s next {da_} days lean {side_a} — "
                      f"the next {db_} lean {side_b}"),
            "spec": ("Net % change over each window, one bar per year · "
                     "panels differ in horizon and lookback"),
            "source": (f"Source: TradeWave seasonal database · left n={na} years, "
                       f"right n={nb} years · long convention"),
        }
        chart_fork([
            (a["years"], a["nets"],
             f"Next {da_} days ({_fmt_mmm_d(anchor)} – {_fmt_mmm_d(a['date2'])}): "
             f"{side_a} in {ka} of {na}"),
            (b["years"], b["nets"],
             f"Next {db_} days ({_fmt_mmm_d(anchor)} – {_fmt_mmm_d(b['date2'])}): "
             f"{side_b} in {kb} of {nb}"),
        ], metaF, "after_fork.png")
        semantics.append(dict(metaF, variant="fork"))
    else:
        print("no FORK pair found in scan — skipping fork sample")

    # ---------- BEFORE: legacy create_report charts ----------
    create_barchart(c["cdata"]["ChartData4"], years,
                    os.path.join(OUT, "before_report_bars.png"),
                    0.005, 0.01, "TradeWave.AI", 0.99, 0.01,
                    f"{sym} TradeWave Gain Loss Barchart - {d1} to {d2}",
                    (14, 6), 14)
    create_seasonals_chart(c["years"], tl, tv, d1, d2, direction.capitalize(),
                           os.path.join(OUT, "before_report_seasonal.png"),
                           0.005, 0.01, "TradeWave.AI", 1, 0.01,
                           f"{sym} {years} Year TradeWave Trend Chart",
                           0.99, 0.97, f"{d1} to {d2}", (14, 6), 14)
    print("wrote legacy report charts")

    # ---------- BEFORE: current article_images style ----------
    import article_images as AI
    pal = AI._make_themes()["light"]
    fig = AI._init_fig(W, H, pal); ax = AI._axes(fig, pal)
    AI._bars(ax, c["years"], c["nets"], W, H, pal)
    AI._footer_labels(ax, pal, "Nvidia")
    AI._caption(fig, pal, f"{sym} Seasonal Pattern | {d1} ➝ {d2} ({days} Days)")
    AI._save(fig, os.path.join(OUT, "before_article_bars.png"), W, H)

    fig = AI._init_fig(W, H, pal); ax = AI._axes(fig, pal)
    i1 = next(i for i, l in enumerate(tl) if l >= d1)
    i2 = next(i for i, l in enumerate(tl) if l >= d2)
    AI._trend(ax, tl, tv, (i1, i2), direction, W, H, pal)
    AI._footer_labels(ax, pal, "Nvidia")
    AI._save(fig, os.path.join(OUT, "before_article_trend.png"), W, H)
    print("wrote current-article-style charts")

    with open(os.path.join(OUT, "semantics.json"), "w") as fh:
        json.dump(semantics, fh, indent=2)
    print("done")

if __name__ == "__main__":
    main()
