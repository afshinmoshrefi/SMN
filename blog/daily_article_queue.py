#!/usr/bin/env python3
"""
daily_article_queue.py

Runs at 3:00 AM via cron.  Reads the article ideas CSV produced by
select_news_articles.py (2:00 AM), picks the top N ideas, and queues them
for article generation by calling the blog_queue write_news_article route.

The article_processor.py worker (always running) picks jobs from the Redis
queue one at a time.  Each article goes through a 9-step AI pipeline
(research → images → hero → prompt → GPT-5.1 → publish) which takes
~3 minutes per article, so 5 articles finish by ~3:20 AM — well before
the 7 AM email send.

Crontab line:
  0 3 * * * /usr/bin/python3 /home/flask/blog/daily_article_queue.py >> /home/flask/blog/logs/daily_queue.log 2>&1
"""

import csv
import datetime
import os
import sys
import json
import requests
import time
import redis

sys.path.insert(0, '/home/flask')
import config

# ============================================================
# CONFIGURATION  —  edit these to control daily publishing
# ============================================================

# How many articles to queue each day.
# The article_processor handles them sequentially; with 5–15 min per
# article a count of 5 will publish throughout the morning naturally.
ARTICLES_PER_DAY = 6

# Only queue articles whose LLM score meets this minimum.
# Scale: 1–10.  7+ = strong idea; 5–6 = decent; below 5 = weak.
MIN_SCORE = 5.0

# Only queue articles that are in the news (in_news=1).
# Set to False to also include pattern-only ideas.
REQUIRE_IN_NEWS = False

# User ID used when queuing the article (the automation user on the site).
USERID = 16

# ---- Lineup diversity rules ----

# Always try to include at least one article with upcoming/recent earnings.
# If no earnings article is available above MIN_SCORE, this is skipped gracefully.
ENSURE_EARNINGS = True

# Always try to include at least one non-stock (index, etf, futures, commodity, forex).
# Keeps the lineup from being all individual stocks.
ENSURE_NON_STOCK = True

# Maximum articles from the same broad sector in a single day's lineup.
# Prevents e.g. 3 semiconductor articles on the same day.
MAX_PER_SECTOR = 2

# Maximum index articles (SPX, DJI, NDX, etc.) per day.
# One index article per day is enough — multiple looks AI-generated.
MAX_INDICES = 1

# Sector map — ticker → sector label.
# Tickers not listed here fall into 'other' (each counts separately, no cap).
SECTOR_MAP = {
    # Technology / Semiconductors
    'AAPL':'tech',  'MSFT':'tech',  'GOOGL':'tech', 'GOOG':'tech',
    'META':'tech',  'NVDA':'tech',  'AMD':'tech',   'INTC':'tech',
    'AVGO':'tech',  'QCOM':'tech',  'TXN':'tech',   'MU':'tech',
    'MRVL':'tech',  'LRCX':'tech',  'AMAT':'tech',  'KLAC':'tech',
    'ORCL':'tech',  'CRM':'tech',   'ADBE':'tech',  'NOW':'tech',
    'SNOW':'tech',  'PLTR':'tech',  'MSTR':'tech',  'SHOP':'tech',
    'UBER':'tech',  'HOOD':'tech',  'COIN':'tech',
    'RBLX':'tech',  'U':'tech',     'PATH':'tech',  'DDOG':'tech',
    'SMCI':'tech',  'MARA':'tech',  'RIOT':'tech',  'WULF':'tech',
    # Consumer / Retail
    'AMZN':'consumer', 'TSLA':'consumer', 'WMT':'consumer', 'COST':'consumer',
    'TGT':'consumer',  'HD':'consumer',   'LOW':'consumer',  'MCD':'consumer',
    'SBUX':'consumer', 'NKE':'consumer',  'CMG':'consumer',  'DASH':'consumer',
    'LYFT':'consumer', 'DKNG':'consumer', 'PINS':'consumer', 'SNAP':'consumer',
    'WEN':'consumer',  'YUM':'consumer',  'DRI':'consumer',  'NFLX':'consumer',
    'DIS':'consumer',  'CMCSA':'consumer','WBD':'consumer',  'PARA':'consumer',
    # Finance / Banks
    'JPM':'finance',  'BAC':'finance',  'GS':'finance',   'MS':'finance',
    'WFC':'finance',  'C':'finance',    'BLK':'finance',  'SCHW':'finance',
    'AXP':'finance',  'V':'finance',    'MA':'finance',   'PYPL':'finance',
    'COF':'finance',  'DFS':'finance',  'USB':'finance',  'PNC':'finance',
    'TFC':'finance',  'KEY':'finance',  'RF':'finance',   'FITB':'finance',
    'HBAN':'finance', 'CFG':'finance',  'BX':'finance',   'KKR':'finance',
    # Healthcare / Biotech / Pharma
    'JNJ':'health',  'UNH':'health',  'PFE':'health',  'MRK':'health',
    'ABBV':'health', 'LLY':'health',  'BMY':'health',  'AMGN':'health',
    'GILD':'health', 'MRNA':'health', 'BNTX':'health', 'CVS':'health',
    'MDT':'health',  'BSX':'health',  'SYK':'health',  'ABT':'health',
    'ISRG':'health', 'VRTX':'health', 'REGN':'health', 'ZTS':'health',
    # Energy
    'XOM':'energy',  'CVX':'energy',  'COP':'energy',  'OXY':'energy',
    'SLB':'energy',  'HAL':'energy',  'DVN':'energy',  'EOG':'energy',
    'MPC':'energy',  'VLO':'energy',  'PSX':'energy',  'KMI':'energy',
    'WMB':'energy',  'EQT':'energy',  'AR':'energy',   'RIG':'energy',
    # Industrial / Aerospace / Transport
    'BA':'industrial',  'CAT':'industrial', 'DE':'industrial',
    'GE':'industrial',  'HON':'industrial', 'RTX':'industrial',
    'LMT':'industrial', 'NOC':'industrial', 'GD':'industrial',
    'UPS':'industrial', 'FDX':'industrial', 'CSX':'industrial',
    'UNP':'industrial', 'UAL':'industrial', 'DAL':'industrial',
    'AAL':'industrial', 'LUV':'industrial', 'JBLU':'industrial',
    'NCLH':'industrial','CCL':'industrial', 'RCL':'industrial',
    # Telecom / Media
    'T':'telecom',   'VZ':'telecom',   'TMUS':'telecom',
    'CMCSA':'telecom','CHTR':'telecom',
}

# Correlated-symbol groups — tickers that track the same underlying index/theme.
# Only ONE ticker from each group is allowed per day's lineup.
# This prevents e.g. SPY + SPX or DJI + DIA on the same day.
CORRELATED_GROUPS = {
    # S&P 500
    'SPX':  'sp500',  'SPY':  'sp500',  'VOO': 'sp500',
    'IVV':  'sp500',  'SPLG': 'sp500',  'ES':  'sp500',
    # Nasdaq-100
    'NDX':  'ndx100', 'QQQ':  'ndx100', 'QQQM':'ndx100',
    'NQ':   'ndx100',
    # Dow Jones
    'DJI':  'dow',    'DIA':  'dow',    'YM':  'dow',
    # Russell 2000
    'RUT':  'rut2k',  'IWM':  'rut2k',  'RTY': 'rut2k',
    # Volatility
    'VIX':  'vix',    'UVXY': 'vix',    'VXX': 'vix',  'SVXY': 'vix',
    # Bitcoin / crypto — mining stocks and spot ETF are highly correlated
    'MSTR': 'btc',    'MARA': 'btc',    'RIOT': 'btc',
    'WULF': 'btc',    'IBIT': 'btc',    'CLSK': 'btc',
}

# ---- Featured article scoring — name recognition tiers ----
# Mega-cap: household names, highest click-through for hero position
MEGA_CAP_TICKERS = {
    'AAPL', 'MSFT', 'GOOGL', 'GOOG', 'AMZN', 'META', 'NVDA', 'TSLA',
    'SPX', 'SPY', 'QQQ', 'DJI', 'NDX',
}
# Well-known: strong recognition, not quite mega-cap
WELL_KNOWN_TICKERS = {
    'JPM', 'GS', 'BAC', 'WFC', 'V', 'MA',
    'XOM', 'CVX', 'COP',
    'BA', 'LMT', 'RTX', 'CAT',
    'JNJ', 'UNH', 'PFE', 'LLY', 'ABBV',
    'WMT', 'COST', 'HD', 'MCD', 'SBUX', 'NKE', 'DIS', 'NFLX',
    'GLD', 'GC', 'CL', 'SI', 'NG',
    'IWM', 'RUT', 'TLT', 'XLE', 'XLF', 'XLK',
    'AMD', 'INTC', 'AVGO', 'CRM', 'ORCL',
    'COIN', 'MSTR', 'PLTR',
}

# Path template for the ideas CSV (matches select_news_articles.py output).
CSV_DIR      = '/home/flask/blog/article_ideas'
CSV_FILE_TPL = 'article_queue_{}.csv'

# blog_queue service URL (Flask app receiving queue requests).
BLOG_QUEUE_URL = config.blog_queue_server   # e.g. 'http://localhost:7171/'

# Seconds to wait between queuing calls — just enough to avoid hammering
# blog_queue.py (the actual processing delay comes from article_processor).
QUEUE_CALL_DELAY = 2

# Log file for this script's own output (separate from article_processor logs).
LOG_DIR  = '/home/flask/blog/logs'
LOG_PATH = os.path.join(LOG_DIR, 'daily_queue.log')


# ============================================================
# FEATURED ARTICLE SCORING
# Determines which article gets the hero position on the homepage.
# Highest featured_score → queued last → published last → featured.
# ============================================================

def featured_score(row):
    """
    Compute front-page worthiness score for a selected article.
    Higher = better featured candidate.  The article with the highest
    featured_score is queued last and becomes the hero on the SMN homepage.
    """
    fs = 0.0
    ticker = row['ticker'].upper()

    # --- Tier 1: Urgency (what's happening RIGHT NOW) ---

    # Earnings
    earn = (row.get('earnings_type') or '').strip().lower()
    if earn == 'recent':
        fs += 3.0        # earnings just reported — peak urgency
    elif earn == 'upcoming':
        fs += 1.5        # earnings soon — anticipation hook

    # Volume spike
    try:
        rvol = float(row.get('rvol') or 0)
    except (ValueError, TypeError):
        rvol = 0.0
    if   rvol >= 3.0:  fs += 2.5   # extreme volume spike
    elif rvol >= 2.0:  fs += 1.5   # notable volume spike

    # Pattern starts soon — actionable
    try:
        pat_start = datetime.date.fromisoformat(row['pat_start_date'][:10])
        days_to_start = (pat_start - TODAY).days
    except Exception:
        days_to_start = 30
    if   days_to_start <= 1:  fs += 2.0   # starts tomorrow or today
    elif days_to_start <= 3:  fs += 1.5   # starts this week
    elif days_to_start <= 7:  fs += 0.5   # starts next week

    # --- Tier 2: Name recognition (will readers click?) ---
    if ticker in MEGA_CAP_TICKERS:
        fs += 3.0
    elif ticker in WELL_KNOWN_TICKERS:
        fs += 1.5

    # Individual stocks are more specific/clickable than ETFs/indices for hero
    atype = (row.get('asset_type') or '').strip().lower()
    if atype == 'stock':
        fs += 0.5

    # --- Tier 3: Pattern confidence ---
    try:
        sr = float(row.get('pat_SR') or 0)
    except (ValueError, TypeError):
        sr = 0.0
    if   sr >= 2.0:  fs += 1.5
    elif sr >= 1.5:  fs += 1.0
    elif sr >= 1.0:  fs += 0.5

    try:
        years  = int(row.get('pat_years')  or 0)
        pyears = int(row.get('pat_pyears') or 0)
    except (ValueError, TypeError):
        years = pyears = 0
    if years >= 12 and pyears >= years:
        fs += 1.0    # long lookback, 100% success — authoritative

    # --- Tier 4: News catalyst ---
    if str(row.get('in_news', '0')).strip() == '1':
        fs += 1.0

    # --- LLM score as tiebreaker ---
    try:
        fs += float(row.get('score') or 0) * 0.2
    except (ValueError, TypeError):
        pass

    return round(fs, 2)


# ============================================================
# LIVE QUEUE DEDUP
# Checked at queue-time (3AM), not just at idea-selection time
# (2AM). This catches articles manually added to the queue by
# the dashboard between 2AM and 3AM.
# ============================================================

def is_already_queued(symbol):
    """
    Return True if this symbol already has a job sitting in the
    Redis queue right now.  Reads the queue non-destructively.
    Silently skips if Redis is unavailable (returns False so we
    don't block automation on a transient connection error).
    """
    sym = symbol.upper().strip()
    try:
        r = redis.Redis(host='localhost', port=6379, db=config.articles_redis_db)
        for item in r.lrange(config.NEWS_QUEUE_NAME, 0, -1):
            try:
                job = json.loads(item)
                if str(job.get('symbol', '')).upper().strip() == sym:
                    return True
            except Exception:
                continue
    except Exception as e:
        print(f'  [WARN] Redis live-queue check failed: {e}')
    return False

# ============================================================
# HELPERS
# ============================================================

TODAY = datetime.date.today()


def find_csv():
    """
    Find today's ideas CSV.  Falls back to yesterday's if today's isn't
    ready yet (edge case: cron ran before select_news_articles.py finished).
    """
    for delta in [0, 1]:
        d    = TODAY - datetime.timedelta(days=delta)
        path = os.path.join(CSV_DIR, CSV_FILE_TPL.format(d.isoformat()))
        if os.path.exists(path):
            return path, d
    return None, None


def read_ideas(csv_path):
    """
    Read the article ideas CSV and return a list of dicts, sorted by
    score descending (the CSV is already sorted but we re-sort to be safe).
    """
    rows = []
    try:
        with open(csv_path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    row['score'] = float(row.get('score', 0) or 0)
                except ValueError:
                    row['score'] = 0.0
                try:
                    row['reader_order'] = int(row.get('reader_order', 0) or 0)
                except ValueError:
                    row['reader_order'] = 0
                rows.append(row)
    except Exception as e:
        print(f'[ERROR] Could not read CSV {csv_path}: {e}')
        return []
    rows.sort(key=lambda r: r['score'], reverse=True)
    return rows


def filter_ideas(rows):
    """
    Apply MIN_SCORE, REQUIRE_IN_NEWS, and basic field-presence checks.
    Returns the ideas that are eligible to queue, score-sorted descending.
    """
    eligible = []
    for row in rows:
        if row['score'] < MIN_SCORE:
            continue
        if REQUIRE_IN_NEWS and str(row.get('in_news', '0')) != '1':
            continue
        required = ['pat_resource_id', 'ticker', 'pat_start_date',
                    'pat_days', 'pat_years', 'pat_direction']
        if any(not str(row.get(f, '')).strip() for f in required):
            print(f"  [SKIP] {row.get('ticker','?')} — missing pattern field(s)")
            continue
        eligible.append(row)
    return eligible


def get_sector(row):
    """Return the broad sector label for a row (falls back to 'other')."""
    return SECTOR_MAP.get(row['ticker'].upper(), 'other')


def select_diverse_lineup(eligible, n):
    """
    Pick up to `n` articles from `eligible` (score-sorted desc) while
    enforcing diversity rules:

      1. ENSURE_EARNINGS  — reserve 1 slot for the best earnings article
                            (earnings_type != '') if one exists above MIN_SCORE.
      2. ENSURE_NON_STOCK — reserve 1 slot for the best non-stock
                            (asset_type not in {'stock','other'}) if available.
      3. MAX_PER_SECTOR   — no more than MAX_PER_SECTOR articles from the
                            same sector label in the final lineup.

    After reserved slots are filled, remaining slots go to the highest-scored
    eligible articles that don't violate the sector cap.
    """
    lineup       = []
    sector_count = {}
    used_tickers = set()

    def _can_add(row):
        if row['ticker'] in used_tickers:
            return False
        if row.get('asset_type', '').lower() == 'index' and sector_count.get('__index__', 0) >= MAX_INDICES:
            return False
        sec = get_sector(row)
        if sec != 'other' and sector_count.get(sec, 0) >= MAX_PER_SECTOR:
            return False
        # Correlated-group cap: max 1 per thematic group (e.g. SPY + SPX = same group)
        grp = CORRELATED_GROUPS.get(row['ticker'].upper())
        if grp and sector_count.get(f'__corr_{grp}__', 0) >= 1:
            return False
        return True

    def _add(row):
        lineup.append(row)
        used_tickers.add(row['ticker'])
        if row.get('asset_type', '').lower() == 'index':
            sector_count['__index__'] = sector_count.get('__index__', 0) + 1
        sec = get_sector(row)
        sector_count[sec] = sector_count.get(sec, 0) + 1
        # Track correlated groups
        grp = CORRELATED_GROUPS.get(row['ticker'].upper())
        if grp:
            sector_count[f'__corr_{grp}__'] = sector_count.get(f'__corr_{grp}__', 0) + 1

    # ── Rule 1: earnings slot ──────────────────────────────────────────
    # Guard: only reserve if there is still room for at least one score-based pick.
    if ENSURE_EARNINGS and len(lineup) + 1 < n:
        earnings_candidates = [r for r in eligible
                               if r.get('earnings_type', '').strip()]
        for r in earnings_candidates:
            if _can_add(r):
                _add(r)
                print(f'  [diversity] Earnings slot  → {r["ticker"]} '
                      f'({r["earnings_type"]})  score={r["score"]:.1f}')
                break

    # ── Rule 2: non-stock slot ─────────────────────────────────────────
    non_stock_types = {'index', 'etf', 'futures', 'commodity', 'forex'}
    if ENSURE_NON_STOCK and len(lineup) + 1 < n:
        non_stock = [r for r in eligible
                     if r.get('asset_type', '').lower() in non_stock_types]
        for r in non_stock:
            if _can_add(r):
                _add(r)
                print(f'  [diversity] Non-stock slot → {r["ticker"]} '
                      f'({r["asset_type"]})  score={r["score"]:.1f}')
                break

    # ── Fill remaining slots by score, respecting sector cap ──────────
    for r in eligible:
        if len(lineup) >= n:
            break
        if _can_add(r):
            _add(r)

    # Print any cap rejections for transparency
    rejected_sector = [r for r in eligible if r['ticker'] not in used_tickers
                       and get_sector(r) != 'other'
                       and sector_count.get(get_sector(r), 0) >= MAX_PER_SECTOR]
    if rejected_sector:
        print(f'  [diversity] Sector-capped (excluded): '
              + ', '.join(f'{r["ticker"]}({get_sector(r)})' for r in rejected_sector[:6]))
    rejected_index = [r for r in eligible if r['ticker'] not in used_tickers
                      and r.get('asset_type', '').lower() == 'index']
    if rejected_index:
        print(f'  [diversity] Index-capped (excluded, max 1/day): '
              + ', '.join(r['ticker'] for r in rejected_index[:6]))

    rejected_corr = [r for r in eligible if r['ticker'] not in used_tickers
                     and CORRELATED_GROUPS.get(r['ticker'].upper())
                     and sector_count.get(
                         f'__corr_{CORRELATED_GROUPS[r["ticker"].upper()]}__', 0) >= 1]
    if rejected_corr:
        print(f'  [diversity] Correlated-group capped (excluded): '
              + ', '.join(
                  f'{r["ticker"]}(≈{CORRELATED_GROUPS[r["ticker"].upper()]})'
                  for r in rejected_corr[:6]))

    return lineup


def queue_article(row, article_publish_date):
    """
    Call blog_queue /write_news_article to enqueue one article job.
    Returns True on HTTP 200, False otherwise.

    Route:
      GET /write_news_article/{resource_id}/{symbol}/{date}/{days}/{years}
                               /{direction}/{userid}/{article_publish_date}
                               ?pattern_mode=pe|consecutive
    """
    resource_id          = str(row['pat_resource_id']).strip()
    symbol               = str(row['ticker']).strip().upper()
    date                 = str(row['pat_start_date']).strip()   # pattern start YYYY-MM-DD
    days                 = str(row['pat_days']).strip()
    years                = str(row['pat_years']).strip()        # MUST stay as string
    direction            = str(row['pat_direction']).strip().lower()
    pattern_mode         = str(row.get('pat_mode', 'consecutive') or 'consecutive').strip().lower()
    userid               = str(USERID)
    pub_date             = str(article_publish_date)

    url = (f'{BLOG_QUEUE_URL.rstrip("/")}/write_news_article/'
           f'{resource_id}/{symbol}/{date}/{days}/{years}/'
           f'{direction}/{userid}/{pub_date}'
           f'?pattern_mode={pattern_mode}')

    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            return True, resp.json()
        else:
            return False, {'status_code': resp.status_code, 'text': resp.text[:200]}
    except Exception as e:
        return False, {'error': str(e)}


def log_run(queued, skipped, csv_path, csv_date):
    """Append a summary JSON line to the daily queue log."""
    os.makedirs(LOG_DIR, exist_ok=True)
    row = {
        'ts':        datetime.datetime.utcnow().isoformat(timespec='seconds') + 'Z',
        'run_date':  TODAY.isoformat(),
        'csv_date':  csv_date.isoformat() if csv_date else None,
        'csv_path':  csv_path,
        'queued':    queued,
        'skipped':   skipped,
    }
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(json.dumps(row) + '\n')


# ============================================================
# MAIN
# ============================================================

def main():
    print('=' * 68)
    print(f'DAILY ARTICLE QUEUE  —  {TODAY}  ({TODAY.strftime("%A")})  —  Started {datetime.datetime.now():%Y-%m-%d %H:%M:%S}')
    print(f'Target: {ARTICLES_PER_DAY} articles  |  min score: {MIN_SCORE}  |  userid: {USERID}')
    print('=' * 68)

    # ── 1. Find today's ideas CSV ──────────────────────────────────────
    csv_path, csv_date = find_csv()
    if not csv_path:
        print(f'[ERROR] No ideas CSV found for {TODAY} or yesterday.  '
              f'Did select_news_articles.py run at 2AM?')
        sys.exit(1)
    print(f'\nCSV: {csv_path}  (ideas date: {csv_date})')

    # ── 2. Read and filter ideas ───────────────────────────────────────
    all_ideas  = read_ideas(csv_path)
    eligible   = filter_ideas(all_ideas)
    to_queue   = select_diverse_lineup(eligible, ARTICLES_PER_DAY)

    print(f'\nIdeas in CSV        : {len(all_ideas)}')
    print(f'Eligible (filtered) : {len(eligible)}')
    print(f'Will queue          : {len(to_queue)}  '
          f'(ARTICLES_PER_DAY={ARTICLES_PER_DAY}, '
          f'ensure_earnings={ENSURE_EARNINGS}, '
          f'ensure_non_stock={ENSURE_NON_STOCK}, '
          f'max_per_sector={MAX_PER_SECTOR})')

    if not to_queue:
        print('[WARN] Nothing to queue — check MIN_SCORE or ideas CSV.')
        log_run([], [], csv_path, csv_date)
        return

    # Compute featured_score for each article and sort so the best featured
    # candidate is queued LAST → published last → hero position on homepage.
    for r in to_queue:
        r['featured_score'] = featured_score(r)
    to_queue.sort(key=lambda r: r['featured_score'])   # ascending: lowest first, highest last

    feat = to_queue[-1]
    print(f'  [featured] {feat["ticker"]} (featured_score={feat["featured_score"]:.1f}) '
          f'→ will be published last (hero position)')
    print(f'  Queue order: '
          + ' → '.join(f'{r["ticker"]}({r["featured_score"]:.1f})' for r in to_queue))

    # ── 3. Print the plan ─────────────────────────────────────────────
    print(f'\n{"#":<4} {"Ticker":<8} {"Score":<6} {"FeatS":<6} {"Earn":<8} {"rvol":<6} '
          f'{"PatDate":<12} {"Dir":<5} {"SR":<5}  Company')
    print('-' * 100)
    for i, row in enumerate(to_queue, 1):
        earn   = (row.get('earnings_type') or '')[:7]
        rvol   = f'{float(row["rvol"]):.1f}x' if row.get('rvol') else ''
        sr     = f'{float(row["pat_SR"]):.2f}' if row.get("pat_SR") else ''
        fs     = row.get('featured_score', 0)
        is_hero = ' *' if i == len(to_queue) else ''
        print(f'{i:<4} {row["ticker"]:<8} {row["score"]:<6.1f} {fs:<6.1f} {earn:<8} {rvol:<6} '
              f'{row["pat_start_date"]:<12} {row["pat_direction"]:<5} {sr:<5}  '
              f'{row["company"][:20]}{is_hero}')

    # ── 4. Queue each article ─────────────────────────────────────────
    print(f'\nQueuing to: {BLOG_QUEUE_URL}')
    queued_log  = []
    skipped_log = []

    for i, row in enumerate(to_queue, 1):
        ticker = row['ticker']
        print(f'\n  [{i}/{len(to_queue)}] {ticker}  score={row["score"]:.1f}  '
              f'pat={row["pat_start_date"]} {row["pat_direction"]} '
              f'{row["pat_years"]}yr  ...', end=' ', flush=True)

        # Live check: skip if this symbol is already in the queue right now
        # (catches manual dashboard queues added between 2AM select run and now)
        if is_already_queued(ticker):
            print(f'SKIP (already in Redis queue — manual or prior auto)')
            skipped_log.append({'ticker': ticker,
                                 'reason': 'already in Redis queue'})
            continue

        ok, detail = queue_article(row, TODAY.isoformat())

        if ok:
            print('OK')
            queued_log.append({
                'ticker':         ticker,
                'score':          row['score'],
                'featured_score': row.get('featured_score', 0),
                'pat_date':       row['pat_start_date'],
                'pat_dir':        row['pat_direction'],
                'pat_years':      row['pat_years'],
                'resource_id':    row['pat_resource_id'],
                'pub_date':       TODAY.isoformat(),
            })
        else:
            print(f'FAILED  {detail}')
            skipped_log.append({'ticker': ticker, 'reason': str(detail)})

        if i < len(to_queue):
            time.sleep(QUEUE_CALL_DELAY)

    # ── 5. Summary ────────────────────────────────────────────────────
    print(f'\n{"=" * 68}')
    print(f'Queued successfully : {len(queued_log)}')
    print(f'Failed              : {len(skipped_log)}')
    if skipped_log:
        for s in skipped_log:
            print(f"  FAIL: {s['ticker']} — {s['reason']}")

    print(f'\nArticles in queue order (article_processor will handle sequentially):')
    for i, q in enumerate(queued_log, 1):
        is_hero = ' ← FEATURED (hero position)' if i == len(queued_log) else ''
        print(f'  {i}. [{q["ticker"]:<6}] score={q["score"]:.1f}  '
              f'feat={q["featured_score"]:.1f}  '
              f'pat={q["pat_date"]} {q["pat_dir"]} {q["pat_years"]}yr{is_hero}')

    print(f'\nNote: article_processor.py handles these one at a time.')
    print(f'      Each article takes ~3-5 min (9-step AI pipeline),')
    print(f'      so {len(queued_log)} articles will be published over ~{len(queued_log)*4} min.')

    log_run(queued_log, skipped_log, csv_path, csv_date)
    print(f'\nDone.  Log appended to {LOG_PATH}')


if __name__ == '__main__':
    main()
