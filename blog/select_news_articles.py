"""
select_news_articles.py

Generates 40-100 article ideas by combining current news with TradeWave
seasonal patterns.  Every idea MUST have a seasonal pattern — the pattern
is what makes the article worth writing on SeasonalMarketNews.com.

Pipeline:
  1. Login to appserver
  2. Fetch weekly OppList4 patterns (next Sat-Fri, multiple year configs)
  3. Tavily: news search (last 4 days) + earnings search (±7 days)
  4. Grok: extract structured ticker list from news
  5. OppBySymbol for every news ticker → patterns in window [today-3, today+30]
     (merge with weekly-scan hits; any ticker without a pattern is dropped)
  6. Grok final ranking: picks best pattern per ticker, assigns publish date,
     writes a one-sentence article angle
  7. Output ranked table + article_ideas.json

Run: python select_news_articles.py
"""

import datetime
from datetime import timedelta
import math
import requests
import sys
import json
import time
import random

sys.path.insert(0, '/home/flask')
import config

import csv
import os
import redis

from blog_tools import get_company_name
from AI_tools import (search_tavily, send_grok_prompt, send_openai_prompt,
                       send_claude_prompt,
                       CLAUDE_OPUS_46, CLAUDE_SONNET_46,
                       CLAUDE_HAIKU_45, CLAUDE_HAIKU_35, CLAUDE_HAIKU_3)

# ============================================================
# CONFIGURATION
# ============================================================

# TW2: read appserver URL from config (env-driven), not the hardcoded TW1 URL.
# This used to be 'https://app1pp.trxstat.com' (the TW1 prod appserver).
PROD_APPSERVER_URL = config.appserver_url.rstrip('/')
USERID = 16

# Resources scanned by weekly OppList4 sweep
SCAN_RESOURCES = [
    (2,  'S&P 500'),
    (5,  'Indices Common'),
    (7,  'Futures & Commodities'),
    (9,  'Forex Liquid'),
    (11, 'ETFs'),
]

# Year configs for OppList4  →  (year1, pyears, api_mode)
# Note: OppList4 uses mode='cons' or 'pe'
WEEKLY_YEAR_CONFIGS = [
    ('10', '10', 'cons'),
    ('10', '10', 'pe'),
    ('15', '13', 'cons'),
    ('20', '17', 'cons'),
    ('30', '25', 'cons'),
]

DAY_RANGES = ['7-30', '31-60']

# OppBySymbol fallback ladders — try each (year1, year2) in order until
# patterns are found in the window.  Lower year2 is more permissive (fewer
# profitable years required) and may reveal dates the tighter combo misses.
# Note: OppBySymbol uses mode='consecutive' or 'pe'  (not 'cons')
#
# Consecutive — stop at 8/8 (below that the signal is too weak)
def _cons_ladder(start, floor_ratio=0.8):
    """
    Generate sequential (year1, year2) ladder rungs for consecutive-year scans.
    Pattern: (n,n) → (n,n-1) → (n-1,n-1) → (n-1,n-2) → ...
    Stops when year2 < start * floor_ratio  (probability floor).
    Example: _cons_ladder(10) → (10,10),(10,9),(9,9),(9,8),(8,8)  [floor=8]
    """
    floor = int(start * floor_ratio)
    rungs, y1, y2 = [], start, start
    while y2 >= floor:
        rungs.append((str(y1), str(y2)))
        if y2 == y1:
            y2 -= 1
        else:
            y1 = y2
    return rungs

# Consecutive — floor at 80% probability (year2 / original_year1 >= 0.8)
# 10yr floor=8:  (10,10),(10,9),(9,9),(9,8),(8,8)
CONS_YEAR_LADDER = _cons_ladder(10)

# PE — go down to 6/5 because 6 PE years ≈ 24 calendar years (still meaningful)
PE_YEAR_LADDER = [
    ('10', '10'), ('10', '9'), ('9', '9'), ('9', '8'), ('8', '8'),
    ('8',  '7'), ('7', '7'), ('7', '6'), ('6', '6'), ('6', '5'),
]

# Longer consecutive lookbacks — generated with same 80% floor rule
# 15yr floor=12: (15,15),(15,14),(14,14),(14,13),(13,13),(13,12),(12,12)
# 20yr floor=16: (20,20),(20,19),(19,19),(19,18),(18,18),(18,17),(17,17),(17,16),(16,16)
# 30yr floor=24: (30,30),(30,29),...,(25,24),(24,24)  — 13 rungs
LONG_CONS_LADDERS = [
    ('15yr', _cons_ladder(15)),
    ('20yr', _cons_ladder(20)),
    ('30yr', _cons_ladder(30)),
]

# Longer PE lookbacks — all asset types
# 15 PE years ≈ 60 calendar years; 20 PE years ≈ 80 calendar years
# Same 80% floor: 15yr floor=12, 20yr floor=16
LONG_PE_LADDERS = [
    ('15yr_pe', _cons_ladder(15)),
    ('20yr_pe', _cons_ladder(20)),
]

# Index-specific extended ladders — only run for indices, only up to their
# actual data depth (avoids wasting API calls on non-existent files).
# Consecutive: 40 → 50 → 60 → 70 → 80 → 90yr, each with 80% floor
INDEX_LONG_CONS_LADDERS = [
    ('40yr', _cons_ladder(40)),   # floor=32
    ('50yr', _cons_ladder(50)),   # floor=40
    ('60yr', _cons_ladder(60)),   # floor=48
    ('70yr', _cons_ladder(70)),   # floor=56
    ('80yr', _cons_ladder(80)),   # floor=64
    ('90yr', _cons_ladder(90)),   # floor=72  — SPX max useful range
]

# Known calendar-year data depth per index symbol.
# Ladders whose starting year1 exceeds the symbol's depth are skipped.
# Default for unknown indices: 30yr (falls back to standard LONG_CONS_LADDERS only).
INDEX_DATA_YEARS = {
    'SPX':  98,  '$SPX': 98,
    'DJI':  75,  'DJU': 38,
    'DJT':  37,  'NYA': 60,
    
    # DJT, DJU, NDX, RUT, VIX etc. get standard 30yr treatment (default below)
}
INDEX_DEFAULT_MAX_YEARS = 30

# PE ladder for deep-data indices (SPX, DJI, etc.).
# Keeps y1 fixed at the symbol's maximum PE years and steps y2 down
# while y2/y1 > 0.9 (90–100% success rate window).
# Max PE years = INDEX_DATA_YEARS[symbol] // 4  (dynamic — auto-adjusts if
# data depth grows, e.g. SPX reaches 100 cal yrs → 25 PE years).
#
# Examples:
#   SPX (98 cal yrs → 24 PE):  (24,24),(24,23),(24,22)   — 3 rungs
#   DJI (75 cal yrs → 18 PE):  (18,18),(18,17)           — 2 rungs
#   SPX future (100 cal yrs → 25 PE): (25,25),(25,24),(25,23) — 3 rungs
def _index_pe_ladder(max_pe_years, min_ratio=0.9):
    """
    Fixed-y1 PE ladder: keeps y1=max_pe_years, steps y2 down while
    y2/y1 >= min_ratio.  Only called for indices with max_pe_years > 10.
    """
    min_y2 = math.ceil(max_pe_years * min_ratio)
    return [(str(max_pe_years), str(y2))
            for y2 in range(max_pe_years, min_y2 - 1, -1)]

# Resource IDs to try for OppBySymbol by asset type (ordered: most likely first)
OBS_RESOURCES = {
    'stock':     [2, 1, 0, 3],
    'index':     [5, 6],
    'futures':   [7],
    'commodity': [7],
    'etf':       [11],
    'forex':     [9],
    'unknown':   [2, 5, 7, 9, 11, 1, 0],
}

# Tickers that exist in multiple asset classes (stock + futures).
# When these appear, only keep patterns from the asset_type Grok identified.
# Prevents e.g. crude oil news → Colgate-Palmolive article.
AMBIGUOUS_TICKERS = {'CL', 'GC', 'SI', 'NG', 'HG', 'HO', 'RB'}

# Tavily search windows
NEWS_DAYS     = 7   # last 7 days for general news
EARNINGS_DAYS = 11  # covers 4-day past + 7-day future earnings window

# Pattern quality minimums (applied to weekly scan; OBS applies its own top_pct)
MIN_SR   = 0.7
MIN_AVGP = 3.5

# OppBySymbol pattern window relative to today
OBS_PAST_DAYS    = 3   # look back – catches recently-started patterns still in play
OBS_HORIZON_DAYS = 30  # look ahead – 30-day max (OppBySymbol window)

# OppList4 forward scan window — how far ahead the pattern sweep looks.
# 14 days is enough for daily mode (heads-up articles for next 2 weeks).
OPP_LIST_HORIZON_DAYS = 14

# Symbol activity check — last traded price must be within this many days.
# Covers weekends + holidays; symbols older than this are likely delisted/suspended.
SYMBOL_MAX_STALE_DAYS = 10

# Min SR to keep a pattern from OppBySymbol
OBS_MIN_SR   = 1.0
OBS_MIN_AVGP = 4.0

# Output — daily mode generates 10-20 ideas; 3AM queuing job picks how many to publish.
# Files are date-stamped with TODAY so each nightly run has its own output.
TARGET_IDEAS_MAX = 20
IDEAS_DIR       = '/home/flask/blog/article_ideas'
OUTPUT_FILE_TPL = IDEAS_DIR + '/article_ideas_{}.json'
CSV_FILE_TPL    = IDEAS_DIR + '/article_queue_{}.csv'

# ---------- Published-article dedup ----------
# Path to the news site posts.json (tracks every published article)
POSTS_JSON_PATH  = config.news_root_folder.rstrip('/') + '/posts.json'
HARD_MIN_DAYS    = 4   # hard skip — never repeat a ticker within this many days
SOFT_MIN_DAYS    = 7   # soft preference — flag and lower score if < 7 days

# ---------- Volume data ----------
VOLUME_LIST_PATH   = '/home/flask/blog/volume_lists/highest_volume_list.csv'
VOLUME_SPIKES_PATH = '/home/flask/blog/volume_lists/highest_volume_spikes.csv'
VOLUME_SPIKE_MIN_RVOL  = 1.5   # minimum rvol to treat as a spike signal
VOLUME_LIST_MIN_RVOL   = 1.3   # minimum rvol for high-volume-list bonus

# ---------- Ranking LLM ----------
# Claude (Anthropic) — recommended:
#   'sonnet46'  → claude-sonnet-4-6           strong + fast, best balance
#   'opus46'    → claude-opus-4-6             most capable, best angles
#   'haiku45'   → claude-haiku-4-5-20251001   fast + cheap
#   'haiku35'   → claude-3-5-haiku-20241022   very cheap
#   'haiku3'    → claude-3-haiku-20240307     cheapest
# OpenAI:
#   'gpt5'      → GPT-5.1
# Grok (xAI):
#   'grok3'     → grok-3
#   'grok3mini' → grok-3-mini
RANKING_LLM = 'sonnet46'

# ---------- Date spreading ----------
MAX_ARTICLES_PER_DAY = 4   # cap articles on any single day during spreading

TODAY    = datetime.date.today()
PE_PHASE = TODAY.year % 4   # 2026 → 2,  2027 → 3

# ---------- PE+2 / Midterm-year pattern preferences ----------
# In PE+2 years (midterm election years, e.g. 2026) TradeWave shows higher
# probability of summer market downturns.  Monetary-policy-sensitive sectors
# (energy, big-cap tech, defense, financials, broad indices) see sharper cycle
# effects, and longer consecutive lookbacks (≥15yr) better confirm these
# tendencies than the default 10-year window.
PE2_YEAR = (PE_PHASE == 2)

PE2_SENSITIVE_TICKERS = {
    # Energy — Fed tightening / oil-price cycles hit directly
    'XOM','CVX','COP','EOG','SLB','PSX','VLO','MPC','OXY','PXD',
    'XLE','XOP','USO','UNG',
    # Energy futures
    'CL','NG','HO','RB',
    # Defense — government-spending politics are acute in midterm years
    'LMT','RTX','NOC','GD','BA','HII','LDOS','SAIC',
    'XAR','ITA',
    # Big-cap tech & broad indices — rate-sensitive valuations
    'AAPL','MSFT','GOOGL','GOOG','AMZN','META','NVDA','TSLA',
    'SPX','NDX','DJI','RUT','SPY','QQQ','IWM',
    # Financials / rates
    'JPM','BAC','GS','MS','WFC','C',
    'XLF','XLK','XLV','TLT','IEF',
    # Gold / safe-haven — cycle turning points / rate uncertainty
    'GLD','GC','SI','SLV',
}

PE2_PE_PATTERN_BONUS  = 1.5   # pre_score bonus: PE-mode pattern in sensitive sector (PE+2 yr)
PE2_LONG_CONS_BONUS   = 1.0   # pre_score bonus: consecutive ≥15yr lookback (PE+2 yr)
PE2_LONG_CONS_MIN_YRS = 15    # consecutive year threshold to earn the long-cons bonus

# PE+2 Danger Zone — midterm-year summer downturn window
PE2_DANGER_START = (4, 15)   # April 15
PE2_DANGER_END   = (9, 27)   # September 27
PE2_DANGER_MIN_CONS_YRS     = 12   # consecutive minimum during danger zone
PE2_PRE_DANGER_MIN_CONS_YRS = 10   # consecutive minimum before danger zone
PE2_MIN_PE_PROFITABLE_YRS   = 5    # PE-mode minimum profitable years


def pe2_filter_patterns(patterns):
    """
    Filter patterns for PE+2 year danger zone rules.

    In PE+2 years (midterm election years), bullish short-consecutive patterns
    are unreliable during the Apr 15 – Sep 27 danger zone because they contain
    only ~2-3 midterm-year samples.  This function enforces hard quality floors:

      Before danger zone (start_date < Apr 15):
        - Bullish consecutive: must be N/N winners (100%), minimum 10yr
        - Bullish PE-mode: ≥5 profitable PE years
        - Bearish: no restriction

      Danger zone (Apr 15 – Sep 27):
        - Bullish consecutive: must be ≥12yr AND 12/12 winners (100%)
        - Bullish PE-mode: ≥5 profitable PE years
        - Bearish: no restriction

      After danger zone (start_date > Sep 27):
        - All patterns pass (normal rules)

    Returns (filtered_list, removed_count).
    """
    if not PE2_YEAR or not patterns:
        return patterns, 0

    filtered = []
    removed = 0
    for p in patterns:
        # Parse (month, day) from start_date string "YYYY-MM-DD"
        try:
            sd = p['start_date']
            start_md = (int(sd[5:7]), int(sd[8:10]))
        except (KeyError, ValueError, IndexError):
            filtered.append(p)
            continue

        direction = p.get('direction', '').lower()
        mode = _norm_mode(p.get('mode', ''))
        years = int(p.get('years', 0))
        pyears = int(p.get('pyears', 0))
        is_bullish = (direction == 'long')
        is_cons = (mode == 'cons')
        is_pe = (mode == 'pe')

        # Bearish patterns — always pass
        if not is_bullish:
            filtered.append(p)
            continue

        # After Sep 27 — normal rules, no restriction
        if start_md > PE2_DANGER_END:
            filtered.append(p)
            continue

        # Danger zone (Apr 15 – Sep 27)
        if start_md >= PE2_DANGER_START:
            if is_pe and pyears >= PE2_MIN_PE_PROFITABLE_YRS:
                filtered.append(p)
            elif is_cons and years >= PE2_DANGER_MIN_CONS_YRS and pyears >= years:
                filtered.append(p)
            else:
                removed += 1
            continue

        # Before danger zone (before Apr 15)
        if is_pe and pyears >= PE2_MIN_PE_PROFITABLE_YRS:
            filtered.append(p)
        elif is_cons and years >= PE2_PRE_DANGER_MIN_CONS_YRS and pyears >= years:
            filtered.append(p)
        else:
            removed += 1

    return filtered, removed


def divergence_filter_patterns(patterns):
    """
    Drop consecutive-mode patterns whose direction is contradicted by a
    PE-mode pattern covering an overlapping date window.

    When consecutive (all calendar years) and PE (election-cycle filtered)
    patterns disagree on direction for the same approximate time window,
    the PE signal is more trustworthy during PE-cycle years because it
    directly models the election-cycle effect.  This mirrors the logic
    the security pages use when they detect divergence and default to
    showing the PE chart.

    Matching rules:
      - Two patterns "overlap" when their start dates are within 14 days
        of each other AND their hold durations overlap by at least 50%.
      - If a consecutive Long is contradicted by a PE Short (or vice
        versa), the consecutive pattern is removed.
      - Consecutive patterns with no PE counterpart are kept as-is.
      - PE patterns are never removed by this filter.

    Returns (filtered_list, removed_count).
    """
    if not patterns or len(patterns) < 2:
        return patterns, 0

    cons_pats = [p for p in patterns if _norm_mode(p.get('mode', '')) == 'cons']
    pe_pats   = [p for p in patterns if _norm_mode(p.get('mode', '')) == 'pe']

    if not cons_pats or not pe_pats:
        return patterns, 0

    def _parse_date(s):
        try:
            return datetime.date.fromisoformat(s[:10])
        except Exception:
            return None

    def _overlaps(c, pe):
        """Check if a consecutive and PE pattern cover an overlapping window."""
        c_start  = _parse_date(c.get('start_date', ''))
        pe_start = _parse_date(pe.get('start_date', ''))
        if not c_start or not pe_start:
            return False
        # Start dates within 14 days of each other
        if abs((c_start - pe_start).days) > 14:
            return False
        # Hold durations overlap by at least 50%
        try:
            c_days  = int(c.get('days', 0))
            pe_days = int(pe.get('days', 0))
        except (ValueError, TypeError):
            return False
        if c_days <= 0 or pe_days <= 0:
            return False
        c_end  = c_start + timedelta(days=c_days)
        pe_end = pe_start + timedelta(days=pe_days)
        overlap_start = max(c_start, pe_start)
        overlap_end   = min(c_end, pe_end)
        overlap_days  = max(0, (overlap_end - overlap_start).days)
        min_hold = min(c_days, pe_days)
        return overlap_days >= (min_hold * 0.5)

    # Find consecutive patterns contradicted by a PE pattern
    contradicted = set()
    for ci, c in enumerate(cons_pats):
        c_dir = c.get('direction', '').lower()
        for pe in pe_pats:
            pe_dir = pe.get('direction', '').lower()
            if c_dir and pe_dir and c_dir != pe_dir and _overlaps(c, pe):
                contradicted.add(id(c))
                break

    if not contradicted:
        return patterns, 0

    filtered = [p for p in patterns if id(p) not in contradicted]
    removed  = len(contradicted)
    return filtered, removed


# ============================================================
# APPSERVER AUTH
# ============================================================

def get_keyprovider_token():
    # TW2: legacy keyprovider hack is dead; SMN authenticates via SERVICE_API_KEY.
    api_key = os.environ.get('SERVICE_API_KEY', '')
    if not api_key:
        raise RuntimeError(
            'SERVICE_API_KEY not set in environment. SMN service requires it to '
            'authenticate with the TW2 appserver via /login/api/<key>.'
        )
    return api_key


def login_appserver(kp_token):
    # TW2: kp_token is the SERVICE_API_KEY supplied by get_keyprovider_token.
    url    = f'{PROD_APPSERVER_URL}/login/api/{kp_token}'
    result = requests.get(url, timeout=15).json()
    if 'token' not in result:
        time.sleep(5)
        result = requests.get(url, timeout=15).json()
        if 'token' not in result:
            return None
    return result.get('token')


# ============================================================
# PUBLISHED-ARTICLE HISTORY (posts.json dedup)
# ============================================================

def load_published_articles(path=POSTS_JSON_PATH, days_back=30):
    """
    Read posts.json and return {TICKER: most_recent_date_str} for articles
    published in the last `days_back` days.  Both 'symbol' and 'tickers'
    fields are indexed so aliases are caught.
    """
    cutoff = TODAY - timedelta(days=days_back)
    ticker_latest = {}   # ticker -> most-recent ISO date string

    try:
        with open(path) as f:
            posts = json.load(f)
    except Exception as e:
        print(f'  Warning: could not load posts.json ({e}) — dedup disabled')
        return {}

    for post in posts:
        pub_str = str(post.get('published_date', ''))[:10]
        try:
            pub_date = datetime.date.fromisoformat(pub_str)
        except Exception:
            continue
        if pub_date < cutoff:
            continue

        tickers_in_post = set()
        sym = str(post.get('symbol', '')).upper().strip()
        if sym:
            tickers_in_post.add(sym)
        for t in post.get('tickers', []):
            t = str(t).upper().strip()
            if t:
                tickers_in_post.add(t)

        for t in tickers_in_post:
            if t not in ticker_latest or pub_str > ticker_latest[t]:
                ticker_latest[t] = pub_str

    return ticker_latest


def load_queued_articles():
    """
    Non-destructively read all pending jobs from the Redis news queue.
    Returns {TICKER: most_recent_publish_date_str} for every article that
    is queued but not yet published — covers both automation-queued jobs
    AND articles manually queued from the TradeWave dashboard.

    Uses lrange (read-only) so nothing is consumed from the queue.
    """
    try:
        r = redis.Redis(host='localhost', port=6379, db=config.articles_redis_db)
        queued = {}
        for item in r.lrange(config.NEWS_QUEUE_NAME, 0, -1):
            try:
                job = json.loads(item)
                sym = str(job.get('symbol', '')).upper().strip()
                pub = str(job.get('article_publish_date', ''))[:10]
                if sym and pub:
                    if sym not in queued or pub > queued[sym]:
                        queued[sym] = pub
            except Exception:
                continue
        return queued
    except Exception as e:
        print(f'  Warning: could not read Redis queue ({e}) — queued-article dedup disabled')
        return {}


def load_article_history(days_back=30):
    """
    Merge published articles (posts.json) and queued-but-pending articles
    (Redis queue) into a single {TICKER: most_recent_date_str} map.
    This is the definitive dedup source — whichever date is more recent wins.
    """
    published = load_published_articles(days_back=days_back)
    queued    = load_queued_articles()
    merged    = dict(published)
    for ticker, date_str in queued.items():
        if ticker not in merged or date_str > merged[ticker]:
            merged[ticker] = date_str
    return merged


def days_since_last_article(ticker, ticker_latest):
    """Return integer days since last article, or 999 if none."""
    last = ticker_latest.get(ticker.upper())
    if not last:
        return 999
    try:
        return (TODAY - datetime.date.fromisoformat(last)).days
    except Exception:
        return 999


# ============================================================
# VOLUME DATA
# ============================================================

def load_volume_data():
    """
    Load both volume CSVs.
    Returns (volume_list, volume_spikes) — each a list of dicts with
    keys: ticker, avg_volume_30d, today_volume, rvol
    """
    def _read_csv(path):
        rows = []
        try:
            with open(path, newline='') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        rows.append({
                            'ticker': row['ticker'].upper().strip(),
                            'avg_volume_30d': int(row['avg_volume_30d']),
                            'today_volume':   int(row['today_volume']),
                            'rvol':           float(row['rvol']),
                        })
                    except Exception:
                        continue
        except Exception as e:
            print(f'  Warning: could not load {path}: {e}')
        return rows

    volume_list   = _read_csv(VOLUME_LIST_PATH)
    volume_spikes = _read_csv(VOLUME_SPIKES_PATH)

    # Only keep genuine spikes
    volume_spikes = [r for r in volume_spikes if r['rvol'] >= VOLUME_SPIKE_MIN_RVOL]
    volume_list   = [r for r in volume_list   if r['rvol'] >= VOLUME_LIST_MIN_RVOL]

    return volume_list, volume_spikes


# ============================================================
# PUBLISH WINDOW + DATE SPREADING
# ============================================================

def get_publish_window():
    """
    Returns (pub_start, pub_end) = upcoming Monday through Saturday (6 days).
    Articles are always published this week regardless of when the pattern starts.

    If today is Monday: pub_start = today (publish Mon-Sat this week).
    Any other day     : pub_start = next Monday.
    """
    wd = TODAY.weekday()   # Mon=0, Tue=1, ..., Sat=5, Sun=6
    days_to_monday = (7 - wd) % 7   # 0 if today is Monday
    pub_start = TODAY + timedelta(days=days_to_monday)
    pub_end   = pub_start + timedelta(days=5)   # Saturday
    return pub_start, pub_end


def spread_article_dates(ideas, pub_start, pub_end, max_per_day=MAX_ARTICLES_PER_DAY):
    """
    Redistribute LLM-assigned publish dates so articles are spread evenly
    across the Mon-Sat publish window.

    Rules:
      - Earnings articles that fall inside the publish window keep their date
      - All other articles (and out-of-window earnings) are assigned to the
        least-filled weekday in [pub_start, pub_end], subject to max_per_day
      - Publish dates are ALWAYS within this week's Mon-Sat — never outside
    Ideas are sorted by article_date then grok_score on exit.
    """
    avail_days = []
    cur = pub_start
    while cur <= pub_end:
        avail_days.append(cur)
        cur += timedelta(days=1)

    day_counts = {}   # date ISO str → count

    # Earnings articles whose date falls inside the publish window keep their date;
    # anything outside the window is treated as flexible.
    locked   = [i for i in ideas
                if i.get('earnings_type')
                and pub_start
                   <= datetime.date.fromisoformat(str(i.get('article_date', '9999-01-01'))[:10])
                   <= pub_end]
    flexible = [i for i in ideas if i not in locked]

    for idea in locked:
        day_counts[idea['article_date']] = day_counts.get(idea['article_date'], 0) + 1

    # Sort flexible by score desc — highest priority gets preferred day
    flexible.sort(key=lambda x: x.get('grok_score', 0), reverse=True)

    for idea in flexible:
        preferred_str = idea.get('article_date', pub_start.isoformat())
        try:
            preferred_dt = datetime.date.fromisoformat(preferred_str[:10])
        except Exception:
            preferred_dt = pub_start

        # Clamp to publish window (Mon-Sat this week)
        if preferred_dt < pub_start: preferred_dt = pub_start
        if preferred_dt > pub_end:   preferred_dt = pub_end

        # Find best day: lowest count, closest to preferred (secondary sort).
        # max_per_day is a soft cap — if all days are full, overflow to the
        # least-full day so every article always gets a valid publish date.
        best_day = min(
            avail_days,
            key=lambda d: (
                day_counts.get(d.isoformat(), 0),   # primary: fewest articles
                abs((d - preferred_dt).days),        # secondary: closest to preferred
            )
        )
        idea['article_date'] = best_day.isoformat()
        day_counts[best_day.isoformat()] = day_counts.get(best_day.isoformat(), 0) + 1

    result = locked + flexible
    result.sort(key=lambda x: (x['article_date'], -x.get('grok_score', 0)))
    return result


# ============================================================
# DATE UTILITIES
# ============================================================

def get_week_range(from_date):
    """
    Next Saturday-to-Friday window + list of OppList4 API call dates.
    Saturday covers Sat/Sun/Mon; Tue-Fri each get their own call.
    """
    days_ahead = (5 - from_date.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    week_start = from_date + timedelta(days=days_ahead)
    week_end   = week_start + timedelta(days=6)

    dates, cur = [], week_start
    while cur <= week_end:
        wd = cur.weekday()
        if wd == 5:
            dates.append(cur); cur += timedelta(days=3)
        elif wd == 6:
            sat = cur - timedelta(days=1)
            if sat not in dates: dates.append(sat)
            cur += timedelta(days=2)
        elif wd == 0:
            sat = cur - timedelta(days=2)
            if sat not in dates: dates.append(sat)
            cur += timedelta(days=1)
        else:
            dates.append(cur); cur += timedelta(days=1)

    return week_start, week_end, sorted(set(dates))


def get_scan_range(horizon_days=None):
    """
    Generate OppList4 API call dates for the next `horizon_days` calendar
    days starting from TODAY.  Saturday covers Sat/Sun/Mon; Tue-Fri each
    get their own call date.
    Returns (scan_start, scan_end, api_dates).
    """
    if horizon_days is None:
        horizon_days = OPP_LIST_HORIZON_DAYS
    scan_start = TODAY
    scan_end   = TODAY + timedelta(days=horizon_days)
    api_dates  = []
    cur = scan_start
    while cur <= scan_end:
        wd = cur.weekday()
        if wd == 5:    # Saturday — covers Sat/Sun/Mon
            api_dates.append(cur); cur += timedelta(days=3)
        elif wd == 6:  # Sunday — covered by Saturday
            cur += timedelta(days=2)
        elif wd == 0:  # Monday — covered by Saturday
            cur += timedelta(days=1)
        else:          # Tue-Fri
            api_dates.append(cur); cur += timedelta(days=1)
    return scan_start, scan_end, sorted(set(api_dates))


# ============================================================
# WEEKLY PATTERN SWEEP (OppList4)
# ============================================================

def _opp_list(resource_id, month, day, years, pyears, day_range, token, mode):
    url = (f'{PROD_APPSERVER_URL}/OppList4/{resource_id}/{month}/{day}/'
           f'{years}/{pyears}/{day_range}/0/0?token={token}&mode={mode}')
    try:
        return requests.get(url, timeout=30).json()
    except Exception:
        return {}


def fetch_weekly_patterns(token, scan_start, scan_end, api_dates):
    """
    Sweep all resource × year-config × day-range × date combos.
    Returns {symbol: [pattern_dict, ...]} for patterns within the scan window.
    """
    patterns = {}
    seen     = set()
    total    = (len(SCAN_RESOURCES) * len(WEEKLY_YEAR_CONFIGS)
                * len(DAY_RANGES) * len(api_dates))
    done     = 0

    for resource_id, res_name in SCAN_RESOURCES:
        for years, pyears, mode in WEEKLY_YEAR_CONFIGS:
            for day_range in DAY_RANGES:
                for call_date in api_dates:
                    done += 1
                    result = _opp_list(
                        resource_id,
                        call_date.strftime('%B'), str(call_date.day),
                        years, pyears, day_range, token, mode
                    )
                    for opp in result.get('OppList', []):
                        try:
                            opp_date = datetime.datetime.strptime(
                                opp[0], '%Y-%m-%d').date()
                        except Exception:
                            continue
                        if not (scan_start <= opp_date <= scan_end):
                            continue
                        key = (resource_id, opp[1], opp[0], mode, years)
                        if key in seen:
                            continue
                        seen.add(key)
                        try:
                            sr = float(opp[4]); avgp = float(opp[5])
                        except Exception:
                            continue
                        if sr < MIN_SR or avgp < MIN_AVGP:
                            continue
                        pat = {
                            'resource_id':   resource_id,
                            'resource_name': res_name,
                            'start_date':    opp[0],
                            'days':          opp[2],
                            'direction':     opp[3],
                            'SR':            sr,
                            'AvgP':          avgp,
                            'median':        float(opp[6]) if len(opp) > 6 else 0.0,
                            'TWA':           float(opp[7]) if len(opp) > 7 else 0.0,
                            'TWR':           float(opp[8]) if len(opp) > 8 else 0.0,
                            'day_range':     day_range,
                            'mode':          mode,
                            'years':         years,
                            'pyears':        pyears,
                            'source':        'weekly_scan',
                        }
                        patterns.setdefault(opp[1], []).append(pat)

        pct = int(100 * done / total)
        print(f'    [{res_name}] {pct}% done — '
              f'{len(patterns)} unique symbols with patterns so far')

    return patterns


# ============================================================
# OppBySymbol — PATTERN LOOKUP FOR INDIVIDUAL SYMBOLS
# ============================================================

def _obs_call(token, resource_id, symbol, year1, year2, mode):
    """
    Raw OppBySymbol call.  Always uses day_range='-' (no hold-duration filter)
    and top_pct=100 so we get the full result set and can window-filter locally.

    Returns:
      list of rows (possibly []) — status was 'ok'
      None                       — status was 'feature_not_available' or error
                                   (this specific year1/year2/mode file doesn't
                                    exist for this symbol in this resource)
    """
    url = (f'{PROD_APPSERVER_URL}/OppBySymbol/{resource_id}/{symbol}/'
           f'{year1}/{year2}/-/100?token={token}&mode={mode}')
    try:
        data = requests.get(url, timeout=30).json()
        if data.get('status') == 'ok':
            return data.get('OppBySymbol', [])
        # 'feature_not_available' → file missing for this combo → return None
    except Exception:
        pass
    return None


def _filter_obs_rows(rows, window_start, window_end, year1, year2, mode, resource_id):
    """
    Filter raw OBS rows to the date window and quality thresholds.
    Returns a list of pattern dicts.
    """
    out = []
    for row in rows:
        try:
            opp_date = datetime.datetime.strptime(row[0], '%Y-%m-%d').date()
            sr       = float(row[4])
            avgp     = float(row[5])
        except Exception:
            continue
        if not (window_start <= opp_date <= window_end):
            continue
        if sr < OBS_MIN_SR or avgp < OBS_MIN_AVGP:
            continue
        out.append({
            'resource_id':   resource_id,
            'resource_name': f'res{resource_id}',
            'start_date':    row[0],
            'days':          row[2],
            'direction':     row[3],
            'SR':            sr,
            'AvgP':          avgp,
            'median':        float(row[6]) if len(row) > 6 else 0.0,
            'TWA':           float(row[7]) if len(row) > 7 else 0.0,
            'TWR':           float(row[8]) if len(row) > 8 else 0.0,
            'day_range':     '-',
            'mode':          mode,
            'years':         year1,
            'pyears':        year2,
            'source':        'obs',
        })
    return out


def _check_symbol_active(token, resource_id, symbol):
    """
    Call StockLastPrice to verify the symbol is still actively traded.
    Returns True  — last price date is within SYMBOL_MAX_STALE_DAYS
    Returns False — delisted ([0,0] response), not found, or data too stale

    On any network/parse error we return True (don't filter on uncertainty).
    """
    url = (f'{PROD_APPSERVER_URL}/StockLastPrice/{resource_id}/{symbol}'
           f'?token={token}')
    try:
        data = requests.get(url, timeout=15).json()
        slp = data.get('StockLastPrice')
        if not slp or slp == [0, 0]:
            return False   # "Not Traded" response
        last_date = datetime.date.fromisoformat(str(slp[0])[:10])
        return (TODAY - last_date).days <= SYMBOL_MAX_STALE_DAYS
    except Exception:
        return True   # assume active on error — don't drop on uncertainty


def _find_resource_for_symbol(token, symbol, resource_ids):
    """
    Identify which resource_id contains this symbol by probing with the most
    permissive combos so we don't miss symbols with limited history.
    Returns the first matching resource_id, or None.
    """
    # Try consecutive first (most common), then PE fallback
    discovery = [
        ('10', '8', 'consecutive'),
        ('8',  '6', 'consecutive'),
        ('6',  '5', 'pe'),
    ]
    for rid in resource_ids:
        for y1, y2, mode in discovery:
            rows = _obs_call(token, rid, symbol, y1, y2, mode)
            if rows is not None:   # [] also means "in this resource, file exists"
                return rid
    return None


def _run_ladder(token, resource_id, symbol, ladder, mode, window_start, window_end):
    """
    Walk a (year1, year2) ladder for one mode, stopping at the first rung
    that returns qualifying patterns inside the window.

    Ladder advance rules:
      - rows is None  → file missing for this combo (feature_not_available)
                        → try next rung
      - rows is []    → file exists but no patterns at all
                        → try next rung
      - rows has data but zero window hits
                        → try next rung (lower year2 adds NEW dates not in
                          tighter combo; some may fall in our window)
      - rows has data AND window hits → use them, stop

    Returns list of pattern dicts (may be empty if whole ladder yields nothing).
    """
    tried = []
    for year1, year2 in ladder:
        tried.append(f'{year1}/{year2}')
        rows = _obs_call(token, resource_id, symbol, year1, year2, mode)
        if rows is None:
            continue            # file missing for this combo
        hits = _filter_obs_rows(rows, window_start, window_end,
                                year1, year2, mode, resource_id)
        if hits:
            rung_label = f'{year1}/{year2}'
            mode_short = 'pe' if mode == 'pe' else 'cons'
            if len(tried) > 1:
                print(f'      [{mode_short}] found at {rung_label} '
                      f'(tried: {", ".join(tried)})')
            return hits         # found qualifying patterns — stop
        # file had rows but none in window → continue down ladder
    return []


def _norm_mode(m):
    """Normalize mode string for dedup keying so OppList4 ('cons') and
    OppBySymbol ('consecutive') don't create duplicate keys for the same pattern."""
    return 'pe' if m == 'pe' else 'cons'


def lookup_patterns_for_symbol(token, symbol, asset_type='unknown'):
    """
    Pull seasonal patterns for `symbol` across all modes and lookback periods,
    within the window [today - OBS_PAST_DAYS, today + OBS_HORIZON_DAYS].

    For indices (SPX, DJI, NDX etc.) also runs extended ladders up to 100yr
    consecutive and 24 PE years, since indices have much longer history.

    Returns up to 8 pattern dicts sorted by SR descending, or [] if nothing.
    """
    window_start = TODAY - timedelta(days=OBS_PAST_DAYS)
    window_end   = TODAY + timedelta(days=OBS_HORIZON_DAYS)
    is_index      = asset_type == 'index'
    max_cal_years = INDEX_DATA_YEARS.get(symbol.upper(),
                    INDEX_DEFAULT_MAX_YEARS) if is_index else 0

    resource_ids = OBS_RESOURCES.get(asset_type, OBS_RESOURCES['unknown'])
    resource_id  = _find_resource_for_symbol(token, symbol, resource_ids)
    if resource_id is None:
        return []

    # Gate: skip symbols that are no longer actively traded
    if not _check_symbol_active(token, resource_id, symbol):
        print(f'      [{symbol}] delisted/stale — skipped')
        return []

    all_patterns = []

    # --- 10yr consecutive ladder ---
    hits = _run_ladder(token, resource_id, symbol,
                       CONS_YEAR_LADDER, 'consecutive', window_start, window_end)
    all_patterns.extend(hits)

    # --- PE ladder ---
    # Indices with more than 10 PE years of data: build a dynamic fixed-y1
    # ladder starting at their maximum PE depth and stepping y2 down while
    # y2/y1 >= 0.9.  max_pe_years is derived from INDEX_DATA_YEARS // 4 so
    # it auto-adjusts if the data set grows.
    max_pe_years = max_cal_years // 4 if is_index else 0
    if is_index and max_pe_years > 10:
        index_pe_ldr = _index_pe_ladder(max_pe_years)
        hits = _run_ladder(token, resource_id, symbol,
                           index_pe_ldr, 'pe', window_start, window_end)
        all_patterns.extend(hits)
    # Standard PE ladder (10 PE yrs → 6) — all asset types
    hits = _run_ladder(token, resource_id, symbol,
                       PE_YEAR_LADDER, 'pe', window_start, window_end)
    all_patterns.extend(hits)

    # --- Longer PE (15yr, 20yr) — all asset types ---
    for label, ladder in LONG_PE_LADDERS:
        hits = _run_ladder(token, resource_id, symbol,
                           ladder, 'pe', window_start, window_end)
        all_patterns.extend(hits)

    # --- Longer consecutive (15yr, 20yr, 30yr) — all asset types ---
    for label, ladder in LONG_CONS_LADDERS:
        hits = _run_ladder(token, resource_id, symbol,
                           ladder, 'consecutive', window_start, window_end)
        all_patterns.extend(hits)

    # --- Index-only extended consecutive (40yr → 90yr) ---
    # Skip ladders whose starting year exceeds the symbol's known data depth.
    if is_index:
        for label, ladder in INDEX_LONG_CONS_LADDERS:
            start_yr = int(ladder[0][0])
            if start_yr > max_cal_years:
                continue   # no data that far back — skip entirely
            hits = _run_ladder(token, resource_id, symbol,
                               ladder, 'consecutive', window_start, window_end)
            all_patterns.extend(hits)

    # Deduplicate by (start_date, normalised_mode, years) — keep highest SR.
    # _norm_mode maps 'consecutive' → 'cons' so OppList4 and OppBySymbol hits
    # for the same start_date/years are correctly recognised as the same pattern.
    deduped = {}
    for p in all_patterns:
        key = (p['start_date'], _norm_mode(p['mode']), p['years'])
        if key not in deduped or p['SR'] > deduped[key]['SR']:
            deduped[key] = p

    result = sorted(deduped.values(), key=lambda x: x['SR'], reverse=True)
    # Indices can have many more pattern types (extended cons + extended PE)
    max_pats = 16 if is_index else 8
    return result[:max_pats]


def merge_patterns(weekly_pats, obs_pats):
    """
    Merge weekly-scan and OppBySymbol patterns, deduplicating by
    (start_date, normalised_mode, years).  Best SR wins for duplicates.

    OppList4 emits mode='cons'; OppBySymbol emits mode='consecutive'.
    _norm_mode maps both to 'cons' so the same pattern isn't counted twice.
    """
    combined = {}
    for p in (weekly_pats or []) + (obs_pats or []):
        key = (p['start_date'], _norm_mode(p['mode']), p['years'])
        if key not in combined or p['SR'] > combined[key]['SR']:
            combined[key] = p
    return sorted(combined.values(), key=lambda x: x['SR'], reverse=True)


def filter_patterns_by_asset_type(patterns, asset_type):
    """For ambiguous tickers, keep only patterns from resources matching asset_type."""
    allowed = set(OBS_RESOURCES.get(asset_type, OBS_RESOURCES['unknown']))
    return [p for p in patterns if p.get('resource_id') in allowed]


# ============================================================
# TAVILY NEWS RESEARCH
# ============================================================

_MONTH_YEAR = TODAY.strftime('%B %Y')

# News queries — ordered by signal quality for SeasonalMarketNews.com.
# The first NEWS_SEARCH_COUNT are used each run.  Queries are deliberately
# varied across categories so Grok gets a broad, non-redundant news universe.
NEWS_SEARCH_COUNT = 10

NEWS_QUERIES = [
    # --- Earnings & analyst moves (highest article value) ---
    f"stock earnings beat miss raised guidance analyst upgrade downgrade {_MONTH_YEAR}",
    f"earnings results reaction stock price move after hours {_MONTH_YEAR}",

    # --- Market-moving macro & Fed ---
    "Federal Reserve interest rates inflation CPI jobs data market impact stocks",
    f"sector rotation institutional money flow Wall Street {_MONTH_YEAR}",

    # --- Big-name movers (always relevant) ---
    "Apple Microsoft Google Amazon Meta NVIDIA Tesla stock news price move",
    "S&P 500 NASDAQ Dow Jones market movers biggest gainers losers today",

    # --- Commodities & futures (pattern-rich universe) ---
    "gold silver crude oil natural gas copper wheat corn futures price outlook",
    "commodity supply demand inventory OPEC agricultural metals energy market",

    # --- Unusual activity & short-term catalysts ---
    "unusual options activity high short interest squeeze dark pool volume spike stocks",
    "FDA approval drug trial result biotech catalyst clinical stock move",

    # --- Sector-specific deep dives ---
    "semiconductor AI chip demand supply Broadcom Intel AMD NVDA TSMC outlook",
    "bank financial earnings JPMorgan Goldman Wells Fargo credit consumer spending",
    "energy sector Chevron Exxon ConocoPhillips natural gas pipeline dividend",
    "retail consumer discretionary holiday sales tariff impact Walmart Target",

    # --- Currency & international (forex patterns) ---
    "US dollar EUR USD GBP JPY currency strength weakness Fed impact forex",
]

EARNINGS_QUERIES = [
    f"earnings calendar this week next week upcoming reports stocks {_MONTH_YEAR}",
    f"quarterly earnings results beat miss EPS revenue guidance {_MONTH_YEAR}",
    f"earnings surprise reaction stock price analyst revision {_MONTH_YEAR}",
]


def run_news_research():
    """Tavily searches.  Returns (news_results, earnings_results)."""
    news_results     = []
    earnings_results = []

    print('  News searches:')
    for query in NEWS_QUERIES[:NEWS_SEARCH_COUNT]:
        try:
            print(f"    '{query[:65]}'")
            resp = search_tavily(query, days=NEWS_DAYS, max_results=8)
            news_results.extend(resp.get('results', []))
            time.sleep(0.3)
        except Exception as e:
            print(f'    Warning: {e}')

    print('  Earnings searches:')
    for query in EARNINGS_QUERIES:
        try:
            print(f"    '{query[:65]}'")
            resp = search_tavily(query, days=EARNINGS_DAYS, max_results=8)
            earnings_results.extend(resp.get('results', []))
            time.sleep(0.3)
        except Exception as e:
            print(f'    Warning: {e}')

    return news_results, earnings_results


def extract_tickers_with_grok(news_results, earnings_results,
                              volume_spikes=None, volume_list=None):
    """
    Grok extracts structured {ticker, company, asset_type, news_reason,
    earnings_date, earnings_type, rvol} from raw Tavily results + volume data.
    """
    # Volume data FIRST — placed before news so it is never cut off by the
    # token limit.  News articles alone exceed 8 k chars, which would truncate
    # volume spikes if they appeared at the end of the blob.
    lines = []
    if volume_spikes:
        lines.append('=== UNUSUAL VOLUME SPIKES TODAY (MUST INCLUDE ALL) ===')
        lines.append('These stocks have anomalous trading volume right now (rvol = today÷30d_avg).')
        lines.append('Include every ticker below even if you have no news reason for it.')
        for row in volume_spikes[:25]:
            lines.append(f"  {row['ticker']}: rvol={row['rvol']:.2f}x  "
                         f"(today {row['today_volume']:,} vs avg {row['avg_volume_30d']:,})")

    if volume_list:
        lines.append('\n=== HIGH ABSOLUTE VOLUME TODAY (include if rvol notable) ===')
        for row in volume_list[:15]:
            lines.append(f"  {row['ticker']}: rvol={row['rvol']:.2f}x")

    lines.append('\n=== MARKET NEWS (last 7 days) ===')
    for r in news_results[:25]:
        lines.append(f"[{str(r.get('published_date',''))[:10]}] {r.get('title','')}")
        lines.append(f"  {str(r.get('content',''))[:250]}")
    lines.append('\n=== EARNINGS NEWS ===')
    for r in earnings_results[:15]:
        lines.append(f"[{str(r.get('published_date',''))[:10]}] {r.get('title','')}")
        lines.append(f"  {str(r.get('content',''))[:250]}")

    text = '\n'.join(lines)

    prompt = f"""Today: {TODAY.isoformat()}

From the market data below (news + earnings + volume), identify ALL publicly
traded securities that should be considered for a financial news article.

For each unique security return JSON with:
- ticker        : standard US ticker (e.g. AAPL, SPY, GC, CL, SPX)
- company       : full official name
- asset_type    : "stock" | "etf" | "index" | "futures" | "commodity" | "forex"
- news_reason   : one sentence – why is this security notable RIGHT NOW?
                  For volume-spike-only names: "Unusual volume spike: Xr normal"
- earnings_date : ISO date if earnings just announced OR scheduled next 7 days; else null
- earnings_type : "recent" (reported last 4 days) | "upcoming" (next 7 days) | null
- rvol          : relative volume float if from volume data, else null

INCLUDE:
- ALL tickers from the UNUSUAL VOLUME SPIKES section (mandatory)
- Major indices: SPX, NDX, DJI, RUT, VIX, FTSE, DAX
- Major ETFs: SPY, QQQ, GLD, SLV, TLT, XLF, XLK, XLE, XLV, IWM, ARKK
- Commodity futures: CL (crude oil), GC (gold), SI (silver), NG (natural gas),
  ZW (wheat), ZC (corn), HG (copper)
- Well-known NYSE/NASDAQ stocks mentioned in news
Target 40-80 securities, deduplicated by ticker.
Return ONLY a valid JSON array, no markdown.

INPUT DATA:
{text[:15000]}

Return format:
[
  {{"ticker":"AAPL","company":"Apple Inc","asset_type":"stock","news_reason":"Beat Q1 earnings by 8%","earnings_date":"2026-02-18","earnings_type":"recent","rvol":null}},
  {{"ticker":"DASH","company":"DoorDash Inc","asset_type":"stock","news_reason":"Unusual volume spike: 2.8x normal volume","earnings_date":null,"earnings_type":null,"rvol":2.79}}
]"""

    for _attempt in range(2):
        try:
            response = send_openai_prompt(prompt, model='gpt-5-mini', reasoning_effort='minimal', timeout=(15, 300))
            clean = response.strip()
            s = clean.find('['); e = clean.rfind(']') + 1
            if s >= 0 and e > s:
                data = json.loads(clean[s:e])
                if isinstance(data, list) and data:
                    return data
        except Exception as ex:
            print(f'  ticker extraction error (attempt {_attempt+1}): {ex}')
    return []


# ============================================================
# GROK FINAL RANKING
# ============================================================

def rank_ideas_with_llm(candidates, pub_start=None, pub_end=None):
    """
    Pass all qualified candidates (each with patterns) to Grok.
    Grok selects the single best pattern per ticker, assigns a publish
    date, writes an article angle, and scores the opportunity.

    candidates: list of dicts (ticker, company, asset_type, news_reason,
                               earnings_type, earnings_date, in_news, patterns)
    Returns: list of dicts with added grok_score, selected_pattern_idx,
             article_date, article_angle  — sorted by grok_score desc.
    """
    # Build compact payload for Grok (keep tokens reasonable)
    compact = []
    for c in candidates:
        compact.append({
            'ticker':         c['ticker'],
            'company':        c['company'],
            'type':           c['asset_type'],
            'news':           c.get('news_reason', ''),
            'earn_type':      c.get('earnings_type'),
            'earn_date':      c.get('earnings_date'),
            'in_news':        c['in_news'],
            'rvol':           round(c['rvol'], 1) if c.get('rvol') else None,
            'days_ago':       c.get('days_since_article', 999),
            'patterns': [
                {
                    'date': p['start_date'],
                    'dir':  p['direction'],
                    'days': p['days'],
                    'SR':   round(p['SR'], 2),
                    'AvgP': round(p['AvgP'], 1),
                    'med':  round(p.get('median', 0), 1),
                    'mode': p['mode'],       # 'cons'/'pe'/'consecutive'
                    'yrs':  p['years'],
                    'pyrs': p.get('pyears', p['years']),
                }
                for p in c['patterns'][:6]
            ],
        })

    # Build PE+2 block separately — can't embed triple-quoted strings inside f"""..."""
    # (Python 3.8 syntax restriction: same-type quotes can't be nested in f-string exprs)
    _pe2_block = (
        f'\nPE+2 MIDTERM-YEAR DANGER ZONE RULES ({TODAY.year} — active now):\n'
        '  TradeWave data shows elevated probability of summer downturns in PE+2 years.\n'
        '  Bullish patterns have already been hard-filtered by quality zone:\n'
        '\n'
        '  ZONE 1 — Before Apr 15 (pre-danger):\n'
        '    Bullish consecutive patterns require 100% success (N/N winners), min 10yr.\n'
        '    Bullish PE-mode patterns require >=5 profitable PE years.\n'
        '\n'
        '  ZONE 2 — Apr 15 to Sep 27 (DANGER ZONE):\n'
        '    Only >=12yr consecutive with 12/12 winners OR PE-mode with >=5 profitable years\n'
        '    survived filtering.  These are confirmed strong signals — score them highly.\n'
        '    BOOST +1.5 for PE-mode patterns (directly capture midterm cycle effects).\n'
        '    BOOST +1.0 for 12/12+ consecutive (long-confirmed signals).\n'
        '    Prefer SHORT (bearish) patterns when both directions available for same ticker.\n'
        '\n'
        '  ZONE 3 — After Sep 27 (post-danger, 100-Year Pattern territory):\n'
        '    All pattern types welcome.  BOOST +0.5 for long bullish patterns here —\n'
        '    Q4 historically strong after midterm lows.\n'
        '\n'
        '  When selecting selected_pattern_idx from multiple valid patterns:\n'
        '    - Prefer PE-mode over short consecutive (PE directly models the cycle)\n'
        '    - Prefer 12/12+ consecutive over 10/10 when both available\n'
        '    - Do NOT downgrade an idea solely because it lacks a news catalyst —\n'
        '      strong PE+2 pattern combinations deserve high scores on their own.\n'
    ) if PE2_YEAR else ''

    prompt = f"""Today: {TODAY.isoformat()}  Publish date: {TODAY.isoformat()}  PE Cycle year: PE{PE_PHASE} ({TODAY.year})

You are the content strategist for SeasonalMarketNews.com.
We publish articles that combine CURRENT NEWS with TRADEWAVE SEASONAL PATTERNS.
The seasonal pattern is the core content — without a pattern there is no article.

All articles publish today ({TODAY.isoformat()}) — set article_date to {TODAY.isoformat()} for every candidate.
The pattern may start today or up to 30 days ahead — this is a forward-looking "heads-up" piece so
readers can act before the pattern starts.

Candidate context fields:
  rvol       : relative volume (today ÷ 30-day avg); null if not in volume data
  days_ago   : days since last article on this ticker (999 = never written); prefer fresher tickers
  patterns[].med  : median return (more robust than AvgP for expected outcome)
  patterns[].pyrs : profitable years count (how many of the yrs lookback were profitable)

For EACH of the {len(compact)} candidates below:
  score (1-10)               : article opportunity quality
  selected_pattern_idx (int) : 0-based index into the patterns array of the best pattern to feature
  article_date (ISO string)  : set to {TODAY.isoformat()} for all candidates
  angle (string)             : ONE compelling sentence — the news hook + seasonal insight
                               that makes someone click; include direction, timeframe, AvgP,
                               and note if pattern starts soon vs. in X days

Score guidance (use the FULL 1-10 range — avoid clustering scores at 7-8):
  9-10 : Big-name + strong pattern (SR>2) + earnings or volume-spike alignment
         Example: major stock beat earnings, 10yr pattern starts in 3 days, long +11% avg
  7-8  : Good name in news OR very strong pattern; one element slightly weaker
         Example: well-known stock with upcoming earnings and pattern SR=1.5-2.0
  5-6  : Decent signal; name is mid-tier OR pattern is marginal (SR=1.0-1.5)
         Example: sector ETF with pattern, no specific news catalyst today
  3-4  : Pattern-only, small/obscure name, no news, marginal SR (<1.2), AvgP<5%
  1-2  : Very weak — no real catalyst, tiny pattern; rarely assigned

Stock vs ETF/Index preference:
  Individual stocks (type="stock") score +0.5 vs an ETF or index of equivalent quality.
  Readers come for stock picks. ETFs/indices should only outscore a stock if their
  signal is clearly stronger (higher SR, bigger news catalyst, earnings alignment).
  Prefer a recognisable mid-cap stock over a sector ETF covering the same theme.

Volume spikes (rvol field in candidates):
  - rvol > 3: very unusual — strong signal, prioritise even without other news
  - rvol > 2: notable — treat like earnings signal for scoring
  - rvol > 1.5: mild spike — small bonus
{_pe2_block}
Reader order (reader_order field):
  After scoring, assign reader_order 1…N to ALL candidates.
  This is the ideal READING SEQUENCE for the day's published set — think like a
  newspaper front-page editor, not a ranked list.  Rules:
    1. Lead (reader_order=1) with the most urgent/exciting stock story — big name,
       earnings beat, volume spike, or pattern starting very soon.
    2. reader_order=2 should contrast or complement #1 (different sector, or broader
       market context that explains why #1 matters).
    3. Alternate between stocks and ETFs/indices to maintain variety.
    4. End the sequence with the most forward-looking "heads-up" piece.
    5. reader_order is independent of score — a score-8 article can be reader_order=1
       if it makes the strongest opening hook.
  Every candidate must have a unique reader_order integer starting from 1.

CANDIDATES (JSON):
{json.dumps(compact, separators=(',', ':'))}

Return ONLY a valid JSON array sorted by score descending.
Each element must include: ticker, score, selected_pattern_idx, article_date, angle, reader_order.
[{{"ticker":"X","score":9.5,"selected_pattern_idx":0,"article_date":"{TODAY.isoformat()}","angle":"NVDA just beat Q4 earnings and its 10-year seasonal pattern shows a Long trade averaging +11% over the next 21 days — the pattern starts {(TODAY + timedelta(days=7)).isoformat()}.","reader_order":1}}]"""

    # Choose ranking LLM
    _llm = RANKING_LLM
    system_msg = ('You are a senior financial content strategist for a CNBC-style '
                  'seasonal market news site. Return only valid JSON.')
    # Map shorthand → Claude model ID
    _claude_models = {
        'opus46':   CLAUDE_OPUS_46,
        'sonnet46': CLAUDE_SONNET_46,
        'haiku45':  CLAUDE_HAIKU_45,
        'haiku35':  CLAUDE_HAIKU_35,
        'haiku3':   CLAUDE_HAIKU_3,
    }
    try:
        if _llm in _claude_models:
            response = send_claude_prompt(
                prompt, model=_claude_models[_llm],
                system=system_msg, max_tokens=8192, timeout=(15, 300)
            )
        elif _llm == 'gpt5':
            response = send_openai_prompt(
                prompt, system=system_msg, timeout=(15, 300)
            )
        elif _llm == 'grok3':
            response = send_grok_prompt(
                prompt, model='grok-3', system=system_msg,
                temperature=0.1, timeout=240
            )
        else:  # grok3mini fallback
            response = send_grok_prompt(
                prompt, model='grok-3-mini', system=system_msg,
                temperature=0.1, timeout=180
            )
        clean = response.strip()
        s = clean.find('['); e = clean.rfind(']') + 1
        if s >= 0 and e > s:
            ranked = json.loads(clean[s:e])
            if isinstance(ranked, list):
                return ranked
    except Exception as ex:
        print(f'  LLM ranking error ({_llm}): {ex}')
    return []


def apply_grok_ranking(candidates, grok_ranked):
    """
    Merge Grok's ranking back onto the candidate list.
    Returns final sorted list of idea dicts.
    """
    # Index candidates by ticker
    by_ticker = {c['ticker']: c for c in candidates}

    # Build Grok-ranked index
    grok_by_ticker = {r['ticker']: r for r in grok_ranked if 'ticker' in r}

    final = []
    seen  = set()

    # Process in Grok's ranked order first
    for r in grok_ranked:
        ticker = r.get('ticker')
        if not ticker or ticker in seen or ticker not in by_ticker:
            continue
        seen.add(ticker)
        c = dict(by_ticker[ticker])

        # Apply Grok selections
        c['grok_score']   = r.get('score', 0)
        c['reader_order'] = r.get('reader_order', 0)
        c['article_date'] = r.get('article_date', TODAY.isoformat())
        c['article_angle']= r.get('angle', c.get('rationale', ''))
        idx = r.get('selected_pattern_idx', 0)
        shown = c['patterns'][:6]   # LLM only saw the first 6 patterns; index against that slice
        if shown and isinstance(idx, int) and 0 <= idx < len(shown):
            c['featured_pattern'] = shown[idx]
        elif c['patterns']:
            c['featured_pattern'] = c['patterns'][0]
        else:
            c['featured_pattern'] = None

        final.append(c)

    # Append any candidates Grok didn't mention (fallback: sort by pre_score)
    remaining = [c for c in candidates if c['ticker'] not in seen]
    remaining.sort(key=lambda x: x.get('pre_score', 0), reverse=True)
    for c in remaining:
        seen.add(c['ticker'])
        c['grok_score']    = 0
        c['article_date']  = c.get('article_date', TODAY.isoformat())
        c['article_angle'] = c.get('rationale', '')
        c['featured_pattern'] = c['patterns'][0] if c['patterns'] else None
        final.append(c)

    return final[:TARGET_IDEAS_MAX]


# ============================================================
# PRE-SCORING (before Grok — used as tiebreaker / fallback)
# ============================================================

def pre_score(news_item, patterns, ticker='', asset_type=''):
    """Quick heuristic score 0-10 (used before Grok ranking)."""
    s = 0.0
    if news_item:
        s += 3.0
        et = news_item.get('earnings_type')
        if et == 'recent':     s += 3.0
        elif et == 'upcoming': s += 2.5

        # Volume spike bonus
        rvol = news_item.get('rvol') or 0
        if   rvol >= 3.0: s += 2.0
        elif rvol >= 2.0: s += 1.5
        elif rvol >= 1.5: s += 1.0

    if patterns:
        best_sr   = max(p['SR']   for p in patterns)
        best_avgp = max(p['AvgP'] for p in patterns)
        has_pe    = any(p['mode'] == 'pe' for p in patterns)
        s += min(2.5, best_sr   / 1.2)
        s += min(1.0, best_avgp / 10.0)
        if has_pe: s += 0.5

        # PE+2 (midterm year) bonuses — zone-aware scoring
        if PE2_YEAR:
            # Classify each pattern's start_date into danger zone
            for p in patterns:
                try:
                    _sd = p['start_date']
                    _pmd = (int(_sd[5:7]), int(_sd[8:10]))
                except (KeyError, ValueError, IndexError):
                    continue
                _pmode = _norm_mode(p.get('mode', ''))
                _pyrs  = int(p.get('years', 0))
                _ppyrs = int(p.get('pyears', 0))
                _dir   = p.get('direction', '').lower()

                # Post-Sep-27 bullish patterns — 100-Year Pattern territory
                if _dir == 'long' and _pmd > PE2_DANGER_END:
                    s += 0.5
                    break  # one bonus per candidate

            # Danger zone bonuses (Apr 15 – Sep 27)
            _in_dz = any(
                PE2_DANGER_START <= (int(p['start_date'][5:7]), int(p['start_date'][8:10])) <= PE2_DANGER_END
                for p in patterns
                if len(p.get('start_date', '')) >= 10
            )
            if _in_dz:
                # PE-mode pattern in danger zone — extra valuable since
                # PE-mode directly captures midterm-year cycle effects
                if has_pe:
                    s += PE2_PE_PATTERN_BONUS
                # 12/12+ consecutive in danger zone — survived the hard filter,
                # so it's a strong confirmed signal
                if any(_norm_mode(p.get('mode', '')) == 'cons'
                       and int(p.get('years', 0)) >= PE2_DANGER_MIN_CONS_YRS
                       and int(p.get('pyears', 0)) >= int(p.get('years', 0))
                       for p in patterns):
                    s += PE2_LONG_CONS_BONUS
            else:
                # Outside danger zone: keep existing sensitive-sector bonuses
                _sensitive = (ticker.upper() in PE2_SENSITIVE_TICKERS
                              or asset_type == 'index')
                if _sensitive and has_pe:
                    s += PE2_PE_PATTERN_BONUS
                if any(_norm_mode(p.get('mode', '')) == 'cons'
                       and int(p.get('years', 0)) >= PE2_LONG_CONS_MIN_YRS
                       for p in patterns):
                    s += PE2_LONG_CONS_BONUS

    return min(10.0, round(s, 2))


# ============================================================
# MAIN
# ============================================================

def main():
    # Daily mode: publish date is always TODAY; 3AM queuing job picks how many to use
    pub_start = pub_end = TODAY
    os.makedirs(IDEAS_DIR, exist_ok=True)
    output_file = OUTPUT_FILE_TPL.format(TODAY.isoformat())
    csv_file    = CSV_FILE_TPL.format(TODAY.isoformat())

    print('=' * 72)
    print(f'SELECT NEWS ARTICLES  —  Daily Article Idea Generator  —  Started {datetime.datetime.now():%Y-%m-%d %H:%M:%S}')
    print(f'Date: {TODAY}  ({TODAY.strftime("%A")})    PE Phase: PE{PE_PHASE} ({TODAY.year})')
    print(f'Publish date  : {TODAY} (all ideas for today — 3AM job picks how many)')
    print(f'Pattern search: today-{OBS_PAST_DAYS}d → today+{OBS_HORIZON_DAYS}d')
    print(f'OppList4 scan : today → today+{OPP_LIST_HORIZON_DAYS}d')
    print(f'Target ideas  : {TARGET_IDEAS_MAX}')
    print(f'Ranking LLM   : {RANKING_LLM}')
    print('=' * 72)

    # ------------------------------------------------------------------
    # 1. Load static data — article history (published + queued) + volume
    # ------------------------------------------------------------------
    print('\n[1/7] Loading article history & volume data...')
    published = load_published_articles()
    queued    = load_queued_articles()
    ticker_latest = load_article_history()
    vol_list, vol_spikes = load_volume_data()
    print(f'  Published articles tracked: {len(published)} tickers')
    print(f'  Queued (Redis, pending)   : {len(queued)} tickers'
          + (f'  ({", ".join(list(queued)[:8])})' if queued else ''))
    print(f'  Combined dedup coverage   : {len(ticker_latest)} tickers')
    print(f'  Volume spikes (rvol≥{VOLUME_SPIKE_MIN_RVOL}): '
          f'{len(vol_spikes)} tickers  '
          f'(top: {", ".join(r["ticker"] for r in vol_spikes[:5])})')
    print(f'  High-volume list (rvol≥{VOLUME_LIST_MIN_RVOL}): '
          f'{len(vol_list)} tickers')

    # Build rvol lookup for quick access
    rvol_map = {}
    for r in vol_spikes + vol_list:
        if r['ticker'] not in rvol_map or r['rvol'] > rvol_map[r['ticker']]:
            rvol_map[r['ticker']] = r['rvol']

    # ------------------------------------------------------------------
    # 2. Login to appserver
    # ------------------------------------------------------------------
    print('\n[2/7] Logging in to appserver...')
    token = None
    for _login_attempt in range(1, 4):
        try:
            kp_token = get_keyprovider_token()
            token    = login_appserver(kp_token)
            if token:
                break
            print(f'  Login returned no token (attempt {_login_attempt}/3) — retrying in 15s...')
        except Exception as e:
            print(f'  Login error (attempt {_login_attempt}/3): {e} — retrying in 15s...')
        if _login_attempt < 3:
            time.sleep(15)
    if not token:
        print('  Login failed after 3 attempts.')
        sys.exit(1)
    print('  OK')

    # ------------------------------------------------------------------
    # 3. Scan ranges
    # ------------------------------------------------------------------
    print('\n[3/7] Computing scan ranges...')
    scan_start, scan_end, api_dates = get_scan_range()
    print(f'  OppList4 scan : {scan_start} → {scan_end}  '
          f'({OPP_LIST_HORIZON_DAYS} days, {len(api_dates)} API dates)')
    print(f'  OppBySymbol   : today-{OBS_PAST_DAYS}d → today+{OBS_HORIZON_DAYS}d  '
          f'[{TODAY - timedelta(days=OBS_PAST_DAYS)} → '
          f'{TODAY + timedelta(days=OBS_HORIZON_DAYS)}]')
    total_opp_calls = (len(SCAN_RESOURCES) * len(WEEKLY_YEAR_CONFIGS)
                       * len(DAY_RANGES) * len(api_dates))
    print(f'  OppList4 calls: {total_opp_calls}  '
          f'({len(SCAN_RESOURCES)} resources × {len(WEEKLY_YEAR_CONFIGS)} year-configs '
          f'× {len(DAY_RANGES)} day-ranges × {len(api_dates)} dates)')

    # ------------------------------------------------------------------
    # 4. Pattern sweep (OppList4) — 30-day window
    # ------------------------------------------------------------------
    print(f'\n[4/7] Fetching OppList4 patterns (next {OPP_LIST_HORIZON_DAYS} days)...')
    weekly_patterns = fetch_weekly_patterns(token, scan_start, scan_end, api_dates)
    wk_syms = len(weekly_patterns)
    print(f'  Unique symbols with patterns in next {OPP_LIST_HORIZON_DAYS}d : {wk_syms}')
    if weekly_patterns:
        top5 = sorted(weekly_patterns.items(),
                      key=lambda x: max(p['SR'] for p in x[1]), reverse=True)[:5]
        print('  Top 5 weekly symbols by SR:')
        for sym, pats in top5:
            b = max(pats, key=lambda p: p['SR'])
            print(f'    {sym:<8}  SR={b["SR"]:.2f}  AvgP={b["AvgP"]:.1f}%  '
                  f'{b["direction"]}  {b["start_date"]}  [{b["mode"]} {b["years"]}yr]')

    # ------------------------------------------------------------------
    # 5. News & earnings research (Tavily → Grok extraction)
    # ------------------------------------------------------------------
    print('\n[5/7] News & earnings research via Tavily...')
    news_results, earnings_results = run_news_research()
    print(f'  News articles   : {len(news_results)}')
    print(f'  Earnings articles: {len(earnings_results)}')

    print('  Extracting tickers via gpt-5-mini (+ volume data)...')
    news_tickers = extract_tickers_with_grok(
        news_results, earnings_results,
        volume_spikes=vol_spikes,
        volume_list=vol_list
    )
    # Patch in rvol from our volume map for any ticker that has it
    for item in news_tickers:
        if item.get('rvol') is None and item['ticker'] in rvol_map:
            item['rvol'] = rvol_map[item['ticker']]
    print(f'  Extracted {len(news_tickers)} securities')

    # ------------------------------------------------------------------
    # 6. OppBySymbol for every news ticker + dedup filter
    # ------------------------------------------------------------------
    print(f'\n[6/7] OppBySymbol lookup + dedup filter '
          f'(window today{-OBS_PAST_DAYS:+d} → today+{OBS_HORIZON_DAYS})...')

    candidates = {}
    skipped_hard   = []
    flagged_soft   = []
    pe2_total_removed = 0   # track patterns removed by PE+2 danger zone filter
    div_total_removed = 0   # track patterns removed by cons/PE direction divergence

    for i, item in enumerate(news_tickers, 1):
        ticker     = item['ticker']
        asset_type = item.get('asset_type', 'unknown')

        # ---- Dedup check ----
        since = days_since_last_article(ticker, ticker_latest)
        if since < HARD_MIN_DAYS:
            skipped_hard.append((ticker, since))
            print(f'  [{i}/{len(news_tickers)}] {ticker} — SKIP (written {since}d ago)')
            continue
        recently_written = since < SOFT_MIN_DAYS   # flag but don't skip

        print(f'  [{i}/{len(news_tickers)}] {ticker}'
              f'{"  ⚠ "+str(since)+"d ago" if recently_written else ""}...',
              end=' ', flush=True)
        if recently_written:
            flagged_soft.append((ticker, since))

        # ---- Pattern lookup ----
        weekly_pats = weekly_patterns.get(ticker, [])
        try:
            obs_pats = lookup_patterns_for_symbol(token, ticker, asset_type)
        except Exception as e:
            print(f'OBS error: {e}')
            obs_pats = []

        all_pats = merge_patterns(weekly_pats, obs_pats)
        if ticker in AMBIGUOUS_TICKERS:
            all_pats = filter_patterns_by_asset_type(all_pats, asset_type)
        all_pats, _pe2_rm = pe2_filter_patterns(all_pats)
        pe2_total_removed += _pe2_rm
        all_pats, _div_rm = divergence_filter_patterns(all_pats)
        div_total_removed += _div_rm

        if not all_pats:
            rm_parts = []
            if _pe2_rm: rm_parts.append(f'{_pe2_rm} PE+2')
            if _div_rm: rm_parts.append(f'{_div_rm} divergence')
            rm_msg = f' ({", ".join(rm_parts)} removed)' if rm_parts else ''
            print(f'no patterns — skipped{rm_msg}')
            continue

        rvol = item.get('rvol') or rvol_map.get(ticker)
        rvol_tag = f'  rvol={rvol:.1f}x' if rvol else ''
        print(f'{len(all_pats)} patterns  SR={all_pats[0]["SR"]:.2f}{rvol_tag}')

        ps = pre_score(item, all_pats, ticker=ticker, asset_type=asset_type)
        # Soft-penalise recently written tickers in pre_score
        if recently_written:
            ps = max(0.0, ps - 2.0)

        candidates[ticker] = {
            'ticker':              ticker,
            'company':             item.get('company', ticker),
            'asset_type':          asset_type,
            'news_reason':         item.get('news_reason', ''),
            'earnings_date':       item.get('earnings_date'),
            'earnings_type':       item.get('earnings_type'),
            'rvol':                rvol,
            'days_since_article':  since,
            'patterns':            all_pats,
            'pre_score':           ps,
            'in_news':             True,
            'in_weekly_patterns':  bool(weekly_pats),
        }

    news_with_patterns = len(candidates)
    news_without       = len(news_tickers) - news_with_patterns - len(skipped_hard)
    print(f'\n  News tickers with patterns      : {news_with_patterns}')
    print(f'  Skipped — written < {HARD_MIN_DAYS}d ago       : {len(skipped_hard)}'
          f'  ({", ".join(t for t,_ in skipped_hard[:8])})')
    print(f'  Flagged — written {HARD_MIN_DAYS}–{SOFT_MIN_DAYS}d ago (penalised) : '
          f'{len(flagged_soft)}')
    print(f'  Dropped — no patterns           : {news_without}')

    # Add top pattern-only symbols from weekly scan
    pattern_only = sorted(
        [(sym, pats) for sym, pats in weekly_patterns.items()
         if sym not in candidates],
        key=lambda x: max(p['SR'] for p in x[1]),
        reverse=True
    )
    added_pattern_only = 0
    for sym, pats in pattern_only:
        if len(candidates) >= TARGET_IDEAS_MAX:
            break
        since = days_since_last_article(sym, ticker_latest)
        if since < HARD_MIN_DAYS:
            continue
        try:
            company = get_company_name(pats[0]['resource_id'], sym) or sym
        except Exception:
            company = sym
        rvol = rvol_map.get(sym)
        # Map resource_id → canonical asset_type (same vocab as Grok extraction)
        _rid = pats[0]['resource_id']
        _atype = {
            0: 'stock', 1: 'stock', 2: 'stock', 3: 'stock',  # DOW30/NDX100/SP500/R1000
            5: 'index',                                         # Indices Common
            7: 'futures',                                       # Futures & Commodities
            9: 'forex',                                         # Forex Liquid
            11: 'etf',                                          # ETFs
        }.get(_rid, 'stock')
        pats, _pe2_rm = pe2_filter_patterns(pats)
        pe2_total_removed += _pe2_rm
        pats, _div_rm = divergence_filter_patterns(pats)
        div_total_removed += _div_rm
        if not pats:
            continue
        ps   = pre_score(None, pats, ticker=sym, asset_type=_atype)
        if since < SOFT_MIN_DAYS:
            ps = max(0.0, ps - 2.0)
        candidates[sym] = {
            'ticker':             sym,
            'company':            company,
            'asset_type':         _atype,
            'news_reason':        f'Volume spike: {rvol:.1f}x normal' if rvol else '',
            'earnings_date':      None,
            'earnings_type':      None,
            'rvol':               rvol,
            'days_since_article': since,
            'patterns':           pats[:8],
            'pre_score':          ps,
            'in_news':            False,
            'in_weekly_patterns': True,
        }
        added_pattern_only += 1

    print(f'  Pattern-only symbols added      : {added_pattern_only}')
    if PE2_YEAR:
        print(f'  PE+2 danger zone filter         : {pe2_total_removed} bullish patterns removed'
              f'  (zone: Apr 15 – Sep 27, min cons={PE2_DANGER_MIN_CONS_YRS}yr)')
    if div_total_removed:
        print(f'  Divergence filter               : {div_total_removed} consecutive patterns removed'
              f'  (PE contradicts direction)')
    print(f'  Total candidates for ranking    : {len(candidates)}')

    # Shuffle candidates before LLM ranking to avoid positional anchoring bias.
    # LLMs inflate scores for items appearing early in a sorted list.
    # pre_score is retained on each dict for display/fallback ordering after ranking.
    cand_list = list(candidates.values())
    random.shuffle(cand_list)

    # ------------------------------------------------------------------
    # 7. LLM final ranking — all ideas publish TODAY
    # ------------------------------------------------------------------
    print(f'\n[7/7] {RANKING_LLM.upper()} final ranking of {len(cand_list)} candidates...')
    print(f'  Publish date: {TODAY} (daily mode — 3AM job decides how many to queue)')
    llm_ranked  = rank_ideas_with_llm(cand_list, pub_start=pub_start, pub_end=pub_end)
    print(f'  LLM returned rankings for {len(llm_ranked)} tickers')

    final_ideas = apply_grok_ranking(cand_list, llm_ranked)

    # Daily mode: all articles publish today — no weekly spreading needed
    for idea in final_ideas:
        idea['article_date'] = TODAY.isoformat()

    # ------------------------------------------------------------------
    # OUTPUT — console table (ranked, pick top N for queuing at 3AM)
    # ------------------------------------------------------------------
    print(f'\n{"=" * 72}')
    print(f'ARTICLE IDEAS  —  {TODAY}  ({TODAY.strftime("%A")})')
    print(f'{len(final_ideas)} ideas ranked by {RANKING_LLM.upper()} — 3AM job picks how many to publish')
    print(f'{"=" * 72}')
    print(f'{"#":<4} {"Ticker":<8} {"Score":<6} {"Earn":<8} {"rvol":<6} '
          f'{"Ago":<5} {"FeatPattern":<28} Company')
    print('-' * 100)

    for i, idea in enumerate(final_ideas, 1):
        fp     = idea.get('featured_pattern') or {}
        fp_str = (f'{fp.get("start_date","")[:10]} {fp.get("direction","")[:1]} '
                  f'SR={fp.get("SR",0):.1f} {fp.get("mode","")[:4]}{fp.get("years","")}yr'
                  if fp else 'none')
        earn   = (idea.get('earnings_type') or '')[:7]
        rvol_s = f'{idea["rvol"]:.1f}x' if idea.get('rvol') else ''
        since  = idea.get('days_since_article', 999)
        ago_s  = f'{since}d' if since < 999 else 'new'
        gs     = idea.get('grok_score', 0)
        co     = idea['company'][:20]
        print(f"{i:<4} {idea['ticker']:<8} {gs:<6.1f} {earn:<8} {rvol_s:<6} "
              f"{ago_s:<5} {fp_str:<28} {co}")

    # Build sched dict (just today) for JSON output below
    sched = {TODAY.isoformat(): final_ideas}

    # Top article angles
    print(f'\n--- Top Article Angles ---')
    for i, idea in enumerate(final_ideas, 1):
        angle = idea.get('article_angle', '')
        print(f'  {i:>2}. [{idea["ticker"]:<6}] {angle[:105]}')

    # ------------------------------------------------------------------
    # Save JSON
    # ------------------------------------------------------------------
    output = {
        'generated_at': TODAY.isoformat(),
        'mode':         'daily',
        'pe_phase':     PE_PHASE,
        'ranking_llm':  RANKING_LLM,
        'publish_date':         TODAY.isoformat(),
        'opp_list4_scan_start': scan_start.isoformat(),
        'opp_list4_scan_end':   scan_end.isoformat(),
        'pattern_window': {
            'from': (TODAY - timedelta(days=OBS_PAST_DAYS)).isoformat(),
            'to':   (TODAY + timedelta(days=OBS_HORIZON_DAYS)).isoformat(),
        },
        'total_ideas': len(final_ideas),
        'stats': {
            'news_articles_found':          len(news_results),
            'earnings_articles_found':      len(earnings_results),
            'tickers_from_news':            len(news_tickers),
            'volume_spikes':                len(vol_spikes),
            'news_tickers_with_patterns':   news_with_patterns,
            'news_tickers_skipped_dedup':   len(skipped_hard),
            'news_tickers_dropped':         news_without,
            'pattern_only_added':           added_pattern_only,
            'opp_list4_scan_symbols':       wk_syms,
            'pe2_patterns_removed':         pe2_total_removed if PE2_YEAR else 0,
            'divergence_patterns_removed':  div_total_removed,
            'llm_ranked':                   len(llm_ranked),
            'published_tickers_tracked':    len(published),
            'queued_tickers_tracked':       len(queued),
        },
        'publish_schedule': {
            d: [{'ticker': i['ticker'], 'score': i.get('grok_score', 0),
                 'company': i['company']}
                for i in sched[d]]
            for d in sorted(sched)
        },
        'ideas': [],
    }

    for idea in final_ideas:
        entry = {k: v for k, v in idea.items() if k not in ('patterns',)}
        entry['featured_pattern'] = (
            {k: (round(v, 3) if isinstance(v, float) else v)
             for k, v in idea['featured_pattern'].items()
             if k != 'resource_id'}
            if idea.get('featured_pattern') else None
        )
        entry['all_patterns'] = [
            {
                'start_date': p['start_date'],
                'direction':  p['direction'],
                'days':       p['days'],
                'SR':         round(p['SR'], 3),
                'AvgP':       round(p['AvgP'], 2),
                'median':     round(p.get('median', 0), 2),
                'mode':       p['mode'],
                'years':      p['years'],
                'pyears':     p['pyears'],
                'day_range':  p.get('day_range', '-'),
                'source':     p.get('source', ''),
            }
            for p in idea['patterns']
        ]
        output['ideas'].append(entry)

    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2, default=str)

    # ------------------------------------------------------------------
    # Save CSV — one row per article idea, ready for queue processing
    # ------------------------------------------------------------------
    CSV_COLUMNS = [
        'rank', 'reader_order', 'publish_date', 'ticker', 'company', 'asset_type',
        'score', 'in_news', 'earnings_type', 'earnings_date', 'rvol',
        'days_since_article',
        # Featured pattern fields
        'pat_start_date', 'pat_direction', 'pat_days',
        'pat_SR', 'pat_AvgP', 'pat_median', 'pat_mode', 'pat_years', 'pat_pyears',
        'pat_resource_id',
        # Article content hints
        'article_angle', 'news_reason',
    ]
    with open(csv_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction='ignore')
        writer.writeheader()
        for rank, idea in enumerate(final_ideas, 1):
            fp = idea.get('featured_pattern') or {}
            writer.writerow({
                'rank':               rank,
                'reader_order':       idea.get('reader_order', 0),
                'publish_date':       idea['article_date'],
                'ticker':             idea['ticker'],
                'company':            idea['company'],
                'asset_type':         idea['asset_type'],
                'score':              round(idea.get('grok_score', 0), 2),
                'in_news':            int(idea['in_news']),
                'earnings_type':      idea.get('earnings_type') or '',
                'earnings_date':      idea.get('earnings_date') or '',
                'rvol':               round(idea['rvol'], 2) if idea.get('rvol') else '',
                'days_since_article': idea.get('days_since_article', 999),
                'pat_start_date':     fp.get('start_date', ''),
                'pat_direction':      fp.get('direction', ''),
                'pat_days':           fp.get('days', ''),
                'pat_SR':             round(fp['SR'], 3)   if fp.get('SR')   else '',
                'pat_AvgP':           round(fp['AvgP'], 2) if fp.get('AvgP') else '',
                'pat_median':         round(fp.get('median', 0), 2) if fp else '',
                'pat_mode':           fp.get('mode', ''),
                'pat_years':          fp.get('years', ''),
                'pat_pyears':         fp.get('pyears', ''),
                'pat_resource_id':    fp.get('resource_id', ''),
                'article_angle':      idea.get('article_angle', ''),
                'news_reason':        idea.get('news_reason', ''),
            })

    print(f'Saved to: {output_file}  +  {csv_file}  ({len(final_ideas)} rows)')

    # Summary
    in_news       = sum(1 for i in final_ideas if i['in_news'])
    with_earnings = sum(1 for i in final_ideas if i.get('earnings_type'))
    recent_earn   = sum(1 for i in final_ideas if i.get('earnings_type') == 'recent')
    upcoming_earn = sum(1 for i in final_ideas if i.get('earnings_type') == 'upcoming')
    with_volume   = sum(1 for i in final_ideas if i.get('rvol') and i['rvol'] >= VOLUME_SPIKE_MIN_RVOL)
    pat_only      = sum(1 for i in final_ideas if not i['in_news'])
    both          = sum(1 for i in final_ideas if i['in_news'] and i['patterns'])
    has_pe        = sum(1 for i in final_ideas
                        if any(p.get('mode') == 'pe' for p in i['patterns']))

    print(f'\n--- Summary ---')
    print(f'  In news                    : {in_news}')
    print(f'  With earnings              : {with_earnings}  '
          f'(recent: {recent_earn}, upcoming: {upcoming_earn})')
    print(f'  With volume spike (rvol≥{VOLUME_SPIKE_MIN_RVOL}) : {with_volume}')
    print(f'  News + patterns (best)     : {both}')
    print(f'  Pattern-only               : {pat_only}')
    print(f'  With PE cycle pattern      : {has_pe}')
    if PE2_YEAR:
        print(f'  PE+2 patterns removed      : {pe2_total_removed}')
    print(f'  Skipped (written <{HARD_MIN_DAYS}d ago)  : {len(skipped_hard)}'
          + (f'  ({", ".join(t for t,_ in skipped_hard[:6])})' if skipped_hard else ''))
    print(f'  Published tickers tracked  : {len(published)}')
    print(f'  Queued tickers tracked     : {len(queued)}'
          + (f'  ({", ".join(list(queued)[:6])})' if queued else ''))
    print(f'  Total article ideas        : {len(final_ideas)}')
    print(f'\nDone!  {TODAY} ({TODAY.strftime("%A")})  PE{PE_PHASE} ({TODAY.year})  |  {RANKING_LLM.upper()}')


if __name__ == '__main__':
    main()
