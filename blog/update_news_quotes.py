"""
update_news_quotes.py
=====================
Lightweight quote injector that keeps news page prices fresh without
triggering a full rebuild_news_home.py run (which involves article
queries, template rendering, etc.).

PURPOSE:
  The news homepage market bar and the 7 individual security pages
  (/markets/*.html) display live prices. Previously these only updated
  when rebuild_news_home.py ran (on new articles) or when
  generate_security_pages.py ran (daily). This script bridges the gap
  by injecting fresh prices every minute during market hours.

WHAT IT DOES:
  1) Fetches realtime quotes for all 7 headline securities
     (S&P 500, DOW, NASDAQ, VIX, Crude Oil, Natural Gas, Gold)
     via get_price_eod.get_quote_details(), which tries the realtime
     price service first (free/fast) and falls back to EODHD direct API.
  2) Injects updated prices into the news homepage market bar
     (/var/www/smn/index.html) using regex-based HTML replacement.
  3) Injects updated prices into each security detail page
     (/var/www/smn/markets/*.html), updating:
       - Hero price and change percentage
       - Quote detail rows (Open, High, Low, Prev Close, Volume)
       - Timestamp
       - Market bar within the security page

CONCURRENCY SAFETY:
  rebuild_news_home.py may regenerate index.html at any time (when new
  articles are published). This script is designed to be safe in that case:
    - All file writes are atomic: writes to a temp file first, then
      os.replace() to swap it in. No partial writes.
    - If a file can't be read (e.g. mid-write by rebuild), it's skipped
      silently. The next run (1 minute later) will pick it up.
    - Entire run completes in under 1 second (typically ~0.3s).

MARKET HOURS:
  The script checks if the current time is within US market hours
  (Mon-Fri, 9:25 AM - 4:10 PM ET). Outside those hours it exits
  immediately and silently. The cron itself runs every minute on
  weekdays; the script handles the time-of-day gating internally.

CRON SETUP:
  * * * * 1-5 cd /home/flask/blog && python update_news_quotes.py >> /home/flask/blog/logs/quotes.log 2>&1

MANUAL RUN:
  python update_news_quotes.py           # respects market hours
  python update_news_quotes.py --force   # runs regardless of time

DEPENDENCIES:
  - get_price_eod.py (realtime service + EODHD fallback)
  - config.py (news_root_folder, realtime_service_url)
  - No AI calls, no appserver calls, no Redis
"""

import sys, os, re, json, tempfile, time
sys.path.insert(0, '/home/flask')
sys.path.insert(0, '/home/flask/blog')
import config

from pathlib import Path
from datetime import datetime, timezone, timedelta
from get_price_eod import get_quote_details

# ─── Paths ───────────────────────────────────────────────────────────
NEWS_ROOT = Path(getattr(config, 'news_root_folder', '/var/www/smn'))
INDEX_HTML = NEWS_ROOT / "index.html"
MARKETS_DIR = NEWS_ROOT / "markets"

# TradeWave
TW_STATIC_DIR = Path(config.web_root_dir) / "_static"
TW_HOME_HTML = TW_STATIC_DIR / "home.html"
TW_MARKETS_DIR = TW_STATIC_DIR / "markets"
TW_DOMAIN_ROOT = config.domain_root

# ─── Tickers (same as rebuild_news_home.py + generate_security_pages.py) ──
TICKERS = [
    {"label": "S&P 500",  "short": "S&P 500",  "symbol": "GSPC", "exchange": "INDX", "slug": "sp500",       "appserver_symbol": "SPX"},
    {"label": "DOW",      "short": "DOW",       "symbol": "DJI",  "exchange": "INDX", "slug": "dow",         "appserver_symbol": "DJI"},
    {"label": "NASDAQ",   "short": "NASDAQ",    "symbol": "IXIC", "exchange": "INDX", "slug": "nasdaq",      "appserver_symbol": "IXIC"},
    {"label": "VIX",      "short": "VIX",       "symbol": "VIX",  "exchange": "INDX", "slug": "vix",         "appserver_symbol": "VIX"},
    {"label": "CRUDE",    "short": "CRUDE",     "symbol": "CL",   "exchange": "COMM", "slug": "crude-oil",   "appserver_symbol": "CL"},
    {"label": "NAT GAS",  "short": "NAT GAS",   "symbol": "NG",   "exchange": "COMM", "slug": "natural-gas", "appserver_symbol": "NG"},
    {"label": "GOLD",     "short": "GOLD",      "symbol": "GC",   "exchange": "COMM", "slug": "gold",        "appserver_symbol": "GC"},
]


# ─── Futures mapping (index → futures proxy) ─────────────────────────
FUTURES_MAP = {
    "GSPC": {"symbol": "ES", "exchange": "COMM"},
    "DJI":  {"symbol": "YM", "exchange": "COMM"},
    "IXIC": {"symbol": "NQ", "exchange": "COMM"},
}

# Tickers that freeze during futures mode (show last close, no update)
FREEZE_DURING_FUTURES = {"VIX"}


# ─── Time window checks ──────────────────────────────────────────────
def _get_et_now():
    """Return current Eastern Time datetime."""
    try:
        import pytz
        return datetime.now(pytz.timezone('US/Eastern'))
    except ImportError:
        return datetime.now(timezone(timedelta(hours=-4)))


def _get_session():
    """Return 'market', 'futures', or 'closed'.

    market:  Mon-Fri 9:30 AM - 4:00 PM ET
    futures: Mon-Thu 4:00 PM - next 9:30 AM ET, Fri 4:00-5:00 PM, Sun 6:00 PM - Mon 9:30 AM
    closed:  Fri 5:00 PM - Sun 6:00 PM, all day Saturday
    """
    et = _get_et_now()
    wd = et.weekday()  # Mon=0 .. Sun=6
    h, m = et.hour, et.minute
    t = h * 60 + m  # minutes since midnight

    market_open = 9 * 60 + 30   # 9:30 AM
    market_close = 16 * 60      # 4:00 PM
    sun_futures_open = 18 * 60  # 6:00 PM

    # Saturday: always closed
    if wd == 5:
        return 'closed'

    # Sunday: closed until 6 PM, then futures
    if wd == 6:
        return 'futures' if t >= sun_futures_open else 'closed'

    # Monday-Friday
    if market_open <= t < market_close:
        return 'market'

    # After market close (4:00 PM+) on Mon-Fri: futures
    # Friday: futures from 4:00-5:00 PM (CME closes at 5 PM), then closed
    # Before market open (<9:30 AM) on Mon-Fri: futures
    if t >= market_close:
        if wd == 4:  # Friday
            return 'futures' if t < 17 * 60 else 'closed'  # futures until 5 PM
        return 'futures'

    # Before market open on Mon-Fri
    return 'futures'


def _is_market_hours():
    """Return True if current time is within US market hours."""
    return _get_session() == 'market'


# ─── Price formatting ────────────────────────────────────────────────
def _fmt_price(val):
    if val is None:
        return "—"
    if abs(val) >= 1000:
        return f"{val:,.2f}"
    if abs(val) >= 10:
        return f"{val:.2f}"
    return f"{val:.4f}"


def _safe_float(val):
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ─── Atomic file write ───────────────────────────────────────────────
def _atomic_write(path, content):
    """Write content to path atomically using temp file + rename.
    Preserves the original file's permissions (defaults to 644 if new)."""
    # Capture original permissions before overwriting
    try:
        orig_mode = os.stat(str(path)).st_mode
    except OSError:
        orig_mode = 0o100644

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix='.tmp')
    closed = False
    try:
        os.write(fd, content.encode('utf-8'))
        os.close(fd)
        closed = True
        os.chmod(tmp, orig_mode)
        os.replace(tmp, str(path))
    except Exception:
        if not closed:
            os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ─── Fetch all quotes ────────────────────────────────────────────────
def _fetch_all_quotes():
    """Fetch quotes for all tickers from realtime service (with EODHD fallback)."""
    quotes = {}
    for t in TICKERS:
        try:
            q = get_quote_details(t["symbol"], t["exchange"])
            if q and q.get("close") is not None:
                quotes[t["symbol"]] = q
        except Exception as e:
            print(f"  [WARN] Failed to get quote for {t['symbol']}: {e}")
    return quotes


# ─── Write quotes.json for article market bars ────────────────────────
def _write_quotes_json(quotes):
    """Write a small JSON file that article pages load client-side for their market bars."""
    assets_dir = NEWS_ROOT / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    json_path = assets_dir / "quotes.json"

    # Only include the fields articles need
    slim = {}
    for sym, q in quotes.items():
        slim[sym] = {
            "close": q.get("close"),
            "change_p": q.get("change_p"),
        }

    try:
        _atomic_write(json_path, json.dumps(slim))
        print("  [OK] Wrote quotes.json")
    except Exception as e:
        print(f"  [ERR] Could not write quotes.json: {e}")


# ─── Inject into news homepage ────────────────────────────────────────
def _inject_homepage(quotes):
    """Replace the market bar in index.html with fresh prices."""
    if not INDEX_HTML.exists():
        print("  [SKIP] index.html not found")
        return False

    try:
        html = INDEX_HTML.read_text("utf-8")
    except Exception:
        print("  [SKIP] Could not read index.html (rebuild in progress?)")
        return False

    # Build new market bar items
    items_html = ""
    for t in TICKERS:
        q = quotes.get(t["symbol"])
        price = _safe_float(q.get("close")) if q else None
        change_p = _safe_float(q.get("change_p")) if q else None

        if price is not None:
            direction = "up" if (change_p or 0) >= 0 else "down"
            sign = "+" if (change_p or 0) >= 0 else ""
            price_fmt = f"{price:,.2f}"
            chg_fmt = f"{sign}{change_p:.2f}%"
        else:
            price_fmt = "—"
            chg_fmt = ""
            direction = "flat"

        items_html += f'''
            <a href="/markets/{t['slug']}.html" class="market-item {direction}">
                <span class="market-symbol">{t['short']}</span>
                <span class="market-price">{price_fmt}</span>
                {"<span class='market-change " + direction + "'>" + chg_fmt + "</span>" if chg_fmt else ""}
            </a>'''

    new_bar = f'''<div class="market-bar">
        <div class="market-bar-content">
            {items_html}
        </div>
    </div>'''

    updated_html = re.sub(
        r'<div class="market-bar">.*?</div>\s*</div>',
        new_bar,
        html,
        flags=re.DOTALL,
        count=1
    )

    if updated_html == html:
        print("  [SKIP] Homepage market bar regex did not match")
        return False

    try:
        _atomic_write(INDEX_HTML, updated_html)
        return True
    except Exception as e:
        print(f"  [ERR] Could not write index.html: {e}")
        return False


# ─── Inject into security pages ──────────────────────────────────────
def _inject_security_page(slug, appserver_symbol, quote, quotes, show_futures=False):
    """Inject fresh prices into a single security page."""
    page_path = MARKETS_DIR / f"{slug}.html"
    if not page_path.exists():
        return False

    try:
        html = page_path.read_text("utf-8")
    except Exception:
        return False

    close_val = _safe_float(quote.get("close"))
    if close_val is None:
        return False

    change = _safe_float(quote.get("change"))
    change_p = _safe_float(quote.get("change_p"))

    # Format change string
    if change_p is not None:
        direction = "up" if change_p >= 0 else "down"
        sign = "+" if change_p >= 0 else ""
        if change is not None:
            chg_str = f"{sign}{change:.2f} ({sign}{change_p:.2f}%)"
        else:
            chg_str = f"{sign}{change_p:.2f}%"
    else:
        direction = "flat"
        chg_str = ""

    # 1) Update hero price
    price_display = _fmt_price(close_val)
    if show_futures:
        price_display += "<sup style='font-size:12px;color:#6366f1;margin-left:2px'>F</sup>"
    html = re.sub(
        r'(<span class="price-main">)[^<]*(?:<sup[^>]*>[^<]*</sup>)?(</span>)',
        rf'\g<1>{price_display}\2',
        html
    )

    # 2) Update price-change
    html = re.sub(
        r"<span class=['\"]price-change [^'\"]*['\"]>[^<]*</span>",
        f"<span class='price-change {direction}'>{chg_str}</span>",
        html
    )

    # 3) Update quote detail rows (Open, High, Low, Prev Close)
    detail_map = {"Open": "open", "High": "high", "Low": "low", "Prev Close": "previousClose"}
    for label, key in detail_map.items():
        val = _safe_float(quote.get(key))
        html = re.sub(
            rf'(<span class="quote-detail-label">{re.escape(label)}</span>'
            rf'<span class="quote-detail-value">)[^<]*(</span>)',
            rf'\g<1>{_fmt_price(val)}\2',
            html
        )

    # 4) Volume
    vol = _safe_float(quote.get("volume"))
    if vol:
        html = re.sub(
            r'(<span class="quote-detail-label">Volume</span>'
            r'<span class="quote-detail-value">)[^<]*(</span>)',
            rf'\g<1>{vol:,.0f}\2',
            html
        )

    # 5) Timestamp
    ts = quote.get("timestamp")
    if ts:
        try:
            from datetime import datetime as dt
            ts_str = dt.fromtimestamp(int(ts), tz=timezone.utc).strftime("%b %d, %Y %I:%M %p UTC")
            html = re.sub(
                r'(<div class="security-meta">)[^<]*(</div>)',
                rf'\g<1>{appserver_symbol} &middot; {ts_str}\2',
                html
            )
        except Exception:
            pass

    # Market bar is updated separately by the session-aware loop in update_quotes()

    try:
        _atomic_write(page_path, html)
        return True
    except Exception:
        return False


# ─── Inject into TradeWave dark security pages ───────────────────────
def _inject_tw_security_page(slug, appserver_symbol, quote, quotes, show_futures=False):
    """Inject fresh prices into a TradeWave dark security page."""
    page_path = TW_MARKETS_DIR / f"{slug}.html"
    if not page_path.exists():
        return False

    try:
        html = page_path.read_text("utf-8")
    except Exception:
        return False

    close_val = _safe_float(quote.get("close"))
    if close_val is None:
        return False

    change = _safe_float(quote.get("change"))
    change_p = _safe_float(quote.get("change_p"))

    if change_p is not None:
        direction = "up" if change_p >= 0 else "down"
        sign = "+" if change_p >= 0 else ""
        if change is not None:
            chg_str = f"{sign}{change:.2f} ({sign}{change_p:.2f}%)"
        else:
            chg_str = f"{sign}{change_p:.2f}%"
    else:
        direction = "flat"
        chg_str = ""

    # Same regex replacements as SMN (identical HTML structure)
    price_display = _fmt_price(close_val)
    if show_futures:
        price_display += "<sup style='font-size:12px;color:#6366f1;margin-left:2px'>F</sup>"
    html = re.sub(r'(<span class="price-main">)[^<]*(?:<sup[^>]*>[^<]*</sup>)?(</span>)',
                  rf'\g<1>{price_display}\2', html)
    html = re.sub(r"<span class=['\"]price-change [^'\"]*['\"]>[^<]*</span>",
                  f"<span class='price-change {direction}'>{chg_str}</span>", html)

    for label, key in [("Open", "open"), ("High", "high"), ("Low", "low"), ("Prev Close", "previousClose")]:
        val = _safe_float(quote.get(key))
        html = re.sub(rf'(<span class="quote-detail-label">{re.escape(label)}</span>'
                      rf'<span class="quote-detail-value">)[^<]*(</span>)',
                      rf'\g<1>{_fmt_price(val)}\2', html)

    vol = _safe_float(quote.get("volume"))
    if vol:
        html = re.sub(r'(<span class="quote-detail-label">Volume</span>'
                      r'<span class="quote-detail-value">)[^<]*(</span>)',
                      rf'\g<1>{vol:,.0f}\2', html)

    ts = quote.get("timestamp")
    if ts:
        try:
            from datetime import datetime as dt
            ts_str = dt.fromtimestamp(int(ts), tz=timezone.utc).strftime("%b %d, %Y %I:%M %p UTC")
            html = re.sub(r'(<div class="security-meta">)[^<]*(</div>)',
                          rf'\g<1>{appserver_symbol} &middot; {ts_str}\2', html)
        except Exception:
            pass

    # Market bar is updated separately by the session-aware loop in update_quotes()

    try:
        _atomic_write(page_path, html)
        return True
    except Exception:
        return False


# ─── Build display items for market bar ──────────────────────────────
def _build_bar_items(quotes, futures_quotes, session, link_prefix="/markets/", current_slug=None):
    """Build market bar HTML items, switching to futures for indices when session='futures'."""
    items_html = ""
    for t in TICKERS:
        sym = t["symbol"]
        is_index = sym in FUTURES_MAP
        is_frozen = sym in FREEZE_DURING_FUTURES

        if session == 'futures' and is_index:
            # Use futures proxy
            fmap = FUTURES_MAP[sym]
            q = futures_quotes.get(fmap["symbol"])
            show_asterisk = True
        elif session == 'futures' and is_frozen:
            # VIX: keep last close, no update
            q = quotes.get(sym)
            show_asterisk = False
        else:
            q = quotes.get(sym)
            show_asterisk = False

        price = _safe_float(q.get("close")) if q else None
        change_p = _safe_float(q.get("change_p")) if q else None

        if price is not None:
            price_fmt = f"{price:,.2f}"
            if show_asterisk:
                price_fmt += "<sup style='font-size:9px;color:#6366f1;margin-left:1px'>F</sup>"
            if change_p is not None:
                direction = "up" if change_p >= 0 else "down"
                sign = "+" if change_p >= 0 else ""
                chg_fmt = f"{sign}{change_p:.2f}%"
            else:
                # Price known but no change value (e.g. some futures-proxy quotes):
                # show the price and omit the change badge rather than crashing the bar.
                chg_fmt, direction = "", "flat"
        else:
            price_fmt, chg_fmt, direction = "", "", "flat"

        active = " current" if t["slug"] == current_slug else ""
        href = f'{link_prefix}{t["slug"]}.html'
        price_style = ' style="color:#818cf8"' if show_asterisk else ''
        items_html += f'''
            <a href="{href}" class="market-item {direction}{active}">
                <span class="market-symbol">{t['short']}</span>
                {f'<span class="market-price"{price_style}>{price_fmt}</span>' if price_fmt else ''}
                {"<span class='market-change " + direction + "'>" + chg_fmt + "</span>" if chg_fmt else ""}
            </a>'''
    return items_html


def _fetch_futures_quotes():
    """Fetch quotes for futures proxies (ES, YM, NQ)."""
    fq = {}
    for idx_sym, fmap in FUTURES_MAP.items():
        try:
            q = get_quote_details(fmap["symbol"], fmap["exchange"])
            if q and q.get("close") is not None:
                fq[fmap["symbol"]] = q
        except Exception as e:
            print(f"  [WARN] Failed to get futures quote for {fmap['symbol']}: {e}")
    return fq


# ─── Main ─────────────────────────────────────────────────────────────
def update_quotes(force=False):
    """Fetch realtime quotes and inject into all news/TW pages."""
    session = _get_session()
    if not force and session == 'closed':
        return

    start = time.time()
    print(f"[QUOTES] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} Session: {session}")

    quotes = _fetch_all_quotes()
    if not quotes:
        print("[QUOTES] No quotes returned, skipping")
        return

    # Fetch futures quotes if in futures session
    futures_quotes = {}
    if session == 'futures':
        futures_quotes = _fetch_futures_quotes()
        fsyms = ", ".join(f"{s}: {futures_quotes[s]['close']}" for s in futures_quotes)
        print(f"  Futures: {fsyms}")

    symbols = ", ".join(f"{s}: {quotes[s]['close']}" for s in quotes)
    print(f"  Got {len(quotes)} quotes: {symbols}")

    # Write quotes.json for article market bars (loaded client-side via JS)
    _write_quotes_json(quotes)

    # ── Inject into SMN homepage ──
    smn_bar_items = _build_bar_items(quotes, futures_quotes, session, link_prefix="/markets/")
    smn_bar = f'''<div class="market-bar">
        <div class="market-bar-content">
            {smn_bar_items}
        </div>
    </div>'''

    if INDEX_HTML.exists():
        try:
            html = INDEX_HTML.read_text("utf-8")
            updated_html = re.sub(r'<div class="market-bar">.*?</div>\s*</div>',
                                  smn_bar, html, flags=re.DOTALL, count=1)
            if updated_html != html:
                _atomic_write(INDEX_HTML, updated_html)
                print("  [OK] Homepage market bar updated")
        except Exception as e:
            print(f"  [ERR] Homepage: {e}")

    # ── Inject into TradeWave homepage ──
    tw_bar_items = _build_bar_items(quotes, futures_quotes, session,
                                    link_prefix=f"{TW_DOMAIN_ROOT}_static/markets/")
    tw_bar = f'''<div class="market-bar">
        <div class="market-bar-content">
            {tw_bar_items}
        </div>
    </div>'''

    if TW_HOME_HTML.exists():
        try:
            html = TW_HOME_HTML.read_text("utf-8")
            updated_html = re.sub(r'<div class="market-bar">.*?</div>\s*</div>',
                                  tw_bar, html, flags=re.DOTALL, count=1)
            if updated_html != html:
                _atomic_write(TW_HOME_HTML, updated_html)
                print("  [OK] TW homepage market bar updated")
        except Exception as e:
            print(f"  [ERR] TW Homepage: {e}")

    # ── Inject into all security pages (hero prices + market bars) ──
    smn_updated = 0
    tw_updated = 0
    for t in TICKERS:
        sym = t["symbol"]
        is_index = sym in FUTURES_MAP
        is_frozen = sym in FREEZE_DURING_FUTURES

        if session == 'futures' and is_index:
            fmap = FUTURES_MAP[sym]
            hero_quote = futures_quotes.get(fmap["symbol"])
            show_futures = True
        elif session == 'futures' and is_frozen:
            hero_quote = quotes.get(sym)
            show_futures = False
        else:
            hero_quote = quotes.get(sym)
            show_futures = False

        # SMN hero prices
        if hero_quote and _inject_security_page(t["slug"], t["appserver_symbol"], hero_quote, quotes, show_futures=show_futures):
            smn_updated += 1

        # TW hero prices
        if hero_quote and _inject_tw_security_page(t["slug"], t["appserver_symbol"], hero_quote, quotes, show_futures=show_futures):
            tw_updated += 1

    print(f"  [OK] Updated {smn_updated} SMN security pages")
    if tw_updated:
        print(f"  [OK] Updated {tw_updated} TW security pages")

    # ── Inject session-aware market bars into all security pages (runs last) ──
    for t in TICKERS:
        slug = t["slug"]
        for markets_dir, link_pf in [(MARKETS_DIR, "/markets/"), (TW_MARKETS_DIR, f"{TW_DOMAIN_ROOT}_static/markets/")]:
            page_path = markets_dir / f"{slug}.html"
            if not page_path.exists():
                continue
            try:
                html = page_path.read_text("utf-8")
                bar_items = _build_bar_items(quotes, futures_quotes, session,
                                             link_prefix=link_pf, current_slug=slug)
                new_bar = f'''<div class="market-bar">
        <div class="market-bar-content">
            {bar_items}
        </div>
    </div>'''
                html = re.sub(r'<div class="market-bar">.*?</div>\s*</div>',
                              new_bar, html, flags=re.DOTALL, count=1)
                _atomic_write(page_path, html)
            except Exception:
                pass

    print(f"[QUOTES] Done in {time.time() - start:.1f}s")


if __name__ == "__main__":
    force = "--force" in sys.argv
    update_quotes(force=force)
