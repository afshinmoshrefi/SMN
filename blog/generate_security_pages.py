"""
generate_security_pages.py
==========================
Generates professional security detail pages for SeasonalMarketNews.com.

Each market bar ticker gets a dedicated page with:
  - Current price/quote data
  - Seasonal price projection charts (30d/60d/90d × consecutive & PE-cycle years)
  - EODHD news with sentiment
  - Index components (for indices with fundamentals)
  - Probability stats and educational content

Output: /var/www/smn/markets/{slug}.html
Charts: /var/www/smn/markets/charts/{symbol}_{date}_{years}_{proj}.jpg

Run once daily (early morning cron):
  30 5 * * 1-5 cd /home/flask/blog && python generate_security_pages.py
"""

import os, sys, re, json, math
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, '/home/flask')
sys.path.insert(0, '/home/flask/blog')
import config
import requests
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import MaxNLocator
from dateutil.parser import parse as dtparse

from get_price_eod import get_quote_details
from create_report import (get_seasonal_chart_data2,
                           get_keyprovider_token, login_appserver)
from thumbnail_tools import get_chart_historical_prices, inc_date_day
from article_images import (_build_projection, _price_with_projection,
                            _human_years_label, _make_themes, _fmt_axes,
                            _footer_labels, DEF_DPI)

# =============================================================================
# CONFIGURATION
# =============================================================================

NEWS_ROOT = Path(config.news_root_folder)
MARKETS_DIR = NEWS_ROOT / "markets"
CHARTS_DIR = MARKETS_DIR / "charts"
SITE_URL = config.news_website_url.rstrip("/")

# Chart dimensions (facebook size = 1280x720)
CHART_W, CHART_H = 1280, 720
CHART_THEME = "light"
CHART_LIGHT_BG = "#f8f9fa"

PRICE_LOOKBACK_DAYS = 365
AI_CACHE_FILE = MARKETS_DIR / "_ai_cache.json"

PE_LABELS = {0: "Pre-Election", 1: "Election", 2: "Midterm Election", 3: "Post-Election"}

def _pe_cycle(year=None):
    """Return (pe_position, pe_label) for the given year."""
    if year is None:
        year = datetime.now().year
    pos = year % 4
    return pos, PE_LABELS[pos]


# =============================================================================
# SECURITY DEFINITIONS — flexible list, add/remove as needed
# =============================================================================

SECURITY_PAGES = [
    {
        "label": "S&P 500",
        "symbol": "GSPC",           # EODHD symbol
        "appserver_symbol": "SPX",   # appserver seasonal symbol
        "exchange": "INDX",
        "resource_id": "5",
        "slug": "sp500",
        "description": "The S&P 500 index tracks 500 of the largest U.S. publicly traded companies, weighted by market capitalization. It is widely regarded as the best single gauge of the U.S. equity market.",
        "has_fundamentals": True,
    },
    {
        "label": "Dow Jones Industrial Average",
        "symbol": "DJI",
        "appserver_symbol": "DJI",
        "exchange": "INDX",
        "resource_id": "5",
        "slug": "dow",
        "description": "The Dow Jones Industrial Average tracks 30 prominent blue-chip companies listed on U.S. stock exchanges. It is one of the oldest and most-watched indices in the world.",
        "has_fundamentals": True,
    },
    {
        "label": "NASDAQ Composite",
        "symbol": "IXIC",
        "appserver_symbol": "IXIC",
        "exchange": "INDX",
        "resource_id": "5",
        "slug": "nasdaq",
        "description": "The NASDAQ Composite index includes over 3,000 stocks listed on the Nasdaq exchange, heavily weighted toward technology and growth companies.",
        "has_fundamentals": True,
    },
    {
        "label": "CBOE Volatility Index",
        "symbol": "VIX",
        "appserver_symbol": "VIX",
        "exchange": "INDX",
        "resource_id": "5",
        "slug": "vix",
        "description": "The VIX measures the market's expectation of 30-day forward-looking volatility, derived from S&P 500 index options. Often called the 'fear gauge,' it rises during periods of market uncertainty.",
        "has_fundamentals": False,
    },
    {
        "label": "Crude Oil (WTI)",
        "symbol": "CL",
        "appserver_symbol": "CL",
        "exchange": "COMM",
        "resource_id": "7",
        "slug": "crude-oil",
        "description": "West Texas Intermediate (WTI) crude oil is the primary benchmark for U.S. oil pricing. It is one of the most actively traded commodities in the world.",
        "has_fundamentals": False,
    },
    {
        "label": "Natural Gas",
        "symbol": "NG",
        "appserver_symbol": "NG",
        "exchange": "COMM",
        "resource_id": "7",
        "slug": "natural-gas",
        "description": "Henry Hub Natural Gas futures represent the benchmark price for natural gas in North America, heavily influenced by weather patterns and seasonal demand.",
        "has_fundamentals": False,
    },
    {
        "label": "Gold",
        "symbol": "GC",
        "appserver_symbol": "GC",
        "exchange": "COMM",
        "resource_id": "7",
        "slug": "gold",
        "description": "COMEX Gold futures are the world's most liquid gold contract. Gold serves as a store of value, inflation hedge, and safe-haven asset during periods of geopolitical uncertainty.",
        "has_fundamentals": False,
    },
]

# Build slug lookup for market bar links
TICKER_TO_SLUG = {s["symbol"]: s["slug"] for s in SECURITY_PAGES}


# =============================================================================
# EODHD DATA FETCHERS
# =============================================================================

def fetch_eodhd_news(symbol, exchange, limit=10):
    """Fetch news articles from EODHD with sentiment."""
    try:
        url = "https://eodhd.com/api/news"
        params = {
            "s": f"{symbol}.{exchange}",
            "api_token": config.EOD_token,
            "limit": limit,
            "fmt": "json",
        }
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [NEWS] Error fetching news for {symbol}.{exchange}: {e}")
        return []


def fetch_actual_pe_year_count(resource_id, symbol, today_str, pe_pos, appserver_token):
    """Get actual PE year count for a specific symbol by querying chart data."""
    from create_report import get_chart_data
    try:
        # Request all PE years with a 30-day window
        cdata = get_chart_data(resource_id, today_str, symbol, "29",
                               f"pe{pe_pos}", True, appserver_token)
        years = cdata.get("ChartData4", [])
        # Exclude current (incomplete) year
        current_year = datetime.now().year
        count = sum(1 for y in years if int(y.get("year", 0)) < current_year)
        return count
    except Exception as e:
        print(f"  [YEARS] Error fetching PE year count for {symbol}: {e}")
        return 0


def fetch_projection_stats(resource_id, symbol, today_str, days_out, years_param, appserver_token):
    """Get win rate, average return, and per-year stats for a projection window."""
    from create_report import get_chart_data
    try:
        cdata = get_chart_data(resource_id, today_str, symbol, str(days_out),
                               years_param, True, appserver_token)
        years = cdata.get("ChartData4", [])
        current_year = datetime.now().year
        returns = []
        for y in years:
            if int(y.get("year", 0)) >= current_year:
                continue
            pct_str = y.get("pct", "0,0,0")
            try:
                p = float(pct_str.split(",")[0])
                returns.append(p)
            except (ValueError, IndexError):
                continue
        if not returns:
            return None
        num_winners = sum(1 for r in returns if r >= 0)
        num_total = len(returns)
        win_rate = (num_winners / num_total) * 100
        avg_return = sum(returns) / num_total
        winners = [r for r in returns if r >= 0]
        losers = [r for r in returns if r < 0]
        avg_win = sum(winners) / len(winners) if winners else 0
        avg_loss = sum(losers) / len(losers) if losers else 0
        sorted_r = sorted(returns)
        mid = len(sorted_r) // 2
        median_return = sorted_r[mid] if len(sorted_r) % 2 else (sorted_r[mid - 1] + sorted_r[mid]) / 2
        return {
            "win_rate": round(win_rate, 1),
            "avg_return": round(avg_return, 2),
            "median_return": round(median_return, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "best": round(max(returns), 2),
            "worst": round(min(returns), 2),
            "num_winners": num_winners,
            "num_total": num_total,
        }
    except Exception as e:
        print(f"  [STATS] Error fetching stats for {symbol}/{years_param}/{days_out}d: {e}")
        return None


def fetch_related_articles(appserver_symbol, exchange, limit=8):
    """Load recent SMN articles related to this security or its market family."""
    import redis
    r = redis.Redis(host=config.webserver_ip, port=6379, db=3)
    articles = []
    for key in r.scan_iter(match="*_neutral_0"):
        raw = r.get(key)
        if not raw:
            continue
        try:
            entry = json.loads(raw).get("entry", {})
        except Exception:
            continue
        if not entry.get("url"):
            continue
        articles.append(entry)

    articles.sort(key=lambda p: p.get("published_date", ""), reverse=True)

    # Prioritize: exact symbol match first, then same market family, then recent others
    exact = [a for a in articles if a.get("symbol", "").upper() == appserver_symbol.upper()]
    family = [a for a in articles if a.get("market_family", "").upper() == exchange.upper()
              and a.get("symbol", "").upper() != appserver_symbol.upper()]
    others = [a for a in articles if a not in exact and a not in family]

    result = []
    for pool in [exact, family, others]:
        for a in pool:
            if a not in result:
                result.append(a)
            if len(result) >= limit:
                break
        if len(result) >= limit:
            break

    return result


# =============================================================================
# CHART GENERATION
# =============================================================================

def _generate_projection_chart(dates, prices, trend_labels, trend_values,
                               proj_days, years_param, symbol, company,
                               today_str, pal, out_dir, fname_prefix=""):
    """Generate a single projection chart image. Returns relative URL path."""
    proj_dates, proj_prices = _build_projection(
        dates, prices, trend_labels, trend_values, proj_days
    )

    W, H = CHART_W, CHART_H
    # No caption — use tighter layout
    P_BOTTOM, P_TOP = 0.12, 0.94
    AX_LEFT, AX_RIGHT = 0.075, 0.040

    fig = plt.figure(figsize=(W / DEF_DPI, H / DEF_DPI), dpi=DEF_DPI)
    fig.patch.set_facecolor(pal["bg"])
    rect = [AX_LEFT, P_BOTTOM, 1.0 - AX_LEFT - AX_RIGHT, P_TOP - P_BOTTOM]
    ax = fig.add_axes(rect)
    ax.set_facecolor(pal["bg"])

    years_label = _human_years_label(years_param)
    _price_with_projection(ax, dates, prices, proj_dates, proj_prices,
                           proj_days, years_label, W, H, pal)
    _footer_labels(ax, pal, company)

    fname = f"{fname_prefix}{symbol}_{today_str}_{years_param}_{proj_days}.jpg"
    fpath = os.path.join(out_dir, fname)
    os.makedirs(out_dir, exist_ok=True)
    fig.set_size_inches(W / DEF_DPI, H / DEF_DPI, forward=True)
    fig.savefig(fpath, dpi=DEF_DPI, facecolor=fig.get_facecolor(),
                edgecolor="none", bbox_inches=None, pad_inches=0, transparent=False)
    plt.close(fig)

    return f"/markets/charts/{fname}"


def generate_all_charts(sec, today_str, appserver_token):
    """Generate all 6 projection charts plus stats. Returns (chart_urls, max_pe, targets, stats)."""
    symbol = sec["appserver_symbol"]
    resource_id = sec["resource_id"]
    pe_pos, _ = _pe_cycle()

    # Query actual PE year count for this specific symbol
    max_pe = fetch_actual_pe_year_count(resource_id, symbol, today_str, pe_pos, appserver_token)
    years_consecutive = "10"
    years_pe = f"pe{pe_pos}-{max_pe}" if max_pe > 0 else None
    print(f"  [YEARS] {symbol}: consecutive=10, PE{pe_pos}={max_pe}")

    THEMES = _make_themes(light_bg=CHART_LIGHT_BG)
    pal = THEMES[CHART_THEME]
    out_dir = str(CHARTS_DIR)
    company = sec["label"]

    # Fetch price data (1 year lookback)
    d0 = inc_date_day(today_str, -PRICE_LOOKBACK_DAYS)
    price_points = get_chart_historical_prices(
        resource_id, symbol, d0, today_str, appserver_token
    )
    if not price_points:
        print(f"  [CHART] No price data for {symbol}")
        return {}, 0, {}, {}, {}

    dates = [dtparse(pt[0]).date() for pt in price_points]
    prices = [float(pt[1]) for pt in price_points]

    # Fetch volume history from EODHD (for avg volume calculation)
    volumes = []
    try:
        eod_sym = f"{sec['symbol']}.{sec['exchange']}"
        eod_url = f"https://eodhd.com/api/eod/{eod_sym}"
        eod_resp = requests.get(eod_url, params={
            "api_token": config.EOD_token, "fmt": "json",
            "from": d0, "to": today_str, "period": "d",
        }, timeout=15)
        if eod_resp.ok:
            for row in eod_resp.json():
                v = row.get("volume")
                if v is not None:
                    volumes.append(float(v))
            print(f"  [VOL] {symbol}: {len(volumes)} days of volume data")
    except Exception as e:
        print(f"  [VOL] Error fetching volume for {symbol}: {e}")

    chart_urls = {}
    dark_chart_urls = {}
    projection_targets = {}
    projection_stats = {}

    # Dark palette for TradeWave version
    dark_pal = THEMES["dark"]

    combos = [(years_consecutive, "consecutive")]
    if years_pe:
        combos.append((years_pe, "pe"))

    for years_param, basis_key in combos:
        # Fetch seasonal data for this years param
        try:
            trend_start = inc_date_day(today_str, -14)
            trend_labels, trend_values = get_seasonal_chart_data2(
                resource_id, symbol, years_param,
                trend_start, today_str, appserver_token
            )
        except Exception as e:
            print(f"  [CHART] Error fetching seasonal data for {symbol}/{years_param}: {e}")
            continue

        if not trend_labels or not trend_values:
            print(f"  [CHART] No seasonal data for {symbol}/{years_param}")
            continue

        for proj_days in (30, 60, 90):
            key = f"{basis_key}_{proj_days}"
            # Light chart (SMN)
            try:
                rel_url = _generate_projection_chart(
                    dates, prices, trend_labels, trend_values,
                    proj_days, years_param, sec["symbol"], company,
                    today_str, pal, out_dir
                )
                chart_urls[key] = rel_url
            except Exception as e:
                print(f"  [CHART] Error generating {key} for {symbol}: {e}")

            # Dark chart (TradeWave)
            try:
                dark_url = _generate_projection_chart(
                    dates, prices, trend_labels, trend_values,
                    proj_days, years_param, sec["symbol"], company,
                    today_str, dark_pal, out_dir,
                    fname_prefix="dark_"
                )
                dark_chart_urls[key] = dark_url
            except Exception as e:
                print(f"  [CHART] Error generating dark {key} for {symbol}: {e}")

            # Compute projection target price
            try:
                proj_d, proj_p = _build_projection(dates, prices, trend_labels, trend_values, proj_days)
                if proj_p and len(proj_p) > 1:
                    target_price = proj_p[-1]
                    return_pct = ((target_price - prices[-1]) / prices[-1]) * 100
                    projection_targets[key] = {
                        "target": round(target_price, 2),
                        "return_pct": round(return_pct, 2),
                        "current": round(prices[-1], 2),
                    }
            except Exception as e:
                print(f"  [TARGET] Error computing target for {key}/{symbol}: {e}")

            # Fetch win rate stats from ChartData4
            stats = fetch_projection_stats(
                resource_id, symbol, today_str, proj_days, years_param, appserver_token
            )
            if stats:
                projection_stats[key] = stats
                print(f"  [STATS] {key}: win={stats['win_rate']}%, avg={stats['avg_return']}%, n={stats['num_total']}")

    # Return price history for 52wk calculations
    price_history = {"dates": dates, "prices": prices, "volumes": volumes} if price_points else {}
    return chart_urls, max_pe, projection_targets, projection_stats, price_history, dark_chart_urls


# =============================================================================
# AI ANALYSIS
# =============================================================================

def generate_ai_analysis(sec, quote, projection_targets, projection_stats, pe_label, pe_pos):
    """Generate unique AI-written analysis for this security using Claude."""
    from AI_tools import send_claude_prompt, CLAUDE_SONNET_46

    name = sec["label"]
    sec_type = "index" if sec["exchange"] == "INDX" else "commodity"
    close = quote.get("close", 0) if quote else 0
    change_p = quote.get("change_p", 0) if quote else 0

    # Gather stats for all 6 combos
    # Indices are levels, not dollar amounts. Only commodities use $
    is_index = sec["exchange"] == "INDX"
    tgt_prefix = "" if is_index else "$"

    stats_text = ""
    for basis_key, basis_label in [("consecutive", "Last 10 Consecutive Years"), ("pe", f"{pe_label} Years")]:
        for days in (30, 60, 90):
            key = f"{basis_key}_{days}"
            t = projection_targets.get(key, {})
            s = projection_stats.get(key, {})
            if t and s:
                stats_text += (
                    f"  {basis_label}, {days}-day: target {tgt_prefix}{t['target']}, "
                    f"return {t['return_pct']:+.2f}%, win rate {s['win_rate']}%, "
                    f"avg return {s['avg_return']:+.2f}%, median {s['median_return']:+.2f}%, "
                    f"best {s['best']:+.2f}%, worst {s['worst']:+.2f}%, "
                    f"{s['num_winners']}/{s['num_total']} positive\n"
                )

    # VIX-specific framing
    vix_instructions = ""
    if sec["symbol"] == "VIX":
        vix_instructions = """
VIX-SPECIFIC FRAMING (CRITICAL):
- VIX is NOT a normal security. It measures market fear/uncertainty. A rising VIX means markets are stressed, a falling VIX means stability.
- NEVER use "win rate" or "return" as if VIX rising is positive. Instead say "VIX rose in X of Y periods" or "VIX increased."
- When VIX is projected higher, frame it as: increasing market anxiety, potential equity weakness, elevated uncertainty.
- When VIX is projected lower, frame it as: favorable conditions for equities, declining fear, market stability.
- Always tie VIX movement back to what it means for the stock market and investors.
- Use language like "diagnostic," "signal," "indicates" rather than "targets" or "returns."
- NEVER use a dollar sign ($) for VIX values. VIX is not priced in dollars, it is a calculated index level. Write "25.72" not "$25.72". Write "toward 16.46" not "toward $16.46".
"""

    price_prefix = tgt_prefix

    prompt = f"""Write a compelling 2-paragraph market analysis for {name} for a seasonal analysis page.

SECURITY: {name} ({sec_type})
CURRENT PRICE: {price_prefix}{close}
RECENT CHANGE: {change_p:+.2f}%
YEAR: 2026 is a {pe_label.lower()} year (position {pe_pos} of 4 in the presidential election cycle)

SEASONAL PROJECTION DATA:
{stats_text}
{vix_instructions}
PARAGRAPH 1: Lead with the most striking data point. What does seasonal history say about {name} right now? Reference specific win rates, projected returns, and time horizons. If the consecutive and PE-cycle projections diverge, highlight that tension. If they agree, emphasize the confluence.

PARAGRAPH 2: Put the seasonal data in context for this specific {sec_type}. What makes {name}'s seasonal behavior distinctive? How does the {pe_label.lower()} year pattern compare to the broader trend? Give the reader a clear takeaway — what should they be watching for.

RULES:
- Be specific with numbers, reference actual win rates and returns
- No disclaimers, caveats, or "past performance" language (that's elsewhere on the page)
- No emojis, no markdown formatting, no bullet points, no em dashes
- Professional but accessible — write for a retail investor who knows basics
- Do not exceed 120 words total
- Write in present tense
- Do not start with the security name — vary the opening"""

    try:
        text = send_claude_prompt(
            prompt=prompt,
            model=CLAUDE_SONNET_46,
            system="You are a senior seasonal pattern analyst at a financial research firm. Write crisp, data-driven market analysis that makes readers smarter.",
            max_tokens=512,
            temperature=0.5,
        )
        return text.strip().replace("—", ",").replace("–", ",") if text else None
    except Exception as e:
        print(f"  [AI] Error generating analysis for {name}: {e}")
        return None


def generate_usage_guide(sec, projection_targets, projection_stats, pe_label):
    """Generate a per-security 'How to Use This Data' guide using Claude."""
    from AI_tools import send_claude_prompt, CLAUDE_SONNET_46

    name = sec["label"]
    sec_type = "index" if sec["exchange"] == "INDX" else "commodity"

    # Gather the 60-day stats for both bases to inform the guide
    cons_60 = projection_stats.get("consecutive_60", {})
    pe_60 = projection_stats.get("pe_60", {})
    cons_t = projection_targets.get("consecutive_60", {})
    pe_t = projection_targets.get("pe_60", {})

    # Determine whether bases agree or diverge
    cons_ret = cons_t.get("return_pct", 0)
    pe_ret = pe_t.get("return_pct", 0)
    cons_wr = cons_60.get("win_rate", 0)
    pe_wr = pe_60.get("win_rate", 0)
    bases_agree = (cons_ret > 0 and pe_ret > 0) or (cons_ret < 0 and pe_ret < 0)
    high_wr = max(cons_wr, pe_wr)
    low_wr = min(cons_wr, pe_wr)

    # VIX-specific context
    vix_guide_note = ""
    if sec["symbol"] == "VIX":
        vix_guide_note = """
VIX-SPECIFIC CONTEXT (CRITICAL):
- VIX measures market fear, not a tradeable asset in the traditional sense. A rising VIX signals increasing uncertainty and typically corresponds with falling equity markets.
- When explaining win rates, say "VIX increased in X% of these periods" not "won." A VIX increase is not a positive outcome for most investors.
- Frame VIX projections as diagnostic signals for equity market conditions: higher VIX = expect turbulence, lower VIX = expect stability.
"""

    prompt = f"""Write a concise "How to Use This Data" guide for someone viewing seasonal projection data for {name}.

CONTEXT:
- Asset type: {sec_type}
- 60-day consecutive win rate: {cons_wr}%, avg return: {cons_60.get('avg_return', 0):+.1f}%, median: {cons_60.get('median_return', 0):+.1f}%
- 60-day {pe_label.lower()} year win rate: {pe_wr}%, avg return: {pe_60.get('avg_return', 0):+.1f}%, median: {pe_60.get('median_return', 0):+.1f}%
- Consecutive projected return: {cons_ret:+.1f}%, PE projected return: {pe_ret:+.1f}%
- Best historical return: {cons_60.get('best', 0):+.1f}% (consecutive), worst: {cons_60.get('worst', 0):+.1f}%
- Bases {"agree (both point same direction)" if bases_agree else "diverge (point in opposite directions)"}
{vix_guide_note}
Write exactly 4 short paragraphs, each 1-3 sentences:

PARAGRAPH 1 - WHAT THIS DATA SHOWS: Explain that seasonal projections show how {name} has historically behaved during this exact calendar period. Reference the specific win rates and what they mean in practical terms. {"A " + str(high_wr) + "% rate means VIX increased in " + str(high_wr) + "% of those historical periods, signaling elevated market anxiety." if sec["symbol"] == "VIX" else "A " + str(high_wr) + "% win rate means " + name + " was higher in " + str(high_wr) + "% of those historical years."}

PARAGRAPH 2 - READING THE TWO BASES: Explain what it means when consecutive and {pe_label.lower()} year patterns {"agree" if bases_agree else "diverge"}. {"Convergence strengthens the signal" if bases_agree else "Divergence suggests the election cycle may create different conditions than recent history"}. Explain why the median return can be more informative than the average (less distorted by extreme years).

PARAGRAPH 3 - WHAT THIS DOES NOT TELL YOU: Be specific about limitations. Seasonal patterns cannot account for breaking news, policy changes, earnings surprises, or geopolitical events. {"A high VIX increase rate does not guarantee volatility will spike in any specific year." if sec["symbol"] == "VIX" else "A high win rate does not mean a gain is guaranteed in any specific year."} The projection is a statistical tendency, not a forecast.

PARAGRAPH 4 - PRACTICAL CONTEXT: Suggest how investors might incorporate seasonal data as one lens alongside fundamental analysis, technical indicators, and risk management. Do NOT recommend any specific action — no "buy", "sell", "hold", or "consider entering a position." Frame it as information that can inform timing and expectations, not dictate decisions.

RULES:
- Do NOT give financial advice or recommend any action
- Do NOT use phrases like "you should", "consider buying", "it may be wise to"
- Write in third person or use "investors" / "traders" / "market participants"
- No emojis, no markdown, no bullet points, no headers, no em dashes
- Professional, educational tone
- Do not exceed 200 words total
- Do not start any paragraph with "This data" — vary openings"""

    try:
        text = send_claude_prompt(
            prompt=prompt,
            model=CLAUDE_SONNET_46,
            system="You are a financial educator writing for a market data platform. You explain data clearly without giving investment advice.",
            max_tokens=600,
            temperature=0.4,
        )
        return text.strip().replace("—", ",").replace("–", ",") if text else None
    except Exception as e:
        print(f"  [AI] Error generating usage guide for {name}: {e}")
        return None


# =============================================================================
# HTML PAGE BUILDER
# =============================================================================

def _fmt_price(val):
    """Format a price value for display."""
    if val is None:
        return "—"
    if abs(val) >= 1000:
        return f"{val:,.2f}"
    if abs(val) >= 10:
        return f"{val:.2f}"
    return f"{val:.4f}"


def _fmt_change(change, change_p):
    """Format change with sign and color class."""
    if change_p is None:
        return "", "flat"
    direction = "up" if change_p >= 0 else "down"
    sign = "+" if change_p >= 0 else ""
    chg_str = f"{sign}{change_p:.2f}%"
    if change is not None:
        chg_str = f"{sign}{change:.2f} ({sign}{change_p:.2f}%)"
    return chg_str, direction


def _news_sentiment_badge(sentiment):
    """Return HTML badge for news sentiment."""
    if not sentiment:
        return ""
    polarity = sentiment.get("polarity", 0)
    if polarity > 0.15:
        return '<span class="sentiment-badge positive">Positive</span>'
    elif polarity < -0.15:
        return '<span class="sentiment-badge negative">Negative</span>'
    return '<span class="sentiment-badge neutral">Neutral</span>'


def _build_page_css():
    """Return page-specific CSS for security detail pages."""
    return """
        /* Security Page Layout */
        .page-container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 24px 60px;
        }

        /* Hero Quote Section */
        .security-hero {
            padding: 40px 0 32px;
            border-bottom: 1px solid var(--border-color);
        }

        .security-hero h1 {
            font-size: 32px;
            font-weight: 700;
            letter-spacing: -0.5px;
            margin-bottom: 8px;
        }

        .security-meta {
            color: var(--text-muted);
            font-size: 14px;
            margin-bottom: 20px;
        }

        .price-display {
            display: flex;
            align-items: baseline;
            gap: 16px;
            flex-wrap: wrap;
        }

        .price-main {
            font-size: 42px;
            font-weight: 700;
            font-family: 'IBM Plex Mono', monospace;
            letter-spacing: -1px;
        }

        .price-change {
            font-size: 18px;
            font-weight: 600;
            font-family: 'IBM Plex Mono', monospace;
            padding: 4px 12px;
            border-radius: 6px;
        }

        .price-change.up {
            color: var(--accent-green);
            background: rgba(13, 122, 62, 0.08);
        }

        .price-change.down {
            color: var(--accent-red);
            background: rgba(196, 30, 58, 0.08);
        }

        .quote-details {
            display: flex;
            gap: 32px;
            margin-top: 16px;
            flex-wrap: wrap;
        }

        .quote-detail {
            font-size: 13px;
        }

        .quote-detail-label {
            color: var(--text-muted);
            margin-right: 6px;
        }

        .quote-detail-value {
            color: var(--text-primary);
            font-weight: 600;
            font-family: 'IBM Plex Mono', monospace;
        }

        .security-description {
            margin-top: 16px;
            font-size: 15px;
            color: var(--text-secondary);
            line-height: 1.7;
            max-width: 800px;
        }

        /* Range bars (Day Range + 52-Week Range) */
        .range-52wk, .range-day {
            margin-top: 16px;
        }
        .range-52wk-header {
            margin-bottom: 6px;
        }
        .range-52wk-title {
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .range-52wk-bar-wrap {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .range-52wk-val {
            font-size: 13px;
            font-weight: 600;
            font-family: 'IBM Plex Mono', monospace;
            color: var(--text-secondary);
            white-space: nowrap;
            min-width: 64px;
        }
        .range-52wk-val:last-child {
            text-align: right;
        }
        .range-52wk-bar {
            flex: 1;
            height: 6px;
            background: var(--border-color);
            border-radius: 3px;
            position: relative;
            max-width: 360px;
        }
        .range-52wk-fill {
            height: 100%;
            border-radius: 3px;
            background: linear-gradient(90deg, var(--accent-red), var(--accent-green));
            opacity: 0.5;
        }
        .range-52wk-marker {
            position: absolute;
            top: 50%;
            transform: translate(-50%, -50%);
            width: 14px;
            height: 14px;
            background: var(--text-primary);
            border: 2px solid var(--bg-secondary);
            border-radius: 50%;
            box-shadow: 0 0 0 2px rgba(255,255,255,0.1);
        }
        .day-bar {
            background: var(--border-color);
        }
        .day-bar .range-52wk-fill {
            background: linear-gradient(90deg, rgba(196,30,58,0.3), rgba(13,122,62,0.3));
        }

        /* Volume context */
        .volume-context {
            margin-top: 16px;
            padding: 12px 16px;
            background: var(--bg-secondary);
            border-radius: 8px;
            border: 1px solid var(--border-color);
            max-width: 480px;
        }
        .vol-row {
            display: flex;
            gap: 24px;
            align-items: center;
        }
        .vol-item {
            display: flex;
            flex-direction: column;
            gap: 2px;
        }
        .vol-label {
            font-size: 11px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .vol-value {
            font-size: 14px;
            font-weight: 700;
            font-family: 'IBM Plex Mono', monospace;
            color: var(--text-primary);
        }
        .vol-ratio.up {
            color: var(--accent-green);
        }
        .vol-ratio.muted {
            color: var(--text-muted);
        }

        /* Projection Section */
        .section-header {
            font-size: 22px;
            font-weight: 700;
            margin-bottom: 8px;
            padding-top: 36px;
            letter-spacing: -0.3px;
        }

        .section-subtext {
            font-size: 14px;
            color: var(--text-muted);
            margin-bottom: 20px;
            line-height: 1.6;
        }

        .projection-controls {
            display: flex;
            gap: 12px;
            margin-bottom: 20px;
            flex-wrap: wrap;
            align-items: center;
        }

        .control-group {
            display: flex;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            overflow: hidden;
        }

        .control-group-label {
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            padding: 8px 12px;
            display: flex;
            align-items: center;
            border-right: 1px solid var(--border-color);
            background: var(--bg-tertiary);
        }

        .tab-btn {
            padding: 8px 16px;
            font-size: 13px;
            font-weight: 500;
            font-family: inherit;
            background: transparent;
            border: none;
            color: var(--text-secondary);
            cursor: pointer;
            transition: all 0.2s ease;
            border-right: 1px solid var(--border-color);
        }

        .tab-btn:last-child {
            border-right: none;
        }

        .tab-btn:hover {
            background: var(--bg-tertiary);
        }

        .tab-btn.active {
            background: var(--accent-blue);
            color: #fff;
            font-weight: 600;
        }

        .chart-container {
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            overflow: hidden;
            margin-bottom: 16px;
        }

        .chart-container img {
            width: 100%;
            height: auto;
            display: block;
        }

        .chart-unavailable {
            padding: 80px 20px;
            text-align: center;
            color: var(--text-muted);
            font-size: 15px;
        }

        /* AI Analysis */
        .ai-analysis {
            font-size: 15px;
            color: var(--text-secondary);
            line-height: 1.75;
            max-width: 860px;
            margin-bottom: 28px;
        }

        .ai-analysis p {
            margin-bottom: 12px;
        }

        .ai-analysis p:last-child {
            margin-bottom: 0;
        }

        /* Projection Callout */
        .projection-callout {
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 20px 24px;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            gap: 28px;
            flex-wrap: wrap;
        }

        .callout-target {
            display: flex;
            flex-direction: column;
        }

        .callout-label {
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-muted);
            margin-bottom: 2px;
        }

        .callout-price {
            font-size: 28px;
            font-weight: 700;
            font-family: 'IBM Plex Mono', monospace;
            letter-spacing: -0.5px;
        }

        .callout-return {
            font-size: 16px;
            font-weight: 600;
            font-family: 'IBM Plex Mono', monospace;
            margin-left: 4px;
        }

        .callout-return.up { color: var(--accent-green); }
        .callout-return.down { color: var(--accent-red); }

        .callout-divider {
            width: 1px;
            height: 48px;
            background: var(--border-color);
        }

        .callout-stats {
            display: flex;
            gap: 24px;
            flex-wrap: wrap;
        }

        .callout-stat {
            display: flex;
            flex-direction: column;
        }

        .callout-stat-value {
            font-size: 20px;
            font-weight: 700;
            font-family: 'IBM Plex Mono', monospace;
        }

        .callout-stat-value.up { color: var(--accent-green); }
        .callout-stat-value.down { color: var(--accent-red); }

        .callout-stat-label {
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-muted);
        }

        .callout-summary {
            font-size: 13px;
            color: var(--text-secondary);
            line-height: 1.5;
            flex-basis: 100%;
            margin-top: -4px;
        }

        /* Basis Comparison */
        .basis-comparison {
            background: linear-gradient(135deg, rgba(0,102,204,0.04), rgba(0,102,204,0.01));
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 16px 20px;
            margin-bottom: 8px;
            font-size: 14px;
            color: var(--text-secondary);
            line-height: 1.6;
        }

        .basis-comparison strong {
            color: var(--text-primary);
        }

        .as-of-stamp {
            font-size: 12px;
            color: var(--text-muted);
            text-align: right;
            margin-bottom: 4px;
        }

        /* News Section */
        .news-list {
            list-style: none;
        }

        .news-item {
            padding: 16px 0;
            border-bottom: 1px solid var(--border-color);
        }

        .news-item:last-child {
            border-bottom: none;
        }

        .news-item a {
            font-size: 15px;
            font-weight: 600;
            color: var(--text-primary);
            text-decoration: none;
            line-height: 1.4;
            display: block;
            margin-bottom: 6px;
        }

        .news-item a:hover {
            color: var(--accent-blue);
        }

        .news-meta {
            font-size: 12px;
            color: var(--text-muted);
            display: flex;
            gap: 12px;
            align-items: center;
        }

        .sentiment-badge {
            font-size: 11px;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 4px;
        }

        .sentiment-badge.positive {
            color: var(--accent-green);
            background: rgba(13, 122, 62, 0.08);
        }

        .sentiment-badge.negative {
            color: var(--accent-red);
            background: rgba(196, 30, 58, 0.08);
        }

        .sentiment-badge.neutral {
            color: var(--text-muted);
            background: var(--bg-tertiary);
        }

        /* Related Articles */
        .related-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
            gap: 16px;
        }

        .related-card {
            display: flex;
            flex-direction: column;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 18px;
            text-decoration: none;
            transition: border-color 0.2s ease, box-shadow 0.2s ease;
        }

        .related-card:hover {
            border-color: var(--accent-blue);
            box-shadow: var(--card-hover-shadow);
        }

        .related-symbol {
            font-size: 12px;
            font-weight: 700;
            font-family: 'IBM Plex Mono', monospace;
            color: var(--accent-blue);
            margin-bottom: 6px;
        }

        .related-title {
            font-size: 15px;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.4;
            margin-bottom: 8px;
        }

        .related-dek {
            font-size: 13px;
            color: var(--text-secondary);
            line-height: 1.5;
            flex: 1;
        }

        .related-meta {
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 10px;
        }

        .related-badge {
            display: inline-block;
            font-size: 11px;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 4px;
            margin-bottom: 8px;
            width: fit-content;
        }

        .related-badge.bullish {
            color: var(--accent-green);
            background: rgba(13, 122, 62, 0.08);
        }

        .related-badge.bearish {
            color: var(--accent-red);
            background: rgba(196, 30, 58, 0.08);
        }

        /* Usage Guide */
        .usage-guide {
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 28px 32px;
            margin-top: 12px;
            margin-bottom: 12px;
        }

        .usage-guide h3 {
            font-size: 16px;
            font-weight: 700;
            margin-bottom: 14px;
            color: var(--text-primary);
        }

        .usage-guide p {
            font-size: 14px;
            color: var(--text-secondary);
            line-height: 1.7;
            margin-bottom: 12px;
        }

        .usage-guide p:last-child {
            margin-bottom: 0;
        }

        .usage-guide .guide-disclaimer {
            font-size: 12px;
            color: var(--text-muted);
            font-style: italic;
            margin-top: 16px;
            padding-top: 14px;
            border-top: 1px solid var(--border-color);
        }

        /* Educational Section */
        .edu-section {
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 28px 32px;
            margin-top: 12px;
        }

        .edu-section h3 {
            font-size: 16px;
            font-weight: 700;
            margin-bottom: 12px;
            color: var(--text-primary);
        }

        .edu-section p {
            font-size: 14px;
            color: var(--text-secondary);
            line-height: 1.7;
            margin-bottom: 12px;
        }

        .edu-section p:last-child {
            margin-bottom: 0;
        }

        .edu-columns {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 24px;
            margin-top: 16px;
        }

        .edu-card {
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 20px;
        }

        .edu-card h4 {
            font-size: 14px;
            font-weight: 700;
            margin-bottom: 8px;
            color: var(--accent-blue);
        }

        .edu-card p {
            font-size: 13px;
        }

        .disclaimer {
            font-size: 12px;
            color: var(--text-muted);
            font-style: italic;
            margin-top: 16px;
            padding-top: 16px;
            border-top: 1px solid var(--border-color);
        }

        /* Responsive */
        @media (max-width: 768px) {
            .market-bar {
                -webkit-overflow-scrolling: touch;
                scrollbar-width: none;
            }
            .market-bar::-webkit-scrollbar { display: none; }
            .market-bar-content {
                justify-content: flex-start;
                padding: 0 16px;
                gap: 16px;
            }
            .security-hero h1 { font-size: 24px; }
            .price-main { font-size: 32px; }
            .quote-details { gap: 16px; }
            .projection-controls { flex-direction: column; }
            .edu-columns { grid-template-columns: 1fr; }
            .projection-callout { flex-direction: column; gap: 16px; align-items: flex-start; }
            .callout-divider { display: none; }
            .callout-price { font-size: 22px; }
            .callout-stats { gap: 16px; }
            .range-52wk-bar { max-width: 100%; }
            .volume-context { max-width: 100%; }
            .vol-row { gap: 16px; }
        }
    """


def _build_base_css():
    """Return base CSS variables and shared styles (from rebuild_news_home.py light theme)."""
    t = {
        "bg_primary": "#ffffff",
        "bg_secondary": "#f8f9fa",
        "bg_tertiary": "#e9ecef",
        "text_primary": "#1a1a1a",
        "text_secondary": "#4a4a4a",
        "text_muted": "#6c757d",
        "accent_blue": "#0066cc",
        "accent_green": "#0d7a3e",
        "accent_red": "#c41e3a",
        "accent_amber": "#d97706",
        "border_color": "#dee2e6",
        "card_shadow": "0 1px 3px rgba(0,0,0,0.08)",
        "card_hover_shadow": "0 4px 12px rgba(0,0,0,0.12)",
        "badge_bullish_bg": "rgba(13, 122, 62, 0.1)",
        "badge_bearish_bg": "rgba(196, 30, 58, 0.1)",
    }
    return f"""
        :root {{
            --bg-primary: {t["bg_primary"]};
            --bg-secondary: {t["bg_secondary"]};
            --bg-tertiary: {t["bg_tertiary"]};
            --text-primary: {t["text_primary"]};
            --text-secondary: {t["text_secondary"]};
            --text-muted: {t["text_muted"]};
            --accent-blue: {t["accent_blue"]};
            --accent-green: {t["accent_green"]};
            --accent-red: {t["accent_red"]};
            --accent-amber: {t["accent_amber"]};
            --border-color: {t["border_color"]};
            --card-shadow: {t["card_shadow"]};
            --card-hover-shadow: {t["card_hover_shadow"]};
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background-color: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
            min-height: 100vh;
        }}

        .market-bar {{
            background: var(--bg-secondary);
            border-top: 1px solid var(--border-color);
            border-bottom: 1px solid var(--border-color);
            padding: 10px 0;
            overflow-x: auto;
        }}
        .market-bar-content {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 24px;
            display: flex;
            gap: 24px;
            align-items: center;
            justify-content: center;
        }}
        .market-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-family: 'IBM Plex Mono', monospace;
            font-size: 14px;
            white-space: nowrap;
            padding: 6px 0;
            border-top: 2px solid transparent;
            border-bottom: 2px solid transparent;
            text-decoration: none;
        }}
        .market-item.up {{
            border-image: linear-gradient(90deg, transparent, var(--accent-green), transparent) 1;
        }}
        .market-item.down {{
            border-image: linear-gradient(90deg, transparent, var(--accent-red), transparent) 1;
        }}
        .market-symbol {{ color: var(--text-primary); font-weight: 500; }}
        .market-price {{ color: var(--text-secondary); font-weight: 700; }}
        .market-change {{ font-weight: 700; }}
        .market-change.up {{ color: var(--accent-green); }}
        .market-change.down {{ color: var(--accent-red); }}

        header {{
            border-bottom: 1px solid var(--border-color);
            background: var(--bg-primary);
        }}
        .header-content {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 16px 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .logo {{
            display: flex;
            align-items: baseline;
            gap: 2px;
            text-decoration: none;
        }}
        .logo-seasonal {{ font-size: 22px; font-weight: 700; color: var(--accent-blue); letter-spacing: -0.5px; }}
        .logo-market {{ font-size: 22px; font-weight: 700; color: var(--text-primary); letter-spacing: -0.5px; }}
        .logo-news {{ font-size: 22px; font-weight: 400; color: var(--text-muted); letter-spacing: -0.5px; }}
        .header-right {{ display: flex; align-items: center; gap: 24px; }}
        nav {{ display: flex; gap: 28px; }}
        nav a {{ color: var(--text-secondary); text-decoration: none; font-size: 14px; font-weight: 500; transition: color 0.2s ease; }}
        nav a:hover {{ color: var(--text-primary); }}

        /* Email CTA */
        .security-cta {{
            max-width: 1200px;
            margin: 32px auto 0;
            padding: 28px 24px;
            background: linear-gradient(135deg, #f0f4ff 0%, #f8f9fa 100%);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            text-align: center;
        }}
        .security-cta h3 {{ font-size: 18px; font-weight: 700; color: var(--text-primary); margin: 0 0 6px; }}
        .security-cta p {{ font-size: 14px; color: var(--text-secondary); margin: 0 0 16px; line-height: 1.5; }}
        .cta-row {{ display: flex; justify-content: center; align-items: center; gap: 10px; flex-wrap: wrap; }}
        .cta-email {{ padding: 10px 16px; border: 1px solid var(--border-color); border-radius: 6px; font-size: 14px; width: 240px; outline: none; font-family: inherit; }}
        .cta-email:focus {{ border-color: var(--accent-blue); }}
        .cta-btn {{ padding: 10px 20px; background: var(--accent-blue); color: #fff; border: none; border-radius: 6px; font-size: 14px; font-weight: 600; cursor: pointer; font-family: inherit; transition: background 0.2s; }}
        .cta-btn:hover {{ background: #0052a3; }}
        .cta-groups {{ display: flex; justify-content: center; gap: 16px; margin-top: 10px; font-size: 13px; color: var(--text-secondary); }}
        .cta-groups label {{ display: flex; align-items: center; gap: 4px; cursor: pointer; }}
        .cta-error {{ color: var(--accent-red); font-size: 12px; margin-top: 6px; display: none; }}
        .cta-success {{ color: var(--accent-green); font-weight: 600; font-size: 14px; display: none; }}

        footer {{
            border-top: 1px solid var(--border-color);
            padding: 24px;
            background: var(--bg-secondary);
        }}
        .footer-content {{
            max-width: 1200px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 13px;
            color: var(--text-muted);
            flex-wrap: wrap;
            gap: 12px;
        }}
        .footer-content a {{ color: var(--text-muted); text-decoration: none; }}
        .footer-content a:hover {{ color: var(--text-primary); }}

        @media (max-width: 768px) {{
            .cta-row {{ flex-direction: column; align-items: center; }}
            .cta-email {{ width: 100%; max-width: 300px; }}
            .footer-content {{ flex-direction: column; text-align: center; }}
        }}
    """


def _build_market_bar_html(current_slug=None, all_quotes=None):
    """Build market bar HTML with links. Highlight current page's ticker."""
    items_html = ""
    for sec in SECURITY_PAGES:
        quote = (all_quotes or {}).get(sec["symbol"]) or get_quote_details(sec["symbol"], sec["exchange"])
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
            price_fmt = f"{price:,.2f}"
            chg_fmt = f"{sign}{change_p:.2f}%"
        else:
            price_fmt = "—"
            chg_fmt = ""
            direction = "flat"

        # Use short label for market bar
        short_labels = {"Dow Jones Industrial Average": "DOW", "NASDAQ Composite": "NASDAQ",
                        "CBOE Volatility Index": "VIX", "Crude Oil (WTI)": "CRUDE",
                        "Natural Gas": "NAT GAS"}
        short_label = short_labels.get(sec["label"], sec["label"])
        is_current = sec["slug"] == current_slug
        active_cls = " current" if is_current else ""

        items_html += f'''
            <a href="/markets/{sec['slug']}.html" class="market-item {direction}{active_cls}">
                <span class="market-symbol">{short_label}</span>
                <span class="market-price">{price_fmt}</span>
                {"<span class='market-change " + direction + "'>" + chg_fmt + "</span>" if chg_fmt else ""}
            </a>'''

    return f'''
    <div class="market-bar">
        <div class="market-bar-content">
            {items_html}
        </div>
    </div>'''


def _build_header_html():
    return '''
    <header>
        <div class="header-content">
            <a href="/" class="logo">
                <span class="logo-seasonal">Seasonal</span><span class="logo-market">Market</span><span class="logo-news">News</span>
            </a>
            <div class="header-right">
                <nav>
                    <a href="/">Home</a>
                    <a href="https://tradewave.ai" target="_blank">TradeWave</a>
                </nav>
            </div>
        </div>
    </header>'''


MAILERLITE_FORM_URL = "https://assets.mailerlite.com/jsonp/489451/forms/173861813170996648/subscribe"
_ML_GROUP_CACHE = None

def _get_ml_group_ids():
    """Fetch MailerLite group IDs (cached for the run)."""
    global _ML_GROUP_CACHE
    if _ML_GROUP_CACHE is not None:
        return _ML_GROUP_CACHE
    try:
        headers = {
            'Authorization': f'Bearer {config.mailerlite_token}',
            'Accept': 'application/json',
        }
        r = requests.get('https://connect.mailerlite.com/api/groups?limit=100',
                         headers=headers, timeout=10)
        result = {}
        for g in r.json().get('data', []):
            if g['name'] in ('SMN', 'SMN-DAILY', 'SMN-WEEKLY'):
                result[g['name']] = g['id']
        _ML_GROUP_CACHE = result
        return result
    except Exception:
        _ML_GROUP_CACHE = {}
        return {}


def _build_cta_html():
    """Return email signup CTA HTML + JS."""
    ml = _get_ml_group_ids()
    smn_id = ml.get('SMN', '')
    daily_id = ml.get('SMN-DAILY', '')
    weekly_id = ml.get('SMN-WEEKLY', '')

    return f'''
    <div class="security-cta">
        <h3>Get Daily Market Intelligence</h3>
        <p>AI-powered seasonal analysis delivered to your inbox. Free, no spam.</p>
        <form id="secCtaForm" onsubmit="return false;">
            <div class="cta-row">
                <input type="email" id="secCtaEmail" class="cta-email" placeholder="Enter your email" required autocomplete="email">
                <button type="submit" class="cta-btn" id="secCtaBtn">Subscribe</button>
            </div>
            <div class="cta-groups">
                <label><input type="checkbox" id="secChkDaily" checked> Daily Digest</label>
                <label><input type="checkbox" id="secChkWeekly"> Weekly Summary</label>
            </div>
            <div class="cta-error" id="secCtaError">Please select at least one option.</div>
        </form>
        <div class="cta-success" id="secCtaSuccess">Thanks! Check your email to confirm.</div>
    </div>
    <script>
    (function() {{
        var form = document.getElementById('secCtaForm');
        if (!form) return;
        form.addEventListener('submit', function(e) {{
            e.preventDefault();
            var email = document.getElementById('secCtaEmail').value.trim();
            var daily = document.getElementById('secChkDaily').checked;
            var weekly = document.getElementById('secChkWeekly').checked;
            var err = document.getElementById('secCtaError');
            if (!daily && !weekly) {{ err.style.display = 'block'; return; }}
            err.style.display = 'none';
            var btn = document.getElementById('secCtaBtn');
            btn.disabled = true;
            btn.textContent = 'Subscribing...';
            var fd = new FormData();
            fd.append('fields[email]', email);
            fd.append('ml-submit', '1');
            fd.append('anticsrf', 'true');
            if ('{smn_id}') fd.append('groups[]', '{smn_id}');
            if (daily && '{daily_id}') fd.append('groups[]', '{daily_id}');
            if (weekly && '{weekly_id}') fd.append('groups[]', '{weekly_id}');
            fetch('{MAILERLITE_FORM_URL}', {{ method: 'POST', body: fd, mode: 'no-cors' }})
            .then(function() {{
                form.style.display = 'none';
                document.getElementById('secCtaSuccess').style.display = 'block';
            }}).catch(function() {{
                form.style.display = 'none';
                document.getElementById('secCtaSuccess').style.display = 'block';
            }});
        }});
    }})();
    </script>'''


def _build_footer_html():
    now = datetime.now(timezone.utc)
    return f'''
{_build_cta_html()}
    <footer>
        <div class="footer-content">
            <div>© {now.year} <a href="https://taradataresearch.com" target="_blank">Tara Data Research LLC</a>. All rights reserved.</div>
            <div><a href="https://tradewave.ai" target="_blank">TradeWave</a></div>
            <div>Updated {now.strftime('%b %d, %Y %H:%M UTC')}</div>
        </div>
    </footer>'''


def build_security_page(sec, quote, news, related, chart_urls, max_pe=0,
                        projection_targets=None, projection_stats=None,
                        ai_analysis=None, usage_guide=None, all_quotes=None,
                        all_ai_snippets=None, price_history=None):
    """Build the full HTML page for a security."""
    pe_pos, pe_label = _pe_cycle()
    slug = sec["slug"]
    today_str = datetime.now().strftime("%Y-%m-%d")
    projection_targets = projection_targets or {}
    projection_stats = projection_stats or {}
    price_history = price_history or {}

    # User-facing symbol (SPX not GSPC)
    display_symbol = sec["appserver_symbol"]

    # Price display
    close_price = quote.get("close") if quote else None
    change = quote.get("change") if quote else None
    change_p = quote.get("change_p") if quote else None
    chg_str, price_direction = _fmt_change(change, change_p)

    # Quote details row
    detail_items = []
    for label, key in [("Open", "open"), ("High", "high"), ("Low", "low"),
                       ("Prev Close", "previousClose")]:
        val = quote.get(key) if quote else None
        detail_items.append(f'<span class="quote-detail"><span class="quote-detail-label">{label}</span>'
                          f'<span class="quote-detail-value">{_fmt_price(val)}</span></span>')
    if quote and quote.get("volume"):
        vol = quote["volume"]
        vol_fmt = f"{vol:,.0f}" if vol else "—"
        detail_items.append(f'<span class="quote-detail"><span class="quote-detail-label">Volume</span>'
                          f'<span class="quote-detail-value">{vol_fmt}</span></span>')

    # ── 52-Week stats from price history ──
    hist_prices = price_history.get("prices", [])
    wk52_high = max(hist_prices) if hist_prices else None
    wk52_low = min(hist_prices) if hist_prices else None
    wk52_range_html = ""
    if wk52_high and wk52_low and close_price:
        try:
            cur = float(close_price)
            spread = wk52_high - wk52_low
            pct_pos = ((cur - wk52_low) / spread * 100) if spread > 0 else 50
            pct_pos = max(0, min(100, pct_pos))
            wk52_range_html = f'''
        <div class="range-52wk">
            <div class="range-52wk-header">
                <span class="range-52wk-title">52-Week Range</span>
            </div>
            <div class="range-52wk-bar-wrap">
                <span class="range-52wk-val">{_fmt_price(wk52_low)}</span>
                <div class="range-52wk-bar">
                    <div class="range-52wk-fill" style="width:{pct_pos:.1f}%"></div>
                    <div class="range-52wk-marker" style="left:{pct_pos:.1f}%"></div>
                </div>
                <span class="range-52wk-val">{_fmt_price(wk52_high)}</span>
            </div>
        </div>'''
        except (ValueError, TypeError):
            pass

    # ── Day Range ──
    day_range_html = ""
    if quote:
        day_high = quote.get("high")
        day_low = quote.get("low")
        if day_high and day_low and close_price:
            try:
                dh, dl, cur = float(day_high), float(day_low), float(close_price)
                d_spread = dh - dl
                d_pct = ((cur - dl) / d_spread * 100) if d_spread > 0 else 50
                d_pct = max(0, min(100, d_pct))
                day_range_html = f'''
        <div class="range-day">
            <div class="range-52wk-header">
                <span class="range-52wk-title">Day Range</span>
            </div>
            <div class="range-52wk-bar-wrap">
                <span class="range-52wk-val">{_fmt_price(dl)}</span>
                <div class="range-52wk-bar day-bar">
                    <div class="range-52wk-fill" style="width:{d_pct:.1f}%"></div>
                    <div class="range-52wk-marker" style="left:{d_pct:.1f}%"></div>
                </div>
                <span class="range-52wk-val">{_fmt_price(dh)}</span>
            </div>
        </div>'''
            except (ValueError, TypeError):
                pass

    # ── Volume context (30-day avg + ratio) ──
    volume_context_html = ""
    hist_volumes = price_history.get("volumes", [])
    if quote and quote.get("volume") and hist_volumes:
        try:
            today_vol = float(quote["volume"])
            avg_30 = sum(hist_volumes[-30:]) / len(hist_volumes[-30:]) if len(hist_volumes) >= 5 else None
            if avg_30 and avg_30 > 0:
                vol_ratio = today_vol / avg_30
                ratio_cls = "up" if vol_ratio >= 1.0 else "muted"
                def _fmt_vol(v):
                    if v >= 1_000_000: return f"{v/1_000_000:.1f}M"
                    if v >= 1_000: return f"{v/1_000:.0f}K"
                    return f"{v:,.0f}"
                volume_context_html = f'''
        <div class="volume-context">
            <div class="vol-row">
                <span class="vol-item">
                    <span class="vol-label">Volume</span>
                    <span class="vol-value">{_fmt_vol(today_vol)}</span>
                </span>
                <span class="vol-item">
                    <span class="vol-label">30d Avg</span>
                    <span class="vol-value">{_fmt_vol(avg_30)}</span>
                </span>
                <span class="vol-item">
                    <span class="vol-label">Relative</span>
                    <span class="vol-value vol-ratio {ratio_cls}">{vol_ratio:.1f}x</span>
                </span>
            </div>
        </div>'''
        except (ValueError, TypeError):
            pass

    # Timestamp
    ts = quote.get("timestamp") if quote else None
    ts_str = ""
    if ts:
        try:
            ts_str = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%b %d, %Y %I:%M %p UTC")
        except Exception:
            ts_str = ""

    # ── Determine default basis (PE when projections diverge) ──
    cons_ret = projection_targets.get("consecutive_60", {}).get("return_pct", 0)
    pe_ret = projection_targets.get("pe_60", {}).get("return_pct", 0)
    diverges = (cons_ret > 0 and pe_ret < 0) or (cons_ret < 0 and pe_ret > 0)
    default_basis = "pe" if (diverges and "pe_60" in chart_urls) else "consecutive"

    # ── Projection data for JS ──
    has_charts = len(chart_urls) > 0
    default_chart = chart_urls.get("%s_60" % default_basis, chart_urls.get("consecutive_60", ""))

    # Data attributes for chart image switching
    data_attrs = ""
    for key, url in chart_urls.items():
        attr_name = key.replace("_", "")
        data_attrs += f' data-{attr_name}="{url}"'

    # Build JS projection data object (targets + stats for dynamic callout)
    proj_data_js = "{\n"
    for key in sorted(set(list(projection_targets.keys()) + list(projection_stats.keys()))):
        t = projection_targets.get(key, {})
        s = projection_stats.get(key, {})
        proj_data_js += f'        "{key}": {{'
        proj_data_js += f'target: {t.get("target", 0)}, '
        proj_data_js += f'return_pct: {t.get("return_pct", 0)}, '
        proj_data_js += f'current: {t.get("current", 0)}, '
        proj_data_js += f'win_rate: {s.get("win_rate", 0)}, '
        proj_data_js += f'avg_return: {s.get("avg_return", 0)}, '
        proj_data_js += f'median_return: {s.get("median_return", 0)}, '
        proj_data_js += f'best: {s.get("best", 0)}, '
        proj_data_js += f'worst: {s.get("worst", 0)}, '
        proj_data_js += f'num_winners: {s.get("num_winners", 0)}, '
        proj_data_js += f'num_total: {s.get("num_total", 0)}'
        proj_data_js += '},\n'
    proj_data_js += '    }'

    # ── AI Analysis section ──
    ai_html = ""
    if ai_analysis:
        # Split into paragraphs
        paragraphs = [p.strip() for p in ai_analysis.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [ai_analysis]
        ai_paras = "".join(f"<p>{p}</p>" for p in paragraphs)
        ai_html = f'<div class="ai-analysis">{ai_paras}</div>'

    # ── News section ──
    news_html = ""
    if news:
        news_items = ""
        for n in news[:10]:
            title = n.get("title", "")
            link = n.get("link", "#")
            date_str = ""
            if n.get("date"):
                try:
                    date_str = datetime.fromisoformat(n["date"].replace("Z", "+00:00")).strftime("%b %d, %Y")
                except Exception:
                    date_str = n["date"][:10]
            sentiment_html = _news_sentiment_badge(n.get("sentiment"))
            news_items += f'''
            <li class="news-item">
                <a href="{link}" target="_blank" rel="noopener">{title}</a>
                <div class="news-meta">
                    <span>{date_str}</span>
                    {sentiment_html}
                </div>
            </li>'''
        news_html = f'''
        <h2 class="section-header">Latest News</h2>
        <p class="section-subtext">Recent headlines related to {sec["label"]}, sourced from major financial news outlets with AI sentiment analysis.</p>
        <ul class="news-list">{news_items}</ul>'''

    # ── Related: other security pages (top) + articles (bottom) ──
    related_html = ""
    # Build security page cards (other securities, excluding current)
    sec_cards = ""
    all_ai_snippets = all_ai_snippets or {}
    other_secs = [s for s in SECURITY_PAGES if s["slug"] != sec["slug"]]
    for other in other_secs[:4]:
        oq = (all_quotes or {}).get(other["symbol"], {})
        o_price = _fmt_price(oq.get("close")) if oq else ""
        o_chg_p = oq.get("change_p")
        o_dir_cls = ""
        o_chg_str = ""
        if o_chg_p is not None:
            o_dir_cls = "up" if o_chg_p >= 0 else "down"
            o_sign = "+" if o_chg_p >= 0 else ""
            o_chg_str = f'{o_sign}{o_chg_p:.2f}%'
        o_dek = all_ai_snippets.get(other["slug"], other["description"][:120] + "...")
        sec_cards += f'''
            <a href="/markets/{other['slug']}.html" class="related-card">
                <div class="related-symbol">{other['appserver_symbol']}</div>
                <h3 class="related-title">{other['label']}</h3>
                <p class="related-dek">{o_dek}</p>
                <div class="related-meta">{o_price} <span class="{o_dir_cls}" style="font-weight:600">{o_chg_str}</span></div>
            </a>'''

    # Build article cards
    article_cards = ""
    if related:
        for a in related[:4]:
            a_title = a.get("title", "")
            a_url = a.get("url", "")
            a_symbol = a.get("symbol", "")
            a_dek = a.get("dek", "")
            a_dir = a.get("direction", "")
            a_date_str = ""
            if a.get("published_date"):
                try:
                    a_date_str = datetime.fromisoformat(
                        a["published_date"].replace("Z", "+00:00")
                    ).strftime("%b %d, %Y")
                except Exception:
                    a_date_str = ""

            badge = ""
            if a_dir:
                if a_dir.lower() == "long":
                    badge = '<span class="related-badge bullish">Bullish</span>'
                elif a_dir.lower() == "short":
                    badge = '<span class="related-badge bearish">Bearish</span>'

            if len(a_dek) > 120:
                a_dek = a_dek[:117] + "..."

            article_cards += f'''
            <a href="{a_url}" class="related-card">
                {badge}
                <div class="related-symbol">{a_symbol}</div>
                <h3 class="related-title">{a_title}</h3>
                <p class="related-dek">{a_dek}</p>
                <div class="related-meta">{a_date_str}</div>
            </a>'''

    if sec_cards or article_cards:
        related_html = f'''
        <h2 class="section-header">Explore More</h2>
        <p class="section-subtext">Other markets with seasonal analysis and recent pattern articles.</p>
        <div class="related-grid">{sec_cards}{article_cards}</div>'''

    # ── Educational section with per-security PE stats ──
    cons_60_s = projection_stats.get("consecutive_60", {})
    pe_60_s = projection_stats.get("pe_60", {})

    edu_cons_detail = ""
    if cons_60_s:
        edu_cons_detail = (
            f" Over the next 60 calendar days, this pattern has been positive "
            f"{cons_60_s['num_winners']} of {cons_60_s['num_total']} times "
            f"with an average return of {cons_60_s['avg_return']:+.1f}%."
        )

    edu_pe_detail = ""
    if pe_60_s:
        edu_pe_detail = (
            f" In {pe_60_s['num_total']} historical {pe_label.lower()} years, "
            f"this 60-day window was positive {pe_60_s['num_winners']} times "
            f"with an average return of {pe_60_s['avg_return']:+.1f}%."
        )

    edu_html = f'''
    <div class="edu-section">
        <h3>Understanding Seasonal Projections</h3>
        <p>Seasonal projections estimate future price movement based on how {sec["label"]} has historically performed during the same calendar period. These are statistical baselines derived from decades of market data, not predictions.</p>

        <div class="edu-columns">
            <div class="edu-card">
                <h4>Consecutive Years (Last 10)</h4>
                <p>Uses the most recent 10 years of data regardless of market regime. This captures the broadest recent behavior, including all economic and political environments.{edu_cons_detail}</p>
            </div>
            <div class="edu-card">
                <h4>{pe_label} Years ({max_pe} Available)</h4>
                <p>Uses only years that fall in the same position within the 4-year U.S. presidential election cycle. {datetime.now().year} is a {pe_label.lower()} year. Markets often exhibit distinct patterns tied to fiscal and monetary policy shifts within this cycle.{edu_pe_detail}</p>
            </div>
        </div>

        <p class="disclaimer">Seasonal patterns reflect historical tendencies and do not guarantee future results. All projections are based on past performance and should be used as one input among many in your investment decision-making process. Data provided by <a href="https://tradewave.ai" target="_blank">TradeWave.ai</a>.</p>
    </div>'''

    # ── Build basis comparison text ──
    comparison_html = ""
    cons_60_t = projection_targets.get("consecutive_60", {})
    pe_60_t = projection_targets.get("pe_60", {})
    if cons_60_t and pe_60_t and cons_60_s and pe_60_s:
        cons_ret = cons_60_t.get("return_pct", 0)
        pe_ret = pe_60_t.get("return_pct", 0)
        cons_wr = cons_60_s.get("win_rate", 0)
        pe_wr = pe_60_s.get("win_rate", 0)

        if abs(cons_ret - pe_ret) < 0.3:
            agree_text = (
                f"Both the consecutive and {pe_label.lower()} year patterns point in the same direction "
                f"for {sec['label']}, projecting similar 60-day returns "
                f"({cons_ret:+.1f}% vs {pe_ret:+.1f}%). This confluence across different historical "
                f"lenses strengthens the seasonal signal."
            )
        elif cons_ret > pe_ret:
            agree_text = (
                f"The consecutive 10-year pattern is more bullish than the {pe_label.lower()} year "
                f"pattern for {sec['label']} ({cons_ret:+.1f}% vs {pe_ret:+.1f}% projected over 60 days). "
                f"The win rate is {cons_wr:.0f}% for consecutive years vs {pe_wr:.0f}% for "
                f"{pe_label.lower()} years."
            )
        else:
            agree_text = (
                f"The {pe_label.lower()} year pattern is more bullish than the consecutive 10-year "
                f"pattern for {sec['label']} ({pe_ret:+.1f}% vs {cons_ret:+.1f}% projected over 60 days). "
                f"The win rate is {pe_wr:.0f}% for {pe_label.lower()} years vs {cons_wr:.0f}% for "
                f"consecutive years."
            )
        comparison_html = f'<div class="basis-comparison"><strong>Pattern Comparison:</strong> {agree_text}</div>'

    # ── Usage guide section ──
    usage_guide_html = ""
    if usage_guide:
        guide_paras = [p.strip() for p in usage_guide.split("\n\n") if p.strip()]
        if not guide_paras:
            guide_paras = [usage_guide]
        guide_body = "".join(f"<p>{p}</p>" for p in guide_paras)
        usage_guide_html = f'''
    <div class="usage-guide">
        <h3>How to Use This Data</h3>
        {guide_body}
        <p class="guide-disclaimer">This information is provided for educational purposes only and does not constitute financial advice, a recommendation, or a solicitation to buy or sell any security. Seasonal patterns are based on historical data and do not guarantee future performance. All investment decisions carry risk. Consult a qualified financial advisor before making investment decisions.</p>
    </div>'''

    # ── Projection section with callout ──
    proj_section = ""
    if has_charts:
        as_of = datetime.now().strftime("%b %d, %Y")
        # Build default callout values from the default basis
        def_t = projection_targets.get("%s_60" % default_basis, {})
        def_s = projection_stats.get("%s_60" % default_basis, {})

        proj_section = f'''
        <h2 class="section-header">Seasonal Price Projections</h2>
        <p class="section-subtext">Select a historical basis and projection horizon to see where seasonal patterns suggest {sec["label"]} may be headed.</p>

        <div class="projection-controls">
            <div class="control-group">
                <span class="control-group-label">Basis</span>
                <button class="tab-btn{' active' if default_basis == 'consecutive' else ''}" data-basis="consecutive" onclick="switchBasis('consecutive')">Last 10 Years</button>
                <button class="tab-btn{' active' if default_basis == 'pe' else ''}" data-basis="pe" onclick="switchBasis('pe')">{max_pe} {pe_label} Years</button>
            </div>
            <div class="control-group">
                <span class="control-group-label">Horizon</span>
                <button class="tab-btn" data-days="30" onclick="switchDays('30')">30 Days</button>
                <button class="tab-btn active" data-days="60" onclick="switchDays('60')">60 Days</button>
                <button class="tab-btn" data-days="90" onclick="switchDays('90')">90 Days</button>
            </div>
        </div>

        <div class="projection-callout" id="projCallout">
            <div class="callout-target">
                <span class="callout-label">Projected Price</span>
                <span>
                    <span class="callout-price" id="calloutPrice">{_fmt_price(def_t.get("target"))}</span>
                    <span class="callout-return {"up" if def_t.get("return_pct", 0) >= 0 else "down"}" id="calloutReturn">{def_t.get("return_pct", 0):+.2f}%</span>
                </span>
            </div>
            <span class="callout-divider"></span>
            <div class="callout-stats">
                <div class="callout-stat">
                    <span class="callout-stat-value" id="calloutWinRate">{def_s.get("win_rate", 0):.0f}%</span>
                    <span class="callout-stat-label">Win Rate</span>
                </div>
                <div class="callout-stat">
                    <span class="callout-stat-value {"up" if def_s.get("avg_return", 0) >= 0 else "down"}" id="calloutAvgReturn">{def_s.get("avg_return", 0):+.1f}%</span>
                    <span class="callout-stat-label">Avg Return</span>
                </div>
                <div class="callout-stat">
                    <span class="callout-stat-value" id="calloutMedian">{def_s.get("median_return", 0):+.1f}%</span>
                    <span class="callout-stat-label">Median</span>
                </div>
                <div class="callout-stat">
                    <span class="callout-stat-value up" id="calloutBest">{def_s.get("best", 0):+.1f}%</span>
                    <span class="callout-stat-label">Best</span>
                </div>
                <div class="callout-stat">
                    <span class="callout-stat-value down" id="calloutWorst">{def_s.get("worst", 0):+.1f}%</span>
                    <span class="callout-stat-label">Worst</span>
                </div>
            </div>
            <div class="callout-summary" id="calloutSummary">
                {def_s.get("num_winners", 0)} of {def_s.get("num_total", 0)} years were positive over this period.
            </div>
        </div>

        <div class="chart-container">
            <img id="projChart" src="{default_chart}" alt="{sec['label']} Seasonal Projection"{data_attrs} loading="lazy">
        </div>
        <p class="as-of-stamp">Projection as of {as_of} from closing price ${_fmt_price(close_price)}</p>

        {comparison_html}
        '''
    else:
        proj_section = '''
        <h2 class="section-header">Seasonal Price Projections</h2>
        <div class="chart-container">
            <div class="chart-unavailable">Projection charts are temporarily unavailable for this security.</div>
        </div>'''

    # ── Tab switching JS with dynamic callout ──
    js = f'''
    <script>
    const projData = {proj_data_js};

    let currentBasis = '{default_basis}';
    let currentDays = '60';

    function fmtPrice(v) {{
        if (!v) return '—';
        return v >= 1000 ? v.toLocaleString('en-US', {{minimumFractionDigits: 2, maximumFractionDigits: 2}})
             : v >= 10 ? v.toFixed(2)
             : v.toFixed(4);
    }}

    function updateChart() {{
        const img = document.getElementById('projChart');
        if (img) {{
            const key = currentBasis + currentDays;
            const url = img.dataset[key];
            if (url) img.src = url;
        }}
        // Update callout
        const dataKey = currentBasis + '_' + currentDays;
        const d = projData[dataKey];
        if (!d) return;
        const el = (id) => document.getElementById(id);
        if (el('calloutPrice')) el('calloutPrice').textContent = fmtPrice(d.target);
        if (el('calloutReturn')) {{
            const sign = d.return_pct >= 0 ? '+' : '';
            el('calloutReturn').textContent = sign + d.return_pct.toFixed(2) + '%';
            el('calloutReturn').className = 'callout-return ' + (d.return_pct >= 0 ? 'up' : 'down');
        }}
        if (el('calloutWinRate')) el('calloutWinRate').textContent = d.win_rate.toFixed(0) + '%';
        if (el('calloutAvgReturn')) {{
            const sign = d.avg_return >= 0 ? '+' : '';
            el('calloutAvgReturn').textContent = sign + d.avg_return.toFixed(1) + '%';
            el('calloutAvgReturn').className = 'callout-stat-value ' + (d.avg_return >= 0 ? 'up' : 'down');
        }}
        if (el('calloutMedian')) {{
            const sign = d.median_return >= 0 ? '+' : '';
            el('calloutMedian').textContent = sign + d.median_return.toFixed(1) + '%';
        }}
        if (el('calloutBest')) el('calloutBest').textContent = '+' + d.best.toFixed(1) + '%';
        if (el('calloutWorst')) el('calloutWorst').textContent = d.worst.toFixed(1) + '%';
        if (el('calloutSummary')) el('calloutSummary').textContent =
            d.num_winners + ' of ' + d.num_total + ' years were positive over this period.';
    }}

    function switchBasis(basis) {{
        currentBasis = basis;
        document.querySelectorAll('[data-basis]').forEach(b => b.classList.remove('active'));
        document.querySelector('[data-basis="' + basis + '"]').classList.add('active');
        updateChart();
    }}

    function switchDays(days) {{
        currentDays = days;
        document.querySelectorAll('[data-days]').forEach(b => b.classList.remove('active'));
        document.querySelector('[data-days="' + days + '"]').classList.add('active');
        updateChart();
    }}

    document.addEventListener('DOMContentLoaded', updateChart);
    </script>'''

    # ── OG image (60-day consecutive chart) ──
    og_image_url = ""
    if default_chart:
        og_image_url = f'{SITE_URL}{default_chart}'
    og_image_tag = f'<meta property="og:image" content="{og_image_url}">' if og_image_url else ""

    # ── Canonical URL ──
    canonical_url = f"{SITE_URL}/markets/{slug}.html"

    # ── JSON-LD structured data ──
    jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": f"{sec['label']} Seasonal Analysis",
        "description": f"Seasonal price projections and historical pattern analysis for {sec['label']}",
        "url": canonical_url,
        "publisher": {
            "@type": "Organization",
            "name": "Seasonal Market News",
            "url": SITE_URL
        },
        "dateModified": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "about": {
            "@type": "FinancialProduct",
            "name": sec["label"],
            "tickerSymbol": display_symbol,
        }
    }, indent=2)

    # ── SEO enhancements ──
    from seo_helpers import ga_snippet, twitter_tags, breadcrumb_jsonld
    seo_head = ''
    if config.seo_enabled:
        seo_head = '\n    '.join(filter(None, [
            f'<meta property="og:site_name" content="Seasonal Market News">',
            f'<meta property="og:image:width" content="1280">',
            f'<meta property="og:image:height" content="720">',
            twitter_tags(
                f'{sec["label"]} Seasonal Analysis | Seasonal Market News',
                f'Seasonal price projections for {sec["label"]} based on historical patterns and presidential election cycle analysis.',
                image=og_image_url,
            ),
            breadcrumb_jsonld([
                ('Home', config.news_website_url.rstrip('/') + '/'),
                ('Markets', config.news_website_url.rstrip('/') + '/markets/'),
                (sec['label'], None),
            ]),
            ga_snippet(),
        ]))

    # ── Assemble page ──
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Cache-Control" content="no-cache, must-revalidate">
    <link rel="icon" type="image/png" href="{config.smn_favicon}">
    <link rel="canonical" href="{canonical_url}">
    <title>{sec["label"]} Seasonal Analysis | Seasonal Market News</title>
    <meta name="description" content="{sec["label"]} seasonal price projections, historical patterns, and market analysis powered by TradeWave AI.">
    <meta property="og:title" content="{sec["label"]} Seasonal Analysis | Seasonal Market News">
    <meta property="og:description" content="Seasonal price projections for {sec["label"]} based on historical patterns and presidential election cycle analysis.">
    <meta property="og:type" content="article">
    <meta property="og:url" content="{canonical_url}">
    {og_image_tag}
    <script type="application/ld+json">
    {jsonld}
    </script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
{_build_base_css()}
{_build_page_css()}
    </style>
    {seo_head}
</head>
<body>
{_build_market_bar_html(current_slug=slug, all_quotes=all_quotes)}
{_build_header_html()}

<div class="page-container">
    <!-- Hero -->
    <div class="security-hero">
        <h1>{sec["label"]}</h1>
        <div class="security-meta">{display_symbol}{(" &middot; " + ts_str) if ts_str else ""}</div>
        <div class="price-display">
            <span class="price-main">{_fmt_price(close_price)}</span>
            {"<span class='price-change " + price_direction + "'>" + chg_str + "</span>" if chg_str else ""}
        </div>
        <div class="quote-details">
            {"".join(detail_items)}
        </div>
        {day_range_html}
        {wk52_range_html}
        {volume_context_html}
        <p class="security-description">{sec["description"]}</p>
    </div>

    <!-- AI Analysis -->
    {ai_html}

    <!-- Projections -->
    {proj_section}

    <!-- How to Use This Data -->
    {usage_guide_html}

    <!-- Education -->
    {edu_html}

    <!-- News -->
    {news_html}

    <!-- Related Articles -->
    {related_html}
</div>

{_build_footer_html()}
{js}
</body>
</html>'''

    return html


# =============================================================================
# MAIN ORCHESTRATOR
# =============================================================================

def generate_all_security_pages():
    """Generate all security detail pages."""
    print(f"[SECURITY PAGES] Starting generation at {datetime.now(timezone.utc).isoformat()}")

    MARKETS_DIR.mkdir(parents=True, exist_ok=True)
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)

    today_str = datetime.now().strftime("%Y-%m-%d")

    # Authenticate once
    try:
        kp_token = get_keyprovider_token()
        app_token = login_appserver(kp_token)
    except Exception as e:
        print(f"[SECURITY PAGES] Auth failed: {e}")
        return

    # Pre-fetch all quotes once for market bar (avoids 7×7=49 redundant API calls)
    print("  Fetching all quotes for market bar...")
    all_quotes = {}
    for sec in SECURITY_PAGES:
        q = get_quote_details(sec["symbol"], sec["exchange"])
        if q:
            all_quotes[sec["symbol"]] = q

    pe_pos, pe_label = _pe_cycle()

    # Pass 1: gather all data + AI content (so each page can reference others' analysis)
    page_data = {}
    for sec in SECURITY_PAGES:
        label = sec["label"]
        symbol = sec["symbol"]
        print(f"\n  Processing {label} ({symbol}.{sec['exchange']})...")

        quote = all_quotes.get(symbol) or {}
        news = fetch_eodhd_news(symbol, sec["exchange"], limit=10)
        related = fetch_related_articles(sec["appserver_symbol"], sec["exchange"])

        chart_urls, max_pe, projection_targets, projection_stats, price_history, dark_chart_urls = generate_all_charts(
            sec, today_str, app_token
        )

        print(f"  [AI] Generating analysis for {label}...")
        ai_analysis = generate_ai_analysis(
            sec, quote, projection_targets, projection_stats, pe_label, pe_pos
        )
        if ai_analysis:
            print(f"  [AI] Analysis: {len(ai_analysis)} chars")

        print(f"  [AI] Generating usage guide for {label}...")
        usage_guide = generate_usage_guide(
            sec, projection_targets, projection_stats, pe_label
        )
        if usage_guide:
            print(f"  [AI] Guide: {len(usage_guide)} chars")

        page_data[sec["slug"]] = {
            "sec": sec, "quote": quote, "news": news, "related": related,
            "chart_urls": chart_urls, "max_pe": max_pe,
            "projection_targets": projection_targets,
            "projection_stats": projection_stats,
            "ai_analysis": ai_analysis, "usage_guide": usage_guide,
            "price_history": price_history,
            "dark_chart_urls": dark_chart_urls,
        }

    # Save intermediate data for dark-theme re-generation
    export_data = {}
    for slug, pd in page_data.items():
        export_data[slug] = {
            "sec": pd["sec"],
            "quote": pd["quote"],
            "chart_urls": pd["chart_urls"],
            "dark_chart_urls": pd.get("dark_chart_urls", {}),
            "max_pe": pd["max_pe"],
            "projection_targets": pd["projection_targets"],
            "projection_stats": pd["projection_stats"],
            "ai_analysis": pd["ai_analysis"],
            "usage_guide": pd["usage_guide"],
        }
    export_path = MARKETS_DIR / "_page_data.json"
    export_path.write_text(json.dumps(export_data, default=str), "utf-8")
    print(f"  Exported page data to {export_path}")

    # Build slug -> AI analysis snippet lookup for cross-linking
    all_ai_snippets = {}
    for slug, pd in page_data.items():
        if pd["ai_analysis"]:
            # First sentence or first 120 chars
            text = pd["ai_analysis"].split("\n\n")[0]
            if len(text) > 120:
                text = text[:117] + "..."
            all_ai_snippets[slug] = text

    # Pass 2: build HTML with cross-references
    generated = []
    for sec in SECURITY_PAGES:
        pd = page_data[sec["slug"]]

        html = build_security_page(
            pd["sec"], pd["quote"], pd["news"], pd["related"],
            pd["chart_urls"], pd["max_pe"],
            projection_targets=pd["projection_targets"],
            projection_stats=pd["projection_stats"],
            ai_analysis=pd["ai_analysis"],
            usage_guide=pd["usage_guide"],
            all_quotes=all_quotes,
            all_ai_snippets=all_ai_snippets,
            price_history=pd["price_history"],
        )

        out_path = MARKETS_DIR / f"{sec['slug']}.html"
        out_path.write_text(html, "utf-8")
        print(f"  -> Wrote {out_path} ({len(html):,} bytes, {len(pd['chart_urls'])} charts)")
        generated.append(sec["slug"])

    print(f"\n[SECURITY PAGES] Done. Generated {len(generated)} pages: {', '.join(generated)}")
    return generated


def inject_security_prices(quotes_by_symbol=None):
    """
    Update prices in existing security page HTML files without regenerating
    charts or AI content. Called by rebuild_news_home.py so prices stay in sync
    with the market bar.

    quotes_by_symbol: dict mapping EODHD symbol -> quote dict
                      (same format as get_quote_details returns).
                      If None, fetches fresh quotes.
    """
    if not MARKETS_DIR.exists():
        print("[SECURITY PRICES] No markets directory, skipping price injection")
        return

    # Fetch quotes if not provided
    if quotes_by_symbol is None:
        quotes_by_symbol = {}
        for sec in SECURITY_PAGES:
            q = get_quote_details(sec["symbol"], sec["exchange"])
            if q:
                quotes_by_symbol[sec["symbol"]] = q

    updated = 0
    for sec in SECURITY_PAGES:
        slug = sec["slug"]
        page_path = MARKETS_DIR / f"{slug}.html"
        if not page_path.exists():
            continue

        quote = quotes_by_symbol.get(sec["symbol"])
        if not quote:
            continue

        html = page_path.read_text("utf-8")

        # --- 1) Update hero price ---
        try:
            close_val = float(quote.get("close", 0))
        except (ValueError, TypeError):
            continue
        change = quote.get("change")
        change_p = quote.get("change_p")
        try:
            change = float(change) if change is not None else None
        except (ValueError, TypeError):
            change = None
        try:
            change_p = float(change_p) if change_p is not None else None
        except (ValueError, TypeError):
            change_p = None

        chg_str, direction = _fmt_change(change, change_p)

        # Replace price-main
        html = re.sub(
            r'(<span class="price-main">)[^<]*(</span>)',
            rf'\g<1>{_fmt_price(close_val)}\2',
            html
        )

        # Replace price-change
        html = re.sub(
            r"<span class=['\"]price-change [^'\"]*['\"]>[^<]*</span>",
            f"<span class='price-change {direction}'>{chg_str}</span>",
            html
        )

        # --- 2) Update quote details row ---
        detail_map = {"Open": "open", "High": "high", "Low": "low", "Prev Close": "previousClose"}
        for label, key in detail_map.items():
            val = quote.get(key)
            try:
                val = float(val) if val is not None else None
            except (ValueError, TypeError):
                val = None
            html = re.sub(
                rf'(<span class="quote-detail-label">{re.escape(label)}</span>'
                rf'<span class="quote-detail-value">)[^<]*(</span>)',
                rf'\g<1>{_fmt_price(val)}\2',
                html
            )

        # Volume
        vol = quote.get("volume")
        if vol:
            try:
                vol_fmt = f"{float(vol):,.0f}"
            except (ValueError, TypeError):
                vol_fmt = "—"
            html = re.sub(
                r'(<span class="quote-detail-label">Volume</span>'
                r'<span class="quote-detail-value">)[^<]*(</span>)',
                rf'\g<1>{vol_fmt}\2',
                html
            )

        # --- 3) Update timestamp ---
        ts = quote.get("timestamp")
        if ts:
            try:
                ts_str = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%b %d, %Y %I:%M %p UTC")
                display_symbol = sec["appserver_symbol"]
                html = re.sub(
                    r'(<div class="security-meta">)[^<]*(</div>)',
                    rf'\g<1>{display_symbol} &middot; {ts_str}\2',
                    html
                )
            except Exception:
                pass

        # --- 4) Update market bar ---
        new_bar = _build_market_bar_html(current_slug=slug, all_quotes=quotes_by_symbol)
        html = re.sub(
            r'<div class="market-bar">.*?</div>\s*</div>',
            new_bar.strip(),
            html,
            flags=re.DOTALL,
            count=1
        )

        page_path.write_text(html, "utf-8")
        updated += 1
        print(f"  [PRICES] Updated {slug}: {_fmt_price(close_val)} {chg_str}")

    print(f"[SECURITY PRICES] Updated {updated} pages")
    return updated


if __name__ == "__main__":
    generate_all_security_pages()
