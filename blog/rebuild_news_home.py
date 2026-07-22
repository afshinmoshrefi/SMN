"""
rebuild_news_home.py
====================
Generates the SeasonalMarketNews.com home page from Redis/posts.json.

V3: Multi-template support with 12 unique designs.
    
    PROFESSIONAL NEWS TEMPLATES (Recommended):
    - wire:       ★ RECOMMENDED - Bloomberg/Reuters style with modular blocks, data bar, mixed layouts
    - pulse:      Cinematic hero + clean 4-column grid + list
    - flagship:   Magazine-style asymmetric layout (large hero left + stacked right)
    - mosaic:     Pinterest/masonry style with varied card sizes
    - spotlight:  Full-width hero + 3-column layout with sidebar
    
    FUNCTIONAL TEMPLATES:
    - calendar:   Timeline view organized by pattern activation date
    - terminal:   Bloomberg-style trading terminal aesthetic
    - broadsheet: Classic newspaper editorial layout
    - dashboard:  Trade signal dashboard with indicators
    - radar:      Grouped by time-to-activation (Today/This Week/Later)
    
    LEGACY TEMPLATES:
    - default:    Featured article + grid layout
    - benzinga:   Hero + 4 secondary cards + list layout
"""

import json
import os
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import sys
import requests
sys.path.insert(0, '/home/flask')
import config
import redis
from get_price_eod import get_quote_details

# =============================================================================
# CONFIGURATION - CHANGE THESE TO CUSTOMIZE YOUR HOME PAGE
# =============================================================================

TEMPLATE = "wire"  # Options: "wire", "pulse", "flagship", "mosaic", "spotlight", "calendar", "terminal", "broadsheet", "dashboard", "radar", "default", "benzinga"
THEME = "light"        # Options: "light", "dark"
SHOW_BADGES = True     # Show Bullish/Bearish badges on articles

SHOW_DATA_BAR = False  # Show stats bar (future: "timing", "sectors", "ticker", "cta")

# Homepage display limits (older articles accessible via search)
DISPLAY_MAX_DAYS_OLD = 14   # Only show articles published within X days (None = no limit)
DISPLAY_MAX_ARTICLES = 50   # Maximum articles to display (None = no limit)

# =============================================================================

# --- core paths ---
NEWS_ROOT  = Path(config.news_root_folder)
POSTS_JSON = NEWS_ROOT / "posts.json"
INDEX_HTML = NEWS_ROOT / "index.html"

SITE_TITLE = "Seasonal Market News"
TAGLINE = "Daily, data-backed coverage of repeating market patterns. Institutional-grade research powered by TradeWave analytics."
CTA_URL = config.domain_root + "register/?lid=1&utm_source=smn&utm_medium=hub"
MAILERLITE_FORM_URL = "https://assets.mailerlite.com/jsonp/489451/forms/173861813170996648/subscribe"


def _get_ml_group_ids():
    """Fetch MailerLite group IDs for SMN, SMN-DAILY, SMN-WEEKLY at page generation time."""
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
        return result
    except Exception:
        return {}

# --- Redis config ---
DEFAULT_TONE = "neutral"
DEFAULT_WEBSITE_ID = 0
redis_client3 = redis.Redis(host=config.webserver_ip, port=6379, db=3)


# =============================================================================
# THEME DEFINITIONS
# =============================================================================

THEMES = {
    "light": {
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
    },
    "dark": {
        "bg_primary": "#0a0a0b",
        "bg_secondary": "#111113",
        "bg_tertiary": "#1a1a1d",
        "text_primary": "#f5f5f7",
        "text_secondary": "#a1a1a6",
        "text_muted": "#6e6e73",
        "accent_blue": "#0a84ff",
        "accent_green": "#30d158",
        "accent_red": "#ff453a",
        "accent_amber": "#fbbf24",
        "border_color": "#2c2c2e",
        "card_shadow": "none",
        "card_hover_shadow": "none",
        "badge_bullish_bg": "rgba(48, 209, 88, 0.15)",
        "badge_bearish_bg": "rgba(255, 69, 58, 0.15)",
    }
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _fmt_date(iso):
    """Format ISO date to 'Dec 15, 2025' style."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%b %d, %Y")
    except Exception:
        return ""


def _fmt_date_short(iso):
    """Format ISO date to 'Dec 15' style (no year)."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%b %d")
    except Exception:
        return ""


def _fmt_weekday(iso):
    """Format ISO date to weekday name."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%A")
    except Exception:
        return ""


def _direction_to_sentiment(direction):
    """Convert direction field to sentiment label and CSS class."""
    if direction and direction.lower() == "long":
        return "Bullish", "bullish"
    elif direction and direction.lower() == "short":
        return "Bearish", "bearish"
    else:
        return None, None


def _get_hero_image_url(article_url, symbol, hero_image=""):
    """Return hero image URL. Uses stored hero_image from posts.json if available,
    otherwise constructs from article URL and symbol (legacy fallback)."""
    if hero_image:
        return hero_image
    if not article_url or not symbol:
        return None
    last_slash = article_url.rfind('/')
    if last_slash == -1:
        return None
    base_path = article_url[:last_slash + 1]
    return f"{base_path}hero_{symbol.upper()}.jpg"


def _extract_pattern_date_from_url(url):
    """
    Extract pattern activation date from article URL.
    URL format: /articles/US/2026/01/26/article-slug.html
    Returns datetime object or None.
    """
    if not url:
        return None
    # Match pattern like /2026/01/26/ or /2025/12/15/
    match = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', url)
    if match:
        try:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return datetime(year, month, day, tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def _get_days_until_pattern(pattern_date):
    """Calculate days until pattern activation."""
    if not pattern_date:
        return None
    now = datetime.now(timezone.utc)
    delta = (pattern_date - now).days
    return delta


def _group_articles_by_pattern_date(articles):
    """Group articles by their pattern activation date."""
    grouped = defaultdict(list)
    for article in articles:
        url = article.get("url", "")
        pattern_date = _extract_pattern_date_from_url(url)
        if pattern_date:
            date_key = pattern_date.strftime("%Y-%m-%d")
            article["_pattern_date"] = pattern_date
            article["_pattern_date_str"] = date_key
            grouped[date_key].append(article)
        else:
            # Fallback to published date
            pub_date = article.get("published_date", "")
            if pub_date:
                date_key = pub_date[:10]
            else:
                date_key = "unknown"
            grouped[date_key].append(article)
    return grouped


def _interleave_by_family(articles):
    """
    Reorder articles so no two adjacent articles share the same market_family,
    while preserving newest-day-first ordering across days.

    Within each calendar day, uses a greedy algorithm: always pick from the
    largest remaining family group that differs from the last picked.
    Example: [ETF, ETF, ETF, US, COMM] → [ETF, US, ETF, COMM, ETF]
    """
    from collections import defaultdict, deque

    # Group by calendar date (first 10 chars of ISO published_date)
    by_date = defaultdict(list)
    for a in articles:
        day = (a.get("published_date") or "")[:10] or "unknown"
        by_date[day].append(a)

    result = []
    for day in sorted(by_date.keys(), reverse=True):
        day_articles = by_date[day]
        if len(day_articles) <= 1:
            result.extend(day_articles)
            continue

        # Pin the first article (last published = intended featured/hero).
        # Articles arrive sorted by published_date desc, so [0] is the hero
        # that daily_article_queue.py deliberately queued last.
        hero = day_articles[0]
        rest = day_articles[1:]

        # Group remaining articles by market_family
        family_queues = defaultdict(deque)
        for a in rest:
            mf = (a.get("market_family") or "other").upper()
            family_queues[mf].append(a)

        result.append(hero)
        last_mf = (hero.get("market_family") or "other").upper()
        while any(family_queues.values()):
            # Sort families: prefer largest queue, break ties alphabetically
            # Always skip last_mf if another option exists
            candidates = sorted(
                [(mf, q) for mf, q in family_queues.items() if q],
                key=lambda x: (-len(x[1]), x[0])
            )
            picked = False
            for mf, q in candidates:
                if mf != last_mf:
                    result.append(q.popleft())
                    last_mf = mf
                    picked = True
                    break
            if not picked:
                # Only one family left — drain it
                mf, q = candidates[0]
                result.append(q.popleft())
                last_mf = mf

    return result


def _filter_articles_for_display(articles):
    """Filter articles based on display settings."""
    if not articles:
        return articles
    
    if DISPLAY_MAX_ARTICLES is None or len(articles) <= DISPLAY_MAX_ARTICLES:
        return articles[:DISPLAY_MAX_ARTICLES] if DISPLAY_MAX_ARTICLES else articles
    
    filtered = articles
    
    if DISPLAY_MAX_DAYS_OLD is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=DISPLAY_MAX_DAYS_OLD)
        filtered_by_age = []
        for p in filtered:
            pub_date_str = p.get("published_date", "")
            if pub_date_str:
                try:
                    pub_date = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
                    if pub_date >= cutoff:
                        filtered_by_age.append(p)
                except Exception:
                    filtered_by_age.append(p)
            else:
                filtered_by_age.append(p)
        filtered = filtered_by_age
    
    if len(filtered) < DISPLAY_MAX_ARTICLES:
        return articles[:DISPLAY_MAX_ARTICLES]
    
    return filtered[:DISPLAY_MAX_ARTICLES]


def _load_articles_from_redis(tone=DEFAULT_TONE, website_id=DEFAULT_WEBSITE_ID, limit=None):
    """Pull article metadata from Redis."""
    pattern = f"*_{tone}_{website_id}"
    articles = []

    for key in redis_client3.scan_iter(match=pattern):
        raw = redis_client3.get(key)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue

        entry = payload.get("entry", {})
        if not entry:
            continue

        entry["_tone"] = payload.get("tone", tone)
        entry["_website_id"] = payload.get("website_id", website_id)
        articles.append(entry)

    articles.sort(key=lambda p: p.get("published_date", ""), reverse=True)

    if limit is not None:
        articles = articles[:limit]

    return articles


def _load_articles_from_json(limit=None):
    """Fallback: load from posts.json if Redis is empty."""
    if not POSTS_JSON.exists():
        return []
    try:
        posts = json.loads(POSTS_JSON.read_text("utf-8"))
    except Exception:
        return []

    posts.sort(key=lambda p: p.get("published_date", ""), reverse=True)
    if limit is not None:
        posts = posts[:limit]
    return posts


# =============================================================================
# SHARED CARD BUILDERS
# =============================================================================

def _build_article_card(p):
    """Generate HTML for a standard article card (default template grid)."""
    url = p.get("url", "")
    title = p.get("title", "")
    dek = p.get("dek", "")
    date = _fmt_date(p.get("published_date", ""))
    symbol = p.get("symbol", "")
    days = p.get("pattern_days", "")
    direction = p.get("direction", "")
    
    meta_parts = [date, symbol]
    if days:
        meta_parts.append(f"{days}-Day Pattern")
    meta = " • ".join(filter(None, meta_parts))
    
    badge_html = ""
    if SHOW_BADGES:
        sentiment_label, sentiment_class = _direction_to_sentiment(direction)
        if sentiment_label and sentiment_class:
            badge_html = f'<span class="article-tag {sentiment_class}">{sentiment_label}</span>'
    
    return f'''
            <a href="{url}" class="article-card">
                {badge_html}
                <h3>{title}</h3>
                <div class="article-meta">{meta}</div>
                <p class="article-excerpt">{dek}</p>
                <span class="read-more">Read Analysis →</span>
            </a>'''


def _build_featured_article(p):
    """Generate HTML for the featured article (larger, with hero image)."""
    url = p.get("url", "")
    title = p.get("title", "")
    dek = p.get("dek", "")
    date = _fmt_date(p.get("published_date", ""))
    symbol = p.get("symbol", "")
    days = p.get("pattern_days", "")
    direction = p.get("direction", "")
    
    meta_parts = [date, symbol]
    if days:
        meta_parts.append(f"{days}-Day Pattern")
    meta = " • ".join(filter(None, meta_parts))
    
    badge_html = ""
    if SHOW_BADGES:
        sentiment_label, sentiment_class = _direction_to_sentiment(direction)
        if sentiment_label and sentiment_class:
            badge_html = f'<span class="featured-tag {sentiment_class}">{sentiment_label}</span>'
    
    hero_image_url = _get_hero_image_url(url, symbol, p.get("hero_image", ""))
    hero_html = ""
    if hero_image_url:
        hero_html = f'''
            <div class="featured-image">
                <img src="{hero_image_url}" alt="{symbol} market analysis - TradeWave.ai">
            </div>'''
    
    return f'''
        <a href="{url}" class="featured-article">
            {hero_html}
            <div class="featured-content">
                {badge_html}
                <h2>{title}</h2>
                <div class="featured-meta">{meta}</div>
                <p class="featured-excerpt">{dek}</p>
                <span class="featured-read-more">Read Full Analysis →</span>
            </div>
        </a>'''


def _build_secondary_card(p):
    """Generate HTML for secondary cards (benzinga template)."""
    url = p.get("url", "")
    title = p.get("title", "")
    date = _fmt_date(p.get("published_date", ""))
    symbol = p.get("symbol", "")
    direction = p.get("direction", "")
    
    badge_html = ""
    if SHOW_BADGES:
        sentiment_label, sentiment_class = _direction_to_sentiment(direction)
        if sentiment_label and sentiment_class:
            badge_html = f'<span class="secondary-tag {sentiment_class}">{sentiment_label}</span>'
    
    hero_image_url = _get_hero_image_url(url, symbol, p.get("hero_image", ""))
    image_html = '<div class="no-image"></div>'
    if hero_image_url:
        image_html = f'<img src="{hero_image_url}" alt="{symbol}">'
    
    return f'''
            <a href="{url}" class="secondary-card">
                <div class="secondary-image">{image_html}</div>
                <div class="secondary-content">
                    {badge_html}
                    <h3>{title}</h3>
                    <div class="secondary-meta">{date} • {symbol}</div>
                </div>
            </a>'''


def _build_list_item(p):
    """Generate HTML for list items (benzinga template)."""
    url = p.get("url", "")
    title = p.get("title", "")
    dek = p.get("dek", "")
    date = _fmt_date(p.get("published_date", ""))
    symbol = p.get("symbol", "")
    days = p.get("pattern_days", "")
    direction = p.get("direction", "")
    
    meta_parts = [date, symbol]
    if days:
        meta_parts.append(f"{days}-Day Pattern")
    meta = " • ".join(filter(None, meta_parts))
    
    badge_html = ""
    if SHOW_BADGES:
        sentiment_label, sentiment_class = _direction_to_sentiment(direction)
        if sentiment_label and sentiment_class:
            badge_html = f'<span class="list-tag {sentiment_class}">{sentiment_label}</span>'
    
    hero_image_url = _get_hero_image_url(url, symbol, p.get("hero_image", ""))
    image_html = '<div class="no-image"></div>'
    if hero_image_url:
        image_html = f'<img src="{hero_image_url}" alt="{symbol}">'
    
    return f'''
            <a href="{url}" class="list-item">
                <div class="list-image">{image_html}</div>
                <div class="list-content">
                    <div class="list-header">
                        {badge_html}
                        <h4>{title}</h4>
                    </div>
                    <p class="list-excerpt">{dek}</p>
                    <div class="list-meta">{meta}</div>
                </div>
            </a>'''


# =============================================================================
# BASE CSS (SHARED BY ALL TEMPLATES)
# =============================================================================

def _get_base_css(t):
    """Return base CSS shared by all templates."""
    return f'''
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
            --badge-bullish-bg: {t["badge_bullish_bg"]};
            --badge-bearish-bg: {t["badge_bearish_bg"]};
        }}

        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background-color: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
            min-height: 100vh;
        }}

        /* Market Bar */
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
            transition: opacity 0.2s ease;
        }}

        .market-item:hover {{
            opacity: 0.75;
        }}

        .market-item.up {{
            border-image: linear-gradient(90deg, transparent, var(--accent-green), transparent) 1;
        }}

        .market-item.down {{
            border-image: linear-gradient(90deg, transparent, var(--accent-red), transparent) 1;
        }}

        .market-symbol {{
            color: var(--text-primary);
            font-weight: 500;
        }}

        .market-price {{
            color: var(--text-secondary);
            font-weight: 700;
        }}

        .market-change {{
            font-weight: 700;
        }}

        .market-change.up {{
            color: var(--accent-green);
        }}

        .market-change.down {{
            color: var(--accent-red);
        }}

        /* Header */
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

        .logo-seasonal {{
            font-size: 22px;
            font-weight: 700;
            color: var(--accent-blue);
            letter-spacing: -0.5px;
        }}

        .logo-market {{
            font-size: 22px;
            font-weight: 700;
            color: var(--text-primary);
            letter-spacing: -0.5px;
        }}

        .logo-news {{
            font-size: 22px;
            font-weight: 400;
            color: var(--text-muted);
            letter-spacing: -0.5px;
        }}

        nav {{
            display: flex;
            gap: 28px;
        }}

        nav a {{
            color: var(--text-secondary);
            text-decoration: none;
            font-size: 14px;
            font-weight: 500;
            transition: color 0.2s ease;
        }}

        nav a:hover {{
            color: var(--text-primary);
        }}

        /* Header Search */
        .header-right {{
            display: flex;
            align-items: center;
            gap: 24px;
        }}

        .header-search {{
            display: flex;
            align-items: center;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            overflow: hidden;
            transition: border-color 0.2s ease;
        }}

        .header-search:focus-within {{
            border-color: var(--accent-blue);
        }}

        .header-search-input {{
            padding: 8px 12px;
            font-size: 13px;
            font-family: inherit;
            background: transparent;
            border: none;
            color: var(--text-primary);
            outline: none;
            width: 180px;
        }}

        .header-search-input::placeholder {{
            color: var(--text-muted);
        }}

        .header-search-btn {{
            padding: 8px 10px;
            background: transparent;
            border: none;
            color: var(--text-muted);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: color 0.2s ease;
        }}

        .header-search-btn:hover {{
            color: var(--accent-blue);
        }}

        /* Hero Section */
        .hero {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 48px 24px 40px;
            text-align: center;
            border-bottom: 1px solid var(--border-color);
        }}

        .hero h1 {{
            font-size: 36px;
            font-weight: 700;
            line-height: 1.2;
            letter-spacing: -1px;
            margin-bottom: 12px;
            color: var(--text-primary);
        }}

        .hero h1 span {{
            color: var(--accent-blue);
        }}

        .hero-subtitle {{
            font-size: 17px;
            color: var(--text-secondary);
            max-width: 580px;
            margin: 0 auto 28px;
            line-height: 1.6;
        }}

        /* Email Form */
        .email-form {{
            max-width: 480px;
            margin: 0 auto;
        }}

        .email-row {{
            display: flex;
            gap: 10px;
            justify-content: center;
            margin-bottom: 12px;
        }}

        .email-input {{
            flex: 1;
            max-width: 280px;
            padding: 12px 16px;
            font-size: 14px;
            font-family: inherit;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            color: var(--text-primary);
            outline: none;
            transition: border-color 0.2s ease, box-shadow 0.2s ease;
        }}

        .email-input::placeholder {{
            color: var(--text-muted);
        }}

        .email-input:focus {{
            border-color: var(--accent-blue);
            box-shadow: 0 0 0 3px rgba(0, 102, 204, 0.1);
        }}

        .submit-btn {{
            padding: 12px 20px;
            font-size: 14px;
            font-family: inherit;
            font-weight: 600;
            background: var(--accent-blue);
            color: white;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            transition: background 0.2s ease, transform 0.1s ease;
            white-space: nowrap;
        }}

        .submit-btn:hover {{
            background: #0052a3;
        }}

        .submit-btn:active {{
            transform: scale(0.98);
        }}

        .success-message {{
            display: none;
            padding: 12px 20px;
            background: rgba(13, 122, 62, 0.1);
            border: 1px solid var(--accent-green);
            border-radius: 6px;
            color: var(--accent-green);
            font-weight: 500;
            max-width: 440px;
            margin: 0 auto;
        }}

        .success-message.show {{
            display: block;
        }}

        .group-checkboxes {{
            display: flex;
            gap: 24px;
            justify-content: center;
            align-items: center;
            margin-bottom: 8px;
        }}

        .group-label {{
            display: flex;
            align-items: center;
            gap: 7px;
            font-size: 14px;
            color: var(--text-secondary);
            cursor: pointer;
            user-select: none;
        }}

        .group-label input[type="checkbox"] {{
            width: 16px;
            height: 16px;
            cursor: pointer;
            accent-color: var(--accent-blue);
            flex-shrink: 0;
        }}

        .group-error {{
            display: none;
            font-size: 13px;
            color: var(--accent-red);
            text-align: center;
            margin-top: 6px;
        }}

        .group-row {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            margin-bottom: 8px;
        }}

        .info-icon {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 17px;
            height: 17px;
            border-radius: 50%;
            border: 1.5px solid var(--text-muted);
            color: var(--text-muted);
            font-size: 10px;
            font-style: italic;
            font-weight: 700;
            cursor: pointer;
            position: relative;
            flex-shrink: 0;
            line-height: 1;
            user-select: none;
        }}

        .info-tooltip {{
            display: none;
            position: absolute;
            bottom: calc(100% + 8px);
            left: 50%;
            transform: translateX(-50%);
            background: var(--text-primary);
            color: var(--bg-primary);
            font-size: 12px;
            font-style: normal;
            font-weight: 400;
            padding: 10px 14px;
            border-radius: 6px;
            width: 230px;
            line-height: 1.55;
            z-index: 100;
            white-space: normal;
            pointer-events: none;
        }}

        .info-tooltip::after {{
            content: '';
            position: absolute;
            top: 100%;
            left: 50%;
            transform: translateX(-50%);
            border: 5px solid transparent;
            border-top-color: var(--text-primary);
        }}

        .info-icon.open .info-tooltip {{
            display: block;
        }}

        /* Section Headers */
        .section-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            padding-bottom: 12px;
            border-bottom: 2px solid var(--text-primary);
        }}

        .section-title {{
            font-size: 13px;
            font-weight: 700;
            color: var(--text-primary);
            text-transform: uppercase;
            letter-spacing: 1px;
        }}

        /* Footer */
        footer {{
            border-top: 1px solid var(--border-color);
            padding: 28px 24px;
            background: var(--bg-secondary);
            margin-top: 40px;
        }}

        .footer-content {{
            max-width: 1200px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
        }}

        .footer-left {{
            font-size: 13px;
            color: var(--text-muted);
        }}

        .footer-left a {{
            color: var(--text-muted);
            text-decoration: none;
            transition: color 0.2s ease;
        }}

        .footer-left a:hover {{
            color: var(--text-secondary);
        }}

        .footer-links {{
            display: flex;
            gap: 24px;
        }}

        .footer-links a {{
            font-size: 13px;
            color: var(--text-muted);
            text-decoration: none;
            transition: color 0.2s ease;
        }}

        .footer-links a:hover {{
            color: var(--text-secondary);
        }}

        .footer-generated {{
            width: 100%;
            text-align: center;
            font-size: 11px;
            color: var(--text-muted);
            margin-top: 12px;
            opacity: 0.6;
        }}

        .no-articles {{
            color: var(--text-muted);
            text-align: center;
            padding: 48px;
        }}

        /* Badge styles (shared) */
        .bullish {{
            background: var(--badge-bullish-bg);
            color: var(--accent-green);
        }}

        .bearish {{
            background: var(--badge-bearish-bg);
            color: var(--accent-red);
        }}

        /* Base Responsive */
        @media (max-width: 768px) {{
            .header-search-input {{
                width: 120px;
            }}
            
            .hero h1 {{
                font-size: 28px;
            }}

            .hero-subtitle {{
                font-size: 15px;
            }}

            .email-row {{
                flex-direction: column;
                align-items: center;
            }}

            .email-input {{
                max-width: 100%;
                width: 100%;
            }}

            .submit-btn {{
                width: 100%;
            }}

            .group-checkboxes {{
                gap: 16px;
            }}

            nav {{
                display: none;
            }}

            .footer-content {{
                flex-direction: column;
                text-align: center;
            }}

            .market-bar {{
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
                scrollbar-width: none;
            }}
            .market-bar::-webkit-scrollbar {{
                display: none;
            }}
            .market-bar-content {{
                justify-content: flex-start;
                padding: 0 16px;
                gap: 16px;
            }}
        }}

        @media (max-width: 480px) {{
            .header-search {{
                flex: 1;
                margin-left: 12px;
            }}
            .header-search-input {{
                width: 100%;
            }}
        }}
        
'''


# =============================================================================
# TEMPLATE 1: FLAGSHIP - Asymmetric Magazine Layout (Premium News Feel)
# =============================================================================

def _get_flagship_template_css():
    """Return CSS specific to flagship template - magazine-style asymmetric layout."""
    return '''
        /* Flagship Template - Premium News Magazine Style */
        .flagship-container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 32px 24px;
        }

        /* Hero Section - Large left + stacked right */
        .flagship-hero {
            display: grid;
            grid-template-columns: 1.4fr 1fr;
            gap: 20px;
            margin-bottom: 32px;
        }

        .flagship-main {
            position: relative;
            border-radius: 12px;
            overflow: hidden;
            aspect-ratio: 4/3;
        }

        .flagship-main-image {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }

        .flagship-main-overlay {
            position: absolute;
            bottom: 0;
            left: 0;
            right: 0;
            padding: 32px;
            background: linear-gradient(transparent, rgba(0,0,0,0.9));
        }

        .flagship-main-tag {
            display: inline-block;
            padding: 5px 12px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-radius: 4px;
            margin-bottom: 12px;
        }

        .flagship-main-tag.bullish {
            background: rgba(48, 209, 88, 0.9);
            color: white;
        }

        .flagship-main-tag.bearish {
            background: rgba(255, 69, 58, 0.9);
            color: white;
        }

        .flagship-main h2 {
            font-size: 32px;
            font-weight: 700;
            color: white;
            line-height: 1.2;
            margin-bottom: 12px;
            letter-spacing: -0.5px;
        }

        .flagship-main-meta {
            font-size: 13px;
            color: rgba(255,255,255,0.8);
            font-family: 'IBM Plex Mono', monospace;
            margin-bottom: 10px;
        }

        .flagship-main-excerpt {
            font-size: 15px;
            color: rgba(255,255,255,0.9);
            line-height: 1.5;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .flagship-main a {
            text-decoration: none;
            display: block;
            height: 100%;
        }

        /* Stacked side articles */
        .flagship-stack {
            display: flex;
            flex-direction: column;
            gap: 20px;
        }

        .flagship-stack-card {
            display: flex;
            gap: 14px;
            text-decoration: none;
            flex: 1;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            overflow: hidden;
            transition: all 0.2s ease;
        }

        .flagship-stack-card:hover {
            border-color: var(--accent-blue);
            box-shadow: var(--card-hover-shadow);
        }

        .flagship-stack-image {
            width: 140px;
            flex-shrink: 0;
            background: var(--bg-tertiary);
        }

        .flagship-stack-image img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }

        .flagship-stack-content {
            padding: 14px 14px 14px 0;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }

        .flagship-stack-tag {
            display: inline-block;
            padding: 2px 8px;
            font-size: 10px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-radius: 3px;
            margin-bottom: 6px;
            width: fit-content;
        }

        .flagship-stack-card h3 {
            font-size: 15px;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.35;
            margin-bottom: 6px;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .flagship-stack-meta {
            font-size: 11px;
            color: var(--text-muted);
            font-family: 'IBM Plex Mono', monospace;
        }

        /* Section Divider */
        .flagship-divider {
            display: flex;
            align-items: center;
            gap: 16px;
            margin-bottom: 24px;
        }

        .flagship-divider-title {
            font-size: 12px;
            font-weight: 700;
            color: var(--text-primary);
            text-transform: uppercase;
            letter-spacing: 1px;
            white-space: nowrap;
        }

        .flagship-divider-line {
            flex: 1;
            height: 2px;
            background: var(--border-color);
        }

        /* Mid-section grid - 3 columns */
        .flagship-grid-3 {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 20px;
            margin-bottom: 40px;
        }

        .flagship-card {
            display: block;
            text-decoration: none;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            overflow: hidden;
            transition: all 0.2s ease;
        }

        .flagship-card:hover {
            border-color: var(--accent-blue);
            box-shadow: var(--card-hover-shadow);
            transform: translateY(-2px);
        }

        .flagship-card-image {
            width: 100%;
            aspect-ratio: 16/10;
            background: var(--bg-tertiary);
            overflow: hidden;
        }

        .flagship-card-image img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            transition: transform 0.3s ease;
        }

        .flagship-card:hover .flagship-card-image img {
            transform: scale(1.03);
        }

        .flagship-card-content {
            padding: 16px;
        }

        .flagship-card-tag {
            display: inline-block;
            padding: 3px 8px;
            font-size: 10px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-radius: 3px;
            margin-bottom: 8px;
        }

        .flagship-card h3 {
            font-size: 17px;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.35;
            margin-bottom: 8px;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .flagship-card-meta {
            font-size: 12px;
            color: var(--text-muted);
            font-family: 'IBM Plex Mono', monospace;
            margin-bottom: 8px;
        }

        .flagship-card-excerpt {
            font-size: 14px;
            color: var(--text-secondary);
            line-height: 1.5;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        /* Bottom list items */
        .flagship-list {
            display: flex;
            flex-direction: column;
            gap: 16px;
        }

        .flagship-list-item {
            display: flex;
            gap: 20px;
            text-decoration: none;
            padding: 16px;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            transition: all 0.2s ease;
        }

        .flagship-list-item:hover {
            border-color: var(--accent-blue);
            box-shadow: var(--card-hover-shadow);
        }

        .flagship-list-image {
            width: 180px;
            height: 120px;
            flex-shrink: 0;
            border-radius: 8px;
            overflow: hidden;
            background: var(--bg-tertiary);
        }

        .flagship-list-image img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }

        .flagship-list-content {
            flex: 1;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }

        .flagship-list-tag {
            display: inline-block;
            padding: 3px 8px;
            font-size: 10px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-radius: 3px;
            margin-bottom: 8px;
            width: fit-content;
        }

        .flagship-list-item h4 {
            font-size: 18px;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.35;
            margin-bottom: 8px;
        }

        .flagship-list-meta {
            font-size: 12px;
            color: var(--text-muted);
            font-family: 'IBM Plex Mono', monospace;
            margin-bottom: 8px;
        }

        .flagship-list-excerpt {
            font-size: 14px;
            color: var(--text-secondary);
            line-height: 1.5;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        /* Flagship Responsive */
        @media (max-width: 1024px) {
            .flagship-hero {
                grid-template-columns: 1fr;
            }

            .flagship-main {
                aspect-ratio: 16/9;
            }

            .flagship-stack {
                flex-direction: row;
                flex-wrap: wrap;
            }

            .flagship-stack-card {
                flex: 1 1 calc(50% - 10px);
                min-width: 280px;
            }

            .flagship-grid-3 {
                grid-template-columns: repeat(2, 1fr);
            }
        }

        @media (max-width: 768px) {
            .flagship-main h2 {
                font-size: 24px;
            }

            .flagship-main-overlay {
                padding: 20px;
            }

            .flagship-stack-card {
                flex: 1 1 100%;
            }

            .flagship-grid-3 {
                grid-template-columns: 1fr;
            }

            .flagship-list-item {
                flex-direction: column;
            }

            .flagship-list-image {
                width: 100%;
                height: 180px;
            }
        }
'''


def _build_flagship_main(p):
    """Build the main hero article for flagship template."""
    url = p.get("url", "")
    title = p.get("title", "")
    dek = p.get("dek", "")
    date = _fmt_date(p.get("published_date", ""))
    symbol = p.get("symbol", "")
    days = p.get("pattern_days", "")
    direction = p.get("direction", "")
    
    hero_image_url = _get_hero_image_url(url, symbol, p.get("hero_image", ""))
    image_html = f'<img src="{hero_image_url}" alt="{symbol}" class="flagship-main-image">' if hero_image_url else '<div class="flagship-main-image" style="background:var(--bg-tertiary)"></div>'
    
    meta_parts = [date, symbol]
    if days:
        meta_parts.append(f"{days}-Day Pattern")
    meta = " • ".join(filter(None, meta_parts))
    
    tag_html = ""
    if SHOW_BADGES:
        sentiment_label, sentiment_class = _direction_to_sentiment(direction)
        if sentiment_label:
            tag_html = f'<span class="flagship-main-tag {sentiment_class}">{sentiment_label}</span>'
    
    return f'''
            <div class="flagship-main">
                <a href="{url}">
                    {image_html}
                    <div class="flagship-main-overlay">
                        {tag_html}
                        <h2>{title}</h2>
                        <div class="flagship-main-meta">{meta}</div>
                        <p class="flagship-main-excerpt">{dek}</p>
                    </div>
                </a>
            </div>'''


def _build_flagship_stack_card(p):
    """Build a stacked side card for flagship template."""
    url = p.get("url", "")
    title = p.get("title", "")
    date = _fmt_date(p.get("published_date", ""))
    symbol = p.get("symbol", "")
    direction = p.get("direction", "")
    
    hero_image_url = _get_hero_image_url(url, symbol, p.get("hero_image", ""))
    image_html = f'<img src="{hero_image_url}" alt="{symbol}">' if hero_image_url else ''
    
    tag_html = ""
    if SHOW_BADGES:
        sentiment_label, sentiment_class = _direction_to_sentiment(direction)
        if sentiment_label:
            tag_html = f'<span class="flagship-stack-tag {sentiment_class}">{sentiment_label}</span>'
    
    return f'''
            <a href="{url}" class="flagship-stack-card">
                <div class="flagship-stack-image">{image_html}</div>
                <div class="flagship-stack-content">
                    {tag_html}
                    <h3>{title}</h3>
                    <div class="flagship-stack-meta">{date} • {symbol}</div>
                </div>
            </a>'''


def _build_flagship_card(p):
    """Build a grid card for flagship template."""
    url = p.get("url", "")
    title = p.get("title", "")
    dek = p.get("dek", "")
    date = _fmt_date(p.get("published_date", ""))
    symbol = p.get("symbol", "")
    days = p.get("pattern_days", "")
    direction = p.get("direction", "")
    
    hero_image_url = _get_hero_image_url(url, symbol, p.get("hero_image", ""))
    image_html = f'<img src="{hero_image_url}" alt="{symbol}">' if hero_image_url else ''
    
    meta_parts = [date, symbol]
    if days:
        meta_parts.append(f"{days}D")
    meta = " • ".join(filter(None, meta_parts))
    
    tag_html = ""
    if SHOW_BADGES:
        sentiment_label, sentiment_class = _direction_to_sentiment(direction)
        if sentiment_label:
            tag_html = f'<span class="flagship-card-tag {sentiment_class}">{sentiment_label}</span>'
    
    return f'''
            <a href="{url}" class="flagship-card">
                <div class="flagship-card-image">{image_html}</div>
                <div class="flagship-card-content">
                    {tag_html}
                    <h3>{title}</h3>
                    <div class="flagship-card-meta">{meta}</div>
                    <p class="flagship-card-excerpt">{dek}</p>
                </div>
            </a>'''


def _build_flagship_list_item(p):
    """Build a list item for flagship template."""
    url = p.get("url", "")
    title = p.get("title", "")
    dek = p.get("dek", "")
    date = _fmt_date(p.get("published_date", ""))
    symbol = p.get("symbol", "")
    days = p.get("pattern_days", "")
    direction = p.get("direction", "")
    
    hero_image_url = _get_hero_image_url(url, symbol, p.get("hero_image", ""))
    image_html = f'<img src="{hero_image_url}" alt="{symbol}">' if hero_image_url else ''
    
    meta_parts = [date, symbol]
    if days:
        meta_parts.append(f"{days}-Day Pattern")
    meta = " • ".join(filter(None, meta_parts))
    
    tag_html = ""
    if SHOW_BADGES:
        sentiment_label, sentiment_class = _direction_to_sentiment(direction)
        if sentiment_label:
            tag_html = f'<span class="flagship-list-tag {sentiment_class}">{sentiment_label}</span>'
    
    return f'''
            <a href="{url}" class="flagship-list-item">
                <div class="flagship-list-image">{image_html}</div>
                <div class="flagship-list-content">
                    {tag_html}
                    <h4>{title}</h4>
                    <div class="flagship-list-meta">{meta}</div>
                    <p class="flagship-list-excerpt">{dek}</p>
                </div>
            </a>'''


def _build_flagship_template(items, t):
    """Build the flagship magazine-style template."""
    if not items:
        return '<section class="flagship-container"><p class="no-articles">No articles yet.</p></section>'
    
    # Main hero (1) + stacked (3) = first 4 articles
    main_article = items[0]
    stack_articles = items[1:4]
    
    # Grid section - next 6 articles
    grid_articles = items[4:10]
    
    # List section - remaining
    list_articles = items[10:]
    
    # Build hero section
    main_html = _build_flagship_main(main_article)
    stack_html = "\n".join(_build_flagship_stack_card(p) for p in stack_articles)
    
    hero_section = f'''
        <div class="flagship-hero">
{main_html}
            <div class="flagship-stack">
{stack_html}
            </div>
        </div>'''
    
    # Build grid section
    grid_section = ""
    if grid_articles:
        grid_html = "\n".join(_build_flagship_card(p) for p in grid_articles)
        grid_section = f'''
        <div class="flagship-divider">
            <span class="flagship-divider-title">More Analysis</span>
            <div class="flagship-divider-line"></div>
        </div>
        <div class="flagship-grid-3">
{grid_html}
        </div>'''
    
    # Build list section
    list_section = ""
    if list_articles:
        list_html = "\n".join(_build_flagship_list_item(p) for p in list_articles)
        list_section = f'''
        <div class="flagship-divider">
            <span class="flagship-divider-title">Recent Coverage</span>
            <div class="flagship-divider-line"></div>
        </div>
        <div class="flagship-list">
{list_html}
        </div>'''
    
    return f'''
    <section class="flagship-container">
{hero_section}
{grid_section}
{list_section}
    </section>
'''


# =============================================================================
# TEMPLATE 2: MOSAIC - Pinterest/Masonry Style with Varied Card Sizes
# =============================================================================

def _get_mosaic_template_css():
    """Return CSS specific to mosaic template - varied card sizes."""
    return '''
        /* Mosaic Template - Pinterest/Magazine Style */
        .mosaic-container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 32px 24px;
        }

        .mosaic-header {
            display: flex;
            align-items: center;
            gap: 16px;
            margin-bottom: 28px;
        }

        .mosaic-title {
            font-size: 12px;
            font-weight: 700;
            color: var(--text-primary);
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .mosaic-line {
            flex: 1;
            height: 2px;
            background: var(--border-color);
        }

        /* Mosaic Grid */
        .mosaic-grid {
            display: grid;
            grid-template-columns: repeat(12, 1fr);
            grid-auto-rows: minmax(120px, auto);
            gap: 20px;
        }

        /* Card Sizes */
        .mosaic-card {
            display: block;
            text-decoration: none;
            position: relative;
            border-radius: 12px;
            overflow: hidden;
            background: var(--bg-tertiary);
            transition: all 0.3s ease;
        }

        .mosaic-card:hover {
            transform: translateY(-4px);
            box-shadow: 0 12px 40px rgba(0,0,0,0.15);
        }

        /* Large - 8 columns, 3 rows */
        .mosaic-card.large {
            grid-column: span 8;
            grid-row: span 3;
        }

        /* Medium - 4 columns, 3 rows */
        .mosaic-card.medium {
            grid-column: span 4;
            grid-row: span 3;
        }

        /* Standard - 4 columns, 2 rows */
        .mosaic-card.standard {
            grid-column: span 4;
            grid-row: span 2;
        }

        /* Wide - 6 columns, 2 rows */
        .mosaic-card.wide {
            grid-column: span 6;
            grid-row: span 2;
        }

        .mosaic-card-image {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            object-fit: cover;
            transition: transform 0.5s ease;
        }

        .mosaic-card:hover .mosaic-card-image {
            transform: scale(1.05);
        }

        .mosaic-card-overlay {
            position: absolute;
            bottom: 0;
            left: 0;
            right: 0;
            padding: 24px;
            background: linear-gradient(transparent, rgba(0,0,0,0.85));
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            min-height: 60%;
        }

        .mosaic-card.large .mosaic-card-overlay {
            padding: 32px;
        }

        .mosaic-card-tag {
            display: inline-block;
            padding: 4px 10px;
            font-size: 10px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-radius: 4px;
            margin-bottom: 10px;
            width: fit-content;
        }

        .mosaic-card-tag.bullish {
            background: rgba(48, 209, 88, 0.9);
            color: white;
        }

        .mosaic-card-tag.bearish {
            background: rgba(255, 69, 58, 0.9);
            color: white;
        }

        .mosaic-card h2,
        .mosaic-card h3 {
            color: white;
            line-height: 1.25;
            margin-bottom: 8px;
            font-weight: 700;
        }

        .mosaic-card.large h2 {
            font-size: 28px;
            letter-spacing: -0.5px;
        }

        .mosaic-card.medium h3,
        .mosaic-card.wide h3 {
            font-size: 20px;
        }

        .mosaic-card.standard h3 {
            font-size: 16px;
        }

        .mosaic-card-meta {
            font-size: 12px;
            color: rgba(255,255,255,0.75);
            font-family: 'IBM Plex Mono', monospace;
        }

        .mosaic-card.large .mosaic-card-meta {
            font-size: 13px;
        }

        .mosaic-card-excerpt {
            font-size: 14px;
            color: rgba(255,255,255,0.9);
            line-height: 1.5;
            margin-top: 10px;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .mosaic-card.standard .mosaic-card-excerpt,
        .mosaic-card.medium .mosaic-card-excerpt {
            display: none;
        }

        /* Symbol Badge */
        .mosaic-symbol {
            position: absolute;
            top: 16px;
            right: 16px;
            background: rgba(0,0,0,0.6);
            backdrop-filter: blur(10px);
            padding: 6px 12px;
            border-radius: 6px;
            font-family: 'IBM Plex Mono', monospace;
            font-size: 13px;
            font-weight: 600;
            color: white;
        }

        /* Mosaic Responsive */
        @media (max-width: 1024px) {
            .mosaic-card.large {
                grid-column: span 12;
                grid-row: span 2;
            }

            .mosaic-card.medium,
            .mosaic-card.wide {
                grid-column: span 6;
                grid-row: span 2;
            }

            .mosaic-card.standard {
                grid-column: span 6;
                grid-row: span 2;
            }
        }

        @media (max-width: 768px) {
            .mosaic-grid {
                grid-template-columns: 1fr;
                grid-auto-rows: minmax(200px, auto);
            }

            .mosaic-card.large,
            .mosaic-card.medium,
            .mosaic-card.wide,
            .mosaic-card.standard {
                grid-column: span 1;
                grid-row: span 1;
            }

            .mosaic-card.large h2 {
                font-size: 22px;
            }
        }
'''


def _build_mosaic_card(p, size="standard"):
    """Build a mosaic card with specified size."""
    url = p.get("url", "")
    title = p.get("title", "")
    dek = p.get("dek", "")
    date = _fmt_date(p.get("published_date", ""))
    symbol = p.get("symbol", "")
    days = p.get("pattern_days", "")
    direction = p.get("direction", "")
    
    hero_image_url = _get_hero_image_url(url, symbol, p.get("hero_image", ""))
    image_html = f'<img src="{hero_image_url}" alt="{symbol}" class="mosaic-card-image">' if hero_image_url else '<div class="mosaic-card-image" style="background:var(--bg-tertiary)"></div>'
    
    meta_parts = [date]
    if days:
        meta_parts.append(f"{days}-Day Pattern")
    meta = " • ".join(meta_parts)
    
    tag_html = ""
    if SHOW_BADGES:
        sentiment_label, sentiment_class = _direction_to_sentiment(direction)
        if sentiment_label:
            tag_html = f'<span class="mosaic-card-tag {sentiment_class}">{sentiment_label}</span>'
    
    heading_tag = "h2" if size == "large" else "h3"
    
    excerpt_html = f'<p class="mosaic-card-excerpt">{dek}</p>' if size in ["large", "wide"] else ""
    
    return f'''
            <a href="{url}" class="mosaic-card {size}">
                {image_html}
                <span class="mosaic-symbol">{symbol}</span>
                <div class="mosaic-card-overlay">
                    {tag_html}
                    <{heading_tag}>{title}</{heading_tag}>
                    <div class="mosaic-card-meta">{meta}</div>
                    {excerpt_html}
                </div>
            </a>'''


def _build_mosaic_template(items, t):
    """Build the mosaic Pinterest-style template."""
    if not items:
        return '<section class="mosaic-container"><p class="no-articles">No articles yet.</p></section>'
    
    cards_html = ""
    
    # Layout pattern: large, medium, medium, wide, wide, standard, standard, standard, etc.
    size_pattern = ["large", "medium", "medium", "wide", "wide", "standard", "standard", "standard", "wide", "wide"]
    
    for i, p in enumerate(items):
        if i < len(size_pattern):
            size = size_pattern[i]
        else:
            # Repeat pattern for remaining items
            size = size_pattern[(i - len(size_pattern)) % 6 + 4] if i >= len(size_pattern) else "standard"
        cards_html += _build_mosaic_card(p, size)
    
    return f'''
    <section class="mosaic-container">
        <div class="mosaic-header">
            <span class="mosaic-title">Latest Patterns</span>
            <div class="mosaic-line"></div>
        </div>
        <div class="mosaic-grid">
{cards_html}
        </div>
    </section>
'''


# =============================================================================
# TEMPLATE 3: WIRE - Bloomberg/Reuters Inspired Modular Layout
# =============================================================================

def _get_wire_template_css():
    """Return CSS specific to wire template - Bloomberg/Reuters inspired."""
    return '''
        /* Wire Template - Modular News Wire Style */
        .wire-container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 24px;
        }

        /* Top Zone - Lead Story + Headlines Stack */
        .wire-top {
            display: grid;
            grid-template-columns: 1fr 380px;
            gap: 24px;
            margin-bottom: 32px;
            padding-bottom: 32px;
            border-bottom: 1px solid var(--border-color);
        }

        /* Lead Story - Large with image */
        .wire-lead {
            position: relative;
            border-radius: 8px;
            overflow: hidden;
            background: var(--bg-tertiary);
        }

        .wire-lead-link {
            display: block;
            height: 100%;
        }

        .wire-lead-image {
            width: 100%;
            height: 100%;
            min-height: 400px;
            object-fit: cover;
        }

        .wire-lead-overlay {
            position: absolute;
            bottom: 0;
            left: 0;
            right: 0;
            padding: 32px;
            background: linear-gradient(transparent, rgba(0,0,0,0.9));
        }

        .wire-lead-category {
            display: inline-block;
            font-size: 11px;
            font-weight: 700;
            color: var(--accent-amber);
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 12px;
        }

        .wire-lead h2 {
            font-size: 32px;
            font-weight: 700;
            color: white;
            line-height: 1.2;
            margin-bottom: 12px;
        }

        .wire-lead-meta {
            font-size: 13px;
            color: rgba(255,255,255,0.7);
            font-family: 'IBM Plex Mono', monospace;
        }

        /* Headlines Stack - Text Only */
        .wire-headlines {
            display: flex;
            flex-direction: column;
        }

        .wire-headlines-header {
            font-size: 11px;
            font-weight: 700;
            color: var(--accent-red);
            text-transform: uppercase;
            letter-spacing: 1px;
            padding-bottom: 12px;
            margin-bottom: 16px;
            border-bottom: 2px solid var(--accent-red);
        }

        .wire-headline-item {
            display: block;
            text-decoration: none;
            padding: 14px 0;
            border-bottom: 1px solid var(--border-color);
            transition: all 0.15s ease;
        }

        .wire-headline-item:hover {
            padding-left: 8px;
            border-color: var(--accent-blue);
        }

        .wire-headline-item:last-child {
            border-bottom: none;
        }

        .wire-headline-tag {
            display: inline-block;
            font-size: 10px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-right: 8px;
        }

        .wire-headline-tag.bullish {
            color: var(--accent-green);
        }

        .wire-headline-tag.bearish {
            color: var(--accent-red);
        }

        .wire-headline-symbol {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 11px;
            font-weight: 600;
            color: var(--accent-blue);
            margin-right: 8px;
        }

        .wire-headline-item h3 {
            font-size: 15px;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.4;
            margin-top: 6px;
        }

        .wire-headline-time {
            font-size: 11px;
            color: var(--text-muted);
            margin-top: 4px;
        }

        /* Pattern Data Bar */
        .wire-data-bar {
            display: flex;
            gap: 16px;
            padding: 16px 20px;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            margin-bottom: 32px;
            overflow-x: auto;
        }

        .wire-data-item {
            display: flex;
            align-items: center;
            gap: 12px;
            padding-right: 16px;
            border-right: 1px solid var(--border-color);
            white-space: nowrap;
        }

        .wire-data-item:last-child {
            border-right: none;
            padding-right: 0;
        }

        .wire-data-label {
            font-size: 11px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .wire-data-value {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 18px;
            font-weight: 600;
            color: var(--text-primary);
        }

        .wire-data-value.green {
            color: var(--accent-green);
        }

        .wire-data-value.red {
            color: var(--accent-red);
        }

        /* Mid Section - Mixed Grid */
        .wire-section {
            margin-bottom: 40px;
        }

        .wire-section-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding-bottom: 12px;
            margin-bottom: 20px;
            border-bottom: 2px solid var(--text-primary);
        }

        .wire-section-title {
            font-size: 13px;
            font-weight: 700;
            color: var(--text-primary);
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .wire-section-more {
            font-size: 12px;
            color: var(--accent-blue);
            text-decoration: none;
        }

        /* Mixed Grid - 2 large + 4 small */
        .wire-mixed-grid {
            display: grid;
            grid-template-columns: repeat(6, 1fr);
            grid-template-rows: auto auto;
            gap: 20px;
        }

        .wire-card {
            display: block;
            text-decoration: none;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            overflow: hidden;
            transition: all 0.2s ease;
        }

        .wire-card:hover {
            border-color: var(--accent-blue);
            box-shadow: 0 4px 20px rgba(0,0,0,0.1);
        }

        .wire-card.large {
            grid-column: span 3;
        }

        .wire-card.small {
            grid-column: span 2;
        }

        .wire-card-image {
            width: 100%;
            aspect-ratio: 16/10;
            background: var(--bg-tertiary);
            overflow: hidden;
        }

        .wire-card.large .wire-card-image {
            aspect-ratio: 16/9;
        }

        .wire-card-image img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            transition: transform 0.3s ease;
        }

        .wire-card:hover .wire-card-image img {
            transform: scale(1.03);
        }

        .wire-card-content {
            padding: 16px;
        }

        .wire-card-top {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 8px;
        }

        .wire-card-symbol {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 11px;
            font-weight: 600;
            color: var(--accent-blue);
            background: rgba(0, 102, 204, 0.1);
            padding: 2px 8px;
            border-radius: 4px;
        }

        .wire-card-direction {
            font-size: 10px;
            font-weight: 700;
            text-transform: uppercase;
        }

        .wire-card-direction.bullish {
            color: var(--accent-green);
        }

        .wire-card-direction.bearish {
            color: var(--accent-red);
        }

        .wire-card h3 {
            font-size: 16px;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.35;
            margin-bottom: 8px;
        }

        .wire-card.large h3 {
            font-size: 20px;
        }

        .wire-card-meta {
            font-size: 11px;
            color: var(--text-muted);
            font-family: 'IBM Plex Mono', monospace;
        }

        .wire-card-excerpt {
            font-size: 14px;
            color: var(--text-secondary);
            line-height: 1.5;
            margin-top: 10px;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .wire-card.small .wire-card-excerpt {
            display: none;
        }

        /* Bottom List - Compact */
        .wire-list {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 16px;
        }

        .wire-list-item {
            display: flex;
            gap: 14px;
            text-decoration: none;
            padding: 14px;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            transition: all 0.2s ease;
        }

        .wire-list-item:hover {
            border-color: var(--accent-blue);
            background: var(--bg-secondary);
        }

        .wire-list-image {
            width: 100px;
            height: 70px;
            flex-shrink: 0;
            border-radius: 6px;
            overflow: hidden;
            background: var(--bg-tertiary);
        }

        .wire-list-image img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }

        .wire-list-content {
            flex: 1;
            min-width: 0;
        }

        .wire-list-top {
            display: flex;
            align-items: center;
            gap: 6px;
            margin-bottom: 4px;
        }

        .wire-list-symbol {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 10px;
            font-weight: 600;
            color: var(--accent-blue);
        }

        .wire-list-direction {
            font-size: 9px;
            font-weight: 700;
            text-transform: uppercase;
        }

        .wire-list-direction.bullish {
            color: var(--accent-green);
        }

        .wire-list-direction.bearish {
            color: var(--accent-red);
        }

        .wire-list-item h4 {
            font-size: 14px;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.35;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .wire-list-meta {
            font-size: 10px;
            color: var(--text-muted);
            margin-top: 4px;
        }

        /* Wire Responsive */
        @media (max-width: 1024px) {
            .wire-top {
                grid-template-columns: 1fr;
            }

            .wire-lead-image {
                min-height: 300px;
            }

            .wire-headlines {
                display: grid;
                grid-template-columns: repeat(2, 1fr);
                gap: 0 20px;
            }

            .wire-headlines-header {
                grid-column: span 2;
            }

            .wire-mixed-grid {
                grid-template-columns: repeat(4, 1fr);
            }

            .wire-card.large {
                grid-column: span 2;
            }

            .wire-card.small {
                grid-column: span 2;
            }
        }

        @media (max-width: 768px) {
            .wire-lead h2 {
                font-size: 24px;
            }

            .wire-lead-overlay {
                padding: 20px;
            }

            .wire-headlines {
                grid-template-columns: 1fr;
            }

            .wire-headlines-header {
                grid-column: auto;
            }

            .wire-data-bar {
                flex-wrap: wrap;
            }

            .wire-data-item {
                border-right: none;
                padding-right: 0;
            }

            .wire-mixed-grid {
                grid-template-columns: 1fr;
            }

            .wire-card.large,
            .wire-card.small {
                grid-column: auto;
            }

            .wire-list {
                grid-template-columns: 1fr;
            }
        }
'''


def _build_wire_lead(p):
    """Build the lead story for wire template."""
    url = p.get("url", "")
    title = p.get("title", "")
    date = _fmt_date(p.get("published_date", ""))
    symbol = p.get("symbol", "")
    days = p.get("pattern_days", "")
    
    hero_image_url = _get_hero_image_url(url, symbol, p.get("hero_image", ""))
    image_html = f'<img src="{hero_image_url}" alt="{symbol}" class="wire-lead-image">' if hero_image_url else '<div class="wire-lead-image" style="background:var(--bg-tertiary)"></div>'
    
    pattern_date = _extract_pattern_date_from_url(url)
    activation = pattern_date.strftime("%b %d") if pattern_date else ""
    
    meta_parts = [date, symbol]
    if days:
        meta_parts.append(f"{days}-Day Pattern")
    if activation:
        meta_parts.append(f"Activates {activation}")
    meta = " • ".join(filter(None, meta_parts))
    
    return f'''
            <div class="wire-lead">
                <a href="{url}" class="wire-lead-link">
                    {image_html}
                    <div class="wire-lead-overlay">
 
                        <h2>{title}</h2>
                        <div class="wire-lead-meta">{meta}</div>
                    </div>
                </a>
            </div>'''


def _build_wire_headline(p):
    """Build a headline item for wire template."""
    url = p.get("url", "")
    title = p.get("title", "")
    date = _fmt_date(p.get("published_date", ""))
    symbol = p.get("symbol", "")
    direction = p.get("direction", "")
    
    tag_html = ""
    if SHOW_BADGES:
        sentiment_label, sentiment_class = _direction_to_sentiment(direction)
        if sentiment_label:
            tag_html = f'<span class="wire-headline-tag {sentiment_class}">{sentiment_label}</span>'
    
    return f'''
                <a href="{url}" class="wire-headline-item">
                    <div>
                        {tag_html}
                        <span class="wire-headline-symbol">{symbol}</span>
                    </div>
                    <h3>{title}</h3>
                    <div class="wire-headline-time">{date}</div>
                </a>'''


def _build_wire_card(p, size="small"):
    """Build a card for wire template."""
    url = p.get("url", "")
    title = p.get("title", "")
    dek = p.get("dek", "")
    date = _fmt_date(p.get("published_date", ""))
    symbol = p.get("symbol", "")
    days = p.get("pattern_days", "")
    direction = p.get("direction", "")
    
    hero_image_url = _get_hero_image_url(url, symbol, p.get("hero_image", ""))
    image_html = f'<img src="{hero_image_url}" alt="{symbol}">' if hero_image_url else ''
    
    sentiment_label, sentiment_class = _direction_to_sentiment(direction)
    direction_html = ""
    if SHOW_BADGES and sentiment_label:
        direction_html = f'<span class="wire-card-direction {sentiment_class}">{sentiment_label}</span>'
    
    meta = f"{date} • {days}D Pattern" if days else date
    
    excerpt_html = f'<p class="wire-card-excerpt">{dek}</p>' if size == "large" else ""
    
    return f'''
            <a href="{url}" class="wire-card {size}">
                <div class="wire-card-image">{image_html}</div>
                <div class="wire-card-content">
                    <div class="wire-card-top">
                        <span class="wire-card-symbol">{symbol}</span>
                        {direction_html}
                    </div>
                    <h3>{title}</h3>
                    <div class="wire-card-meta">{meta}</div>
                    {excerpt_html}
                </div>
            </a>'''


def _build_wire_list_item(p):
    """Build a list item for wire template."""
    url = p.get("url", "")
    title = p.get("title", "")
    date = _fmt_date(p.get("published_date", ""))
    symbol = p.get("symbol", "")
    days = p.get("pattern_days", "")
    direction = p.get("direction", "")
    
    hero_image_url = _get_hero_image_url(url, symbol, p.get("hero_image", ""))
    image_html = f'<img src="{hero_image_url}" alt="{symbol}">' if hero_image_url else ''
    
    sentiment_label, sentiment_class = _direction_to_sentiment(direction)
    direction_html = ""
    if SHOW_BADGES and sentiment_label:
        direction_html = f'<span class="wire-list-direction {sentiment_class}">• {sentiment_label}</span>'
    
    meta = f"{date} • {days}D" if days else date
    
    return f'''
            <a href="{url}" class="wire-list-item">
                <div class="wire-list-image">{image_html}</div>
                <div class="wire-list-content">
                    <div class="wire-list-top">
                        <span class="wire-list-symbol">{symbol}</span>
                        {direction_html}
                    </div>
                    <h4>{title}</h4>
                    <div class="wire-list-meta">{meta}</div>
                </div>
            </a>'''


def _build_wire_template(items, t):
    """Build the wire template - Bloomberg/Reuters style."""
    if not items:
        return '<section class="wire-container"><p class="no-articles">No articles yet.</p></section>'
    
    # Calculate stats for data bar
    total = len(items)
    bullish = sum(1 for p in items if p.get("direction", "").lower() == "long")
    bearish = sum(1 for p in items if p.get("direction", "").lower() == "short")
    
    # Lead story (1) + headlines (5)
    lead = items[0]
    headlines = items[1:6]
    
    # Mixed grid (2 large + 3 small = 5)
    grid_items = items[6:11]
    large_items = grid_items[:2] if len(grid_items) >= 2 else grid_items
    small_items = grid_items[2:5] if len(grid_items) > 2 else []
    
    # List items (remaining)
    list_items = items[11:]
    
    # Build lead
    lead_html = _build_wire_lead(lead)
    
    # Build headlines
    headlines_html = "\n".join(_build_wire_headline(p) for p in headlines)
    
    # Build grid
    large_html = "\n".join(_build_wire_card(p, "large") for p in large_items)
    small_html = "\n".join(_build_wire_card(p, "small") for p in small_items)
    
    grid_section = ""
    if grid_items:
        grid_section = f'''
        <div class="wire-section">
            <div class="wire-section-header">
                <span class="wire-section-title">Market Analysis</span>
            </div>
            <div class="wire-mixed-grid">
{large_html}
{small_html}
            </div>
        </div>'''
    
    # Build list
    list_section = ""
    if list_items:
        list_html = "\n".join(_build_wire_list_item(p) for p in list_items)
        list_section = f'''
        <div class="wire-section">
            <div class="wire-section-header">
                <span class="wire-section-title">Recent Coverage</span>
            </div>
            <div class="wire-list">
{list_html}
            </div>
        </div>'''
    
    # Data bar (optional)
    data_bar_html = ""
    if SHOW_DATA_BAR:
        data_bar_html = f'''
        <!-- Data Bar -->
        <div class="wire-data-bar">
            <div class="wire-data-item">
                <div>
                    <div class="wire-data-label">Active Patterns</div>
                    <div class="wire-data-value">{total}</div>
                </div>
            </div>
            <div class="wire-data-item">
                <div>
                    <div class="wire-data-label">Bullish</div>
                    <div class="wire-data-value green">{bullish}</div>
                </div>
            </div>
            <div class="wire-data-item">
                <div>
                    <div class="wire-data-label">Bearish</div>
                    <div class="wire-data-value red">{bearish}</div>
                </div>
            </div>
            <div class="wire-data-item">
                <div>
                    <div class="wire-data-label">Ratio</div>
                    <div class="wire-data-value">{bullish}:{bearish}</div>
                </div>
            </div>
        </div>'''
    
    return f'''
    <section class="wire-container">
        <!-- Top Zone -->
        <div class="wire-top">
{lead_html}
            <div class="wire-headlines">
                <div class="wire-headlines-header">Latest Patterns</div>
{headlines_html}
            </div>
        </div>
{data_bar_html}
{grid_section}
{list_section}
    </section>
'''

# =============================================================================
# TEMPLATE 4: PULSE - Clean Cinematic Hero + Uniform Grid (Best of Both)
# =============================================================================

def _get_pulse_template_css():
    """Return CSS specific to pulse template - clean and breathable."""
    return '''
        /* Pulse Template - Clean, Professional, Breathable */
        .pulse-container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 32px 24px;
        }

        /* Cinematic Hero - Full Width */
        .pulse-hero {
            position: relative;
            border-radius: 16px;
            overflow: hidden;
            margin-bottom: 40px;
            aspect-ratio: 21/9;
            min-height: 380px;
        }

        .pulse-hero-link {
            display: block;
            height: 100%;
        }

        .pulse-hero-image {
            width: 100%;
            height: 100%;
            object-fit: cover;
            transition: transform 0.5s ease;
        }

        .pulse-hero:hover .pulse-hero-image {
            transform: scale(1.02);
        }

        .pulse-hero-overlay {
            position: absolute;
            bottom: 0;
            left: 0;
            right: 0;
            padding: 48px;
            background: linear-gradient(transparent, rgba(0,0,0,0.9));
        }

        .pulse-hero-tag {
            display: inline-block;
            padding: 6px 14px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1px;
            border-radius: 4px;
            margin-bottom: 16px;
        }

        .pulse-hero-tag.bullish {
            background: var(--accent-green);
            color: white;
        }

        .pulse-hero-tag.bearish {
            background: var(--accent-red);
            color: white;
        }

        .pulse-hero h2 {
            font-size: 38px;
            font-weight: 700;
            color: white;
            line-height: 1.15;
            margin-bottom: 14px;
            letter-spacing: -0.5px;
            max-width: 800px;
        }

        .pulse-hero-meta {
            font-size: 14px;
            color: rgba(255,255,255,0.75);
            font-family: 'IBM Plex Mono', monospace;
            margin-bottom: 14px;
        }

        .pulse-hero-excerpt {
            font-size: 17px;
            color: rgba(255,255,255,0.9);
            line-height: 1.6;
            max-width: 700px;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        /* Section Divider */
        .pulse-divider {
            display: flex;
            align-items: center;
            gap: 16px;
            margin-bottom: 28px;
        }

        .pulse-divider-title {
            font-size: 12px;
            font-weight: 700;
            color: var(--text-primary);
            text-transform: uppercase;
            letter-spacing: 1px;
            white-space: nowrap;
        }

        .pulse-divider-line {
            flex: 1;
            height: 2px;
            background: var(--border-color);
        }

        /* Card Grid - 4 columns, uniform */
        .pulse-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 24px;
            margin-bottom: 48px;
        }

        .pulse-card {
            display: block;
            text-decoration: none;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            overflow: hidden;
            transition: all 0.25s ease;
        }

        .pulse-card:hover {
            border-color: var(--accent-blue);
            box-shadow: 0 8px 30px rgba(0,0,0,0.12);
            transform: translateY(-4px);
        }

        .pulse-card-image {
            width: 100%;
            aspect-ratio: 16/10;
            background: var(--bg-tertiary);
            overflow: hidden;
        }

        .pulse-card-image img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            transition: transform 0.4s ease;
        }

        .pulse-card:hover .pulse-card-image img {
            transform: scale(1.05);
        }

        .pulse-card-content {
            padding: 18px;
        }

        .pulse-card-tag {
            display: inline-block;
            padding: 4px 10px;
            font-size: 10px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-radius: 4px;
            margin-bottom: 10px;
        }

        .pulse-card h3 {
            font-size: 16px;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.4;
            margin-bottom: 10px;
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .pulse-card-meta {
            font-size: 12px;
            color: var(--text-muted);
            font-family: 'IBM Plex Mono', monospace;
        }

        /* List Section */
        .pulse-list {
            display: flex;
            flex-direction: column;
            gap: 16px;
        }

        .pulse-list-item {
            display: flex;
            gap: 20px;
            text-decoration: none;
            padding: 20px;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            transition: all 0.2s ease;
        }

        .pulse-list-item:hover {
            border-color: var(--accent-blue);
            box-shadow: 0 4px 20px rgba(0,0,0,0.08);
        }

        .pulse-list-image {
            width: 200px;
            height: 130px;
            flex-shrink: 0;
            border-radius: 8px;
            overflow: hidden;
            background: var(--bg-tertiary);
        }

        .pulse-list-image img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }

        .pulse-list-content {
            flex: 1;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }

        .pulse-list-tag {
            display: inline-block;
            padding: 4px 10px;
            font-size: 10px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-radius: 4px;
            margin-bottom: 10px;
            width: fit-content;
        }

        .pulse-list-item h4 {
            font-size: 20px;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.35;
            margin-bottom: 10px;
        }

        .pulse-list-meta {
            font-size: 12px;
            color: var(--text-muted);
            font-family: 'IBM Plex Mono', monospace;
            margin-bottom: 10px;
        }

        .pulse-list-excerpt {
            font-size: 15px;
            color: var(--text-secondary);
            line-height: 1.55;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        /* Pulse Responsive */
        @media (max-width: 1024px) {
            .pulse-grid {
                grid-template-columns: repeat(2, 1fr);
            }
        }

        @media (max-width: 768px) {
            .pulse-hero {
                aspect-ratio: 4/3;
                min-height: 300px;
            }

            .pulse-hero-overlay {
                padding: 24px;
            }

            .pulse-hero h2 {
                font-size: 26px;
            }

            .pulse-hero-excerpt {
                display: none;
            }

            .pulse-grid {
                grid-template-columns: 1fr;
            }

            .pulse-list-item {
                flex-direction: column;
                padding: 16px;
            }

            .pulse-list-image {
                width: 100%;
                height: 180px;
            }

            .pulse-list-item h4 {
                font-size: 18px;
            }
        }
'''


def _build_pulse_hero(p):
    """Build the cinematic hero for pulse template."""
    url = p.get("url", "")
    title = p.get("title", "")
    dek = p.get("dek", "")
    date = _fmt_date(p.get("published_date", ""))
    symbol = p.get("symbol", "")
    days = p.get("pattern_days", "")
    direction = p.get("direction", "")
    
    hero_image_url = _get_hero_image_url(url, symbol, p.get("hero_image", ""))
    image_html = f'<img src="{hero_image_url}" alt="{symbol}" class="pulse-hero-image">' if hero_image_url else '<div class="pulse-hero-image" style="background:var(--bg-tertiary)"></div>'
    
    meta_parts = [date, symbol]
    if days:
        meta_parts.append(f"{days}-Day Pattern")
    meta = " • ".join(filter(None, meta_parts))
    
    tag_html = ""
    if SHOW_BADGES:
        sentiment_label, sentiment_class = _direction_to_sentiment(direction)
        if sentiment_label:
            tag_html = f'<span class="pulse-hero-tag {sentiment_class}">{sentiment_label}</span>'
    
    return f'''
        <div class="pulse-hero">
            <a href="{url}" class="pulse-hero-link">
                {image_html}
                <div class="pulse-hero-overlay">
                    {tag_html}
                    <h2>{title}</h2>
                    <div class="pulse-hero-meta">{meta}</div>
                    <p class="pulse-hero-excerpt">{dek}</p>
                </div>
            </a>
        </div>'''


def _build_pulse_card(p):
    """Build a grid card for pulse template."""
    url = p.get("url", "")
    title = p.get("title", "")
    date = _fmt_date(p.get("published_date", ""))
    symbol = p.get("symbol", "")
    days = p.get("pattern_days", "")
    direction = p.get("direction", "")
    
    hero_image_url = _get_hero_image_url(url, symbol, p.get("hero_image", ""))
    image_html = f'<img src="{hero_image_url}" alt="{symbol}">' if hero_image_url else ''
    
    meta_parts = [date, symbol]
    if days:
        meta_parts.append(f"{days}D")
    meta = " • ".join(filter(None, meta_parts))
    
    tag_html = ""
    if SHOW_BADGES:
        sentiment_label, sentiment_class = _direction_to_sentiment(direction)
        if sentiment_label:
            tag_html = f'<span class="pulse-card-tag {sentiment_class}">{sentiment_label}</span>'
    
    return f'''
            <a href="{url}" class="pulse-card">
                <div class="pulse-card-image">{image_html}</div>
                <div class="pulse-card-content">
                    {tag_html}
                    <h3>{title}</h3>
                    <div class="pulse-card-meta">{meta}</div>
                </div>
            </a>'''


def _build_pulse_list_item(p):
    """Build a list item for pulse template."""
    url = p.get("url", "")
    title = p.get("title", "")
    dek = p.get("dek", "")
    date = _fmt_date(p.get("published_date", ""))
    symbol = p.get("symbol", "")
    days = p.get("pattern_days", "")
    direction = p.get("direction", "")
    
    hero_image_url = _get_hero_image_url(url, symbol, p.get("hero_image", ""))
    image_html = f'<img src="{hero_image_url}" alt="{symbol}">' if hero_image_url else ''
    
    meta_parts = [date, symbol]
    if days:
        meta_parts.append(f"{days}-Day Pattern")
    meta = " • ".join(filter(None, meta_parts))
    
    tag_html = ""
    if SHOW_BADGES:
        sentiment_label, sentiment_class = _direction_to_sentiment(direction)
        if sentiment_label:
            tag_html = f'<span class="pulse-list-tag {sentiment_class}">{sentiment_label}</span>'
    
    return f'''
            <a href="{url}" class="pulse-list-item">
                <div class="pulse-list-image">{image_html}</div>
                <div class="pulse-list-content">
                    {tag_html}
                    <h4>{title}</h4>
                    <div class="pulse-list-meta">{meta}</div>
                    <p class="pulse-list-excerpt">{dek}</p>
                </div>
            </a>'''


def _build_pulse_template(items, t):
    """Build the pulse template - clean and professional."""
    if not items:
        return '<section class="pulse-container"><p class="no-articles">No articles yet.</p></section>'
    
    # Hero - first article
    hero_html = _build_pulse_hero(items[0])
    
    # Grid - next 8 articles (2 rows of 4)
    grid_items = items[1:9]
    grid_html = "\n".join(_build_pulse_card(p) for p in grid_items) if grid_items else ""
    
    grid_section = ""
    if grid_html:
        grid_section = f'''
        <div class="pulse-divider">
            <span class="pulse-divider-title">Latest Analysis</span>
            <div class="pulse-divider-line"></div>
        </div>
        <div class="pulse-grid">
{grid_html}
        </div>'''
    
    # List - remaining articles
    list_items = items[9:]
    list_html = "\n".join(_build_pulse_list_item(p) for p in list_items) if list_items else ""
    
    list_section = ""
    if list_html:
        list_section = f'''
        <div class="pulse-divider">
            <span class="pulse-divider-title">More Coverage</span>
            <div class="pulse-divider-line"></div>
        </div>
        <div class="pulse-list">
{list_html}
        </div>'''
    
    return f'''
    <section class="pulse-container">
{hero_html}
{grid_section}
{list_section}
    </section>
'''


# =============================================================================
# TEMPLATE 4: SPOTLIGHT - Full Hero + 3-Column Layout Below
# =============================================================================

def _get_spotlight_template_css():
    """Return CSS specific to spotlight template - full hero with columns below."""
    return '''
        /* Spotlight Template - Full Hero + Columns */
        .spotlight-container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 32px 24px;
        }

        /* Full-width Hero */
        .spotlight-hero {
            position: relative;
            border-radius: 16px;
            overflow: hidden;
            margin-bottom: 32px;
            aspect-ratio: 21/9;
            min-height: 360px;
        }

        .spotlight-hero-image {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }

        .spotlight-hero-overlay {
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: linear-gradient(90deg, rgba(0,0,0,0.85) 0%, rgba(0,0,0,0.4) 50%, transparent 100%);
            display: flex;
            flex-direction: column;
            justify-content: center;
            padding: 48px;
        }

        .spotlight-hero-content {
            max-width: 600px;
        }

        .spotlight-hero-tag {
            display: inline-block;
            padding: 6px 14px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1px;
            border-radius: 4px;
            margin-bottom: 16px;
        }

        .spotlight-hero-tag.bullish {
            background: var(--accent-green);
            color: white;
        }

        .spotlight-hero-tag.bearish {
            background: var(--accent-red);
            color: white;
        }

        .spotlight-hero h2 {
            font-size: 36px;
            font-weight: 700;
            color: white;
            line-height: 1.2;
            margin-bottom: 16px;
            letter-spacing: -0.5px;
        }

        .spotlight-hero-meta {
            font-size: 14px;
            color: rgba(255,255,255,0.8);
            font-family: 'IBM Plex Mono', monospace;
            margin-bottom: 16px;
        }

        .spotlight-hero-excerpt {
            font-size: 16px;
            color: rgba(255,255,255,0.9);
            line-height: 1.6;
            margin-bottom: 20px;
        }

        .spotlight-hero-cta {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 12px 24px;
            background: white;
            color: #1a1a1a;
            font-size: 14px;
            font-weight: 600;
            border-radius: 8px;
            text-decoration: none;
            transition: all 0.2s ease;
        }

        .spotlight-hero-cta:hover {
            background: var(--accent-blue);
            color: white;
            transform: translateX(4px);
        }

        /* Three-column layout */
        .spotlight-columns {
            display: grid;
            grid-template-columns: 1fr 1fr 340px;
            gap: 32px;
        }

        .spotlight-column {
            display: flex;
            flex-direction: column;
        }

        .spotlight-column-header {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 20px;
            padding-bottom: 12px;
            border-bottom: 2px solid var(--text-primary);
        }

        .spotlight-column-title {
            font-size: 12px;
            font-weight: 700;
            color: var(--text-primary);
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        /* Main columns - cards with images */
        .spotlight-card {
            display: block;
            text-decoration: none;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            overflow: hidden;
            margin-bottom: 20px;
            transition: all 0.2s ease;
        }

        .spotlight-card:hover {
            border-color: var(--accent-blue);
            box-shadow: var(--card-hover-shadow);
        }

        .spotlight-card-image {
            width: 100%;
            aspect-ratio: 16/10;
            background: var(--bg-tertiary);
            overflow: hidden;
        }

        .spotlight-card-image img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            transition: transform 0.3s ease;
        }

        .spotlight-card:hover .spotlight-card-image img {
            transform: scale(1.03);
        }

        .spotlight-card-content {
            padding: 16px;
        }

        .spotlight-card-tag {
            display: inline-block;
            padding: 3px 8px;
            font-size: 10px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-radius: 3px;
            margin-bottom: 8px;
        }

        .spotlight-card h3 {
            font-size: 17px;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.35;
            margin-bottom: 8px;
        }

        .spotlight-card-meta {
            font-size: 12px;
            color: var(--text-muted);
            font-family: 'IBM Plex Mono', monospace;
        }

        /* Sidebar - upcoming patterns */
        .spotlight-sidebar {
            background: var(--bg-secondary);
            border-radius: 12px;
            padding: 20px;
            height: fit-content;
        }

        .spotlight-sidebar .spotlight-column-header {
            margin-bottom: 16px;
            padding-bottom: 10px;
        }

        .spotlight-upcoming {
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        .spotlight-upcoming-item {
            display: flex;
            gap: 14px;
            text-decoration: none;
            padding: 12px;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            transition: all 0.2s ease;
        }

        .spotlight-upcoming-item:hover {
            border-color: var(--accent-blue);
            transform: translateX(4px);
        }

        .spotlight-upcoming-image {
            width: 70px;
            height: 50px;
            flex-shrink: 0;
            border-radius: 6px;
            overflow: hidden;
            background: var(--bg-tertiary);
        }

        .spotlight-upcoming-image img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }

        .spotlight-upcoming-content {
            flex: 1;
            min-width: 0;
        }

        .spotlight-upcoming-symbol {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 12px;
            font-weight: 600;
            color: var(--accent-blue);
            margin-bottom: 4px;
        }

        .spotlight-upcoming-title {
            font-size: 13px;
            font-weight: 500;
            color: var(--text-primary);
            line-height: 1.3;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .spotlight-upcoming-date {
            font-size: 11px;
            color: var(--text-muted);
            margin-top: 4px;
        }

        .spotlight-upcoming-direction {
            font-weight: 600;
        }

        .spotlight-upcoming-direction.bullish {
            color: var(--accent-green);
        }

        .spotlight-upcoming-direction.bearish {
            color: var(--accent-red);
        }

        /* Spotlight Responsive */
        @media (max-width: 1024px) {
            .spotlight-columns {
                grid-template-columns: 1fr 1fr;
            }

            .spotlight-sidebar {
                grid-column: span 2;
            }

            .spotlight-upcoming {
                display: grid;
                grid-template-columns: repeat(2, 1fr);
            }
        }

        @media (max-width: 768px) {
            .spotlight-hero {
                aspect-ratio: 4/3;
            }

            .spotlight-hero-overlay {
                padding: 24px;
                background: linear-gradient(0deg, rgba(0,0,0,0.9) 0%, rgba(0,0,0,0.4) 100%);
                justify-content: flex-end;
            }

            .spotlight-hero h2 {
                font-size: 24px;
            }

            .spotlight-hero-excerpt {
                display: none;
            }

            .spotlight-columns {
                grid-template-columns: 1fr;
            }

            .spotlight-sidebar {
                grid-column: auto;
            }

            .spotlight-upcoming {
                grid-template-columns: 1fr;
            }
        }
'''


def _build_spotlight_hero(p):
    """Build the full-width hero for spotlight template."""
    url = p.get("url", "")
    title = p.get("title", "")
    dek = p.get("dek", "")
    date = _fmt_date(p.get("published_date", ""))
    symbol = p.get("symbol", "")
    days = p.get("pattern_days", "")
    direction = p.get("direction", "")
    
    hero_image_url = _get_hero_image_url(url, symbol, p.get("hero_image", ""))
    image_html = f'<img src="{hero_image_url}" alt="{symbol}" class="spotlight-hero-image">' if hero_image_url else '<div class="spotlight-hero-image" style="background:var(--bg-tertiary)"></div>'
    
    meta_parts = [date, symbol]
    if days:
        meta_parts.append(f"{days}-Day Pattern")
    meta = " • ".join(filter(None, meta_parts))
    
    tag_html = ""
    if SHOW_BADGES:
        sentiment_label, sentiment_class = _direction_to_sentiment(direction)
        if sentiment_label:
            tag_html = f'<span class="spotlight-hero-tag {sentiment_class}">{sentiment_label}</span>'
    
    return f'''
        <div class="spotlight-hero">
            {image_html}
            <div class="spotlight-hero-overlay">
                <div class="spotlight-hero-content">
                    {tag_html}
                    <h2>{title}</h2>
                    <div class="spotlight-hero-meta">{meta}</div>
                    <p class="spotlight-hero-excerpt">{dek}</p>
                    <a href="{url}" class="spotlight-hero-cta">Read Full Analysis →</a>
                </div>
            </div>
        </div>'''


def _build_spotlight_card(p):
    """Build a column card for spotlight template."""
    url = p.get("url", "")
    title = p.get("title", "")
    date = _fmt_date(p.get("published_date", ""))
    symbol = p.get("symbol", "")
    days = p.get("pattern_days", "")
    direction = p.get("direction", "")
    
    hero_image_url = _get_hero_image_url(url, symbol, p.get("hero_image", ""))
    image_html = f'<img src="{hero_image_url}" alt="{symbol}">' if hero_image_url else ''
    
    meta_parts = [date, symbol]
    if days:
        meta_parts.append(f"{days}D")
    meta = " • ".join(filter(None, meta_parts))
    
    tag_html = ""
    if SHOW_BADGES:
        sentiment_label, sentiment_class = _direction_to_sentiment(direction)
        if sentiment_label:
            tag_html = f'<span class="spotlight-card-tag {sentiment_class}">{sentiment_label}</span>'
    
    return f'''
                <a href="{url}" class="spotlight-card">
                    <div class="spotlight-card-image">{image_html}</div>
                    <div class="spotlight-card-content">
                        {tag_html}
                        <h3>{title}</h3>
                        <div class="spotlight-card-meta">{meta}</div>
                    </div>
                </a>'''


def _build_spotlight_upcoming(p):
    """Build an upcoming item for spotlight sidebar."""
    url = p.get("url", "")
    title = p.get("title", "")
    symbol = p.get("symbol", "")
    direction = p.get("direction", "")
    
    pattern_date = _extract_pattern_date_from_url(url)
    days_until = _get_days_until_pattern(pattern_date) if pattern_date else None
    
    if days_until == 0:
        date_str = "Today"
    elif days_until == 1:
        date_str = "Tomorrow"
    elif days_until and days_until > 0:
        date_str = f"In {days_until} days"
    else:
        date_str = pattern_date.strftime("%b %d") if pattern_date else ""
    
    hero_image_url = _get_hero_image_url(url, symbol, p.get("hero_image", ""))
    image_html = f'<img src="{hero_image_url}" alt="{symbol}">' if hero_image_url else ''
    
    sentiment_label, sentiment_class = _direction_to_sentiment(direction)
    direction_html = ""
    if sentiment_label:
        direction_html = f' • <span class="spotlight-upcoming-direction {sentiment_class}">{sentiment_label}</span>'
    
    return f'''
                    <a href="{url}" class="spotlight-upcoming-item">
                        <div class="spotlight-upcoming-image">{image_html}</div>
                        <div class="spotlight-upcoming-content">
                            <div class="spotlight-upcoming-symbol">{symbol}{direction_html}</div>
                            <div class="spotlight-upcoming-title">{title}</div>
                            <div class="spotlight-upcoming-date">{date_str}</div>
                        </div>
                    </a>'''


def _build_spotlight_template(items, t):
    """Build the spotlight template with full hero and columns."""
    if not items:
        return '<section class="spotlight-container"><p class="no-articles">No articles yet.</p></section>'
    
    # Hero - first article
    hero_article = items[0]
    hero_html = _build_spotlight_hero(hero_article)
    
    # Split remaining into columns
    remaining = items[1:]
    col1_items = remaining[::2][:4]  # Every other, max 4
    col2_items = remaining[1::2][:4]  # Every other offset, max 4
    sidebar_items = remaining[:8]  # First 8 for sidebar
    
    col1_html = "\n".join(_build_spotlight_card(p) for p in col1_items)
    col2_html = "\n".join(_build_spotlight_card(p) for p in col2_items)
    sidebar_html = "\n".join(_build_spotlight_upcoming(p) for p in sidebar_items)
    
    return f'''
    <section class="spotlight-container">
{hero_html}
        
        <div class="spotlight-columns">
            <div class="spotlight-column">
                <div class="spotlight-column-header">
                    <span class="spotlight-column-title">Latest Analysis</span>
                </div>
{col1_html}
            </div>
            
            <div class="spotlight-column">
                <div class="spotlight-column-header">
                    <span class="spotlight-column-title">Market Coverage</span>
                </div>
{col2_html}
            </div>
            
            <div class="spotlight-sidebar">
                <div class="spotlight-column-header">
                    <span class="spotlight-column-title">Upcoming Patterns</span>
                </div>
                <div class="spotlight-upcoming">
{sidebar_html}
                </div>
            </div>
        </div>
    </section>
'''


# =============================================================================
# TEMPLATE 4: CALENDAR - Timeline View by Pattern Activation Date
# =============================================================================

def _get_calendar_template_css():
    """Return CSS specific to calendar template."""
    return '''
        /* Calendar Template */
        .calendar-container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 32px 24px;
        }

        .calendar-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 32px;
        }

        .calendar-title {
            font-size: 14px;
            font-weight: 700;
            color: var(--text-primary);
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .calendar-legend {
            display: flex;
            gap: 20px;
            font-size: 12px;
        }

        .legend-item {
            display: flex;
            align-items: center;
            gap: 6px;
            color: var(--text-muted);
        }

        .legend-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
        }

        .legend-dot.bullish {
            background: var(--accent-green);
        }

        .legend-dot.bearish {
            background: var(--accent-red);
        }

        /* Timeline */
        .timeline {
            position: relative;
        }

        .timeline::before {
            content: '';
            position: absolute;
            left: 120px;
            top: 0;
            bottom: 0;
            width: 3px;
            background: var(--border-color);
            border-radius: 2px;
        }

        .timeline-section {
            margin-bottom: 40px;
        }

        .timeline-date {
            display: flex;
            align-items: flex-start;
            gap: 24px;
            position: relative;
        }

        .date-marker {
            width: 120px;
            flex-shrink: 0;
            text-align: right;
            padding-right: 24px;
        }

        .date-day {
            font-size: 32px;
            font-weight: 700;
            color: var(--text-primary);
            line-height: 1;
        }

        .date-month {
            font-size: 14px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-top: 4px;
        }

        .date-weekday {
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 2px;
        }

        .date-relative {
            font-size: 11px;
            color: var(--accent-blue);
            font-weight: 600;
            margin-top: 6px;
        }

        .date-relative.today {
            color: var(--accent-green);
        }

        .date-relative.past {
            color: var(--text-muted);
        }

        .timeline-dot {
            position: absolute;
            left: 120px;
            top: 8px;
            width: 13px;
            height: 13px;
            background: var(--accent-blue);
            border: 3px solid var(--bg-primary);
            border-radius: 50%;
            transform: translateX(-5px);
            z-index: 1;
        }

        .timeline-dot.today {
            background: var(--accent-green);
            width: 17px;
            height: 17px;
            transform: translateX(-7px);
        }

        .timeline-dot.past {
            background: var(--text-muted);
        }

        .timeline-articles {
            flex: 1;
            padding-left: 24px;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        /* Calendar Article Cards */
        .calendar-card {
            display: flex;
            gap: 16px;
            padding: 16px;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            text-decoration: none;
            transition: all 0.2s ease;
            box-shadow: var(--card-shadow);
        }

        .calendar-card:hover {
            border-color: var(--accent-blue);
            box-shadow: var(--card-hover-shadow);
            transform: translateX(4px);
        }

        .calendar-card-image {
            width: 140px;
            height: 90px;
            flex-shrink: 0;
            border-radius: 6px;
            overflow: hidden;
            background: var(--bg-tertiary);
        }

        .calendar-card-image img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }

        .calendar-card-content {
            flex: 1;
            min-width: 0;
        }

        .calendar-card-header {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 6px;
        }

        .calendar-card-symbol {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 12px;
            font-weight: 600;
            color: var(--accent-blue);
            background: rgba(0, 102, 204, 0.1);
            padding: 2px 8px;
            border-radius: 4px;
        }

        .calendar-card-direction {
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            padding: 2px 8px;
            border-radius: 4px;
        }

        .calendar-card-days {
            font-size: 11px;
            color: var(--text-muted);
        }

        .calendar-card h3 {
            font-size: 16px;
            font-weight: 600;
            color: var(--text-primary);
            margin-bottom: 4px;
            line-height: 1.3;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .calendar-card-excerpt {
            font-size: 13px;
            color: var(--text-secondary);
            line-height: 1.5;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        /* Calendar Responsive */
        @media (max-width: 768px) {
            .timeline::before {
                left: 20px;
            }

            .date-marker {
                width: auto;
                min-width: 60px;
                padding-right: 16px;
            }

            .date-day {
                font-size: 24px;
            }

            .timeline-dot {
                left: 20px;
            }

            .timeline-articles {
                padding-left: 16px;
            }

            .calendar-card {
                flex-direction: column;
            }

            .calendar-card-image {
                width: 100%;
                height: 150px;
            }

            .calendar-header {
                flex-direction: column;
                gap: 16px;
                align-items: flex-start;
            }
        }
'''


def _build_calendar_card(p):
    """Generate HTML for a calendar timeline card."""
    url = p.get("url", "")
    title = p.get("title", "")
    dek = p.get("dek", "")
    symbol = p.get("symbol", "")
    days = p.get("pattern_days", "")
    direction = p.get("direction", "")
    
    hero_image_url = _get_hero_image_url(url, symbol, p.get("hero_image", ""))
    image_html = ""
    if hero_image_url:
        image_html = f'''
            <div class="calendar-card-image">
                <img src="{hero_image_url}" alt="{symbol}">
            </div>'''
    
    direction_html = ""
    if SHOW_BADGES:
        sentiment_label, sentiment_class = _direction_to_sentiment(direction)
        if sentiment_label and sentiment_class:
            direction_html = f'<span class="calendar-card-direction {sentiment_class}">{sentiment_label}</span>'
    
    days_html = f'<span class="calendar-card-days">{days}-Day Pattern</span>' if days else ""
    
    return f'''
                <a href="{url}" class="calendar-card">
                    {image_html}
                    <div class="calendar-card-content">
                        <div class="calendar-card-header">
                            <span class="calendar-card-symbol">{symbol}</span>
                            {direction_html}
                            {days_html}
                        </div>
                        <h3>{title}</h3>
                        <p class="calendar-card-excerpt">{dek}</p>
                    </div>
                </a>'''


def _build_calendar_template(items, t):
    """Build the calendar template HTML."""
    if not items:
        return '<section class="calendar-container"><p class="no-articles">No patterns scheduled.</p></section>'
    
    # Group articles by pattern activation date
    grouped = _group_articles_by_pattern_date(items)
    
    # Sort dates
    sorted_dates = sorted(grouped.keys())
    
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    timeline_html = ""
    for date_str in sorted_dates:
        articles = grouped[date_str]
        
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            day_num = date_obj.strftime("%d").lstrip("0")
            month = date_obj.strftime("%b")
            weekday = date_obj.strftime("%A")
            
            # Calculate relative time
            days_diff = _get_days_until_pattern(date_obj.replace(tzinfo=timezone.utc))
            if days_diff is None:
                relative = ""
                dot_class = ""
            elif days_diff == 0:
                relative = "Today"
                dot_class = "today"
            elif days_diff == 1:
                relative = "Tomorrow"
                dot_class = ""
            elif days_diff > 1:
                relative = f"In {days_diff} days"
                dot_class = ""
            else:
                relative = f"{abs(days_diff)} days ago"
                dot_class = "past"
        except Exception:
            day_num = "?"
            month = date_str
            weekday = ""
            relative = ""
            dot_class = ""
        
        cards_html = "\n".join(_build_calendar_card(p) for p in articles)
        
        relative_class = "today" if relative == "Today" else ("past" if "ago" in relative else "")
        
        timeline_html += f'''
            <div class="timeline-section">
                <div class="timeline-date">
                    <div class="date-marker">
                        <div class="date-day">{day_num}</div>
                        <div class="date-month">{month}</div>
                        <div class="date-weekday">{weekday}</div>
                        <div class="date-relative {relative_class}">{relative}</div>
                    </div>
                    <div class="timeline-dot {dot_class}"></div>
                    <div class="timeline-articles">
{cards_html}
                    </div>
                </div>
            </div>'''
    
    return f'''
    <section class="calendar-container">
        <div class="calendar-header">
            <span class="calendar-title">Pattern Activation Timeline</span>
            <div class="calendar-legend">
                <div class="legend-item">
                    <span class="legend-dot bullish"></span>
                    <span>Bullish Pattern</span>
                </div>
                <div class="legend-item">
                    <span class="legend-dot bearish"></span>
                    <span>Bearish Pattern</span>
                </div>
            </div>
        </div>
        <div class="timeline">
{timeline_html}
        </div>
    </section>
'''


# =============================================================================
# TEMPLATE 2: TERMINAL - Bloomberg-Style Trading Terminal
# =============================================================================

def _get_terminal_template_css():
    """Return CSS specific to terminal template."""
    return '''
        /* Terminal Template - Override fonts */
        .terminal-mode {
            font-family: 'IBM Plex Mono', 'SF Mono', 'Fira Code', monospace;
        }

        .terminal-mode .logo {
            font-family: 'IBM Plex Mono', monospace;
        }

        .terminal-mode .logo-seasonal {
            color: var(--accent-green);
        }

        .terminal-mode .hero h1 {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 28px;
            letter-spacing: -0.5px;
        }

        .terminal-mode .hero h1 span {
            color: var(--accent-green);
        }

        /* Terminal Container */
        .terminal-container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 24px;
        }

        /* Terminal Stats Bar */
        .terminal-stats {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 16px;
            margin-bottom: 24px;
        }

        .stat-box {
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 4px;
            padding: 16px;
        }

        .stat-label {
            font-size: 10px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 6px;
        }

        .stat-value {
            font-size: 24px;
            font-weight: 600;
            color: var(--text-primary);
        }

        .stat-value.green {
            color: var(--accent-green);
        }

        .stat-value.red {
            color: var(--accent-red);
        }

        .stat-change {
            font-size: 11px;
            color: var(--text-muted);
            margin-top: 4px;
        }

        /* Terminal Grid */
        .terminal-grid {
            display: grid;
            grid-template-columns: 1fr 380px;
            gap: 20px;
        }

        /* Main Feed */
        .terminal-feed {
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 4px;
            overflow: hidden;
        }

        .feed-header {
            background: var(--bg-tertiary);
            padding: 10px 16px;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .feed-title {
            font-size: 11px;
            font-weight: 600;
            color: var(--accent-green);
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .feed-timestamp {
            font-size: 10px;
            color: var(--text-muted);
        }

        .terminal-news-list {
            max-height: 800px;
            overflow-y: auto;
        }

        .terminal-news-item {
            display: block;
            padding: 14px 16px;
            border-bottom: 1px solid var(--border-color);
            text-decoration: none;
            transition: background 0.15s ease;
        }

        .terminal-news-item:hover {
            background: var(--bg-tertiary);
        }

        .terminal-news-item:last-child {
            border-bottom: none;
        }

        .terminal-news-header {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 6px;
        }

        .terminal-news-time {
            font-size: 10px;
            color: var(--accent-amber);
            font-weight: 500;
        }

        .terminal-news-symbol {
            font-size: 11px;
            font-weight: 600;
            color: var(--accent-blue);
            background: rgba(10, 132, 255, 0.1);
            padding: 1px 6px;
            border-radius: 2px;
        }

        .terminal-news-direction {
            font-size: 10px;
            font-weight: 600;
            padding: 1px 6px;
            border-radius: 2px;
        }

        .terminal-news-direction.bullish {
            background: rgba(48, 209, 88, 0.2);
            color: var(--accent-green);
        }

        .terminal-news-direction.bearish {
            background: rgba(255, 69, 58, 0.2);
            color: var(--accent-red);
        }

        .terminal-news-title {
            font-size: 13px;
            font-weight: 500;
            color: var(--text-primary);
            line-height: 1.4;
            margin-bottom: 4px;
        }

        .terminal-news-excerpt {
            font-size: 11px;
            color: var(--text-secondary);
            line-height: 1.4;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        /* Sidebar */
        .terminal-sidebar {
            display: flex;
            flex-direction: column;
            gap: 16px;
        }

        .sidebar-panel {
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 4px;
            overflow: hidden;
        }

        .panel-header {
            background: var(--bg-tertiary);
            padding: 10px 16px;
            border-bottom: 1px solid var(--border-color);
        }

        .panel-title {
            font-size: 11px;
            font-weight: 600;
            color: var(--accent-amber);
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .panel-content {
            padding: 12px 16px;
        }

        /* Upcoming Patterns Panel */
        .upcoming-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 0;
            border-bottom: 1px solid var(--border-color);
        }

        .upcoming-item:last-child {
            border-bottom: none;
        }

        .upcoming-symbol {
            font-weight: 600;
            color: var(--text-primary);
        }

        .upcoming-direction {
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 2px;
        }

        .upcoming-date {
            font-size: 11px;
            color: var(--text-muted);
        }

        /* Terminal Responsive */
        @media (max-width: 1024px) {
            .terminal-grid {
                grid-template-columns: 1fr;
            }

            .terminal-sidebar {
                flex-direction: row;
                flex-wrap: wrap;
            }

            .sidebar-panel {
                flex: 1;
                min-width: 280px;
            }
        }

        @media (max-width: 768px) {
            .terminal-stats {
                grid-template-columns: repeat(2, 1fr);
            }

            .sidebar-panel {
                min-width: 100%;
            }
        }
'''


def _build_terminal_news_item(p):
    """Generate HTML for a terminal news item."""
    url = p.get("url", "")
    title = p.get("title", "")
    dek = p.get("dek", "")
    date = p.get("published_date", "")
    symbol = p.get("symbol", "")
    direction = p.get("direction", "")
    
    # Format time as HH:MM
    try:
        dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
        time_str = dt.strftime("%H:%M")
    except Exception:
        time_str = "00:00"
    
    direction_html = ""
    if SHOW_BADGES:
        sentiment_label, sentiment_class = _direction_to_sentiment(direction)
        if sentiment_label and sentiment_class:
            direction_html = f'<span class="terminal-news-direction {sentiment_class}">{sentiment_label.upper()}</span>'
    
    return f'''
            <a href="{url}" class="terminal-news-item">
                <div class="terminal-news-header">
                    <span class="terminal-news-time">{time_str}</span>
                    <span class="terminal-news-symbol">{symbol}</span>
                    {direction_html}
                </div>
                <div class="terminal-news-title">{title}</div>
                <div class="terminal-news-excerpt">{dek}</div>
            </a>'''


def _build_terminal_template(items, t):
    """Build the terminal template HTML."""
    if not items:
        return '<section class="terminal-container"><p class="no-articles">No market data available.</p></section>'
    
    # Calculate stats
    total = len(items)
    bullish = sum(1 for p in items if p.get("direction", "").lower() == "long")
    bearish = sum(1 for p in items if p.get("direction", "").lower() == "short")
    
    # Build news feed
    news_html = "\n".join(_build_terminal_news_item(p) for p in items)
    
    # Build upcoming patterns (next 5 by pattern date)
    upcoming_html = ""
    for p in items[:8]:
        symbol = p.get("symbol", "")
        direction = p.get("direction", "")
        url = p.get("url", "")
        pattern_date = _extract_pattern_date_from_url(url)
        
        date_str = pattern_date.strftime("%b %d") if pattern_date else "TBD"
        
        sentiment_label, sentiment_class = _direction_to_sentiment(direction)
        dir_html = f'<span class="upcoming-direction {sentiment_class or ""}">{sentiment_label or "—"}</span>' if sentiment_label else ""
        
        upcoming_html += f'''
                <div class="upcoming-item">
                    <span class="upcoming-symbol">{symbol}</span>
                    {dir_html}
                    <span class="upcoming-date">{date_str}</span>
                </div>'''
    
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    
    return f'''
    <section class="terminal-container">
        <!-- Stats Bar -->
        <div class="terminal-stats">
            <div class="stat-box">
                <div class="stat-label">Active Patterns</div>
                <div class="stat-value">{total}</div>
                <div class="stat-change">Last 14 days</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Bullish Signals</div>
                <div class="stat-value green">{bullish}</div>
                <div class="stat-change">{round(bullish/total*100) if total > 0 else 0}% of total</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Bearish Signals</div>
                <div class="stat-value red">{bearish}</div>
                <div class="stat-change">{round(bearish/total*100) if total > 0 else 0}% of total</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Win Rate (Hist.)</div>
                <div class="stat-value green">67%</div>
                <div class="stat-change">Based on backtests</div>
            </div>
        </div>

        <!-- Main Grid -->
        <div class="terminal-grid">
            <!-- News Feed -->
            <div class="terminal-feed">
                <div class="feed-header">
                    <span class="feed-title">▸ Live Pattern Feed</span>
                    <span class="feed-timestamp">{timestamp}</span>
                </div>
                <div class="terminal-news-list">
{news_html}
                </div>
            </div>

            <!-- Sidebar -->
            <div class="terminal-sidebar">
                <div class="sidebar-panel">
                    <div class="panel-header">
                        <span class="panel-title">▸ Upcoming Activations</span>
                    </div>
                    <div class="panel-content">
{upcoming_html}
                    </div>
                </div>
            </div>
        </div>
    </section>
'''


# =============================================================================
# TEMPLATE 3: BROADSHEET - Classic Newspaper Editorial Layout
# =============================================================================

def _get_broadsheet_template_css():
    """Return CSS specific to broadsheet template."""
    return '''
        /* Broadsheet Template - Typography Override */
        .broadsheet-mode .hero h1 {
            font-family: 'Playfair Display', 'Georgia', 'Times New Roman', serif;
            font-size: 42px;
            font-weight: 700;
            letter-spacing: -1px;
        }

        .broadsheet-mode .hero-subtitle {
            font-family: 'Georgia', serif;
            font-style: italic;
        }

        /* Broadsheet Container */
        .broadsheet-container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 40px 24px;
        }

        /* Edition Header */
        .edition-header {
            text-align: center;
            padding-bottom: 20px;
            margin-bottom: 32px;
            border-bottom: 3px double var(--border-color);
        }

        .edition-date {
            font-family: 'Georgia', serif;
            font-size: 13px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 2px;
        }

        .edition-tagline {
            font-family: 'Georgia', serif;
            font-style: italic;
            font-size: 14px;
            color: var(--text-secondary);
            margin-top: 8px;
        }

        /* Lead Story */
        .lead-story {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 32px;
            margin-bottom: 40px;
            padding-bottom: 40px;
            border-bottom: 1px solid var(--border-color);
        }

        .lead-image {
            aspect-ratio: 4/3;
            background: var(--bg-tertiary);
            border-radius: 4px;
            overflow: hidden;
        }

        .lead-image img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }

        .lead-content {
            display: flex;
            flex-direction: column;
        }

        .lead-kicker {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 11px;
            font-weight: 600;
            color: var(--accent-blue);
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 12px;
        }

        .lead-headline {
            font-family: 'Playfair Display', 'Georgia', serif;
            font-size: 36px;
            font-weight: 700;
            color: var(--text-primary);
            line-height: 1.15;
            margin-bottom: 16px;
            letter-spacing: -0.5px;
        }

        .lead-headline a {
            color: inherit;
            text-decoration: none;
        }

        .lead-headline a:hover {
            color: var(--accent-blue);
        }

        .lead-byline {
            font-family: 'Georgia', serif;
            font-size: 13px;
            color: var(--text-muted);
            margin-bottom: 16px;
        }

        .lead-excerpt {
            font-family: 'Georgia', serif;
            font-size: 17px;
            color: var(--text-secondary);
            line-height: 1.7;
            flex: 1;
        }

        .lead-excerpt::first-letter {
            font-size: 48px;
            font-weight: 700;
            float: left;
            line-height: 1;
            margin-right: 8px;
            margin-top: 4px;
            color: var(--text-primary);
        }

        /* Column Layout */
        .column-section {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 24px;
            margin-bottom: 40px;
        }

        .column {
            border-right: 1px solid var(--border-color);
            padding-right: 24px;
        }

        .column:last-child {
            border-right: none;
            padding-right: 0;
        }

        .column-header {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 11px;
            font-weight: 600;
            color: var(--text-primary);
            text-transform: uppercase;
            letter-spacing: 1px;
            padding-bottom: 10px;
            margin-bottom: 20px;
            border-bottom: 2px solid var(--text-primary);
        }

        /* Broadsheet Article */
        .broadsheet-article {
            margin-bottom: 24px;
            padding-bottom: 24px;
            border-bottom: 1px solid var(--border-color);
        }

        .broadsheet-article:last-child {
            margin-bottom: 0;
            padding-bottom: 0;
            border-bottom: none;
        }

        .broadsheet-article-link {
            text-decoration: none;
            display: block;
        }

        .broadsheet-article-image {
            width: 100%;
            aspect-ratio: 16/10;
            background: var(--bg-tertiary);
            border-radius: 4px;
            overflow: hidden;
            margin-bottom: 12px;
        }

        .broadsheet-article-image img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }

        .broadsheet-article-kicker {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 6px;
        }

        .broadsheet-article-kicker.bullish {
            color: var(--accent-green);
        }

        .broadsheet-article-kicker.bearish {
            color: var(--accent-red);
        }

        .broadsheet-article h3 {
            font-family: 'Playfair Display', 'Georgia', serif;
            font-size: 20px;
            font-weight: 700;
            color: var(--text-primary);
            line-height: 1.25;
            margin-bottom: 8px;
            transition: color 0.2s ease;
        }

        .broadsheet-article-link:hover h3 {
            color: var(--accent-blue);
        }

        .broadsheet-article-meta {
            font-family: 'Georgia', serif;
            font-size: 12px;
            color: var(--text-muted);
            margin-bottom: 8px;
        }

        .broadsheet-article p {
            font-family: 'Georgia', serif;
            font-size: 14px;
            color: var(--text-secondary);
            line-height: 1.6;
        }

        /* Broadsheet Responsive */
        @media (max-width: 1024px) {
            .column-section {
                grid-template-columns: repeat(2, 1fr);
            }

            .column:nth-child(2) {
                border-right: none;
                padding-right: 0;
            }

            .column:nth-child(3) {
                grid-column: span 2;
                border-right: none;
                border-top: 1px solid var(--border-color);
                padding-top: 24px;
                padding-right: 0;
            }
        }

        @media (max-width: 768px) {
            .lead-story {
                grid-template-columns: 1fr;
            }

            .lead-headline {
                font-size: 28px;
            }

            .column-section {
                grid-template-columns: 1fr;
            }

            .column {
                border-right: none;
                padding-right: 0;
                border-bottom: 1px solid var(--border-color);
                padding-bottom: 24px;
                margin-bottom: 24px;
            }

            .column:last-child {
                border-bottom: none;
                padding-bottom: 0;
                margin-bottom: 0;
            }

            .column:nth-child(3) {
                grid-column: auto;
                border-top: none;
                padding-top: 0;
            }
        }
'''


def _build_broadsheet_article(p, show_image=True):
    """Generate HTML for a broadsheet column article."""
    url = p.get("url", "")
    title = p.get("title", "")
    dek = p.get("dek", "")
    date = _fmt_date(p.get("published_date", ""))
    symbol = p.get("symbol", "")
    direction = p.get("direction", "")
    days = p.get("pattern_days", "")
    
    hero_image_url = _get_hero_image_url(url, symbol, p.get("hero_image", ""))
    image_html = ""
    if show_image and hero_image_url:
        image_html = f'''
            <div class="broadsheet-article-image">
                <img src="{hero_image_url}" alt="{symbol}">
            </div>'''
    
    sentiment_label, sentiment_class = _direction_to_sentiment(direction)
    kicker_html = ""
    if SHOW_BADGES and sentiment_label:
        kicker_html = f'<div class="broadsheet-article-kicker {sentiment_class}">{symbol} • {sentiment_label}</div>'
    else:
        kicker_html = f'<div class="broadsheet-article-kicker">{symbol}</div>'
    
    return f'''
                <article class="broadsheet-article">
                    <a href="{url}" class="broadsheet-article-link">
                        {image_html}
                        {kicker_html}
                        <h3>{title}</h3>
                        <div class="broadsheet-article-meta">{date} • {days}-Day Pattern</div>
                        <p>{dek}</p>
                    </a>
                </article>'''


def _build_broadsheet_template(items, t):
    """Build the broadsheet template HTML."""
    if not items:
        return '<section class="broadsheet-container"><p class="no-articles">No articles available.</p></section>'
    
    # Lead story
    lead = items[0]
    lead_url = lead.get("url", "")
    lead_title = lead.get("title", "")
    lead_dek = lead.get("dek", "")
    lead_symbol = lead.get("symbol", "")
    lead_date = _fmt_date(lead.get("published_date", ""))
    lead_days = lead.get("pattern_days", "")
    lead_direction = lead.get("direction", "")
    
    lead_image_url = _get_hero_image_url(lead_url, lead_symbol, lead.get("hero_image", ""))
    lead_image_html = ""
    if lead_image_url:
        lead_image_html = f'<img src="{lead_image_url}" alt="{lead_symbol}">'
    
    sentiment_label, _ = _direction_to_sentiment(lead_direction)
    lead_kicker = f"{lead_symbol} • {sentiment_label}" if sentiment_label else lead_symbol
    
    # Remaining articles divided into columns
    remaining = items[1:]
    col1 = remaining[:3]
    col2 = remaining[3:6]
    col3 = remaining[6:9]
    
    col1_html = "\n".join(_build_broadsheet_article(p, show_image=True) for p in col1)
    col2_html = "\n".join(_build_broadsheet_article(p, show_image=False) for p in col2)
    col3_html = "\n".join(_build_broadsheet_article(p, show_image=False) for p in col3)
    
    today = datetime.now(timezone.utc).strftime("%A, %B %d, %Y")
    
    return f'''
    <section class="broadsheet-container">
        <!-- Edition Header -->
        <div class="edition-header">
            <div class="edition-date">{today}</div>
            <div class="edition-tagline">"All the seasonal patterns fit to print"</div>
        </div>

        <!-- Lead Story -->
        <div class="lead-story">
            <div class="lead-image">
                {lead_image_html}
            </div>
            <div class="lead-content">
                <div class="lead-kicker">{lead_kicker}</div>
                <h2 class="lead-headline"><a href="{lead_url}">{lead_title}</a></h2>
                <div class="lead-byline">{lead_date} • {lead_days}-Day Pattern</div>
                <p class="lead-excerpt">{lead_dek}</p>
            </div>
        </div>

        <!-- Three Column Layout -->
        <div class="column-section">
            <div class="column">
                <div class="column-header">Latest Analysis</div>
{col1_html}
            </div>
            <div class="column">
                <div class="column-header">Market Outlook</div>
{col2_html}
            </div>
            <div class="column">
                <div class="column-header">Pattern Watch</div>
{col3_html}
            </div>
        </div>
    </section>
'''


# =============================================================================
# TEMPLATE 4: DASHBOARD - Trade Signal Dashboard with Indicators
# =============================================================================

def _get_dashboard_template_css():
    """Return CSS specific to dashboard template."""
    return '''
        /* Dashboard Template */
        .dashboard-container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 24px;
        }

        /* Dashboard Header */
        .dashboard-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--border-color);
        }

        .dashboard-title {
            font-size: 14px;
            font-weight: 700;
            color: var(--text-primary);
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .dashboard-filters {
            display: flex;
            gap: 12px;
        }

        .filter-btn {
            padding: 6px 14px;
            font-size: 12px;
            font-weight: 500;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            color: var(--text-secondary);
            cursor: pointer;
            transition: all 0.2s ease;
        }

        .filter-btn:hover,
        .filter-btn.active {
            background: var(--accent-blue);
            border-color: var(--accent-blue);
            color: white;
        }

        /* Signal Grid */
        .signal-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 16px;
        }

        /* Signal Card */
        .signal-card {
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            overflow: hidden;
            text-decoration: none;
            transition: all 0.2s ease;
            box-shadow: var(--card-shadow);
        }

        .signal-card:hover {
            border-color: var(--accent-blue);
            box-shadow: var(--card-hover-shadow);
            transform: translateY(-2px);
        }

        /* Card Header with Direction Arrow */
        .signal-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 14px 16px;
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border-color);
        }

        .signal-symbol-group {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .signal-direction-arrow {
            width: 36px;
            height: 36px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 20px;
        }

        .signal-direction-arrow.bullish {
            background: rgba(13, 122, 62, 0.15);
            color: var(--accent-green);
        }

        .signal-direction-arrow.bearish {
            background: rgba(196, 30, 58, 0.15);
            color: var(--accent-red);
        }

        .signal-direction-arrow.neutral {
            background: var(--bg-tertiary);
            color: var(--text-muted);
        }

        .signal-symbol {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 18px;
            font-weight: 700;
            color: var(--text-primary);
        }

        .signal-name {
            font-size: 11px;
            color: var(--text-muted);
        }

        .signal-direction-label {
            font-size: 12px;
            font-weight: 700;
            text-transform: uppercase;
            padding: 4px 10px;
            border-radius: 4px;
        }

        .signal-direction-label.bullish {
            background: var(--badge-bullish-bg);
            color: var(--accent-green);
        }

        .signal-direction-label.bearish {
            background: var(--badge-bearish-bg);
            color: var(--accent-red);
        }

        /* Card Body */
        .signal-body {
            padding: 16px;
        }

        .signal-title {
            font-size: 15px;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.35;
            margin-bottom: 12px;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        /* Signal Metrics */
        .signal-metrics {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 12px;
            margin-bottom: 14px;
        }

        .metric {
            text-align: center;
            padding: 10px 8px;
            background: var(--bg-secondary);
            border-radius: 6px;
        }

        .metric-value {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 16px;
            font-weight: 600;
            color: var(--text-primary);
        }

        .metric-label {
            font-size: 10px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-top: 2px;
        }

        /* Strength Indicator */
        .signal-strength {
            margin-bottom: 14px;
        }

        .strength-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 6px;
        }

        .strength-label {
            font-size: 11px;
            color: var(--text-muted);
        }

        .strength-value {
            font-size: 11px;
            font-weight: 600;
            color: var(--text-primary);
        }

        .strength-bar {
            height: 6px;
            background: var(--bg-tertiary);
            border-radius: 3px;
            overflow: hidden;
        }

        .strength-fill {
            height: 100%;
            border-radius: 3px;
            transition: width 0.3s ease;
        }

        .strength-fill.high {
            background: var(--accent-green);
        }

        .strength-fill.medium {
            background: var(--accent-amber);
        }

        .strength-fill.low {
            background: var(--accent-red);
        }

        /* Card Footer */
        .signal-footer {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-top: 12px;
            border-top: 1px solid var(--border-color);
        }

        .signal-date {
            font-size: 11px;
            color: var(--text-muted);
        }

        .signal-cta {
            font-size: 12px;
            font-weight: 600;
            color: var(--accent-blue);
        }

        /* Dashboard Responsive */
        @media (max-width: 768px) {
            .dashboard-header {
                flex-direction: column;
                gap: 16px;
                align-items: flex-start;
            }

            .dashboard-filters {
                flex-wrap: wrap;
            }

            .signal-grid {
                grid-template-columns: 1fr;
            }

            .signal-metrics {
                grid-template-columns: repeat(3, 1fr);
            }
        }
'''


def _build_signal_card(p):
    """Generate HTML for a dashboard signal card."""
    url = p.get("url", "")
    title = p.get("title", "")
    symbol = p.get("symbol", "")
    days = p.get("pattern_days", "")
    direction = p.get("direction", "")
    date = _fmt_date(p.get("published_date", ""))
    
    # Pattern date
    pattern_date = _extract_pattern_date_from_url(url)
    pattern_date_str = pattern_date.strftime("%b %d") if pattern_date else "TBD"
    days_until = _get_days_until_pattern(pattern_date) if pattern_date else None
    
    # Direction styling
    sentiment_label, sentiment_class = _direction_to_sentiment(direction)
    arrow = "↑" if sentiment_class == "bullish" else ("↓" if sentiment_class == "bearish" else "→")
    
    direction_arrow_class = sentiment_class if sentiment_class else "neutral"
    direction_label_html = ""
    if SHOW_BADGES and sentiment_label:
        direction_label_html = f'<span class="signal-direction-label {sentiment_class}">{sentiment_label}</span>'
    
    # Simulated strength (can be replaced with real data later)
    import hashlib
    strength = int(hashlib.md5(symbol.encode()).hexdigest()[:2], 16) % 40 + 60  # 60-100%
    strength_class = "high" if strength >= 75 else ("medium" if strength >= 50 else "low")
    
    # Time horizon
    horizon = f"{days}D" if days else "—"
    
    return f'''
            <a href="{url}" class="signal-card">
                <div class="signal-header">
                    <div class="signal-symbol-group">
                        <div class="signal-direction-arrow {direction_arrow_class}">{arrow}</div>
                        <div>
                            <div class="signal-symbol">{symbol}</div>
                        </div>
                    </div>
                    {direction_label_html}
                </div>
                <div class="signal-body">
                    <h3 class="signal-title">{title}</h3>
                    
                    <div class="signal-metrics">
                        <div class="metric">
                            <div class="metric-value">{pattern_date_str}</div>
                            <div class="metric-label">Activation</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value">{horizon}</div>
                            <div class="metric-label">Horizon</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value">{days_until if days_until is not None and days_until >= 0 else "Active"}</div>
                            <div class="metric-label">{"Days Out" if days_until is not None and days_until >= 0 else "Status"}</div>
                        </div>
                    </div>
                    
                    <div class="signal-strength">
                        <div class="strength-header">
                            <span class="strength-label">Pattern Strength</span>
                            <span class="strength-value">{strength}%</span>
                        </div>
                        <div class="strength-bar">
                            <div class="strength-fill {strength_class}" style="width: {strength}%"></div>
                        </div>
                    </div>
                    
                    <div class="signal-footer">
                        <span class="signal-date">Published {date}</span>
                        <span class="signal-cta">View Analysis →</span>
                    </div>
                </div>
            </a>'''


def _build_dashboard_template(items, t):
    """Build the dashboard template HTML."""
    if not items:
        return '<section class="dashboard-container"><p class="no-articles">No signals available.</p></section>'
    
    cards_html = "\n".join(_build_signal_card(p) for p in items)
    
    return f'''
    <section class="dashboard-container">
        <div class="dashboard-header">
            <span class="dashboard-title">Active Pattern Signals</span>
            <div class="dashboard-filters">
                <button class="filter-btn active">All Signals</button>
                <button class="filter-btn">Bullish</button>
                <button class="filter-btn">Bearish</button>
                <button class="filter-btn">This Week</button>
            </div>
        </div>
        
        <div class="signal-grid">
{cards_html}
        </div>
    </section>
'''


# =============================================================================
# TEMPLATE 5: RADAR - Radial Layout by Time-to-Activation
# =============================================================================

def _get_radar_template_css():
    """Return CSS specific to radar template."""
    return '''
        /* Radar Template */
        .radar-container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 32px 24px;
        }

        .radar-header {
            text-align: center;
            margin-bottom: 40px;
        }

        .radar-title {
            font-size: 14px;
            font-weight: 700;
            color: var(--text-primary);
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 8px;
        }

        .radar-subtitle {
            font-size: 14px;
            color: var(--text-muted);
        }

        /* Time Rings - Visual Layout */
        .radar-visualization {
            position: relative;
            margin-bottom: 48px;
        }

        /* Ring Labels */
        .ring-labels {
            display: flex;
            justify-content: center;
            gap: 40px;
            margin-bottom: 32px;
        }

        .ring-label {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 16px;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 20px;
        }

        .ring-indicator {
            width: 12px;
            height: 12px;
            border-radius: 50%;
        }

        .ring-indicator.today {
            background: var(--accent-green);
            box-shadow: 0 0 8px var(--accent-green);
        }

        .ring-indicator.week {
            background: var(--accent-blue);
        }

        .ring-indicator.later {
            background: var(--accent-amber);
        }

        .ring-label-text {
            font-size: 12px;
            font-weight: 500;
            color: var(--text-secondary);
        }

        /* Time-Based Sections */
        .time-section {
            margin-bottom: 40px;
        }

        .time-section-header {
            display: flex;
            align-items: center;
            gap: 16px;
            margin-bottom: 20px;
        }

        .time-section-dot {
            width: 16px;
            height: 16px;
            border-radius: 50%;
            flex-shrink: 0;
        }

        .time-section-dot.today {
            background: var(--accent-green);
            box-shadow: 0 0 12px var(--accent-green);
        }

        .time-section-dot.week {
            background: var(--accent-blue);
        }

        .time-section-dot.later {
            background: var(--accent-amber);
        }

        .time-section-title {
            font-size: 18px;
            font-weight: 700;
            color: var(--text-primary);
        }

        .time-section-count {
            font-size: 13px;
            color: var(--text-muted);
            padding: 4px 12px;
            background: var(--bg-secondary);
            border-radius: 12px;
        }

        .time-section-line {
            flex: 1;
            height: 1px;
            background: var(--border-color);
        }

        /* Radar Cards Grid */
        .radar-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 16px;
        }

        /* Radar Card */
        .radar-card {
            display: block;
            padding: 16px;
            background: var(--bg-primary);
            border: 2px solid var(--border-color);
            border-radius: 12px;
            text-decoration: none;
            transition: all 0.2s ease;
            position: relative;
            overflow: hidden;
        }

        .radar-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 4px;
            height: 100%;
        }

        .radar-card.bullish::before {
            background: var(--accent-green);
        }

        .radar-card.bearish::before {
            background: var(--accent-red);
        }

        .radar-card:hover {
            border-color: var(--accent-blue);
            transform: translateY(-2px);
            box-shadow: var(--card-hover-shadow);
        }

        .radar-card-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 10px;
        }

        .radar-card-symbol {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 16px;
            font-weight: 700;
            color: var(--text-primary);
        }

        .radar-card-countdown {
            font-size: 12px;
            font-weight: 600;
            padding: 4px 10px;
            border-radius: 12px;
            background: var(--bg-secondary);
            color: var(--text-secondary);
        }

        .radar-card-countdown.today {
            background: rgba(13, 122, 62, 0.15);
            color: var(--accent-green);
        }

        .radar-card-title {
            font-size: 14px;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.35;
            margin-bottom: 10px;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .radar-card-meta {
            display: flex;
            gap: 12px;
            font-size: 11px;
            color: var(--text-muted);
        }

        .radar-card-meta span {
            display: flex;
            align-items: center;
            gap: 4px;
        }

        .radar-card-direction {
            font-weight: 600;
        }

        .radar-card-direction.bullish {
            color: var(--accent-green);
        }

        .radar-card-direction.bearish {
            color: var(--accent-red);
        }

        /* No patterns message */
        .no-patterns {
            text-align: center;
            padding: 32px;
            color: var(--text-muted);
            background: var(--bg-secondary);
            border-radius: 12px;
        }

        /* Radar Responsive */
        @media (max-width: 768px) {
            .ring-labels {
                flex-wrap: wrap;
                gap: 12px;
            }

            .radar-grid {
                grid-template-columns: 1fr;
            }

            .time-section-header {
                flex-wrap: wrap;
            }

            .time-section-line {
                display: none;
            }
        }
'''


def _build_radar_card(p, days_until):
    """Generate HTML for a radar card."""
    url = p.get("url", "")
    title = p.get("title", "")
    symbol = p.get("symbol", "")
    days = p.get("pattern_days", "")
    direction = p.get("direction", "")
    
    sentiment_label, sentiment_class = _direction_to_sentiment(direction)
    card_class = sentiment_class if sentiment_class else ""
    
    # Countdown display
    if days_until == 0:
        countdown = "TODAY"
        countdown_class = "today"
    elif days_until == 1:
        countdown = "Tomorrow"
        countdown_class = ""
    elif days_until is not None and days_until > 0:
        countdown = f"{days_until} days"
        countdown_class = ""
    else:
        countdown = "Active"
        countdown_class = ""
    
    direction_html = ""
    if SHOW_BADGES and sentiment_label:
        direction_html = f'<span class="radar-card-direction {sentiment_class}">{sentiment_label}</span>'
    
    return f'''
                <a href="{url}" class="radar-card {card_class}">
                    <div class="radar-card-header">
                        <span class="radar-card-symbol">{symbol}</span>
                        <span class="radar-card-countdown {countdown_class}">{countdown}</span>
                    </div>
                    <h3 class="radar-card-title">{title}</h3>
                    <div class="radar-card-meta">
                        {direction_html}
                        <span>{days}-Day Pattern</span>
                    </div>
                </a>'''


def _build_radar_template(items, t):
    """Build the radar template HTML."""
    if not items:
        return '<section class="radar-container"><p class="no-articles">No patterns on the radar.</p></section>'
    
    # Group by time proximity
    today_patterns = []
    week_patterns = []
    later_patterns = []
    
    for p in items:
        url = p.get("url", "")
        pattern_date = _extract_pattern_date_from_url(url)
        days_until = _get_days_until_pattern(pattern_date) if pattern_date else None
        
        p["_days_until"] = days_until
        
        if days_until is not None:
            if days_until <= 0:
                today_patterns.append(p)
            elif days_until <= 7:
                week_patterns.append(p)
            else:
                later_patterns.append(p)
        else:
            later_patterns.append(p)
    
    # Sort each group
    today_patterns.sort(key=lambda x: x.get("_days_until", 999))
    week_patterns.sort(key=lambda x: x.get("_days_until", 999))
    later_patterns.sort(key=lambda x: x.get("_days_until", 999))
    
    sections_html = ""
    
    # Today / Active section
    if today_patterns:
        cards_html = "\n".join(_build_radar_card(p, p.get("_days_until", 0)) for p in today_patterns)
        sections_html += f'''
        <div class="time-section">
            <div class="time-section-header">
                <div class="time-section-dot today"></div>
                <span class="time-section-title">Activating Now</span>
                <span class="time-section-count">{len(today_patterns)} pattern{"s" if len(today_patterns) != 1 else ""}</span>
                <div class="time-section-line"></div>
            </div>
            <div class="radar-grid">
{cards_html}
            </div>
        </div>'''
    
    # This week section
    if week_patterns:
        cards_html = "\n".join(_build_radar_card(p, p.get("_days_until", 0)) for p in week_patterns)
        sections_html += f'''
        <div class="time-section">
            <div class="time-section-header">
                <div class="time-section-dot week"></div>
                <span class="time-section-title">This Week</span>
                <span class="time-section-count">{len(week_patterns)} pattern{"s" if len(week_patterns) != 1 else ""}</span>
                <div class="time-section-line"></div>
            </div>
            <div class="radar-grid">
{cards_html}
            </div>
        </div>'''
    
    # Later section
    if later_patterns:
        cards_html = "\n".join(_build_radar_card(p, p.get("_days_until")) for p in later_patterns)
        sections_html += f'''
        <div class="time-section">
            <div class="time-section-header">
                <div class="time-section-dot later"></div>
                <span class="time-section-title">Coming Up</span>
                <span class="time-section-count">{len(later_patterns)} pattern{"s" if len(later_patterns) != 1 else ""}</span>
                <div class="time-section-line"></div>
            </div>
            <div class="radar-grid">
{cards_html}
            </div>
        </div>'''
    
    return f'''
    <section class="radar-container">
        <div class="radar-header">
            <div class="radar-title">Pattern Radar</div>
            <div class="radar-subtitle">Seasonal patterns organized by time to activation</div>
        </div>
        
        <div class="ring-labels">
            <div class="ring-label">
                <span class="ring-indicator today"></span>
                <span class="ring-label-text">Active Now</span>
            </div>
            <div class="ring-label">
                <span class="ring-indicator week"></span>
                <span class="ring-label-text">This Week</span>
            </div>
            <div class="ring-label">
                <span class="ring-indicator later"></span>
                <span class="ring-label-text">Coming Up</span>
            </div>
        </div>
        
        <div class="radar-visualization">
{sections_html}
        </div>
    </section>
'''


# =============================================================================
# TEMPLATE 6: DEFAULT - Featured Article + Grid Layout (Original)
# =============================================================================

def _get_default_template_css():
    """Return CSS specific to default template."""
    return '''
        /* Default Template - Featured Section */
        .featured-section {
            max-width: 1200px;
            margin: 0 auto;
            padding: 32px 24px;
            border-bottom: 1px solid var(--border-color);
        }

        .featured-section .section-header {
            margin-bottom: 20px;
        }

        .featured-article {
            display: flex;
            gap: 28px;
            text-decoration: none;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            overflow: hidden;
            transition: border-color 0.2s ease, box-shadow 0.2s ease;
            box-shadow: var(--card-shadow);
        }

        .featured-article:hover {
            border-color: var(--accent-blue);
            box-shadow: var(--card-hover-shadow);
        }

        .featured-image {
            flex: 0 0 45%;
            max-width: 500px;
            background: var(--bg-tertiary);
        }

        .featured-image img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            display: block;
        }

        .featured-content {
            flex: 1;
            padding: 28px;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }

        .featured-tag {
            display: inline-block;
            padding: 4px 10px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-radius: 4px;
            margin-bottom: 14px;
            width: fit-content;
        }

        .featured-article h2 {
            font-size: 28px;
            font-weight: 700;
            color: var(--text-primary);
            margin-bottom: 10px;
            line-height: 1.25;
            letter-spacing: -0.5px;
        }

        .featured-meta {
            font-size: 13px;
            color: var(--text-muted);
            margin-bottom: 14px;
            font-family: 'IBM Plex Mono', monospace;
        }

        .featured-excerpt {
            font-size: 16px;
            color: var(--text-secondary);
            line-height: 1.6;
            margin-bottom: 16px;
        }

        .featured-read-more {
            display: inline-block;
            font-size: 14px;
            font-weight: 600;
            color: var(--accent-blue);
        }

        /* Default Template - Articles Grid */
        .articles-section {
            max-width: 1200px;
            margin: 0 auto;
            padding: 40px 24px;
        }

        .articles-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
            gap: 20px;
        }

        .article-card {
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 20px;
            transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
            text-decoration: none;
            display: block;
            box-shadow: var(--card-shadow);
        }

        .article-card:hover {
            border-color: var(--accent-blue);
            box-shadow: var(--card-hover-shadow);
            transform: translateY(-2px);
        }

        .article-tag {
            display: inline-block;
            padding: 3px 8px;
            font-size: 10px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-radius: 3px;
            margin-bottom: 10px;
        }

        .article-card h3 {
            font-size: 17px;
            font-weight: 600;
            color: var(--text-primary);
            margin-bottom: 6px;
            line-height: 1.35;
        }

        .article-meta {
            font-size: 12px;
            color: var(--text-muted);
            margin-bottom: 10px;
            font-family: 'IBM Plex Mono', monospace;
        }

        .article-excerpt {
            font-size: 14px;
            color: var(--text-secondary);
            line-height: 1.55;
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .read-more {
            display: inline-block;
            margin-top: 12px;
            font-size: 13px;
            font-weight: 600;
            color: var(--accent-blue);
        }

        /* Default Template - Responsive */
        @media (max-width: 768px) {
            .articles-grid {
                grid-template-columns: 1fr;
            }

            .section-header {
                flex-direction: column;
                gap: 8px;
                text-align: center;
            }

            .featured-article {
                flex-direction: column;
                gap: 0;
            }

            .featured-image {
                flex: none;
                max-width: 100%;
                height: 200px;
            }

            .featured-content {
                padding: 20px;
            }

            .featured-article h2 {
                font-size: 22px;
            }

            .featured-excerpt {
                font-size: 15px;
            }
        }
'''


def _build_default_template(items, t):
    """Build the default template HTML."""
    
    featured_section_html = ""
    grid_items = items
    
    if items:
        featured_article = items[0]
        featured_article_html = _build_featured_article(featured_article)
        featured_section_html = f'''
    <!-- Featured Article -->
    <section class="featured-section">
        <div class="section-header">
            <span class="section-title">Featured Analysis</span>
        </div>
{featured_article_html}
    </section>
'''
        grid_items = items[1:]

    cards_html = "\n".join(_build_article_card(p) for p in grid_items) if grid_items else '<p class="no-articles">No articles yet.</p>'

    articles_section = f'''
    <!-- Articles Section -->
    <section class="articles-section" id="articles">
        <div class="section-header">
            <span class="section-title">Latest Research</span>
        </div>

        <div class="articles-grid">
{cards_html}
        </div>
    </section>
'''

    return featured_section_html + articles_section


# =============================================================================
# TEMPLATE 7: BENZINGA - Hero + Secondary Cards + List (Original)
# =============================================================================

def _get_benzinga_template_css():
    """Return CSS specific to benzinga template."""
    return '''
        /* Benzinga Template - Hero Featured Article */
        .featured-section {
            max-width: 1200px;
            margin: 0 auto;
            padding: 32px 24px 24px;
        }

        .featured-article {
            display: block;
            text-decoration: none;
            position: relative;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: var(--card-shadow);
        }

        .featured-article:hover {
            box-shadow: var(--card-hover-shadow);
        }

        .featured-image {
            width: 100%;
            height: 400px;
            background: var(--bg-tertiary);
        }

        .featured-image img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            display: block;
        }

        .featured-content {
            position: absolute;
            bottom: 0;
            left: 0;
            right: 0;
            padding: 32px;
            background: linear-gradient(transparent, rgba(0,0,0,0.85));
            color: white;
        }

        .featured-tag {
            display: inline-block;
            padding: 4px 10px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-radius: 4px;
            margin-bottom: 12px;
        }

        .featured-tag.bullish {
            background: rgba(48, 209, 88, 0.9);
            color: white;
        }

        .featured-tag.bearish {
            background: rgba(255, 69, 58, 0.9);
            color: white;
        }

        .featured-article h2 {
            font-size: 32px;
            font-weight: 700;
            color: white;
            margin-bottom: 10px;
            line-height: 1.2;
            letter-spacing: -0.5px;
        }

        .featured-meta {
            font-size: 13px;
            color: rgba(255,255,255,0.8);
            margin-bottom: 12px;
            font-family: 'IBM Plex Mono', monospace;
        }

        .featured-excerpt {
            font-size: 15px;
            color: rgba(255,255,255,0.9);
            line-height: 1.5;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .featured-read-more {
            display: none;
        }

        /* Benzinga Template - Secondary Cards Grid */
        .secondary-section {
            max-width: 1200px;
            margin: 0 auto;
            padding: 24px;
            border-bottom: 1px solid var(--border-color);
        }

        .secondary-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 20px;
        }

        .secondary-card {
            display: block;
            text-decoration: none;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            overflow: hidden;
            transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
            box-shadow: var(--card-shadow);
        }

        .secondary-card:hover {
            border-color: var(--accent-blue);
            box-shadow: var(--card-hover-shadow);
            transform: translateY(-2px);
        }

        .secondary-image {
            width: 100%;
            height: 140px;
            background: var(--bg-tertiary);
            overflow: hidden;
        }

        .secondary-image img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }

        .secondary-image .no-image {
            width: 100%;
            height: 100%;
            background: var(--bg-tertiary);
        }

        .secondary-content {
            padding: 14px;
        }

        .secondary-tag {
            display: inline-block;
            padding: 2px 6px;
            font-size: 9px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-radius: 3px;
            margin-bottom: 8px;
        }

        .secondary-card h3 {
            font-size: 14px;
            font-weight: 600;
            color: var(--text-primary);
            margin-bottom: 6px;
            line-height: 1.35;
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .secondary-meta {
            font-size: 11px;
            color: var(--text-muted);
            font-family: 'IBM Plex Mono', monospace;
        }

        /* Benzinga Template - Recent News List */
        .list-section {
            max-width: 1200px;
            margin: 0 auto;
            padding: 32px 24px;
        }

        .list-section .section-header {
            margin-bottom: 20px;
        }

        .news-list {
            display: flex;
            flex-direction: column;
            gap: 16px;
        }

        .list-item {
            display: flex;
            gap: 16px;
            text-decoration: none;
            padding: 16px;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            transition: border-color 0.2s ease, box-shadow 0.2s ease;
            box-shadow: var(--card-shadow);
        }

        .list-item:hover {
            border-color: var(--accent-blue);
            box-shadow: var(--card-hover-shadow);
        }

        .list-image {
            flex: 0 0 120px;
            height: 80px;
            background: var(--bg-tertiary);
            border-radius: 6px;
            overflow: hidden;
        }

        .list-image img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }

        .list-image .no-image {
            width: 100%;
            height: 100%;
            background: var(--bg-tertiary);
        }

        .list-content {
            flex: 1;
            min-width: 0;
        }

        .list-header {
            display: flex;
            align-items: flex-start;
            gap: 10px;
            margin-bottom: 6px;
        }

        .list-tag {
            flex-shrink: 0;
            padding: 2px 6px;
            font-size: 9px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-radius: 3px;
            margin-top: 3px;
        }

        .list-content h4 {
            font-size: 16px;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.35;
            margin: 0;
        }

        .list-excerpt {
            font-size: 13px;
            color: var(--text-secondary);
            line-height: 1.5;
            margin: 6px 0;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .list-meta {
            font-size: 11px;
            color: var(--text-muted);
            font-family: 'IBM Plex Mono', monospace;
        }

        /* Benzinga Template - Responsive */
        @media (max-width: 1024px) {
            .secondary-grid {
                grid-template-columns: repeat(2, 1fr);
            }
        }

        @media (max-width: 768px) {
            .featured-image {
                height: 250px;
            }

            .featured-content {
                padding: 20px;
            }

            .featured-article h2 {
                font-size: 22px;
            }

            .secondary-grid {
                grid-template-columns: 1fr;
            }

            .secondary-image {
                height: 180px;
            }

            .list-item {
                flex-direction: column;
            }

            .list-image {
                flex: none;
                width: 100%;
                height: 150px;
            }
        }
'''


def _build_benzinga_template(items, t):
    """Build the benzinga-style template HTML."""
    
    if not items:
        return '<section class="list-section"><p class="no-articles">No articles yet.</p></section>'
    
    # 1. Hero - First article as large featured
    featured_article = items[0]
    featured_html = _build_featured_article(featured_article)
    featured_section = f'''
    <!-- Featured Article -->
    <section class="featured-section">
{featured_html}
    </section>
'''
    
    # 2. Secondary grid - Next 4 articles
    secondary_items = items[1:5]
    secondary_cards = "\n".join(_build_secondary_card(p) for p in secondary_items) if secondary_items else ''
    secondary_section = ""
    if secondary_cards:
        secondary_section = f'''
    <!-- Secondary Articles -->
    <section class="secondary-section">
        <div class="secondary-grid">
{secondary_cards}
        </div>
    </section>
'''
    
    # 3. Recent news list - Remaining articles
    list_items = items[5:]
    list_html = "\n".join(_build_list_item(p) for p in list_items) if list_items else ''
    list_section = ""
    if list_html:
        list_section = f'''
    <!-- Recent News -->
    <section class="list-section" id="articles">
        <div class="section-header">
            <span class="section-title">Recent News</span>
        </div>
        <div class="news-list">
{list_html}
        </div>
    </section>
'''

    return featured_section + secondary_section + list_section


# =============================================================================
# SHARED HTML COMPONENTS
# =============================================================================

MARKET_BAR_TICKERS = [
    {"label": "S&P 500", "symbol": "GSPC", "exchange": "INDX", "slug": "sp500"},
    {"label": "DOW",     "symbol": "DJI",  "exchange": "INDX", "slug": "dow"},
    {"label": "NASDAQ",  "symbol": "IXIC", "exchange": "INDX", "slug": "nasdaq"},
    {"label": "VIX",     "symbol": "VIX",  "exchange": "INDX", "slug": "vix"},
    {"label": "CRUDE",   "symbol": "CL",   "exchange": "COMM", "slug": "crude-oil"},
    {"label": "NAT GAS", "symbol": "NG",   "exchange": "COMM", "slug": "natural-gas"},
    {"label": "GOLD",    "symbol": "GC",   "exchange": "COMM", "slug": "gold"},
]

def _get_market_bar_html():
    """Return market bar HTML with live prices (realtime service, EODHD fallback)."""
    tickers = list(MARKET_BAR_TICKERS)

    items_html = ""
    for t in tickers:
        quote = get_quote_details(t["symbol"], t["exchange"])
        try:
            close_val = quote.get("close") if quote else None
            _price = float(close_val) if close_val not in (None, "NA", "N/A", "") else None
        except (ValueError, TypeError):
            _price = None
        if _price is not None:
            price     = _price
            try:
                change_p  = float(quote.get("change_p") or 0)
            except (ValueError, TypeError):
                change_p = 0
            direction = "up" if change_p >= 0 else "down"
            sign      = "+" if change_p >= 0 else ""
            price_fmt = f"{price:,.2f}"
            chg_fmt   = f"{sign}{change_p:.2f}%"
        else:
            price_fmt = "—"
            chg_fmt   = ""
            direction = "flat"

        slug = t.get("slug", "")
        href = f'/markets/{slug}.html' if slug else '#'
        items_html += f'''
            <a href="{href}" class="market-item {direction}">
                <span class="market-symbol">{t["label"]}</span>
                <span class="market-price">{price_fmt}</span>
                {"<span class='market-change " + direction + "'>" + chg_fmt + "</span>" if chg_fmt else ""}
            </a>'''

    return f'''
    <div class="market-bar">
        <div class="market-bar-content">
            {items_html}
        </div>
    </div>
'''


def _get_header_html():
    """Return header HTML."""
    return '''
    <!-- Header -->
    <header>
        <div class="header-content">
            <a href="./" class="logo">
                <span class="logo-seasonal">Seasonal</span><span class="logo-market">Market</span><span class="logo-news">News</span>
            </a>
            <div class="header-right">
                <form class="header-search" action="search.html" method="get">
                    <input type="text" name="q" placeholder="Search symbols, topics..." class="header-search-input">
                    <button type="submit" class="header-search-btn">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <circle cx="11" cy="11" r="8"></circle>
                            <path d="m21 21-4.35-4.35"></path>
                        </svg>
                    </button>
                </form>
                <nav>
                    <a href="https://tradewave.ai" target="_blank">TradeWave</a>
                </nav>
            </div>
        </div>
    </header>
'''


def _get_hero_section_html():
    """Return hero section HTML with email form."""
    return f'''
    <!-- Hero Section -->
    <section class="hero">
        <h1>AI-Powered <span>Seasonal</span> Market Intelligence</h1>
        <p class="hero-subtitle">{TAGLINE}</p>
        
        <form class="email-form" id="emailForm">
            <div class="email-row">
                <input type="email" id="emailInput" class="email-input" placeholder="Enter your email" required autocomplete="email">
                <button type="submit" class="submit-btn">Get Updates</button>
            </div>
            <div class="group-row">
                <div class="group-checkboxes">
                    <label class="group-label">
                        <input type="checkbox" id="chkDaily" value="1"> Daily Digest
                    </label>
                    <label class="group-label">
                        <input type="checkbox" id="chkWeekly" value="1"> Weekly Summary
                    </label>
                </div>
                <span class="info-icon" id="infoIcon">i
                    <span class="info-tooltip">To update or pause your subscription, click the preferences link in any email we send you.</span>
                </span>
            </div>
            <div class="group-error" id="groupError">Please select Daily Digest and/or Weekly Summary.</div>
        </form>

        <div class="success-message" id="successMessage">
            Thanks! Please check your email to confirm your subscription.
        </div>
    </section>
'''


def _get_footer_html(displayed_count, total_count):
    """Return footer HTML."""
    if displayed_count < total_count:
        count_text = f"{displayed_count} of {total_count} articles"
    else:
        count_text = f"{displayed_count} articles"
    
    return f'''
    <!-- Footer -->
    <footer>
        <div class="footer-content">
            <div class="footer-left">
                © {datetime.now().year} <a href="https://taradataresearch.com" target="_blank">Tara Data Research LLC</a>. All rights reserved.
            </div>
            <div class="footer-links">
                <a href="https://tradewave.ai" target="_blank">TradeWave</a>
            </div>
            <div class="footer-generated">
                Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} • {count_text}
            </div>
        </div>
    </footer>
'''


def _get_email_script_html(ml_groups):
    """Return email form script with embedded MailerLite group IDs."""
    smn_id        = ml_groups.get('SMN', '')
    smn_daily_id  = ml_groups.get('SMN-DAILY', '')
    smn_weekly_id = ml_groups.get('SMN-WEEKLY', '')
    return f'''
    <script>
        // Info icon toggle
        document.getElementById('infoIcon').addEventListener('click', function(e) {{
            e.stopPropagation();
            this.classList.toggle('open');
        }});
        document.addEventListener('click', function() {{
            document.getElementById('infoIcon').classList.remove('open');
        }});

        document.getElementById('emailForm').addEventListener('submit', function(e) {{
            e.preventDefault();

            const email  = document.getElementById('emailInput').value.trim();
            const daily  = document.getElementById('chkDaily').checked;
            const weekly = document.getElementById('chkWeekly').checked;
            const errorEl = document.getElementById('groupError');

            if (!daily && !weekly) {{
                errorEl.style.display = 'block';
                return;
            }}
            errorEl.style.display = 'none';

            const form = this;
            const btn  = form.querySelector('.submit-btn');
            btn.disabled    = true;
            btn.textContent = 'Subscribing\u2026';

            const formData = new FormData();
            formData.append('fields[email]', email);
            formData.append('ml-submit', '1');
            formData.append('anticsrf', 'true');
            if ('{smn_id}')        formData.append('groups[]', '{smn_id}');
            if (daily  && '{smn_daily_id}')  formData.append('groups[]', '{smn_daily_id}');
            if (weekly && '{smn_weekly_id}') formData.append('groups[]', '{smn_weekly_id}');

            fetch('{MAILERLITE_FORM_URL}', {{
                method: 'POST',
                body: formData,
                mode: 'no-cors'
            }}).then(() => {{
                form.style.display = 'none';
                document.getElementById('successMessage').classList.add('show');
            }}).catch(() => {{
                form.style.display = 'none';
                document.getElementById('successMessage').classList.add('show');
            }});
        }});
    </script>
'''


# =============================================================================
# MAIN BUILD FUNCTION
# =============================================================================

def build_home():
    """Build the home page HTML and write to index.html."""

    from seo_helpers import ga_snippet, og_tags, twitter_tags, organization_jsonld, website_jsonld
    seo_head = ''
    if config.seo_enabled:
        _title = f'{SITE_TITLE} | AI-Powered Market Intelligence'
        _desc = 'Daily, data-backed coverage of repeating market patterns. Institutional-grade seasonal analysis powered by TradeWave AI.'
        _url = config.news_website_url.rstrip('/')
        seo_head = '\n    '.join(filter(None, [
            og_tags(_title, _desc, _url + '/', og_type='website', image=config.smn_og_image),
            twitter_tags(_title, _desc, image=config.smn_og_image),
            organization_jsonld(),
            website_jsonld(),
            ga_snippet(),
        ]))

    # Get theme colors
    t = THEMES.get(THEME, THEMES["light"])
    
    # posts.json is the canonical published-article catalog. Prefer it so stale
    # Redis metadata cannot override repaired URLs or collision-safe hero names.
    all_items = _load_articles_from_json(limit=99)
    if not all_items:
        all_items = _load_articles_from_redis(limit=99)

    # Interleave by market_family so same-type articles don't cluster together
    all_items = _interleave_by_family(all_items)

    # Apply display filters (age and count limits)
    items = _filter_articles_for_display(all_items)

    # Fetch MailerLite group IDs for the signup form (server-side, token never exposed)
    ml_groups = _get_ml_group_ids()

    # Template-specific CSS and content
    template_map = {
        # New professional news-style templates
        "wire": (_get_wire_template_css, _build_wire_template),
        "pulse": (_get_pulse_template_css, _build_pulse_template),
        "flagship": (_get_flagship_template_css, _build_flagship_template),
        "mosaic": (_get_mosaic_template_css, _build_mosaic_template),
        "spotlight": (_get_spotlight_template_css, _build_spotlight_template),
        # Original functional templates
        "calendar": (_get_calendar_template_css, _build_calendar_template),
        "terminal": (_get_terminal_template_css, _build_terminal_template),
        "broadsheet": (_get_broadsheet_template_css, _build_broadsheet_template),
        "dashboard": (_get_dashboard_template_css, _build_dashboard_template),
        "radar": (_get_radar_template_css, _build_radar_template),
        "default": (_get_default_template_css, _build_default_template),
        "benzinga": (_get_benzinga_template_css, _build_benzinga_template),
    }
    
    css_func, build_func = template_map.get(TEMPLATE, template_map["default"])
    template_css = css_func()
    content_html = build_func(items, t)
    
    # Body class for template-specific styling
    body_class = ""
    if TEMPLATE == "terminal":
        body_class = ' class="terminal-mode"'
    elif TEMPLATE == "broadsheet":
        body_class = ' class="broadsheet-mode"'
    
    # Extra fonts for broadsheet
    extra_fonts = ""
    if TEMPLATE == "broadsheet":
        extra_fonts = '<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700&display=swap" rel="stylesheet">'

    # Build full HTML
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Cache-Control" content="no-cache, must-revalidate">
    <link rel="icon" type="image/png" href="{config.smn_favicon}">
    <title>{SITE_TITLE} | AI-Powered Market Intelligence</title>
    <meta name="description" content="Daily, data-backed coverage of repeating market patterns. Institutional-grade seasonal analysis powered by TradeWave AI.">
    <meta name="robots" content="index, follow">
    <link rel="canonical" href="{config.news_website_url.rstrip('/')}/">
    {seo_head}
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
    {extra_fonts}
    <style>
{_get_base_css(t)}
{template_css}
    </style>
</head>
<body{body_class}>
{_get_market_bar_html()}
{_get_header_html()}
{_get_hero_section_html()}
{content_html}
{_get_footer_html(len(items), len(all_items))}
{_get_email_script_html(ml_groups)}
</body>
</html>
'''

    INDEX_HTML.write_text(html, "utf-8")

    # Sync security page prices with the market bar
    try:
        from generate_security_pages import inject_security_prices
        inject_security_prices()
    except Exception as e:
        print(f"[HOME] Security price sync skipped: {e}")

    # Sync TradeWave dark security page prices
    try:
        from generate_tw_security_pages import inject_tw_security_prices
        inject_tw_security_prices()
    except Exception as e:
        print(f"[HOME] TW security price sync skipped: {e}")

    return {"wrote": str(INDEX_HTML), "count": len(items), "total": len(all_items), "template": TEMPLATE, "theme": THEME}


if __name__ == "__main__":
    out = build_home()
    print(out)
