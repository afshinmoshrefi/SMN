# article_prompt.py
# Multi-market, multi-template prompt generator.
# RESTORED: Full CNBC style logic, Hero Image handling, and Hook Guides.
# FIXED: Optimized source filtering for Tavily (no longer deletes valid sources).

import os
import re
import json
from typing import Dict, Any, List, Tuple
import datetime
import sys
sys.path.insert(0, '/home/flask')
import config  # exposes config.available_resources

import AI_tools 
from article_images import create_article_images
from create_report import get_chart_data, get_keyprovider_token, login_appserver
from blog_tools import get_company_name, convert_param_base64

from article_hero_image import hero_image_workflow
from get_price_eod import get_current_price, get_quote_details # retrieves the current security price from EODHD

FILENAME_RE = re.compile(r"^([A-Za-z0-9\-\._]+)_(\d{4}-\d{2}-\d{2})_(\d+)_([A-Za-z0-9\-]+)_")
VOL_TICKERS = {"VIX", "VXN", "VIX9D", "VIX3M", "RVX"} # volitility tickers write a little about what they mean to the market

CHART_WIDTH_ATTR = 1600  # real rendered px (1600x746) for all TradeWave charts -- for CLS
CHART_HEIGHT_ATTR = 746

HERO_WIDTH_ATTR = 1536   # real rendered px (1536x640) hero -- for CLS
HERO_HEIGHT_ATTR = 640

# ---------------------------------------
# ARTICLE GENERATION MODE
# ---------------------------------------
ARTICLE_MODE = 1

##############################################################
# UPDATED MASTER WHITELIST (For Tavily Context)
##############################################################
WHITELISTED_SOURCE_DOMAINS = [
    # --- TIER 1: GLOBAL NEWS WIRES ---
    "reuters.com", "bloomberg.com", "apnews.com", "cnbc.com", 
    "wsj.com", "ft.com", "barrons.com", "marketwatch.com", 
    "nytimes.com", "fortune.com", "forbes.com", "finance.yahoo.com",
    "businessinsider.com",

    # --- TIER 2: OFFICIAL & REGULATORY ---
    "sec.gov", "federalreserve.gov", "treasury.gov", 
    "bls.gov", "bea.gov", "nasdaq.com", "nyse.com", "cmegroup.com",

    # --- TIER 3: HIGH-QUALITY MARKET DATA ---
    "morningstar.com", "spglobal.com", "msci.com", 
    "fintel.io", "openinsider.com", "barchart.com", 
    "etf.com", "etfdb.com", "tradingeconomics.com",
    "fool.com", "seekingalpha.com",

    # --- TIER 4: HIGH-AUTHORITY EQUITY DATA PORTALS ---
    "marketbeat.com",       # Analyst ratings, earnings results, valuation metrics
    "gurufocus.com",        # Valuation models, ROIC/ROE, profitability, fundamentals
    "public.com",           # Brokerage-grade company profiles and quote data
    "chartmill.com",        # Technicals, analyst ratings, earnings, screeners
    "finviz.com",           # Industry-standard market screener, fundamentals, news mix
    "zacks.com"             # Earnings estimates, revisions, analyst summaries

]


# ============================================================
# Helpers
# ============================================================

def _filter_research_sources(research: Dict[str, Any],
                             symbol: str = "",
                             company: str = "") -> Dict[str, Any]:
    """
    Clean up Research JSON.
    FIXED: We trust the upstream Tavily whitelist.
    This function now only removes empty/broken entries and deduplicates.
    It does NOT strictly filter by the local whitelist (avoiding double-filtering).
    It does NOT crash if source count is low (just warns).

    NEW: When symbol/company are provided, rejects sources whose title+url
    do not reference the target security. This prevents ticker-confusion
    contamination (e.g. APLD articles leaking into ADP research).
    """
    if not isinstance(research, dict):
        return research

    sources = research.get("sources")
    if not isinstance(sources, list):
        return research

    # Build match tokens for ticker validation
    match_tokens = set()
    if symbol:
        match_tokens.add(symbol.upper())
        match_tokens.add(symbol.lower())
    if company:
        # Add full company name and first meaningful word (e.g. "Toll" from "Toll Brothers")
        match_tokens.add(company.lower())
        first_word = company.split()[0].lower() if company.split() else ""
        if first_word and len(first_word) > 2:  # skip "The", "A", etc. implicitly by length
            match_tokens.add(first_word)

    valid_sources: List[Dict[str, Any]] = []
    seen_urls = set()
    rejected_count = 0

    for src in sources:
        if not isinstance(src, dict):
            continue
        
        url = (src.get("url") or "").strip()
        title = (src.get("title") or "").strip()
        
        # 1. Basic Health Check
        if not url or not title:
            continue

        # 2. Deduplication
        if url in seen_urls:
            continue

        # 3. Ticker/company validation (when match_tokens are available)
        if match_tokens:
            haystack = f"{title} {url}".lower()
            if not any(token in haystack for token in match_tokens):
                rejected_count += 1
                print(f"[FILTER] Rejected off-target source: '{title[:80]}' (no match for {symbol}/{company})")
                continue
        
        seen_urls.add(url)
        valid_sources.append(src)

    # 4. CRITICAL FIX: Warn but do NOT crash if sources < 8
    # Tavily usually returns 5-7 high quality sources.
    if len(valid_sources) < 3:
        print(f"[WARN] Low source count: {len(valid_sources)} sources found. Proceeding anyway.")
    if rejected_count > 0:
        print(f"[FILTER] Rejected {rejected_count} off-target source(s) for {symbol}")
    
    research["sources"] = valid_sources
    return research


def _annotate_research_temporal(research: Dict[str, Any], freshness_days: int = 60) -> Dict[str, Any]:
    """
    Adds temporal metadata so the writer cannot treat old sources as current.
    - sources[].age_days: int (days old vs today)
    - sources[].fresh: bool (age_days <= freshness_days)
    - research["temporal"]: summary fields used by the prompt rules
    """
    if not isinstance(research, dict):
        return research

    sources = research.get("sources")
    if not isinstance(sources, list):
        return research

    asof = datetime.date.today()
    fresh_ids: List[int] = []
    background_ids: List[int] = []

    for src in sources:
        if not isinstance(src, dict):
            continue

        src_date_str = (src.get("date") or "").strip()
        age_days = 99999

        try:
            src_date = datetime.date.fromisoformat(src_date_str)
            age_days = (asof - src_date).days
        except Exception:
            pass

        src["age_days"] = age_days
        src["fresh"] = (age_days >= 0 and age_days <= freshness_days)

        sid = src.get("id")
        if isinstance(sid, int):
            if src["fresh"]:
                fresh_ids.append(sid)
            else:
                background_ids.append(sid)

    research["temporal"] = {
        "asof": asof.strftime("%Y-%m-%d"),
        "freshness_days": freshness_days,
        "news_hook_allowed": (len(fresh_ids) > 0),
        "fresh_source_ids": fresh_ids,
        "background_source_ids": background_ids,
    }

    return research

# ----- Presidential-cycle helpers -----
def _parse_pe_cycle(years: str) -> Tuple[str, str]:
    """
    Parse PE cycle string like 'PE2-10' into (phase, count).
    Returns ('pe2', '10') for 'PE2-10'.
    Returns ('', '') if invalid format.
    """
    y = (years or "").lower().strip()
    match = re.match(r"^(pe[0-3])-(\d+)$", y)
    if match:
        return match.group(1), match.group(2)
    return "", ""

def _pe_phase_only(years: str) -> str:
    """Extract just the phase from 'PE2-10' -> 'pe2'."""
    phase, _ = _parse_pe_cycle(years)
    return phase

def _is_pe_cycle(years: str) -> bool:
    """Check if years string is valid PE cycle format (e.g., PE2-10)."""
    y = (years or "").lower().strip()
    return re.match(r"^pe[0-3]-\d+$", y) is not None

def _pe_public_label(years: str) -> str:
    """
    Generate natural, reader-friendly label for PE cycle.
    Example: 'PE2-10' -> 'the last 10 midterm election years'
    Also accepts just a phase like 'pe2' for backward compatibility.
    """
    y = (years or "").lower().strip()
    
    # Try new format first (PE2-10)
    phase, count = _parse_pe_cycle(years)
    if phase and count:
        phase_names = {
            "pe0": "presidential election years",
            "pe1": "post-election years",
            "pe2": "midterm election years",
            "pe3": "pre-election years",
        }
        phase_name = phase_names.get(phase, "")
        if phase_name:
            return f"the last {count} {phase_name}"
    
    # Fallback for just phase (pe0, pe1, pe2, pe3)
    mapping = {
        "pe0": "presidential election years",
        "pe1": "post-election years",
        "pe2": "midterm election years",
        "pe3": "pre-election years",
    }
    return mapping.get(y, "")

def _pe_caption_label(years: str) -> str:
    """
    Generate chart caption for PE cycle.
    Example: 'PE2-10' -> 'Midterm election years (last 10)'
    Also accepts just a phase like 'pe2' for backward compatibility.
    """
    y = (years or "").lower().strip()
    
    # Try new format first (PE2-10)
    phase, count = _parse_pe_cycle(years)
    if phase and count:
        caption_bases = {
            "pe0": "Presidential election years",
            "pe1": "Post-election years",
            "pe2": "Midterm election years",
            "pe3": "Pre-election years",
        }
        base = caption_bases.get(phase, "")
        if base:
            return f"{base} (last {count})"
    
    # Fallback for just phase
    mapping = {
        "pe0": "Presidential election years",
        "pe1": "Post-election years",
        "pe2": "Midterm election years",
        "pe3": "Pre-election years",
    }
    return mapping.get(y, "")

def _pe_phase_from_year(year_int: int) -> str:
    # PE0 = years divisible by 4 (U.S. presidential election years)
    base = (year_int % 4)
    if base == 0: return "pe0"
    if base == 1: return "pe1"
    if base == 2: return "pe2"
    return "pe3"

def _current_pe_phase_from_date(date_str: str) -> str:
    try:
        yr = int(date_str.split("-")[0])
        return _pe_phase_from_year(yr)
    except Exception:
        return ""

def _is_full_year_window(date_str: str, days: str) -> bool:
    """
    Treat a pattern as a full-year buy-and-hold window when:
      - the start date is Jan 1, and
      - the configured window length is effectively a full year.
    Display-only rule; do not change underlying stats.
    """
    try:
        parts = date_str.split("-")
        if len(parts) != 3:
            return False
        month = int(parts[1])
        day = int(parts[2])
        length = int(str(days).strip())
    except Exception:
        return False

    # Jan 1 start + 'year-ish' length (covers 365/366 etc.)
    if month == 1 and day == 1 and length >= 360:
        return True
    return False

def _safe_get(d: Dict, key: str, default="") -> str:
    v = d.get(key, default)
    return "" if v is None else str(v)

def _parse_meta_from_img_paths(img_paths: List[Dict[str, str]]) -> Tuple[str, str, str, str]:
    """
    Extract symbol, date, days, years from filenames like:
    AAPL_2025-10-21_30_10_bars.jpg  or  SPY_2025-11-06_47_pe1_bars.jpg
    """
    if not img_paths:
        return "", "", "", ""
    fname = os.path.basename(img_paths[0].get("path") or img_paths[0].get("url") or "")
    m = FILENAME_RE.match(fname)
    if not m:
        return "", "", "", ""
    return m.group(1), m.group(2), m.group(3), m.group(4)  # years stays a STRING

def _variant_to_caption(symbol: str, variant: str, years: str) -> str:
    # Choose lookback label based on cycle or years
    if _is_pe_cycle(years):
        lookback_label = f"{_pe_caption_label(years)}"
    else:
        lookback_label = f"{years}-year average"

    captions = {
        "price": f"{symbol} Price Chart | Past 12 Months with 60-Day Seasonal Projection",
        "trend": f"{symbol} Seasonal Trend | {lookback_label}",
        "bars": f"{symbol} Return Bars | Per-Year Net",
        "bars_mfe": f"{symbol} Return Bars | Max Favorable Excursion",
        "bars_mae": f"{symbol} Return Bars | Max Adverse Excursion",
        "bars_mae_mfe": f"{symbol} Return Bars | Net with MFE and MAE",
        "cumulative": f"{symbol} Cumulative Return | Pattern Window",
        "stats": f"{symbol} Pattern Stats | Summary",
    }
    return captions.get(variant, f"{symbol} Chart | {variant}")

def _build_image_manifest(img_paths: List[Dict[str, str]], symbol: str, years: str) -> List[Dict[str, str]]:
    out = []
    # Prefer server-generated caption/alt already on the image dict (chartkit
    # semantics — computed from the same arrays being drawn). Fall back to the
    # legacy variant->caption map only when the dict carries neither.
    for it in img_paths:
        variant = _safe_get(it, "variant")
        legacy = _variant_to_caption(symbol, variant, years)
        cap = _safe_get(it, "caption") or legacy
        alt = _safe_get(it, "alt") or cap
        row = {
            "variant": variant,
            "url": _safe_get(it, "url"),
            "rel": _safe_get(it, "rel"),
            "path": _safe_get(it, "path"),
            "caption": cap,
            "alt": alt,
        }
        if it.get("semantics"):
            row["semantics"] = it["semantics"]
        out.append(row)
    return out

def _summarize_year_rows(chart_rows: List[Dict[str, Any]], max_rows: int = 10) -> List[Dict[str, Any]]:
    """Clean up per-year rows, keeping up to `max_rows` most recent non-zero years."""
    clean = []
    for r in chart_rows or []:
        try:
            year = int(r.get("year"))
            net_str, mfe_str, mae_str = [s.strip() for s in (r.get("pct", "0,0,0")).split(",")]
            entry_str, exit_str = [s.strip() for s in (r.get("price", "0,0")).split(",")]
        except Exception:
            continue
        if net_str == "0" and mfe_str == "0" and mae_str == "0" and entry_str == "0" and exit_str == "0":
            continue
        clean.append({
            "year": year,
            "net_return_pct": net_str,
            "mfe_pct": mfe_str,
            "mae_pct": mae_str,
            "entry_price": entry_str,
            "exit_price": exit_str,
        })
    clean.sort(key=lambda x: x["year"])
    return clean[-max_rows:]

def _format_stats(stats: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize stats fields and map Sharpe Ratio2 -> TradeWave Ratio if needed."""
    return {
        "Annualized Return": _safe_get(stats, "Annualized Return"),
        "Cumulative Return": _safe_get(stats, "Cumulative Return"),
        "Median Profit": _safe_get(stats, "Median Profit"),
        "Avg Profit": _safe_get(stats, "Avg Profit"),             # winners only
        "Avg Profit - All": _safe_get(stats, "Avg Profit - All"), # winners + losers
        "Avg Loss": _safe_get(stats, "Avg Loss"),
        "Percent Profitable": _safe_get(stats, "Percent Profitable"),
        "Num Winners": _safe_get(stats, "Num Winners"),
        "Num Losers": _safe_get(stats, "Num Losers"),
        "Std Dev": _safe_get(stats, "Std Dev"),
        "Sharpe Ratio": _safe_get(stats, "Sharpe Ratio"),
        "TradeWave Ratio": _safe_get(stats, "TradeWave Ratio") or _safe_get(stats, "Sharpe Ratio2"),
        "Trade Dir": _safe_get(stats, "Trade Dir"),
        "Trend Long": _safe_get(stats, "Trend Long"),
        "Trend Short": _safe_get(stats, "Trend Short"),
        "Trend Long1": _safe_get(stats, "Trend Long1"),
        "Trend Short1": _safe_get(stats, "Trend Short1"),
        "last_trade_date": _safe_get(stats, "last_trade_date"),
    }


# ============================================================
# Market family detection (from config.available_resources)
# ============================================================
EX2FAMILY = {
    "INDX":  "indices",
    "ETF":   "etfs",
    "COMM":  "futures_commodities",
    "FOREX": "forex",
    "GBOND": "gov_bonds",
    "CC":    "crypto",
    # everything else -> stocks (US and international)
}

def detect_market_family(resource_id: int) -> Tuple[str, str]:
    rid = str(resource_id)
    resource_name = config.available_resources.get(rid, f"Resource {rid}")
    exch = config.exchange_mapping.get(rid, "").upper().strip()
    family = EX2FAMILY.get(exch, "stocks") 
    return family, resource_name


# ============================================================
# Template registry
# ============================================================
TEMPLATES_PRESIDENTIAL: List[Dict[str, Any]] = [
    {
        "name": "Election-cycle → Price → Macro",
        "hook_focus": "pe_cycle_hook",
        "section_order": ["cycle_context", "price_context", "macro_political", "seasonal", "wrap"],
        "chart_priority": ["price", "trend", "bars_mae_mfe", "stats"],
        "closing_focus": "policy_calendar",
    },
    {
        "name": "Election-cycle → Sector/Breadth → Seasonal",
        "hook_focus": "pe_cycle_breadth",
        "section_order": ["cycle_context", "sector_breadth", "seasonal", "macro_political", "wrap"],
        "chart_priority": ["price", "trend", "stats"],
        "closing_focus": "breadth_and_policy",
    },
    {
        "name": "Election-cycle → Forward look vs current phase",
        "hook_focus": "pe_cycle_forward",
        "section_order": ["cycle_context", "forward_look", "macro_political", "seasonal", "wrap"],
        "chart_priority": ["price", "trend", "bars_mae_mfe", "stats"],
        "closing_focus": "transition_risks",
    },
]

TEMPLATES: Dict[str, List[Dict[str, Any]]] = {
    "stocks": [
        {"name": "Price → Macro → Earnings → Seasonal",
         "hook_focus": "price_52w_or_ytd",
         "section_order": ["drivers_news", "earnings", "macro", "valuation", "transition_to_tradewave", "seasonal", "wrap"],
         "chart_priority": ["price", "trend", "bars_mfe"],
         "closing_focus": "earnings_and_levels"},
        {"name": "Earnings-first → Price → Seasonal",
         "hook_focus": "earnings_window",
         "section_order": ["earnings", "price_context", "macro","transition_to_tradewave", "seasonal", "valuation", "wrap"],
         "chart_priority": ["price", "bars","trend","stats"],
         "closing_focus": "earnings_consensus"},
        {"name": "Seasonal-first → Reconcile with news",
         "hook_focus": "seasonal_headline",
         "section_order": ["seasonal", "drivers_news", "macro", "valuation", "earnings", "wrap"],
         "chart_priority": ["price","bars_mae_mfe", "trend", "stats"],
         "closing_focus": "risk_and_catalysts"},
        {"name": "Macro-first → Sector context → Seasonal",
         "hook_focus": "macro_sector",
         "section_order": ["macro", "sector_context", "price_context", "transition_to_tradewave","seasonal", "earnings", "wrap"],
         "chart_priority": ["price", "trend", "stats"],
         "closing_focus": "macro_path"},
        {"name": "Valuation angle → Price → Seasonal",
         "hook_focus": "valuation_comp",
         "section_order": ["valuation", "price_context", "drivers_news", "transition_to_tradewave","seasonal", "earnings", "wrap"],
         "chart_priority": ["price","stats", "trend", "bars"],
         "closing_focus": "levels_and_guide"},
    ],
    "etfs": [
        {"name": "Flows-first → Price → Macro → Seasonal",
         "hook_focus": "etf_flows",
         "section_order": ["flows", "price_context", "macro", "exposure", "seasonal", "wrap"],
         "chart_priority": ["price", "trend", "stats"],
         "closing_focus": "flows_and_benchmark"},
        {"name": "Benchmark linkage → Price → Seasonal",
         "hook_focus": "etf_benchmark",
         "section_order": ["exposure", "price_context", "flows", "macro", "seasonal", "wrap"],
         "chart_priority": ["price","trend", "bars", "stats"],
         "closing_focus": "benchmark_and_levels"},
        {"name": "Seasonal-first → Link to flows",
         "hook_focus": "seasonal_headline",
         "section_order": ["seasonal", "flows", "exposure", "macro", "wrap"],
         "chart_priority": ["price","bars_mae_mfe", "trend", "stats"],
         "closing_focus": "flows_and_macro"},
        {"name": "Macro-first → ETF as proxy",
         "hook_focus": "macro_proxy",
         "section_order": ["macro", "exposure", "flows", "seasonal", "wrap"],
         "chart_priority": ["price", "stats", "trend"],
         "closing_focus": "macro_path"},
        {"name": "Sector rotation angle",
         "hook_focus": "sector_rotation",
         "section_order": ["sector_context", "price_context", "flows", "seasonal", "wrap"],
         "chart_priority": [ "price","trend" ,"stats"],
         "closing_focus": "sector_and_flows"},
    ],
    "futures_commodities": [
        {"name": "Macro-first (yields, dollar) → Seasonal → S/D",
         "hook_focus": "macro_fx_yields",
         "section_order": ["macro", "seasonal", "supply_demand", "positioning", "wrap"],
         "chart_priority": ["price","trend", "bars_mae_mfe", "stats"],
         "closing_focus": "macro_and_inventory"},
        {"name": "Supply/Demand-first → Macro → Seasonal",
         "hook_focus": "supply_demand",
         "section_order": ["supply_demand", "macro", "positioning", "seasonal", "wrap"],
         "chart_priority": ["price","bars", "trend", "stats"],
         "closing_focus": "weather_opec_inventory"},
        {"name": "Positioning-first (COT) → Macro → Seasonal",
         "hook_focus": "positioning",
         "section_order": ["positioning", "macro", "supply_demand", "seasonal", "wrap"],
         "chart_priority": ["price","stats", "trend", "bars_mae_mfe"],
         "closing_focus": "positioning_and_macro"},
        {"name": "Seasonal-first → Reconcile with S/D",
         "hook_focus": "seasonal_headline",
         "section_order": ["seasonal", "supply_demand", "macro", "positioning", "wrap"],
         "chart_priority": ["price","bars_mae_mfe", "trend", "stats"],
         "closing_focus": "risk_and_catalysts"},
        {"name": "Term-structure angle (contango/backwardation)",
         "hook_focus": "term_structure",
         "section_order": ["term_structure", "macro", "supply_demand", "seasonal", "wrap"],
         "chart_priority": ["price", "trend", "stats"],
         "closing_focus": "roll_costs_and_reports"},
    ],
    "gov_bonds": [
        {"name": "Yield move → Auction → Fed path → Seasonal",
         "hook_focus": "yields_auction",
         "section_order": ["yields", "auction", "fed_path", "inflation_data", "seasonal", "wrap"],
         "chart_priority": ["price", "trend", "stats"],
         "closing_focus": "cpi_jobs_fomc"},
        {"name": "Fed expectations-first → Inflation → Seasonal",
         "hook_focus": "fed_expectations",
         "section_order": ["fed_path", "inflation_data", "yields", "seasonal", "wrap"],
         "chart_priority": ["price","trend", "stats"],
         "closing_focus": "policy_path"},
        {"name": "Inflation-first → Yields → Auction",
         "hook_focus": "inflation_first",
         "section_order": ["inflation_data", "yields", "auction", "seasonal", "wrap"],
         "chart_priority": ["price","trend", "bars", "stats"],
         "closing_focus": "data_watch"},
        {"name": "Seasonal-first → Reconcile with Fed",
         "hook_focus": "seasonal_headline",
         "section_order": ["seasonal", "fed_path", "inflation_data", "wrap"],
         "chart_priority": ["price","bars_mae_mfe", "trend", "stats"],
         "closing_focus": "fed_minutes"},
        {"name": "Curve angle (2s10s/5s30s) → Seasonal",
         "hook_focus": "curve_shape",
         "section_order": ["curve", "fed_path", "inflation_data", "seasonal", "wrap"],
         "chart_priority": ["price", "trend", "stats"],
         "closing_focus": "auctions_and_curve"},
    ],
    "forex": [
        {"name": "Rate-differential first → Price levels → Seasonal",
            "hook_focus": "rate_diff",
            "section_order": ["macro_divergence", "price_context", "seasonal", "wrap"],
            "chart_priority": ["price", "trend", "stats"],
            "closing_focus": "cb_meetings"},
        {"name": "Macro divergence → Technicals → Seasonal",
            "hook_focus": "macro_divergence",
            "section_order": ["macro_divergence", "technicals", "seasonal", "wrap"],
            "chart_priority": ["price","trend", "bars", "stats"],
            "closing_focus": "data_and_cb"},
        {"name": "Seasonal-first → Reconcile with CB path",
            "hook_focus": "seasonal_headline",
            "section_order": ["seasonal", "macro_divergence", "technicals", "wrap"],
            "chart_priority": ["price","bars_mae_mfe", "trend", "stats"],
            "closing_focus": "risk_and_cb"},
        {"name": "Flow/positioning → Macro → Seasonal",
            "hook_focus": "positioning",
            "section_order": ["positioning", "macro_divergence", "seasonal", "wrap"],
            "chart_priority": ["price","stats", "trend"],
            "closing_focus": "cbcalendar"},
        {"name": "Technical levels-first → Macro → Seasonal",
            "hook_focus": "technicals",
            "section_order": ["technicals", "macro_divergence", "seasonal", "wrap"],
            "chart_priority": ["price", "trend", "stats"],
            "closing_focus": "levels_and_events"},
    ],
    "indices": [
        {"name": "Price vs record → Sector leadership → Seasonal",
         "hook_focus": "index_price_record",
         "section_order": ["sector_breadth", "macro", "etf_flows", "seasonal", "wrap"],
         "chart_priority": ["price", "trend", "stats"],
         "closing_focus": "breadth_and_earnings"},
        {"name": "Macro-first → Tech/energy leadership → Seasonal",
         "hook_focus": "macro_sector",
         "section_order": ["macro", "sector_breadth", "etf_flows", "seasonal", "wrap"],
         "chart_priority": [ "price","trend", "stats"],
         "closing_focus": "macro_and_breadth"},
        {"name": "ETF flows-first (SPY/QQQ/IWM) → Seasonal",
         "hook_focus": "etf_flows",
         "section_order": ["etf_flows", "sector_breadth", "macro", "seasonal", "wrap"],
         "chart_priority": ["price", "stats", "trend"],
         "closing_focus": "flows_and_macro"},
        {"name": "Seasonal-first → Reconcile with breadth",
         "hook_focus": "seasonal_headline",
         "section_order": ["seasonal", "sector_breadth", "macro", "wrap"],
         "chart_priority": ["price","bars_mae_mfe", "trend", "stats"],
         "closing_focus": "risk_and_earnings"},
        {"name": "Volatility angle (VIX link) → Seasonal",
         "hook_focus": "volatility",
         "section_order": ["volatility", "macro", "sector_breadth", "seasonal", "wrap"],
         "chart_priority": ["price", "trend", "stats"],
         "closing_focus": "eventscalendar"},
    ],
    "crypto": [
        {"name": "ETF flows + threshold levels → Seasonal",
         "hook_focus": "crypto_etf_flows",
         "section_order": ["etf_flows", "price_context", "on_chain", "seasonal", "wrap"],
         "chart_priority": ["price", "trend", "stats"],
         "closing_focus": "etf_and_levels"},
        {"name": "Macro liquidity → Dollar/Yields → Seasonal",
         "hook_focus": "macro_liquidity",
         "section_order": ["macro", "price_context", "on_chain", "seasonal", "wrap"],
         "chart_priority": ["price","trend", "bars", "stats"],
         "closing_focus": "liquidity_and_flows"},
        {"name": "On-chain first → Seasonal",
         "hook_focus": "on_chain",
         "section_order": ["on_chain", "price_context", "seasonal", "wrap"],
         "chart_priority": ["price","stats", "trend", "bars_mae_mfe"],
         "closing_focus": "network_and_flows"},
        {"name": "Seasonal-first → Reconcile with flows",
         "hook_focus": "seasonal_headline",
         "section_order": ["seasonal", "on_chain", "macro", "wrap"],
         "chart_priority": ["price","bars_mae_mfe", "trend", "stats"],
         "closing_focus": "risk_and_events"},
        {"name": "Halving/cycle angle → Seasonal",
         "hook_focus": "cycle_halving",
         "section_order": ["cycle", "price_context", "seasonal", "wrap"],
         "chart_priority": ["price", "trend", "stats"],
         "closing_focus": "etf_and_policy"},
    ],
}


# Guidance text blocks that swap based on market family
MARKET_RESEARCH_BULLETS = {
    "stocks": [
        "Include: current price and percent move (today), notable news or catalysts, next earnings date and any guidance context, analyst rating/target changes, sector and index context, one simple valuation marker (P/E vs peers, dividend yield, or price-to-book).",
        "Add one consensus waypoint: what the street expects for the upcoming quarter or year if available.",
        "IMPORTANT: For individual stocks, do NOT discuss general market index ETF flows (like SPY or QQQ). Focus only on news and drivers specific to the company itself."
    ],
    "etfs": [
        "Include: ETF price and percent move (today), net inflows/outflows and AUM if available, what the ETF tracks (benchmark and method), sector/commodity exposure, relevant macro themes, and tracking considerations (fees, roll, tracking error).",
        "Add a benchmark waypoint: how the ETF compares to its benchmark over the recent period.",
    ],
    "futures_commodities": [
        "Include: front-month contract move and context, macro drivers (yields, dollar, inflation), supply/demand details (OPEC reports, harvests, inventories), and positioning (CFTC COT) if available.",
        "Add one logistics waypoint: inventory/stockpile trend or shipping/weather impact, where relevant.",
    ],
    "gov_bonds": [
        "Include: yield move (today), auction outcomes (bid-to-cover, indirect/direct), Fed expectations (CME FedWatch), key inflation data (CPI/PCE), and curve context.",
        "Add one consensus waypoint: current market-implied policy path or term premium commentary.",
    ],
    "forex": [
        "Include: pair move and daily range versus recent levels, macro divergence across central banks, notable data surprises, and key technical levels (support/resistance).",
        "Add one positioning waypoint: speculative or real-money flow color if available.",
    ],
    "indices": [
        "Include: index level versus record highs/lows or YTD, sector leadership/laggards, breadth measures (advance/decline, new highs/lows), macro drivers, and ETF flows (SPY/QQQ/IWM).",
        "Add one breadth waypoint: percent of members above 50/200-day moving averages if available.",
    ],
    "crypto": [
        "Include: coin price and percent move (today), ETF inflows/outflows (for spot ETFs), on-chain metrics (addresses, fees, hash rate or staking), and regulatory backdrop.",
        "Add one cycle waypoint: halving or cycle phase context where relevant.",
    ],
}

# Hook phrase guidance depending on variation focus
HOOK_GUIDE = {
    "price_52w_or_ytd": "Start with today’s price and either distance to 52-week high/low or YTD performance.",
    "earnings_window": "Lead with the timing of the next earnings window and today’s price context.",
    "seasonal_headline": "Lead with the seasonal window result (e.g., Percent Profitable, Num Winners/Losers, Avg Profit) then connect to today’s setup.",
    "macro_sector": "Lead with macro tone and sector context before zooming into the instrument.",
    "valuation_comp": "Lead with a simple valuation marker against peers, then connect to price and catalysts.",

    "etf_flows": "Lead with latest ETF flows and today’s price context.",
    "etf_benchmark": "Lead with the ETF’s linkage to its benchmark and today’s price context.",
    "macro_proxy": "Lead with macro context, framing the ETF as a proxy for exposure.",
    "sector_rotation": "Lead with sector rotation narrative and how the ETF fits.",

    "macro_fx_yields": "Lead with dollar/yields/inflation macro impulse tied to the contract’s move.",
    "supply_demand": "Lead with supply/demand or inventory conditions for the commodity.",
    "positioning": "Lead with CFTC positioning or comparable positioning insight.",
    "term_structure": "Lead with term structure (contango/backwardation) and roll dynamics.",

    "yields_auction": "Lead with today’s yield move and a recent auction readout.",
    "fed_expectations": "Lead with the policy path and market-implied odds.",
    "inflation_first": "Lead with inflation print impact and yields reaction.",
    "curve_shape": "Lead with curve shape (2s10s/5s30s) and implications.",

    "rate_diff": "Lead with interest-rate differentials and today’s move in the pair.",
    "macro_divergence": "Lead with policy divergence and data differentials.",
    "technicals": "Lead with key technical levels and recent tests.",

    "index_price_record": "Lead with the index against records/YTD with breadth flavor.",
    "volatility": "Lead with volatility regime or VIX linkage.",

    "crypto_etf_flows": "Lead with spot ETF flows and threshold levels.",
    "macro_liquidity": "Lead with liquidity conditions and dollar/yields backdrop.",
    "on_chain": "Lead with an on-chain datapoint and tie to price.",
    "cycle_halving": "Lead with the halving/cycle frame and current placement.",
}


# ============================================================
# Prompt builder (market-aware + variant-aware)
# ============================================================

def _avg_profit_rules(stats: Dict[str, Any]):
    def _to_int_or_none(s):
        try:
            return int(str(s).strip().replace(",", ""))
        except Exception:
            return None

    losers = _to_int_or_none(stats.get("Num Losers"))
    pp_raw = str(stats.get("Percent Profitable", "")).replace("%", "").strip()
    try:
        pp_val = float(pp_raw)
    except Exception:
        pp_val = None

    winners_all = (losers == 0) or (pp_val is not None and pp_val >= 100.0)

    if winners_all:
        key_stats_rows_instruction = (
            "One <aside class=\"key-stats\"> summarizing key TradeWave stats with reader-facing rows in this order: "
            "Trade Direction, Percent Profitable, Num Winners, Num Losers, Avg Profit, TradeWave Ratio, Sharpe Ratio. "
            "Use the label “Trade Direction” (not “Trade Dir”) and populate it from the Trade Dir field in the Data.stats object. "
            "Omit “Avg Profit - All” everywhere, since all years were winners in this sample."
        )
        avg_profit_rule_line = (
            "- When Percent Profitable is 100% (Num Losers = 0), do not mention “Avg Profit - All” anywhere. "
            "Use Avg Profit only."
        )
        avg_profit_note_html = (
            '<p style="margin-top:8px;font-size:.9rem;color:#222;">'
            'All years in this lookback were winners. “Avg Profit - All” equals “Avg Profit,” so only “Avg Profit” is shown.'
            '</p>'
        )
    else:
        key_stats_rows_instruction = (
            "One <aside class=\"key-stats\"> summarizing key TradeWave stats with reader-facing rows in this order: "
            "Trade Direction, Percent Profitable, Num Winners, Num Losers, Avg Profit, Avg Profit - All, TradeWave Ratio, Sharpe Ratio. "
            "Use the label “Trade Direction” (not “Trade Dir”) and populate it from the Trade Dir field in the Data.stats object. "
            "Include both Avg Profit and Avg Profit - All because there are losing years in the sample."
        )
        avg_profit_rule_line = (
            "- Clarify once: “Avg Profit reflects winners only, while Avg Profit - All includes every year in the sample.”"
        )
        avg_profit_note_html = (
            '<p style="margin-top:8px;font-size:.9rem;color:#222;">'
            'Avg Profit reflects winners only, while Avg Profit - All includes every year in the sample.'
            '</p>'
        )
    return key_stats_rows_instruction, avg_profit_rule_line, avg_profit_note_html

def _market_specific_sections(market_family: str, template: Dict[str, Any], cta_link: str) -> str:
    hook = HOOK_GUIDE.get(template["hook_focus"], "Start with today’s price context.")
    research_bullets = MARKET_RESEARCH_BULLETS.get(market_family, MARKET_RESEARCH_BULLETS["stocks"])
    chart_priority = ", ".join(template.get("chart_priority", []))
    section_order = " → ".join(template.get("section_order", []))
    closing_focus = template.get("closing_focus", "standard_watchlist")

    return f"""
Template focus: {template["name"]}
Hook angle: {hook}
Section order: {section_order}
Preferred chart emphasis: {chart_priority}
What-to-watch emphasis: {closing_focus}

Web research (required for this market family):
- {research_bullets[0]}
- {research_bullets[1]}


"""

_PERP_RESEARCH_SYSTEM = (
    "You are a meticulous research assistant. "
    "Your ONLY job is to return a single JSON object with structured research. "
    "Follow the JSON format exactly and do not add any extra text."
)

# ----- Presidential-cycle helpers (additions) -----
def _month_from_date(date_str: str) -> int:
    try:
        return int(date_str.split("-")[1])
    except Exception:
        return 0  # caller can treat 0 as "unknown"

def _pe_calendar_subphase(date_str: str) -> str:
    """
    Return 'early', 'mid', or 'late' for the calendar position inside a PE year,
    based on the month of the provided date. Used only for prose guidance.
    """
    m = _month_from_date(date_str)
    if m == 0:
        return ""
    if 1 <= m <= 4:
        return "early"
    if 5 <= m <= 8:
        return "mid"
    return "late"  # Sep-Dec


def _presidential_cycle_sections(template: Dict[str, Any], pattern_phase: str, current_phase: str, subphase: str) -> str:
    """
    Writes guidance for PE-mode pieces. Enforces:
      - If we're in the latter months of a PE year, say 'concluding', not 'entering'.
      - Compare primarily to the adjacent phase (the upcoming one).
      - Use TradeWave data for hit-rate/strength claims; avoid generic phase tropes.
    """
    # Normalize phases: extract just 'pe2' from 'PE2-10' if needed
    if _is_pe_cycle(pattern_phase):
        pattern_phase_normalized = _pe_phase_only(pattern_phase)
    else:
        pattern_phase_normalized = (pattern_phase or "").lower().strip()
    
    if _is_pe_cycle(current_phase):
        current_phase_normalized = _pe_phase_only(current_phase)
    else:
        current_phase_normalized = (current_phase or "").lower().strip()
    
    hook_map = {
        "pe_cycle_hook": "Lead with the historical behavior of this election-cycle phase and today's price context.",
        "pe_cycle_breadth": "Lead with the election-cycle phase and how sector leadership/breadth typically behave in this phase.",
        "pe_cycle_forward": "Lead with this phase's average path and explicitly compare to the CURRENT phase to set expectations ahead.",
    }
    hook = hook_map.get(template.get("hook_focus"), "Lead with election-cycle context.")
    section_order = " -> ".join(template.get("section_order", []))
    chart_priority = ", ".join(template.get("chart_priority", []))
    closing_focus = template.get("closing_focus", "policy_calendar")

    phrasing_rule = ""
    if pattern_phase_normalized and current_phase_normalized and pattern_phase_normalized == current_phase_normalized:
        if subphase == "late":
            phrasing_rule = '- Language: write "concluding {ph}" (or "wrapping up {ph}"), not "entering {ph}."'.format(
                ph=_phase_label(pattern_phase_normalized)
            )
        elif subphase == "early":
            phrasing_rule = '- Language: write "entering {ph}" only if the current month is in the early part of that year.'.format(
                ph=_phase_label(pattern_phase_normalized)
            )
        else:
            phrasing_rule = '- Language: avoid "entering {ph}"; use neutral phrasing like "in {ph}."'.format(
                ph=_phase_label(pattern_phase_normalized)
            )

    upcoming = _next_phase(current_phase_normalized)

    return f"""
Template focus: {template["name"]}
Hook angle: {hook}
Section order: {section_order}
Preferred chart emphasis: {chart_priority}
What-to-watch emphasis: {closing_focus}

Election-cycle guidance (must follow):
- State both pattern phase and calendar phase explicitly (e.g., "Pattern phase = {_phase_label(pattern_phase_normalized)}, Calendar phase = {_phase_label(current_phase_normalized)} ({subphase or 'unspecified'} part of the year)").
{phrasing_rule}
- When contrasting phases, focus first on the adjacent transition ({_phase_label(current_phase_normalized)} to {upcoming}) and its near-term implications for this window.
- Use TradeWave's phase/window data for any statements about typical strength or persistence; avoid generic claims that are not supported by the provided Data.

Web research (required for election-cycle pieces):
- Summarize credible context for the policy backdrop in this phase using reputable sources (no forums/SEO scrapes).
- Keep macro commentary anchored to current catalysts (rates, inflation, earnings breadth) rather than partisan narratives.
"""



def _phase_label(phase: str) -> str:
    """
    Map internal phase ids ('pe0','pe1','pe2','pe3') to human-readable labels.
    Returns '' for unknown.
    """
    return {
        "pe0": "presidential election year",
        "pe1": "post-election year",
        "pe2": "midterm election year",
        "pe3": "pre-election year",
    }.get((phase or "").lower().strip(), "")



def _next_phase(phase: str) -> str:
    """
    Given an internal phase id ('pe0'..'pe3'), return the next phase
    as a human-readable label.
    """
    return {
        "pe0": "post-election year",
        "pe1": "midterm election year",
        "pe2": "pre-election year",
        "pe3": "presidential election year",
    }.get((phase or "").lower().strip(), "")



def build_article_context(
    symbol: str,
    date: str,
    days: str,
    years: str,
    cdata: Dict[str, Any],
    img_paths: List[Dict[str, str]],
    company: str,
    resource_id: int,
    variant_index: int = 1,
    byline: str = "",
    ai_disclosure: bool = False,
    hero_html: str = "",
) -> Dict[str, Any]:
    """
    Build the shared context needed to write an article.
    No AI calls. This is the common core for manual UI and future API workflows.
    """

    # Today is for article context, pattern_start is the seasonal window start
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    # ---- Compute future-window flag (only for far-ahead regimes) ----
    pattern_date = datetime.date.fromisoformat(date)
    today = datetime.date.fromisoformat(today_str)
    delta_days = (pattern_date - today).days

    # Only treat as "future regime" if the window starts well ahead (e.g. > 30 days)
    # This keeps "now" and "next week" behavior exactly as before.
    is_future_window = delta_days > 30

    # ---- Parse inputs & normalize data ----
    rows = _summarize_year_rows(cdata.get("ChartData4") or [], 10)
    stats = _format_stats(cdata.get("stats") or {})
    images = _build_image_manifest(img_paths, symbol, years)

    # ---- Market context from TradeWave daily history (instrument-level, not pattern-only) ----
    # Get current price from EODHD (authoritative source)
    exchange = config.exchange_mapping.get(str(resource_id), "US")
    eodhd_price = get_current_price(symbol, exchange)
    eodhd_quote = get_quote_details(symbol, exchange, use_realtime=False)
    
    market_context = {
        "current_price": eodhd_price,  # <--- NEW: Live price from EODHD
        "eodhd_quote": eodhd_quote,     # <--- NEW: Full quote details
        "one_month_return": cdata.get("stats", {}).get("1M Return"),
        "fifty_two_week_high": cdata.get("stats", {}).get("52W High"),
        "fifty_two_week_low": cdata.get("stats", {}).get("52W Low"),
        "avg_volume_20d": cdata.get("stats", {}).get("Avg Volume 20d"),
        "sma_50": cdata.get("stats", {}).get("SMA 50"),
    }

    # ---- Build direct TradeWave pattern link to open the pattern in tradewave ----
    param = convert_param_base64(resource_id, symbol, date, days, years)
    pattern_url = f"{config.domain_root}wave-viewer?o={param}"  # opens this exact pattern
    # utm = f"&utm_source=SMN&utm_medium=article&utm_campaign=pattern_deeplink&utm_content={symbol}-{date}-{days}-{years}"
    cta_link = f"{pattern_url}"

    # ---- PE detection & display phrase ----
    is_pe = _is_pe_cycle(years)
    # current_phase = _current_pe_phase_from_date(date) if is_pe else ""
    # subphase = _pe_calendar_subphase(date) if is_pe else ""
    pattern_phase = years if is_pe else ""
    # Calendar phase is based on "today", not the window start
    calendar_phase = _current_pe_phase_from_date(today_str) if is_pe else ""
    calendar_subphase = _pe_calendar_subphase(today_str) if is_pe else ""

    # ---- Full-year / buy-and-hold detection (display only) ----
    is_full_year = _is_full_year_window(date, days)

    # ---- Market detection & template selection ----
    if is_pe:
        variants = TEMPLATES_PRESIDENTIAL
        idx = max(1, min(len(variants), int(variant_index))) - 1
        template = variants[idx]
        template_copy = template.copy() # Avoid modifying the global template
        market_family, resource_name = detect_market_family(resource_id)

        if is_future_window:
            # Special NVDA / BA style piece:
            # article written in calendar_phase, but window grouped by pattern_phase
            market_block = _presidential_cycle_sections(
                template_copy,
                pattern_phase=pattern_phase,
                current_phase=calendar_phase,
                subphase=calendar_subphase,
            )
        else:
            # Old behavior, untouched
            market_block = _presidential_cycle_sections(
                template_copy,
                pattern_phase=pattern_phase,
                current_phase=pattern_phase,
                subphase=_pe_calendar_subphase(date),
            )
        template = template_copy # Ensure we use the copy
    else:
        market_family, resource_name = detect_market_family(resource_id)
        variants = TEMPLATES.get(market_family, TEMPLATES["stocks"])
        idx = max(1, min(len(variants), int(variant_index))) - 1
        template = variants[idx]
        template_copy = template.copy()
        market_block = _market_specific_sections(market_family, template_copy, cta_link)
        template = template_copy

    # ---- Conditional rules for Avg Profit vs Avg Profit - All ----
    key_stats_rows_instruction, avg_profit_rule_line, avg_profit_note_html = _avg_profit_rules(stats)

    # ---- Image guidance (goes into the Data block; not echoed) ----
    image_guidance = {
        "bars": "Per-year net returns for the window. Use for quick win/loss by year.",
        "bars_mfe": "Maximum favorable excursion per year. Use to illustrate upside potential.",
        "bars_mae": "Maximum adverse excursion per year. Use to show drawdowns and risk.",
        "bars_mae_mfe": "Stacked net/MFE/MAE. Use to communicate upside and drawdown ranges together.",
        "trend": "Historical seasonal average across lookback years for THIS window (NOT a current price chart). Use only in the seasonal section.",
        "price": "Recent actual price path (30-90 days). Place in 'Price and near-term drivers' immediately after the first paragraph that states price context.",
        "cumulative": "Average cumulative return within the window. Shows typical accrual path day by day.",
        "stats": "Summary metrics. Use to present Percent Profitable, Avg Profit, Avg Profit - All, TradeWave Ratio, Sharpe ratio.",
    }

    # ---- Compact data blob the model reads from ----
    data_block = {
        "meta": {
            "resource_id": resource_id,
            "resource_name": resource_name,
            "market_family": market_family,
            "symbol": symbol,
            "security_name": company,
            "pattern_start_date": date,
            "pattern_window_days": days,
            "lookback_years": years,
            "variant_name": template["name"],
            "pe_subphase": calendar_subphase if is_pe else "",
            "pattern_phase": pattern_phase,
            "calendar_today": today_str,
            "calendar_phase": calendar_phase,
            "is_future_window": is_future_window,
        },
        "stats": stats,
        "per_year": rows,
        "images": images,
        "image_guidance": image_guidance,
        "template": template,
        "market_context": market_context,  # <--- NEW
    }

    # ---- Meta strip fragments ----
    byline_html = f"<span>{byline}</span>" if byline else ""
    ai_html = (
        '<span>AI-assisted: Draft generated with TradeWave analytics and reviewed before publication.</span>'
        if ai_disclosure else ""
    )
    meta_market_html = f"<span>Resource: {resource_name}</span>"

    if is_pe:
        pe_public = _pe_public_label(years)
        lookback_phrase = f"Cycle: {pe_public}"
    else:
        lookback_phrase = f"Lookback: {years} years"

    if is_full_year:
        window_phrase = "Window: full-year buy-and-hold period"
    else:
        window_phrase = f"Window: {days} calendar days"

    # ---- Seasonal paragraph wording ----
    pe_note = ""
    if is_pe:
        pe_note = (
            "- Write a brief, plain-English note explaining why grouping by the presidential election cycle "
            "(election year / year after / midterm / year before) is relevant to today's setup, using one fresh angle "
            "(policy calendar, fiscal stance, regulation, liquidity, or earnings breadth). Avoid boilerplate; vary cadence "
            "and emphasis. Place this note at the start of the seasonal section.\n"
        )
        # _next_phase now returns human-readable text like "post-election year"
        upcoming_public = _next_phase(calendar_phase)
        entering_or_concluding = (
            "concluding" if calendar_subphase == "late"
            else ("entering" if calendar_subphase == "early" else "in")
        )
        current_phase_public = _pe_public_label(calendar_phase)
        seasonal_integration = (
            f"- Explain the presidential election cycle window for {{company}} ({{symbol}}) starting {date} "
            f"in plain English as \"{pe_public}.\" Make clear that results aggregate across all historical years "
            f"that match this phase (election-cycle grouping), not consecutive calendar years. "
            f"State direction: {stats.get('Trade Dir','')}. "
            f"Write that the market is {entering_or_concluding} {current_phase_public} given the current month; "
            f"then emphasize the near-term transition to the {upcoming_public} "
            f"and what that historically implies for this specific window using the provided TradeWave data."
        )
    else:
        if is_full_year:
            seasonal_integration = (
                f"- Explain the historical window for {{company}} ({{symbol}}) starting {date} as a full-year buy-and-hold period "
                f"based on {years} years of data, instead of calling it a '{days}-day window' or similar. "
                "Use phrases like \"full-year buy-and-hold period\" or \"full calendar-year holding window\" in prose. "
                "Summarize TradeWave stats concisely and focus on what they imply for current market context."
            )
        else:
            seasonal_integration = (
                f"- Explain the historical window for {{company}} ({{symbol}}) starting {date} covering {years} years of data. "
                f"Summarize TradeWave stats concisely and focus on what they imply for current market context."
            )

    return {
        "rows": rows,
        "stats": stats,
        "images": images,
        "image_guidance": image_guidance,
        "template": template,
        "market_family": market_family,
        "resource_name": resource_name,
        "is_pe": is_pe,
        "current_phase": calendar_phase,
        "subphase": calendar_subphase,
        "is_full_year": is_full_year,
        "market_block": market_block,
        "key_stats_rows_instruction": key_stats_rows_instruction,
        "avg_profit_rule_line": avg_profit_rule_line,
        "avg_profit_note_html": avg_profit_note_html,
        "hero_html": hero_html,
        "byline_html": byline_html,
        "ai_html": ai_html,
        "meta_market_html": meta_market_html,
        "window_phrase": window_phrase,
        "lookback_phrase": lookback_phrase,
        "pe_note": pe_note,
        "seasonal_integration": seasonal_integration,
        "cta_link": cta_link,
        "pattern_url": pattern_url,
        "data_block": data_block,
        "market_context": market_context,  # <--- NEW
    }
#--------------------------------------------------------------------------------------------
def _web_research_block_from_json() -> str:
    """
    Guidance when precomputed Research JSON is provided (Mode 2/3).
    The model must NOT browse and must treat Research as the only external context.
    """
    return """
Web research (from JSON – do NOT browse):
- Do NOT perform any web browsing or live search.
- All external facts, numbers, dates, and URLs must come from the Research JSON object provided later in this prompt.
- Use the Research.price, Research.earnings, Research.analyst, Research.catalysts, Research.macro,
  Research.sector, Research.special_signals, Research.equity_etf_index, Research.futures_commodities,
  and Research.sources fields as your only external context.
- When you need a news or macro fact, select the best-matching entry from Research.catalysts, Research.macro, or Research.sector.
- When building the “Sources” ordered list, you MUST select 10–14 entries from Research.sources.
  You may shorten titles slightly, but you must NOT invent new URLs, sources, or dates.
- You must NOT add any new sources that are not present in Research.sources.
- Construct inline <sup>[n]</sup> citations so that each cited paragraph maps to one of these sources.
""".strip()
#--------------------------------------------------------------------------------------------
def _web_research_block_inline() -> str:
    return """
        Web research (required):
        - Browsing/search is required. Use only reputable primary or well-established secondary sources.
        - Whitelist (prioritize): company IR & SEC filings; Reuters; Bloomberg; Associated Press; CNBC; Wall Street Journal; Financial Times; MarketWatch; Nasdaq; Yahoo Finance (stats/targets); Morningstar; S&P Global; FactSet; Refinitiv; official .gov/.edu sites (BLS, Fed).
        - Blacklist (do NOT use): TradingView news/statistics/estimates pages, StockScan, SEO blogs, forums/wikis, unattributed aggregators.
        - Recency: for news/price context/targets use ≤30 days; for fundamentals use ≤one quarter unless clearly evergreen.
        - Minimum sources: the “Sources” list MUST contain 10-14 distinct reputable items from the whitelist or primary sources. If you have fewer than 10, continue researching until you reach 10-14. Do not return the article until this condition is met.
        - Coverage mix (aim to include at least one from each bucket, when relevant): a real-time market data source (Reuters/Bloomberg), a major wire (AP/Reuters), an official calendar or release (BLS/Fed/SEC), a valuation or market stats page (WSJ Market Data/Nasdaq/Yahoo/Morningstar), and at least one high-quality analysis outlet (FT/WSJ/CNBC/MarketWatch). Prefer unique domains.
        - Inline citations: EVERY paragraph that contains any external number, date, definition, or claim must include a matching <sup>[n]</sup> that resolves to the ordered Sources list. Number citations in order of first appearance.
        - Sources list formatting: each source MUST be a hyperlink in this form:<li><a href="URL">Publisher - Title</a></li>. Do not print raw URLs anywhere.
        - When discussing earnings, always anchor to the NEXT scheduled earnings report date from reputable sources.
        Do not skip ahead to distant future earnings dates just because the seasonal window lies in the next year.
        """.strip()
#--------------------------------------------------------------------------------------------

# ============================================================
# create_article_prompt
# ============================================================
def create_article_prompt(
    symbol: str,
    date: str,
    days: str,
    years: str,
    cdata: Dict[str, Any],
    img_paths: List[Dict[str, str]],
    company: str,
    resource_id: int,
    variant_index: int = 1,
    byline: str = "",
    ai_disclosure: bool = False,
    hero_html: str = "",
    mode: str = "0",                 # "0", "1", or "2"
    research: Dict[str, Any] = None  # only used if mode=="1" or "2"
) -> str:
    """
    Builds the HTML-generation prompt with strict output and sourcing controls.

    Modes:
      0 = Local writer model with browsing performs both research and article generation
          (no Research JSON; browsing allowed)
      1 = External research agent (Perplexity) performs research; local writer model generates article
          (Research JSON required; no browsing)
      2 = External research agent (Perplexity) performs research; server-side writer model generates article
          (Research JSON required; no browsing)
    """

    # ---- MODE HANDLING (fail-fast, deterministic) ----
    if mode == "0" :

        sources_requirement_line = "6) The “Sources” list MUST contain 10-14 distinct reputable items."
        sources_quality_check_line = "- Verify there are 10-14 sources in the ordered list."

        # Browsing is allowed; research JSON must not be used.
        if research is not None:
            # You can relax this to a warning if you want, but fail-fast is safer.
            raise ValueError("Mode 0 must not receive research JSON. Omit the 'research' argument.")
        browsing_allowed = True
        research_json_str = "{}"
        web_research_block = _web_research_block_inline()
        research_header_line = (
            "Research (no precomputed research JSON is provided; you must perform your own "
            "web research based on the Web research block above):"
        )
    # filter out the banned sources in case perplexity uses them
    elif mode == "1" or mode == "2":

        sources_requirement_line = (
            "6) The “Sources” list MUST contain exactly the distinct entries provided in Research.sources. "
            "If Research.sources contains fewer than 10 items, output fewer than 10. "
            "Do not add padding sources. Do not invent new URLs, sources, or dates."
        )
        sources_quality_check_line = (
            "- Verify every source in the Sources list comes from Research.sources. "
            "If Research.sources contains fewer than 10 sources, that is acceptable. Do not pad."
        )

        # Research-only, write-only modes. No browsing.
        if research is None:
            raise ValueError(f"Mode {mode} requires Research JSON but 'research' argument was None.")

        # Minimal hard filter: strip blacklisted domains from the sources list.
        # This keeps TradingView and similar junk out, even if Perplexity ignores instructions.
        research = _filter_research_sources(research, symbol=symbol, company=company)
        research = _annotate_research_temporal(research, freshness_days=60)

        # Override Tavily price with EODHD price (authoritative source)
        exchange = config.exchange_mapping.get(str(resource_id), "US")
        eodhd_quote = get_quote_details(symbol, exchange, use_realtime=False)
        if eodhd_quote and eodhd_quote.get("close"):
            if "price" not in research or not isinstance(research.get("price"), dict):
                research["price"] = {}
            research["price"]["last"] = eodhd_quote["close"]
            research["price"]["change_percent"] = eodhd_quote.get("change_p")
            # Add note that price is from EODHD
            research["price"]["source"] = "EODHD"
            print(f"[EODHD] Overriding research price with EODHD: {symbol}.{exchange} = ${eodhd_quote['close']}")

        browsing_allowed = False
        research_json_str = json.dumps(research, ensure_ascii=False)
        web_research_block = _web_research_block_from_json()
        research_header_line = (
            "Research (verbatim Research JSON – do not alter values; you must NOT perform any web "
            "browsing and must treat this JSON as your only external factual context):"
        )
    else:
        raise ValueError(
            f"Invalid mode: {mode}. Expected '0' (local with browsing), '1' (Perplexity + local writer), or '2' (Perplexity + server writer)."
        )

    # ---- Build shared context (unchanged core) ----
    ctx = build_article_context(
        symbol=symbol,
        date=date,
        days=days,
        years=years,
        cdata=cdata,
        img_paths=img_paths,
        company=company,
        resource_id=resource_id,
        variant_index=variant_index,
        byline=byline,
        ai_disclosure=ai_disclosure,
        hero_html=hero_html,
    )

    data_block = ctx["data_block"]
    market_block = ctx["market_block"]
    stats = ctx["stats"]
    trade_dir_value = str(stats.get("Trade Dir", "")).strip()
    trade_dir_upper = trade_dir_value.upper() if trade_dir_value else ""

    # ---- Direction-aware guidance (unchanged logic, just encapsulated) ----
    directional_lines: List[str] = []
    directional_lines.append("Direction-aware interpretation of the TradeWave seasonal pattern (must follow):")
    directional_lines.append("")
    directional_lines.append(
        "- The Trade Direction for this pattern is provided in the Data.stats['Trade Dir'] field and in the key-stats box "
        "as “Trade Direction: Long” or “Trade Direction: Short.” You must align your narrative with that direction."
    )
    directional_lines.append("")
    directional_lines.append('- For LONG patterns (Trade Direction = "Long"):')
    directional_lines.append(
        "  • Positive historical returns, rallies and upside trends are favorable years for the pattern."
    )
    directional_lines.append(
        "  • Negative returns, drawdowns and sharp downside breaks are unfavorable years."
    )
    directional_lines.append("")
    directional_lines.append('- For SHORT patterns (Trade Direction = "Short"):')
    directional_lines.append(
        "  • Negative historical returns, softening or drifting-lower behavior are favorable years for the pattern."
    )
    directional_lines.append(
        "  • Strong rallies, upside spikes or sharp squeezes are unfavorable years for the pattern."
    )
    directional_lines.append(
        "  • Never describe a big upside spike as “strong performance” for the pattern; make clear that it was a losing "
        "year for a short setup."
    )

    if symbol.upper() in VOL_TICKERS:
        directional_lines.append("")
        directional_lines.append(
            "- This ticker is a volatility index. Use professional markets language: implied volatility typically compresses "
            "in calm, well-supported equity markets and expands sharply during risk-off episodes or abrupt equity drawdowns."
        )
        directional_lines.append(
            "- Mention this volatility–equity relationship in at most one short sentence to signal that you understand how "
            "volatility behavior relates to the broader market. Keep it subtle, not didactic."
        )

    directional_guidance = "\n".join(directional_lines)

    seasonal_integration = ctx["seasonal_integration"]
    pe_note = ctx["pe_note"]
    avg_profit_rule_line = ctx["avg_profit_rule_line"]
    cta_link = ctx["cta_link"]
    hero_html = ctx["hero_html"]
    byline_html = ctx["byline_html"]
    ai_html = ctx["ai_html"]
    key_stats_rows_instruction = ctx["key_stats_rows_instruction"]
    window_phrase = ctx["window_phrase"]
    lookback_phrase = ctx["lookback_phrase"]
    meta_market_html = ctx["meta_market_html"]
    avg_profit_note_html = ctx["avg_profit_note_html"]
    market_context = ctx.get("market_context", {})

    # ---- Data block JSON (used in prompt, not echoed by model) ----
    data_json = json.dumps(
        {
            "meta": data_block["meta"],
            "stats": data_block["stats"],
            "per_year": data_block["per_year"],
            "images": data_block["images"],
            "image_guidance": data_block["image_guidance"],
            "template": data_block["template"],
            "market_context": market_context,
        },
        ensure_ascii=False,
    )

    # ---- PROMPT TEXT (mode-aware, single HTML output) ----
    prompt = f"""You are a financial journalist. Produce one complete HTML article in a CNBC-like style.
The piece is about {company} (ticker {symbol}). Treat {company} as the instrument name appropriate for its market family.
TradeWave is referenced only as a data source later in the story, not in the hook or dek.

Assume today's date for this article is {data_block["meta"]["calendar_today"]}.
The TradeWave seasonal analysis uses pattern_start_date = {date} and pattern_window_days = {days}.
If pattern_start_date is after today's date, treat the window as a future seasonal regime,
not something that is already underway.

Time sanity rules (non-negotiable):
- Research.sources includes "date", plus server-added fields "age_days" and "fresh".
- You MUST NOT base the headline, dek, or first paragraph on any source where fresh is false.
- If you mention any non-fresh source at all, you MUST explicitly date it (example: "In June 2025, ...")
  and you MUST NOT use words like "recently", "this week", "today", or "now" in that sentence.
- The headline MUST be seasonality-first. Do not lead with analyst/bank target changes or price targets.

Absolute output rules (non-negotiable):
- Return ONE valid HTML document only (from <!doctype html> through </html>). No extra text before or after.
- Do NOT include any internal notes, analysis, planning, or <think> tags. If any such text is generated, remove it before returning the final HTML.
- Do NOT use markdown at all (no ##, ###, *, -, ```). Use only proper HTML tags.

Hard requirements (follow exactly):
- When citing distance to a 52-week high/low, compute ONE percentage to one decimal place (e.g., “about 1.6% below”). Do not write ranges.
- If reputable sources disagree, recompute once from last price and the 52-week extreme; then state one “about X.X%” figure with a citation.
- Use the % symbol (not the word ‘percent’) for all percentages.
- In the <head>, include a <script type="application/ld+json"> block containing valid schema.org NewsArticle JSON-LD for this article. The JSON-LD must:
  • Use "@context": "https://schema.org" and "@type": "NewsArticle".
  • Set "headline" equal to the <h1> headline text.
  • Set "description" equal to the <meta name="description"> content.
  • Set "datePublished" and "dateModified" to today’s date in ISO format (YYYY-MM-DD) at the time of writing.
  • Use "author": {{ "@type": "Organization", "name": "TradeWave.ai", "url": "https://tradewave.ai/" }}.
  • Use "publisher": {{ "@type": "Organization", "name": "TradeWave.ai",
                      "logo": {{ "@type": "ImageObject", "url": "https://tradewave.ai/logo.png" }} }}.
  • Set "image" to the hero image URL if present, otherwise the first chart image URL.
  • Include an "about" object like {{ "@type": "Thing", "name": "{company}", "tickerSymbol": "{symbol}" }}.
  • Be strict valid JSON: double quotes only, no comments, no trailing commas.
- The first sentence in the “Seasonal window” section must be a seasonal hook based on the TradeWave window, not a generic price lead.
- In the rest of the opening paragraph:
  • Second sentence: immediately follow the seasonal hook with today’s price context and one additional orientation datapoint (for stocks/indices: distance to 52-week high/low or YTD performance; for futures/forex/crypto: a well-known threshold or range).
  • Third sentence (ONLY when a Special Insight exists): briefly describe the confirmed signal (for example, unusual options flow, volume spike, short-interest jump, or insider activity) and why it matters now, with a proper <sup>[n]</sup> citation.
  • Optional fourth sentence: one short “why this combination matters” sentence that links the seasonal backdrop, current price context, and the Special Insight into a single narrative frame.
- The first paragraph must always read as “seasonal first, price second, and (when present) Special Insight as the amplifying detail,” with no mention of TradeWave by name.
- Do not mention TradeWave in the first paragraph or the dek.
- 1,000 to 1,300 words.
- Short paragraphs. Plain English. Confident, data-driven newsroom tone. Write like a senior markets reporter who found something the reader does not already know.
- Prefer short, punchy sentences over long compound constructions. If a sentence has more than 25 words, split it.
- When presenting cumulative or striking stats, use direct language: "Add it up: 238% cumulative gains across ten spring windows" beats "The cumulative gain of 238% across the decade underscores how powerful this specific slice of the calendar has been for long exposure."
- Avoid throat-clearing phrases: "It is worth noting that," "It should be mentioned," "The data underscores," "This highlights the fact that." Just state the fact.
- Write sentences a retail trader would text to a friend: "NEM is 10 for 10 in this window" not "The percent profitable metric stands at 100% across the lookback period."
- Do not use em dashes anywhere in the article.
- Do not ask questions or request clarification. Use the provided Data and research context.
- Do **not** output any code fences, backticks, or pseudo-markdown blocks anywhere in the HTML.
- For election-cycle pieces, only say “entering PE+X” if the current month is in the early part of that phase; if in late months, say “concluding PE+X”; otherwise use “in PE+X.”
    In the headline, spell out the phase (use the plain-English labels above). Do not use PE/PE+1/PE+2/PE+3 in the title.
    In prose, spell out the phase clearly (“the presidential election year,” “the year after the presidential election,”
    “the midterm election year,” or “the year before the presidential election”). Do not use PE/PE+1/PE+2/PE+3 shorthand
    in the article body; shorthand may appear in chart captions only if space is constrained.
    When mentioning TradeWave as a data source, link the first mention to {cta_link} as “TradeWave.ai”.
    Use the exact text “TradeWave.ai” on first mention and link it to {cta_link}.

- Quote TradeWave numbers exactly as shown in the Data section. Do not round beyond what is provided.
- Whenever you state a success rate, include all three items in the same sentence: Percent Profitable, Num Winners, and Num Losers.
- Do not mention TradeWave anywhere before the transition_to_tradewave bridge paragraph.
{avg_profit_rule_line}
- If you discuss drawdowns, MAE, MFE, or intraperiod downside anywhere in the Seasonal section, you must use the 'bars_mae_mfe' bar chart variant so the visual shows MAE and MFE, not the simple net-return 'bars' chart.
- Include one concise risk note immediately after the seasonal stats section: history does not guarantee future results, and MAE can be large even in winning windows.
- Close with a brief “What to watch” summary for this window, aligned to the template's closing emphasis.
  • Always highlight 2–4 concrete items: upcoming data/catalysts, levels or ranges that matter during the window, and how behavior inside the window would confirm or contradict the historical pattern.
  • When a Special Insight exists, you must explicitly revisit it here: explain what traders should monitor next for that signal (for example, whether options flow, volume, or short interest continues to build or reverses), and how that follow-through would interact with the seasonal pattern. Use a proper <sup>[n]</sup> citation tied to the same underlying source.
- When Research.special_signals contains a clearly documented, recent (≤30 days) data-backed signal from a reputable source (unusual options activity, abnormal volume vs average, short-interest spike, significant insider buying/selling, or notable ETF/sector flows), treat it as a Special Insight.
- When a Special Insight exists, you must:
  • Mention it once in the opening paragraph (never as the first sentence): after the seasonal hook and today’s price context, add one concise sentence that describes the confirmed signal and why it matters now, with a proper <sup>[n]</sup> citation.
  • Return to it once in the “What to watch” portion near the end, explaining how traders might monitor follow-through or reversal in that signal (for example, whether options flow, volume, or positioning continues or fades), again with a proper <sup>[n]</sup> citation.
- Do not create a standalone “Special insight” section. The Special Insight should be woven into the narrative in these two locations only (opening paragraph and What to watch).
- If no such signal is confirmed in the available Research.special_signals context, do not mention Special Insight at all. Do not invent or infer one.
- Do not infer or speculate about signals; only use facts explicitly reported by reputable sources.
- Avoid repeating any single TradeWave stat more than twice in the body; do not restate the same thought in multiple sections.
- Do not provide financial advice.
- Use one consistent date format for prose, “Sep 18, 2025”.
- Do not echo the Data block. Use it to write the article, but do not print it.

Headline and dek:
- Craft a clear, news-style headline that includes {company} ({symbol}) and explicitly references the seasonal pattern or its key statistic. Do not use raw quant jargon like MAE/MFE in the title.
- Keep the headline under 16 words, but make it feel urgent and time-sensitive. When the data is strong (Percent Profitable >= 80%), lead with the striking stat (e.g. "9 of 9 midterm summers" or "has risen every March in midterm years"). When the data is mixed, use constructions like "heads into a historically strong seasonal window" or "faces a historically weak stretch." The reader should feel "I need to know what this means now."
- When the TradeWave window lies ahead (is_future_window = true), the headline should clearly signal that the seasonality is upcoming, using language such as “approaches,” “set to enter,” or “heading toward,” not “is in.”
- When the TradeWave window is already active, the headline should signal that the market is now inside the regime, using language such as “trades inside a historically strong seasonal window” or “sits in a historically weak stretch.”
- Write a one-sentence dek that mentions {company}, today’s context (price move or macro backdrop), and hints at why this seasonal window matters (upside opportunity, downside risk, or volatility). Do not mention TradeWave in the dek.
- If election-cycle grouping is active, include the plain-English phase in the headline (for example, “the year after the presidential election,” “the midterm election year,” or “the year before the presidential election”). Do not use PE/PE+1/PE+2/PE+3 shorthand in the title.

Title Style Examples (for model alignment)
When generating headlines, choose the style that best fits the strength of the data:

STYLE A: Stat-forward headline (USE when Percent Profitable >= 80% or pattern has >= 8 winners)
These lead with the most striking data point because the pattern is strong enough to carry the headline.
Good examples:
"Toll Brothers (TOL) Has Fallen Every Midterm Summer for 9 Straight Cycles"
"ADP Has Risen in 10 of 10 Midterm-Year March Windows, Averaging 3.9%"
"NVIDIA Has Dropped 6 of 6 Midterm Election Summers, Averaging 18% Losses"
"Amazon (AMZN) Has Rallied in 9 of 10 Years During This Late-October Window"
"The S&P 500 Has Never Lost Money in This 295-Day Midterm Stretch Since 1930"
These examples show: a specific, verifiable number in the headline; the security name and ticker; a time reference that creates urgency.

STYLE B: Calm analytical headline (USE when Percent Profitable < 80% or pattern is mixed)
These frame the seasonal window without leading with stats.
Good examples:
"NVIDIA Enters a Historically Volatile Seasonal Window This Week"
"Tesla Approaches a Historically Weak Seasonal Stretch Into December"
"S&P 500 Moves Into a Historically Strong Pre-Election Seasonal Window"
"Gold Enters Its Strongest Seasonal Window of the Year as December Begins"
These examples show: calm, analytical wording; "historically" + "seasonal window/stretch/pattern"; timing cues; seasonal framing takes priority.

SELECTION RULE: If the pattern has 100% win rate or >= 8 of 10 winners, prefer Style A.
If the pattern is weaker or mixed, use Style B.
Either style must include: one asset, one seasonal descriptor, one timing cue, and stay under 16 words.

Avoid these headline styles:

Bad examples (do not use)
"Amazon enters a 61 percent bullish window" (too quant-heavy, confusing to general readers)
"Tesla seasonality shows MAE/MFE divergence ahead" (quant jargon, not headline-appropriate)
"Big breakout looming for NVIDIA" (vague, sensational, not based on seasonality)
"Analysts say buy now" (generic, non-seasonal)
"Tech stocks explode higher as markets rally" (purely newswire-style, lacks the seasonal focus)

Do not include:
price targets
insider trade amounts
TradeWave branding
election-year shorthand such as PE/PE+1/PE+2

Hook Style Examples (for model alignment)

The first paragraph must always begin with a seasonal-first opening sentence.
Never open with price, macro, or catalysts.
Do not mention TradeWave in the hook.

Choose the hook style that matches the strength of the data:

HOOK STYLE A: Stat-forward hook (USE when Percent Profitable >= 80% or >= 8 winners)
Lead with the single most surprising number. Make the reader stop scrolling.
Good examples:
"Toll Brothers has dropped in every single midterm election summer for nine straight cycles. The window just opened again, and the stock is trading near its 2025 high."
"NVIDIA has fallen in six of six midterm-year summers, averaging 18% losses each time. Shares slipped early Tuesday as the latest window began."
"The S&P 500 has not posted a single losing year during this 295-day midterm stretch since 1930. That window opens again on September 27."
"Amazon has rallied during this late-October window in nine of the last ten years. The stock edged higher today as the pattern kicked in."
These examples show: the striking stat appears in the very first sentence; price/timing follows immediately; calm and factual, not breathless; no TradeWave mention.

HOOK STYLE B: Calm seasonal hook (USE when Percent Profitable < 80% or pattern is mixed)
Good examples:
"NVIDIA entered a historically volatile seasonal window this week, slipping early Tuesday as traders positioned ahead of earnings."
"Tesla moved into a historically weak December stretch today, even as shares firmed in morning trading."
"Amazon is entering a historically soft holiday-season window, with the stock easing slightly in early trade."
These examples show: seasonality in the first clause; price action follows; calm, analytical tone.

SELECTION RULE: If 100% win rate or >= 8 of 10 winners, always use Hook Style A.
If mixed pattern, use Hook Style B.

Avoid these hook styles:
"Shares of NVIDIA slipped Tuesday..." (price-first violates the seasonal-first rule)
"Investors waited for earnings today..." (generic news intro, no seasonal element)
"The stock rose 1 percent before entering a seasonal window..." (seasonality must come first)
"TradeWave data shows a seasonal pattern..." (TradeWave cannot be mentioned in paragraph one)

Always follow this structure:
Sentence 1: seasonal hook with the most compelling data point (stat-forward) or seasonal framing (calm)
Sentence 2: price, catalyst, intraday, or volatility context
No TradeWave references in the first paragraph
No MAE/MFE jargon; plain English only

The model will automatically:
Start every article with a seasonal hook
Lead with the strongest number when the data justifies it
Blend price into the first paragraph properly
Never drift into Bloomberg-style "price first"
Never introduce TradeWave too early
Maintain your brand: quant urgency + calm authority

Semantic keyword richness (apply throughout every article):
Weave in semantically related terms naturally where they fit the context. Include variants such as "[month] seasonal pattern", "historical seasonality", "{symbol} seasonal trend", "stock pattern analysis", "{company} trading window", and "[sector] seasonal outlook". Do not force or repeat keywords artificially. The goal is organic coverage of the full vocabulary readers use when searching for seasonal stock analysis, expanding the article's long-tail keyword footprint without harming readability.

Transitional Paragraph (for model alignment)
After the Key Takeaways section and before the Seasonal window section, include a short transitional paragraph that introduces the seasonal analysis for the first time. This paragraph must follow these rules:

Structure and tone
“This paragraph must be a single, concise bridge with id="transition_to_tradewave" that starts with ‘According to historical data from TradeWave.ai,’ and does not include any statistics. It sits after Key takeaways and before the Seasonal window section.”
Follow immediately with the first explicit mention of the data source: “According to historical patterns analyzed by TradeWave.ai,” or “Based on nearly a century of seasonal behavior compiled by TradeWave.ai,” or “TradeWave.ai’s historical database shows…”
This is the first allowed mention of TradeWave in the article. It must not appear earlier in the headline, dek, or first paragraph.

Purpose and content
The transitional paragraph must bridge the reader from news-driven context into quantitative seasonality, signaling that the next section is unique to SMN.
Do not include statistics, dates, probabilities, Sharpe ratios, or MAE/MFE values here. Those belong in the dedicated seasonal section.
Keep this paragraph to 1–2 sentences only. It must be clean and forward-moving.

Good examples
"The timing of today's move is notable. According to historical patterns analyzed by TradeWave.ai, this period has shown a distinct seasonal bias in prior years."
"There is a reason this window keeps delivering. TradeWave.ai's lookback across a decade of data shows a pattern that most investors have never seen."
"The headlines are about gold prices and analyst upgrades. But underneath, there is a quieter story. TradeWave.ai's seasonal database flags this specific stretch as one of the most consistent windows on the calendar."
"Before diving into the data, context matters. TradeWave.ai's multi-decade seasonal analysis highlights this window as historically unusual, and the next iteration begins in days."

Bad examples (do not use)
"TradeWave.ai says the stock will go up." (no predictions)
"TradeWave shows an 80 percent win rate." (stats do not belong in this bridge paragraph)
"Historically the stock has done X without TradeWave mention." (you must introduce TradeWave explicitly)
"Seasonality is bullish here" (too vague; must introduce TradeWave as the source)
"According to historical data from TradeWave.ai, this upcoming stretch has behaved very differently from an average month on the calendar." (too generic and templated; vary the language each time)

Mandatory rule
This paragraph must always appear immediately before the seasonal window section and after the near-term drivers section, forming a clear narrative link between the two.


Seasonal Window Section (for model alignment)
The seasonal analysis section must always begin with a clear, plain-English statement identifying the seasonal window and its historical directional tendency. The goal is to translate the TradeWave pattern into understandable human language without losing quantitative precision.

Required opening sentence pattern
When the pattern is strong (Percent Profitable >= 80% or >= 8 winners), lead with the most striking stat:
"{company} has [risen/fallen] in [X] of [Y] [years/cycles] during this window, averaging [Z]% [gains/losses]. The [next/current] window [begins on {date} / is now underway]."
Example: "Toll Brothers has dropped in every midterm election summer for nine straight cycles, averaging 13% losses. That window opened again this week."

When the pattern is mixed or weaker, use a calmer frame:
"This seasonal window begins on {date} and spans {days} days. Historically, during this period, {company} has shown a [strong/weak/volatile] directional tendency."
Or, for already-active windows: "This seasonal window is currently underway, spanning {days} days, and has historically been a [strong/weak/volatile] stretch for {company}."

Mandatory content in this section (in order)
Trade direction: long/short/undefined, based strictly on TradeWave’s direction field. No interpretation or prediction.
Percent Profitable + Num Winners / Num Losers: state plainly, using TradeWave’s exact values without rounding or embellishment.
Avg Profit (winners only) and Avg Profit-All (includes losers): explain clearly what each value means in plain English.
MAE/MFE profile: summarize the historical best-case and worst-case excursions using clear language such as “maximum favorable move” and “maximum adverse move,” but do not use MAE/MFE acronyms without explanation. Keep each explanation to one sentence.
Trend view (if available): describe whether the return profile tends to accelerate early, late, or remain choppy, depending on the chart data.

Cumulative return chart: briefly explain what the multi-year cumulative trend suggests (strong clustering, mixed behavior, or steady bias).

Bars chart (bars_mae_mfe): For future windows, interpret the bars chart as the most important visualization. One paragraph only.

Tone and framing
The seasonal section must be objective, descriptive, and data-driven.
Do not recommend trades, do not imply certainty, and do not use predictive language.
Use “historically,” “in prior years,” “has tended to,” “the typical pattern shows,” “the historical range suggests.”
Do not exaggerate or make predictions. But when the data is striking, say so plainly and let the numbers speak. Readers should feel "quant clarity" and genuine surprise at patterns they did not know about.

Good example phrasing
“Historically, this window has produced more losing years than winning ones, with 6 winners and 11 losers across the lookback.”
“Average winner gains of 4.3 percent contrast with average losses when including down years, which reduces the all-years average to 1.1 percent.”
“The MAE/MFE profile shows that in the stronger years, the stock has often rallied early in the window, while the weaker years show a deeper and earlier adverse move.”

Bad examples (do not use)
“This is a high-probability trade setup.” (no recommendations)
“The stock usually crashes here.” (exaggeration)
“MAE is 5 and MFE is 2.” (raw acronyms without explanation)
“Seasonality guarantees a move.” (no guarantees)

Mandatory end-of-section rule
End this section with a short, punchy summary that restates the single most important takeaway from the data.
Good examples:
"Nine for nine. That is the record this window carries into this cycle."
"The pattern is clear: this window has favored longs in 8 of 10 years, with average gains of 4.3%."
"History does not guarantee a repeat, but the consistency across a decade of data is hard to ignore."
Bad example (do not use):
"Taken together, the historical pattern defines the quantitative seasonal backdrop for the current period." (too academic, no one shares this)

Why does this pattern repeat? (add one H2 question + short paragraph at the end of the seasonal section)
After the mandatory end-of-section summary, add one final H2 question and a short explanatory paragraph. The H2 must follow question format: "Why does {company} ({symbol}) follow this seasonal pattern?" Then write 2-3 sentences proposing the likely mechanism. Choose from: earnings calendar clustering, fiscal year-end rebalancing, institutional portfolio repositioning, sector rotation, consumer spending cycles (holiday, back-to-school, summer travel), options expiration patterns, or commodity supply/demand seasonality. Frame as hypothesis using language such as "one likely driver is...", "analysts have pointed to...", or "this pattern may reflect...". Never state the cause as certain.

Subheading format rule:
All <h2> headings the model generates must be phrased as natural questions a reader might type into a search engine. This applies to the seasonal window section heading and any other sections the model creates. Examples: "How has {company} ({symbol}) performed in this [month] window?" / "What does history say about {company} ({symbol}) in [month]?" / "Why does {company} ({symbol}) follow this seasonal pattern?" Choose the phrasing that fits the data.

{market_block.strip()}

{web_research_block}

Verification rules (must follow):
- If a data point (52-week high/low, YTD performance, consensus price target) is not available in the Research or Data context, omit it silently. Do NOT write phrases like "without a clear reference point" or "data is not yet available." Just skip it and move on.
- STALE DATA CHECK: If a consensus analyst price target is more than 30% below the current stock price, it is likely outdated. Either note it explicitly as reflecting an earlier price regime in one short clause, or omit it entirely. Do not present a stale target as if it is current consensus.
- Do not invent numbers; if a value cannot be confirmed from the provided research context, omit it.
- Analyst price targets: report a consensus or median and NAME the provider (e.g., FactSet, Refinitiv, Nasdaq, Yahoo Finance). If two reputable sources disagree, state the range and cite both.
- YTD or 52-week context: compute or quote from a reputable source in the research context and attach a matching citation <sup>[n]</sup>.
- For 1-month return, 52-week high/low, 20-day average volume, and the 50-day moving average, ALWAYS use the exact values provided in the Data.market_context block. Do not recompute them from external sources.
- CURRENT/LIVE PRICE SOURCING: The current price, today's move, and intraday band come from the live quote feed (EODHD market data), NOT from any news article. When you state the current/live price you must NOT attach a <sup>[n]</sup> that points to a news source; if the price is cited at all, cite only the data methodology page, never a dated news article. Never imply that a months-old news article is the source of today's price.
- INDEX/FUTURES/COMMODITY PRICE FRESHNESS: If the market is closed (outside regular trading hours), refer to the level as the most recent close ("the prior session's close"), not as a live or current intraday price. Do not present a prior-session close as today's live level.
- CITATION INTEGRITY (mandatory): Use ONLY the sources provided in the Sources list. NEVER invent or fabricate a URL, NEVER use a placeholder link such as example.com, and NEVER use an empty or '#' href. Every <sup>[n]</sup> must map to a real, existing entry in the ordered Sources list, and you must NOT use a citation number larger than the number of sources provided. If a claim cannot be supported by one of the provided sources, omit the claim rather than fabricate a citation.

Seasonal integration (make it clear and accessible):
{seasonal_integration}
{pe_note}

Direction and Trade Direction handling (must follow):
{directional_guidance}

Intelligent seasonal window interpretation (forward-looking behavior):
- Use the pattern_start_date and pattern_window_days from the Data meta block when reasoning about timing.
- Compare today’s date to pattern_start_date. Only treat a TradeWave window as a future “heads-up” window when pattern_start_date is after the publication date; otherwise treat it as current or historical behavior without forward-warning language.
- Apply context-aware timing:
  • Short windows (5–30 calendar days): treat as near-term setups. Give a heads-up tone only when the start date is more than about 14 days away; if it is closer, describe it as an approaching setup rather than a distant regime shift.
  • Medium windows (31–90 calendar days): treat as behavior shifts over several weeks. Give a forward heads-up when the start date is more than about 30 days away.
  • Long windows (over 90 calendar days): treat as seasonal regimes. Give a forward heads-up when the start date is more than about 60 days away, and frame it as a potential change in behavior for the next quarter or so.
- If the start date is inside these heads-up thresholds, keep the tone focused on an approaching pattern rather than long-range preparation.

Systemically important special-situation handling:
- Most articles should be written in a normal neutral style, using seasonal data as context without special warnings. Only activate enhanced “special-situation” framing when all of the following are clearly supported by the Data and research context:
  • The instrument is systemically important in its market family, for example:
    - Mega-cap equities that strongly influence major indices (such as NVDA, AAPL, MSFT, AMZN, META, GOOGL, TSLA), or
    - Major commodities, futures, FX pairs, or rates that materially affect macro sentiment (such as crude oil, gold, key index futures, benchmark government bond yields, or large FX pairs like EUR/USD or USD/JPY).
  • The upcoming seasonal window is statistically strong, such as:
    - A clear directional tendency in the historical stats,
    - Meaningful average returns or unusually large MFE or MAE compared with typical conditions, or
    - Consistent multi-year behavior.
  • The direction of the upcoming window contradicts the current trend described by recent price action (for example, the instrument is currently in a powerful uptrend but the upcoming window is historically bearish, or the instrument has been weak but the upcoming window is historically strong).
- If any of these conditions are not met, do not use special warning language. Write a normal neutral article and treat the seasonal window as contextual, not as a central risk alert.

When the special-situation criteria are met for equities:
- Within the first three or four paragraphs, after explaining today’s trend and role of the instrument, include a calm, factual contrast paragraph that acknowledges the tension between current behavior and the future window. Use wording in this style, adapted to the specific case:
  “{company} remains in a powerful [bullish or bearish] trend and continues to influence [its index, sector, or theme]. Even so, TradeWave’s long-term seasonal data highlights a future window that has repeatedly shown [downside or upside] risk for the stock. Because {company} is a key driver of [the relevant index or sector], volatility during that period has often carried broader market implications, and the historical MFE and MAE pattern suggests that when this window moves, it tends to move quickly.”
- Keep this paragraph neutral and probability-based. Do not predict guaranteed reversals or crashes; describe historical behavior and potential risk.

When the special-situation criteria are met for commodities, futures, FX, or rates:
- Use a similar early contrast paragraph, but emphasize macro or sector spillover instead of index weight. For example:
  “{company} is currently trading in a firm [bullish or bearish] trend and remains an important reference point for [inflation expectations, energy costs, global growth, sector margins, or FX flows]. Even so, TradeWave’s seasonal data highlights a future window with historically sharp moves in this contract. Because {company} influences [macro variables or related sectors], volatility in this period has often spilled into connected equities and asset classes, and the historical MFE and MAE profile shows that these swings can develop quickly.”
- Again, keep the tone measured and data-driven.

Use of MFE and MAE for future windows:
- When is_future_window in the Data meta is true, you MUST use the 'bars_mae_mfe' bar chart variant
  in the Seasonal section, not simple net-return bars. In that case, the prose in the Seasonal section
  must explicitly refer to both MFE and MAE when describing the pattern.
- When is_future_window is true, place the 'bars_mae_mfe' chart after the trend chart.
  Only use the simple 'bars' variant when is_future_window is false AND you do not mention MAE, MFE, drawdowns, or intraperiod downside in the Seasonal prose. If you mention MAE, MFE, drawdowns, or intraperiod downside, you must use the 'bars_mae_mfe' variant so the chart matches the text.
- When discussing any upcoming seasonal window, do not rely only on average close-to-close returns.
- Use MFE (maximum favorable excursion) and MAE (maximum adverse excursion) from the Data to characterize volatility:
  • Large MFE combined with large MAE indicates a high-variance window where both sharp rallies and sharp drawdowns have occurred.
  • Large MAE relative to typical conditions indicates elevated downside risk, even if the average result is positive.
  • Large MFE with relatively contained MAE indicates historically favorable upside with more limited drawdowns.
- Always present these as historical tendencies, not guarantees.

SPX and presidential election-cycle handling:
- When the instrument is the S&P 500 or a direct SPX proxy and the years field indicates a presidential election-cycle grouping, explicitly connect the seasonal window to the relevant phase:
  • Presidential election year: policy uncertainty and front-loaded volatility.
  • Post-election year: often digestion or choppy behavior.
  • Midterm election year: distinct mid-cycle behavior.
  • Pre-election year: historically strong risk-on tendencies in many cycles.
- When a future SPX seasonal window in a given phase has a strong and distinctive pattern that contradicts the current trend, apply the special-situation logic above and explain clearly how this phase has behaved historically, using TradeWave data and election-cycle context.

Midterm election year structural behavior — Afshin rules:
- Midterm election years display a distinct two-phase structure not seen in other election-cycle years.
- Q1 and Q2 historically contain a high concentration of losing years, elevated volatility, and sharper adverse excursions (MAE), even when longer-term trends remain bullish.
- When the S&P 500 or any major megacap (AAPL, MSFT, NVDA, AMZN, GOOG, META, TSLA) falls into a historically negative midterm-year window, clearly note that weakness in these names has historically dragged down the broader market because of their systemic weight.
- Long-history sectors (energy, defense, industrials, materials) often show choppy or negative early-year behavior in midterm election years despite strong multi-decade performance.

Contrast with the late-year midterm super-window:
- Explicitly contrast the volatile early-year midterm behavior with the historically powerful September 27 – July 18 midterm-year window, which shows unusually smooth upward behavior and no losing years since 1930.
- Treat midterm election years as a “two-playbook” year: a choppy, risk-heavy start followed by a powerful seasonal climb beginning late September.

Risk tone (data-driven only):
- Use factual language such as: “Although markets have recently been strong, this specific midterm-year window has historically produced sharper reversals and deeper intraperiod drawdowns.”
- Apply especially when the upcoming seasonal window contradicts current market trend direction.

Forward-looking windows:
- When the seasonal window lies in the future and begins more than ~30–60 days ahead (depending on pattern length), frame the narrative as a historical heads-up: “Investors should be aware that this upcoming period behaves differently from typical bull-market environments according to historical seasonality.”

Special handling for the SPX 100-Year Pattern:
- There is a specific long seasonal regime identified in the research: it begins on September 27 of each midterm election year and lasts 295 days, ending around July 18 of the following year. This midterm-to-pre-election window has shown consistently strong behavior across decades in the provided data and forms the basis of the “100-Year Pattern” framework.
- When a seasonal analysis window for the S&P 500 or a direct SPX proxy clearly overlaps any portion of this midterm-to-pre-election span, include a brief, neutral reference to the fact that the window sits inside or overlaps this historically strong regime. For example:
  “This upcoming window also overlaps the long-term midterm-to-pre-election seasonal pattern, a 295-day period that has historically shown unusually strong behavior for the S&P 500 across many cycles since 1930.”
- Present this as a historical tendency only. Do not state or imply that it guarantees future gains.
- Do not mention this pattern in articles whose TradeWave seasonal window does not obviously intersect this midterm-to-pre-election regime.

When the article's window is effectively the 100-Year Pattern itself:
- If the opportunity window corresponds directly to the midterm-to-pre-election regime, defined as a window starting within about five calendar days of September 27 in a midterm election year and lasting roughly 295 days so that it ends around July 18 of the following year, treat the 100-Year Pattern as a primary topic rather than a background reference.
- In that case, state clearly in the body that the analysis is taking place inside the 100-Year Pattern and briefly explain what that means in plain English. For example:
  "This analysis takes place within the long midterm-to-pre-election window often referred to as the 100-Year Pattern, a 295-day seasonal regime that has historically delivered strong S&P 500 performance across data going back to 1930. The pattern and its methodology are detailed in Afshin Moshrefi's 2026 book The 100-Year Pattern."
- This can be mentioned when the article is directly about the S&P 500 or about a major stock trading during this regime.
- Keep the explanation concise and factual; do not overstate it, but do not hide the strength of the data either.

- Define jargon crisply on first mention (use these exact wordings):
  • TradeWave Ratio (TWR): how far price typically travels in the trade direction within the window, independent of the final close. Do not compare it to the “final net” in prose; use this definition verbatim.
  • Sharpe ratio: risk-adjusted average return based on end-of-window outcomes.
  • MFE/MAE: best and worst intraperiod excursions from the entry (peak run-up and worst drawdown within the window).
- When explaining TWR anywhere, do NOT phrase it as “X times further than the final net.”
- Use the Per-Year table to reference one strongest and one weakest historical year. If citing excursions, make clear that MFE is the best point-to-peak move and MAE is the worst drawdown from entry.
- Place the single-paragraph risk note immediately after this section.

Image guide:
- Place the hero image (if provided) immediately after the dek.
- Do not place any chart before the “Price and near-term drivers” section.
- In “Price and near-term drivers”, insert the PRICE chart immediately after the FIRST paragraph that states price context (today’s move plus 52-week or YTD and intraday band). Precede it with a one-sentence bridge that cues the figure. The price chart includes a 60-day seasonal projection overlay; the bridge sentence should acknowledge both recent price action and the forward projection naturally.
- Use up to three images from the data.
- Place the “trend” chart ONLY inside the seasonal section and state clearly in the caption that it is a historical seasonal average.
- Place the “bars” (or bars_mae_mfe) chart AFTER the trend chart, with a one-sentence bridge between them.
- Use semantic markup: <figure> with <img alt="..."> and <figcaption>. Provide meaningful alt text. Do not inline base64; use the provided URLs.

Output exactly one HTML document with:
- A <head> and <style> that define a clean, CNBC-like layout.
- A <body> containing one <article> with:
  1) Title in <h1>, a dek in <p class="dek">, and a byline/timestamp strip in <div class="meta">.
  2) Body with proper HTML subheads using <h2>.
  3) {key_stats_rows_instruction}
  4) Up to three <figure> blocks with captions and alt text.
  5) A clearly labeled “Sources” section as an ordered list.
  {sources_requirement_line}

Styling (keep simple and readable):
- Overall: max-width: 860px; margin: auto; generous line-height; white background; dark text.
- h1 large and bold; dek slightly larger than body, muted color; meta row small and subtle.
- h2 subheads spaced; pull quotes styled with a left border.
- figures full-width with centered captions; images responsive.
- key-stats box with light border, subtle background, and compact rows.
- figures.hero img {{ margin-bottom: 24px; }}

{research_header_line}
{research_json_str}

Data (verbatim TradeWave data - do not alter numbers; you may quote directly):
{data_json}

Quality checks before returning:
- HEADLINE SELF-CHECK (mandatory): Before finalizing, re-read the headline. Does it contain a seasonal reference ("seasonal window," "historical pattern," "straight years," "every spring," or similar)? If NOT, rewrite the headline to lead with the seasonal angle. Do NOT lead with analyst upgrades, price targets, earnings, or macro themes. The seasonal pattern is the unique value of this article; the headline must reflect that.
{sources_quality_check_line}
- Verify every <sup>[n]</sup> citation in the body appears exactly once in Sources.
- Verify no blacklisted domains appear.
- Fail the draft if the price chart appears before the “Price and near-term drivers” section or not immediately after its first paragraph.
- Fail the draft if “TradeWave” appears anywhere in the BODY before the transition_to_tradewave bridge paragraph. Mentions inside the header (title/dek/hero/byline/meta) are allowed.
- Verify every <li> in the Sources list contains an <a href="...">...</a>.
- Verify there are no bare 'http' or 'https' strings printed in the body or Sources; URLs must only appear inside href attributes.

Return only the full HTML document with this structure:

<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <link rel="icon" type="image/png" href="{config.smn_favicon}">
  <title>SEO Title here</title>
  <meta name="description" content="<!-- 150–160 character summary about {company} ({symbol}) and this analysis. The model must replace this with real text. -->">
  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "<!-- The model must set this to the <h1> headline text -->",
    "description": "<!-- The model must set this to match the meta description above -->",
    "datePublished": "<!-- The model must set this to today's date in YYYY-MM-DD -->",
    "dateModified": "<!-- The model must set this to the same value as datePublished -->",
    "author": {{
      "@type": "Organization",
      "name": "TradeWave.ai",
      "url": "https://tradewave.ai/"
    }},
    "publisher": {{
      "@type": "Organization",
      "name": "TradeWave.ai",
      "logo": {{
        "@type": "ImageObject",
        "url": "https://tradewave.ai/logo.png"
      }}
    }},
    "image": "<!-- The model must set this to the hero image URL if present, otherwise the first chart URL -->",
    "about": {{
      "@type": "Thing",
      "name": "{company}",
      "tickerSymbol": "{symbol}"
    }}
  }}
  </script>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    :root {{ --ink:#0a0a0a; --muted:#5a6b7a; --rule:#e6e9ee; --accent:#0059ff; }}
    body {{ margin:0; font-family: Helvetica, Arial, sans-serif; color:var(--ink); background:#fff; }}
    article {{ max-width:860px; margin: 40px auto; padding: 0 20px 64px; line-height:1.6; }}
    h1 {{ font-size: 2.0rem; line-height:1.25; margin: 0 0 8px; }}
    .dek {{ font-size:1.125rem; color:var(--muted); margin: 0 0 16px; }}
    figure.hero img {{ margin-bottom: 24px; }}
    .meta {{ font-size:.875rem; color:var(--muted); border-top:1px solid var(--rule); border-bottom:1px solid var(--rule); padding:8px 0; margin-bottom:24px; display:flex; gap:12px; flex-wrap:wrap; }}
    h2 {{ font-size:1.25rem; margin:28px 0 8px; }}
    p {{ margin: 0 0 14px; }}
    blockquote.pull {{ margin: 18px 0; padding: 12px 16px; border-left: 4px solid var(--rule); color:#111; background:#fafbfe; font-style: italic; }}
    aside.key-stats {{ border:1px solid var(--rule); background:#fafbfe; padding:12px 14px; margin: 20px 0; font-size:.95rem; }}
    aside.key-stats h3 {{ margin:0 0 8px; font-size:1rem; }}
    aside.key-stats .row {{ display:flex; justify-content:space-between; border-bottom:1px dashed var(--rule); padding:6px 0; }}
    figure {{ margin: 20px 0; }}
    figure img {{ width:100%; height:auto; display:block; }}
    figcaption {{ font-size:.9rem; color:var(--muted); text-align:center; margin-top:6px; }}
    .sources {{ border-top:1px solid var(--rule); margin-top:28px; padding-top:14px; }}
    .sources h3 {{ margin:0 0 8px; font-size:1rem; }}
    .sources ol {{ margin:0 0 0 18px; padding:0; }}
    .sources li {{ margin:6px 0; }}
    a {{ color:var(--accent); text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}

    .pattern-meta {{ font-size:.9rem; color:var(--muted); border-top:1px solid var(--rule); border-bottom:1px solid var(--rule); padding:8px 0; margin:16px 0; display:flex; gap:12px; flex-wrap:wrap; }}
    .chart-bridge {{ margin: 8px 0 6px; color: var(--muted); font-size:.95rem; }}
    .price-chart img, .trend-chart img, .bars-chart img {{ width:100%; height:auto; display:block; }}

    .key-takeaways-box ul {{ margin:0; padding-left:0; list-style:none; }}
    .key-takeaways-box li {{ margin:6px 0; }}
    .direct-answer {{ font-size:1.05rem; font-weight:600; color:var(--ink); border-left:3px solid var(--accent); padding-left:12px; margin:0 0 14px; }}
    .methodology-note {{ border-top:1px solid var(--rule); margin-top:28px; padding-top:14px; font-size:.95rem; color:var(--muted); }}
    .methodology-note h2 {{ font-size:1.1rem; color:var(--ink); margin:0 0 8px; }}

  </style>
</head>
<body>
    <article>
      <header>
        <h1><!-- Headline must include {company} ({symbol}) and have a clear hook --></h1>
        <p class="dek"><!-- One-sentence dek about {company} ({symbol}); do not mention TradeWave --></p>
        {hero_html}
        <div class="meta">
          {byline_html}
          {ai_html}
        </div>
      </header>

      <section id="key-takeaways">
        <h2>What is the seasonal pattern for {company} ({symbol})?</h2>
        <p class="direct-answer"><!-- DIRECT ANSWER (required for featured snippets): Write exactly one sentence that directly answers the heading question using the TradeWave data. Format: "{company} has [risen/fallen] in [X] of [Y] years during this [month/season] window, with an average [gain/loss] of [Z]% in winning years." This sentence must be self-contained so Google can display it as a standalone featured snippet answer. --></p>
        <div class="key-takeaways-box">
          <!-- The model must generate 3–6 concise bullet points summarizing:
               - FIRST BULLET must lead with the win/loss record as a statistic: e.g., "9 for 10 in this window, averaging 4.3% gains in winning years" — data-first, not description-first
               - Seasonal direction (bullish / bearish / high-volatility) and window dates ({date} plus {days} days)
               - Percent Profitable and Num Winners/Num Losers stated plainly
               - Average profit in winning years, and Avg Profit - All if applicable
               - Typical drawdown / MAE flavor in plain English.
               This box is intended to be snippet-friendly and data-forward. -->
        </div>
      </section>

      <p id="transition_to_tradewave" class="chart-bridge">
        <!-- Transitional paragraph: 1–2 sentences introducing TradeWave.ai as the seasonal data source, with no statistics. -->
      </p>

      <section id="seasonal-window">
        <h2>Seasonal window</h2>

        <!-- OPENING PARAGRAPH: this is the first body paragraph of the article.
             First sentence MUST be the seasonal hook for the current TradeWave window.
             Immediately follow it with today’s price context and one orientation datapoint
             (distance to 52-week high/low or YTD performance). Do NOT mention TradeWave here. -->
        <p><!-- The model must write: seasonal-first hook + today’s price context + why this window matters. --></p>


        <figure class="preseason-bars-chart">
                <img src="<!-- SIMPLE_BARS_CHART_URL (use the simple 'bars' variant: net returns only, no MAE/MFE) -->"
                    alt="<!-- SIMPLE_BARS_CHART_ALT -->"
                    width="{CHART_WIDTH_ATTR}" height="{CHART_HEIGHT_ATTR}">
                <figcaption><!-- SIMPLE_BARS_CHART_CAPTION: brief description of yearly net returns in the seasonal window --></figcaption>
        </figure>


        <div class="pattern-meta">
          <span>Symbol: {symbol}</span>
          <span>{window_phrase}</span>
          <span>{lookback_phrase}</span>
          <span>Pattern start: {date}</span>

        

          {meta_market_html}
        </div>

        <aside class="key-stats">
          <h3>TradeWave Key Stats</h3>
          {avg_profit_note_html}
          <p style="margin-top:4px;font-size:.85rem;color:#5a6b7a;">
            Source: <a href="{cta_link}">TradeWave.ai</a> seasonal database. TradeWave Ratio (TWR) reflects how far price typically travels in the trade direction within the window regardless of the final close. MFE is the peak favorable excursion during the window, MAE is the worst adverse excursion.
          </p>
          <p style="margin-top:4px;font-size:.85rem;color:#5a6b7a;">
            <a href="{config.news_website_url.rstrip('/')}/methodology.html">Methodology</a>: seasonal analysis framework described in <a href="{config.book_amazon_url}"><em>The 100-Year Pattern</em></a> by Afshin Moshrefi (2026 edition).
          </p>
        </aside>

        <figure class="trend-chart">
          <img src="<!-- TREND_CHART_URL -->"
               alt="<!-- TREND_CHART_ALT -->"
               width="{CHART_WIDTH_ATTR}" height="{CHART_HEIGHT_ATTR}">
          <figcaption><!-- TREND_CHART_CAPTION --></figcaption>
        </figure>

        <p class="chart-bridge">Yearly net and peak moves highlight upside persistence amid typical drawdowns.</p>

        <figure class="bars-chart">
          <img src="<!-- BARS_CHART_URL -->"
               alt="<!-- BARS_CHART_ALT -->"
               width="{CHART_WIDTH_ATTR}" height="{CHART_HEIGHT_ATTR}">
          <figcaption><!-- BARS_CHART_CAPTION --></figcaption>
        </figure>

        <p class="risk-note">History does not guarantee future results; adverse excursions (MAE) can be large even in winning windows.</p>
      </section>

      <section id="drivers-news">
        <h2>What is driving {company} ({symbol}) today?</h2>
        <p><!-- First paragraph: recap today’s price move and one orientation datapoint (52-week context or YTD), plus the key near-term drivers (news, catalysts, flows). Do NOT repeat the seasonal hook here. When a Special Insight exists, you may expand on it with more detail and a <sup>[n]</sup> citation. --></p>
        <p class="chart-bridge">The chart below situates the latest move in its recent multi-month context.</p>

        <figure class="price-chart">
          <img src="<!-- PRICE_CHART_URL -->"
               alt="<!-- PRICE_CHART_ALT -->"
               width="{CHART_WIDTH_ATTR}" height="{CHART_HEIGHT_ATTR}">
          <figcaption><!-- PRICE_CHART_CAPTION --></figcaption>
        </figure>
      </section>



      <section class="sources">
        <h3>Sources</h3>
        <ol>
          <!-- Ordered list... -->
        </ol>
      </section>

      <section id="methodology" class="methodology-note">
        <h2>About this seasonal analysis</h2>
        <p>Seasonal pattern data is sourced from <a href="https://tradewave.ai/">TradeWave.ai</a>, which analyzes historical price behavior across annual calendar windows going back up to 30 years. Read the full <a href="{config.news_website_url.rstrip('/')}/methodology.html">data methodology</a> or the book <a href="{config.book_amazon_url}"><em>The 100-Year Pattern</em></a> by Afshin Moshrefi (2026 edition). Past performance of seasonal patterns does not guarantee future results. This article is for informational purposes only and does not constitute investment advice.</p>
      </section>
    </article>
  </body>
</html>"""

    prompt = prompt.replace("{company}", company).replace("{symbol}", symbol)
    return prompt

# ============================================================
# end of create_article_prompt
# ============================================================

# perplexity prompt generator for research:
def build_perplexity_research_prompt(symbol: str, company: str, market_family: str) -> str:
    """
    Build the research-only prompt sent to Perplexity Sonar-Pro.
    Optimized to find accessible high-quality data without hitting paywalls 
    or falling for SEO prediction spam.
    """
    return f"""
You are a high-precision financial research assistant.
Your ONLY task is to generate a JSON object containing factual, REAL-TIME market data.

TARGET:
- Ticker: {symbol}
- Company: {company}
- Market Family: {market_family}

### 1. SOURCE SELECTION RULES (THE "PROXY" STRATEGY)

**THE PROBLEM:** Many top-tier sites (Bloomberg, WSJ) block AI bots.
**THE SOLUTION:** You must find high-quality data on **Accessible Tier 1** sites.

**PRIORITY 1 (ACCESSIBLE & AUTHORITATIVE):**
- **News:** cnbc.com, apnews.com, finance.yahoo.com (for news), reuters.com.
- **Data:** nasdaq.com, google finance, yahoo finance (for price/stats).
- **Official:** sec.gov, investor.{company.lower().replace(" ", "")}.com.

**PRIORITY 2 (PAYWALLED - CITE IF VISIBLE, DON'T HALLUCINATE):**
- bloomberg.com, wsj.com, ft.com, barrons.com.
- *Instruction:* If you see a headline from these sites in your search results but cannot read the full text, look for a **CNBC or Yahoo Finance article that summarizes it.**

**BANNED (STRICTLY PROHIBITED):**
- **Prediction Farms:** coincodex.com, walletinvestor.com, govcapital.com, longforecast.com. (These sites invent fake future numbers—NEVER use them).
- **SEO Spam:** copygram, stockanalysis.com, user-generated blogs, linkedin articles.

### 2. SEARCH INSTRUCTIONS

Construct your internal search queries to target *mentions* of data in news reports:
- **For Special Signals:** Search for "{symbol} unusual options activity cnbc" or "{symbol} insider trading report yahoo". (Do not try to scrape raw data tables from paid tools).
- **For Analyst Ratings:** Search for "{symbol} analyst upgrades downgrades cnbc" or "{symbol} price target consensus marketwatch".
- **For Earnings:** Search for "{symbol} earnings report key numbers cnbc".

### 3. DATE & FACT CHECKING
- **CRITICAL:** Check today's date. If a source says "Price Prediction 2026", IGNORE IT. Only return data for the *current* or *past* trading sessions.
- If you cannot find a specific data point (like "Unusual Options") on a reputable site, return `null`. Do NOT invent it.

---

### REQUIRED JSON OUTPUT

Return ONLY a single JSON object.

{{
  "symbol": "{symbol}",
  "company": "{company}",
  "market_family": "{market_family}",

  "price": {{
    "last": null,      // Number (Current price)
    "change_percent": null, // Number (Daily change)
    "ytd_percent": null,    // Number (YTD change)
    "range_52w_high": null, // Number (% distance from high)
    "range_52w_low": null   // Number (% distance from low)
  }},

  "catalysts": [
    {{
      "type": "earnings/product/regulation/macro",
      "date": "YYYY-MM-DD or null",
      "headline": "Short headline",
      "summary": "1-2 sentences on impact.",
      "source_id": 1
    }}
  ],

  "earnings": {{
    "next_earnings_date": "YYYY-MM-DD or null",
    "fiscal_period": "e.g., Q3 2025",
    "recent_results": "Revenue/EPS vs est & key quotes.",
    "guidance": "Forward guidance summary.",
    "sources": []
  }},

  "analyst": {{
    "consensus_rating": "Buy/Hold/Sell",
    "price_target_consensus": null, // Number
    "price_target_high": null,      // Number
    "price_target_low": null,       // Number
    "provider": "e.g. FactSet via CNBC",
    "sources": []
  }},

  "macro": [
    {{
      "theme": "Macro theme label",
      "summary": "Impact on {symbol}.",
      "source_id": null
    }}
  ],

  "sector": [
    {{
      "theme": "Sector theme label",
      "summary": "Impact on {symbol}.",
      "source_id": null
    }}
  ],

  "special_signals": {{
    "unusual_options": {{
      "summary": "Search for news mentions of 'unusual call/put activity' on CNBC/Yahoo. If none, return null.",
      "source_id": null
    }},
    "insider_activity": {{
      "summary": "Search for news mentions of 'insider buying/selling' on reputable sites. If none, return null.",
      "source_id": null
    }},
    "volume_spike": {{
      "summary": "If recent news mentions 'heavy trading volume', summarize why. Else null.",
      "source_id": null
    }},
    "short_interest_change": {{
      "summary": "If news mentions 'short squeeze' or 'rising short interest', summarize. Else null.",
      "source_id": null
    }}
  }},

  "equity_etf_index": {{
    "etf_flows": [
      {{
        "ticker": "e.g., XLK",
        "direction": "inflow/outflow",
        "amount_usd": "approx $",
        "window": "1w/1m",
        "summary": "Context.",
        "source_id": null
      }}
    ],
    "index_context": [
      {{
        "index": "e.g. Nasdaq 100",
        "summary": "Performance context.",
        "source_id": null
      }}
    ]
  }},

  "futures_commodities": {{
    "term_structure": {{ "summary": null, "source_id": null }},
    "positioning": {{ "summary": null, "source_id": null }},
    "inventory": {{ "summary": null, "source_id": null }}
  }},

  "sources": [
    {{
      "id": 1,
      "publisher": "Name (e.g. CNBC)",
      "title": "Title of Article",
      "url": "https://...",
      "date": "YYYY-MM-DD",
      "domain_tier": "1",
      "justification": "One short sentence on what this source covers (e.g. 'CNBC report on earnings')."
    }}
  ]
}}
""".strip()

def build_perplexity_research_payload(symbol: str, company: str, market_family: str) -> Dict[str, Any]:
    """
    Build the Perplexity Sonar-Pro payload for research-only mode.
    Does NOT write an article; just returns JSON research.
    """
    prompt = build_perplexity_research_prompt(symbol, company, market_family)
    return {
        "model": "sonar-pro",
        "messages": [
            {"role": "system", "content": _PERP_RESEARCH_SYSTEM},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "stream": False,
        "search_mode": "web",
        "enable_search_classifier": True,
        "search_recency_filter": "month",
        "web_search_options": {"search_context_size": "high"},
    }

# ============================================================
# Appserver connector
# ============================================================
def get_opp_data(resource_id, date, symbol, days, years, zero_last_year):
    token = get_keyprovider_token()
    appserver_token = login_appserver(token)
    days_corrected = str(int(days)-1)
    return get_chart_data(resource_id, date, symbol, days_corrected, years, zero_last_year, appserver_token)

#########################################################################################################################

if __name__ == '__main__':

    print('TradeWave AI Article Prompt Generator (Perplexity Sonar Pro optimized)')

    # ----- Common inputs -----
    image_size_key = 'x'
    theme = "light"
    zero_last_year = True

    # Example: SPX pattern (adjust however you want for local testing)
    resource_id = 0
    date = "2025-11-30"
    symbol = "AAPL"
    days = "11"
    years = "10"

    try:
        company = get_company_name(resource_id, symbol) or symbol
    except Exception:
        company = symbol

    variant = 1
    byline = "TradeWave AI Newsroom"
    ai_disclosure = False

    # ----- Generate images + hero image -----
    img_paths = create_article_images(image_size_key, str(resource_id), date, symbol, days, years, theme)

    hero_html = ""
    try:
        hero_info = hero_image_workflow(resource_id=str(resource_id), symbol=symbol, date=date)
        if hero_info and hero_info.get("image_url"):
            alt_text = f"{company} ({symbol}) market analysis and seasonal trends"
            hero_html = f'<figure class="hero"><img src="{hero_info["image_url"]}" width="{HERO_WIDTH_ATTR}" height="{HERO_HEIGHT_ATTR}" alt="{alt_text}"></figure>'

            # Keep hero in manifest so charts + hero live together in Data.images
            img_paths.append({
                "variant": "hero",
                "url": hero_info["image_url"],
                "path": hero_info["image_path"],
                "rel": "",
                "alt": alt_text,
            })
            print(f"[SUCCESS] Hero image generated: {hero_info['image_url']}")
        else:
            print("[WARN] No hero image generated or missing URL.")
    except Exception as e:
        print(f"[WARN] Hero image generation failed: {e}")

    # ----- Get TradeWave pattern data -----
    cdata = get_opp_data(resource_id, date, symbol, days, years, zero_last_year)

    

    # ----- DEV: Perplexity research smoke test (Mode 2 pipeline) -----
    market_family, resource_name = detect_market_family(resource_id)
    research_prompt = build_perplexity_research_prompt(symbol, company, market_family)

    print("\n--- SENDING PERPLEXITY RESEARCH REQUEST ---")
    research_raw = AI_tools.send_perplexity_prompt(
        research_prompt,
        model="sonar-pro",
        system=_PERP_RESEARCH_SYSTEM,
        temperature=0.1,
        stream=False,
        search_mode="web",
        enable_search_classifier=True,
        search_recency_filter="month",
        web_search_options={"search_context_size": "high"},
    )

    print("RAW RESEARCH RESPONSE:")
    print('rrrresearch rrrraw=',research_raw)

    # ---------- CLEAN PERPLEXITY RESPONSE INTO PURE JSON STRING ----------
    raw = research_raw.strip()

    # 1) If Perplexity wrapped output in ```...``` code fences, unwrap them.
    if raw.startswith("```"):
        first_fence = raw.find("```")
        second_fence = raw.find("```", first_fence + 3)
        if second_fence != -1:
            raw = raw[first_fence + 3:second_fence].strip()
        else:
            raw = raw[first_fence + 3:].strip()

    # 2) If there are notes after '---', drop everything after that.
    if "\n---" in raw:
        raw = raw.split("\n---", 1)[0].strip()

    # 3) Keep only the part between the first '{' and the last '}'.
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Perplexity response does not contain a valid JSON object:\n" + research_raw)

    research_json_str = raw[start:end + 1].strip()

    # Crash loudly if Perplexity doesn’t follow instructions
    research_json = json.loads(research_json_str)
    print("RESEARCH JSON TOP-LEVEL KEYS:", list(research_json.keys()))


    print('calling create article prompt')
    # ----- Build article prompt using Mode 2 (research JSON, no browsing) -----
    article_prompt = create_article_prompt(
        symbol=symbol,
        date=date,
        days=days,
        years=years,
        cdata=cdata,
        img_paths=img_paths,
        company=company,
        resource_id=resource_id,
        variant_index=variant,
        byline=byline,
        ai_disclosure=ai_disclosure,
        hero_html=hero_html,
        mode='2',                 # IMPORTANT: Mode 2 uses research JSON, no browsing
        research=research_json, # feed the parsed research into the article prompt
    )

    print("\n--- ARTICLE PROMPT (WITH RESEARCH JSON, MODE 2) ---")
    print(article_prompt)

    exit()