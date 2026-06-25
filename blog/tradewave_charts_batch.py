
import math
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Tuple, List, Optional

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick

# ------------------------
# Theme system
# ------------------------

@dataclass(frozen=True)
class Theme:
    name: str
    bg: Tuple[float, float, float]            # figure background (0-1 floats)
    fg: Tuple[float, float, float]            # primary text color
    muted: Tuple[float, float, float]         # secondary text color
    grid: Tuple[float, float, float]          # gridline color
    accent_pos: Tuple[float, float, float]    # positive bars/lines
    accent_neg: Tuple[float, float, float]    # negative bars/lines
    watermark: Tuple[float, float, float]     # watermark color

LIGHT = Theme(
    name="light",
    bg=(1, 1, 1),
    fg=(0.05, 0.05, 0.07),
    muted=(0.33, 0.35, 0.40),
    grid=(0.90, 0.92, 0.95),
    accent_pos=(0.20, 0.70, 0.25),   # green
    accent_neg=(0.85, 0.20, 0.25),   # red
    watermark=(0.85, 0.88, 0.90),
)

DARK = Theme(
    name="dark",
    bg=(0.10, 0.12, 0.14),
    fg=(0.92, 0.94, 0.96),
    muted=(0.70, 0.74, 0.78),
    grid=(0.23, 0.26, 0.30),
    accent_pos=(0.30, 0.85, 0.38),
    accent_neg=(0.95, 0.35, 0.40),
    watermark=(0.18, 0.20, 0.24),
)

def custom_theme(hex_bg: str) -> Theme:
    def hex_to_rgb(hex_color: str) -> Tuple[float, float, float]:
        hex_color = hex_color.strip().lstrip('#')
        return tuple(int(hex_color[i:i+2], 16)/255.0 for i in (0, 2, 4))  # type: ignore
    bg = hex_to_rgb(hex_bg)
    # Choose text color by luminance for contrast
    lum = 0.2126*bg[0] + 0.7152*bg[1] + 0.0722*bg[2]
    fg = (0.05, 0.05, 0.07) if lum > 0.55 else (0.95, 0.96, 0.98)
    muted = tuple((c*0.6 + 0.4) for c in fg) if lum <= 0.55 else tuple((c*0.7) for c in fg)
    grid = tuple((c*0.85 + 0.15) for c in bg)
    return Theme(
        name=f"custom_{hex_bg.replace('#','')}",
        bg=bg, fg=fg, muted=muted, grid=grid,
        accent_pos=(0.30, 0.85, 0.38), accent_neg=(0.95, 0.35, 0.40),
        watermark=tuple((c*0.9) for c in bg),
    )

# ------------------------
# Utilities
# ------------------------

def _apply_theme(theme: Theme, ax: plt.Axes):
    ax.set_facecolor(theme.bg)
    ax.figure.set_facecolor(theme.bg)
    for spine in ax.spines.values():
        spine.set_color(theme.grid)
    ax.tick_params(colors=theme.muted, labelcolor=theme.muted)

def _title(ax: plt.Axes, text: str, theme: Theme, size: int = 24, pad: float = 12):
    ax.set_title(text, fontsize=size, color=theme.fg, pad=pad, loc="center", fontweight="bold")

def _subtitle(fig: plt.Figure, text: str, theme: Theme, size: int = 14, y: float = 0.93):
    fig.text(0.5, y, text, ha="center", va="center", fontsize=size, color=theme.muted)

def _watermark(fig: plt.Figure, text: str, theme: Theme, size: int = 12, x: float = 0.02, y: float = 0.02):
    fig.text(x, y, text, fontsize=size, color=theme.muted, alpha=0.9, ha="left", va="bottom")

def _save(fig: plt.Figure, out: Path, transparent: bool = False, pad_inches: float = 0.25, dpi: int = 180):
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=dpi, bbox_inches="tight", pad_inches=pad_inches, facecolor=fig.get_facecolor(), transparent=transparent)
    plt.close(fig)

# Avoid label clipping
def _new_fig_ax(width: int, height: int) -> Tuple[plt.Figure, plt.Axes]:
    fig = plt.figure(figsize=(width/100, height/100), layout="constrained")
    ax = fig.add_subplot(111)
    return fig, ax

# ------------------------
# Chart types
# ------------------------

def chart_seasonal_bar(
    returns_by_year: pd.Series,
    symbol: str,
    window_label: str,
    out: Path,
    theme: Theme,
    size: Tuple[int, int] = (1280, 720),
    transparent: bool = False
):
    fig, ax = _new_fig_ax(*size)
    _apply_theme(theme, ax)
    years = returns_by_year.index.astype(int).tolist()
    values = returns_by_year.values * 100.0

    bar_colors = [theme.accent_pos if v >= 0 else theme.accent_neg for v in values]
    ax.bar(years, values, color=bar_colors, edgecolor=theme.bg, linewidth=0.5)

    ax.yaxis.set_major_formatter(mtick.PercentFormatter())
    ax.grid(axis="y", color=theme.grid, linestyle="--", linewidth=0.8, alpha=0.7)
    ax.set_axisbelow(True)

    _title(ax, f"{symbol} 10-Year Seasonal Window Returns", theme, size=26)
    _subtitle(fig, window_label, theme, size=16)
    _watermark(fig, "TradeWave.AI", theme, size=14)

    ax.tick_params(axis="x", rotation=45)
    ax.set_xlabel("Year", color=theme.muted)
    ax.set_ylabel("Return", color=theme.muted)

    _save(fig, out, transparent=transparent)

def chart_cumulative_trend(
    daily_pct: pd.Series,
    shaded_start: Optional[pd.Timestamp],
    shaded_end: Optional[pd.Timestamp],
    symbol: str,
    out: Path,
    theme: Theme,
    size: Tuple[int, int] = (1400, 600),
    transparent: bool = False
):
    fig, ax = _new_fig_ax(*size)
    _apply_theme(theme, ax)

    cum = (1 + daily_pct).cumprod() - 1
    x = pd.to_datetime(cum.index)
    ax.plot(x, cum.values*100.0, linewidth=2.0, color=theme.accent_pos)

    ax.yaxis.set_major_formatter(mtick.PercentFormatter())
    ax.grid(color=theme.grid, linestyle=":", linewidth=0.8, alpha=0.8)
    ax.set_axisbelow(True)

    # Shade window
    if shaded_start is not None and shaded_end is not None:
        ax.axvspan(shaded_start, shaded_end, color=theme.accent_pos, alpha=0.10)

    _title(ax, f"{symbol} 10-Year TradeWave Trend", theme, size=22)
    _watermark(fig, "TradeWave.AI", theme, size=12)

    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)

    _save(fig, out, transparent=transparent)

def chart_kpi_panel(
    kpis: Dict[str, str],
    headline: str,
    out: Path,
    theme: Theme,
    size: Tuple[int, int] = (1200, 675),
    transparent: bool = False
):
    fig, ax = _new_fig_ax(*size)
    _apply_theme(theme, ax)
    ax.axis("off")

    _title(ax, headline, theme, size=36, pad=20)
    _subtitle(fig, "Seasonal Edge Snapshot", theme, size=18, y=0.90)

    # Draw KPI cards
    n = len(kpis)
    cols = 3
    rows = math.ceil(n/cols)
    card_w = 0.28
    card_h = 0.18
    start_x = 0.10
    start_y = 0.75

    items = list(kpis.items())
    idx = 0
    for r in range(rows):
        y = start_y - r*(card_h + 0.05)
        for c in range(cols):
            if idx >= n: break
            x = start_x + c*(card_w + 0.04)
            # card
            rect = plt.Rectangle((x, y - card_h), card_w, card_h, facecolor=theme.grid, edgecolor=theme.grid, linewidth=1.0, zorder=0)
            ax.add_patch(rect)
            key, val = items[idx]
            ax.text(x + 0.02, y - 0.05, key, color=theme.muted, fontsize=16, fontweight="bold", ha="left", va="top")
            ax.text(x + 0.02, y - 0.12, val, color=theme.fg, fontsize=28, ha="left", va="top")
            idx += 1

    _watermark(fig, "TradeWave.AI", theme, size=12)
    _save(fig, out, transparent=transparent)

# ------------------------
# Batch runner
# ------------------------

SIZE_PRESETS = {
    "article": (1280, 720),
    "social": (1080, 1350),     # portrait for IG/FB
    "twitter": (1600, 900),
    "wide": (1400, 600),
}

def generate_article_images(
    symbol: str,
    out_dir: Path,
    theme: Theme = DARK,
    size_key: str = "article",
    transparent: bool = False,
    seed: int = 7
) -> List[Path]:
    np.random.seed(seed)
    size = SIZE_PRESETS[size_key]
    outputs: List[Path] = []

    # ---- Example data you should replace with real series ----
    years = list(range(2015, 2025))
    yr_ret = pd.Series(np.random.uniform(-0.05, 0.28, len(years)), index=pd.Index(years, name="Year"))
    idx = pd.date_range("2025-09-16", "2025-11-04", freq="D")
    daily = pd.Series(np.random.normal(0.0008, 0.01, len(idx)), index=idx)

    # 1) Seasonal bar
    p1 = out_dir / f"{symbol}_seasonal_bar_{theme.name}.png"
    chart_seasonal_bar(yr_ret, symbol, "Seasonal Edge | 2025-09-14 to 2026-05-04 (233 Days)", p1, theme, size=size, transparent=transparent)
    outputs.append(p1)

    # 2) Cumulative trend with shaded window
    p2 = out_dir / f"{symbol}_trend_{theme.name}.png"
    chart_cumulative_trend(daily, idx.min(), idx.max(), symbol, p2, theme, size=SIZE_PRESETS["wide"], transparent=transparent)
    outputs.append(p2)

    # 3) KPI panel
    kpis = {
        "Avg Gain": "17.8%",
        "Win Rate": "10 / 10",
        "Sharpe": "1.92",
        "Days in Window": "233",
        "Peak Drawdown": "-6.3%",
        "Next Key Date": "2025-10-15",
    }
    p3 = out_dir / f"{symbol}_kpis_{theme.name}.png"
    chart_kpi_panel(kpis, f"${symbol} Seasonal Snapshot", p3, theme, size=size, transparent=transparent)
    outputs.append(p3)

    # Add more chart calls here until you reach 10–15 assets.
    # For example: distribution histograms, month heatmaps, entry-exit panel, risk table render, etc.

    return outputs

def generate_both_themes(symbol: str, out_root: Path, size_key: str = "article", transparent_light: bool = False, transparent_dark: bool = False) -> List[Path]:
    out_light = out_root / "light"
    out_dark = out_root / "dark"
    paths = []
    paths += generate_article_images(symbol, out_light, theme=LIGHT, size_key=size_key, transparent=transparent_light)
    paths += generate_article_images(symbol, out_dark, theme=DARK, size_key=size_key, transparent=transparent_dark)
    return paths

if __name__ == "__main__":
    # Demo run
    root = Path("./tradewave_article_images")
    paths = generate_both_themes(symbol="FAST", out_root=root, size_key="article", transparent_light=False, transparent_dark=False)
    print("Wrote:", len(paths), "files:")
    for p in paths:
        print(" -", p)
