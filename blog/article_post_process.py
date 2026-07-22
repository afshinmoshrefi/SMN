# post processing of an article takes output of the AI that writes the article and 

import json
import re
from pathlib import Path
from article_prompt import get_opp_data
from blog_tools import get_company_name
from article_tools import compute_article_paths_and_url
import difflib
from typing import List, Dict, Any, Optional
import sys
sys.path.insert(0, '/home/flask')
import config
from datetime import date as _date
import seo_helpers

# Related articles functionality
from related_articles import select_related_articles
from html_injection import (
    inject_related_articles_html, 
    strip_existing_related_articles, 
    generate_related_articles_schema
)
RELATED_ARTICLES_ENABLED = True

MAILERLITE_FORM_URL = "https://assets.mailerlite.com/jsonp/489451/forms/173861813170996648/subscribe"

# ---------- SMN site wrapper (market bar, header, footer) ----------

_SITE_WRAPPER_MARKER = '<!-- smn-site-wrapper -->'

def _get_ml_group_ids_cached():
    """Fetch MailerLite group IDs (cached in module)."""
    if not hasattr(_get_ml_group_ids_cached, '_cache'):
        try:
            import requests as _req
            headers = {
                'Authorization': f'Bearer {config.mailerlite_token}',
                'Accept': 'application/json',
            }
            r = _req.get('https://connect.mailerlite.com/api/groups?limit=100',
                         headers=headers, timeout=10)
            result = {}
            for g in r.json().get('data', []):
                if g['name'] in ('SMN', 'SMN-DAILY', 'SMN-WEEKLY'):
                    result[g['name']] = g['id']
            _get_ml_group_ids_cached._cache = result
        except Exception:
            _get_ml_group_ids_cached._cache = {}
    return _get_ml_group_ids_cached._cache


def _site_chrome_css():
    """Return CSS for the site wrapper elements (header, market bar, footer, CTA)."""
    return '''
    /* ── SMN Site Chrome ────────────────────────────────── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

    .smn-chrome {
        --bg-primary: #ffffff;
        --bg-secondary: #f8f9fa;
        --text-primary: #1a1a1a;
        --text-secondary: #4a4a4a;
        --text-muted: #6c757d;
        --accent-blue: #0066cc;
        --accent-green: #0d7a3e;
        --accent-red: #c41e3a;
        --border-color: #dee2e6;
    }

    .smn-chrome * {
        box-sizing: border-box;
    }

    .smn-chrome a {
        text-decoration: none;
    }

    /* Market Bar */
    .smn-market-bar {
        background: var(--bg-secondary);
        border-bottom: 1px solid var(--border-color);
        padding: 10px 0;
        overflow-x: auto;
        font-family: 'Inter', -apple-system, sans-serif;
    }

    .smn-market-bar-content {
        max-width: 1200px;
        margin: 0 auto;
        padding: 0 24px;
        display: flex;
        gap: 24px;
        align-items: center;
        justify-content: center;
    }

    .smn-market-item {
        display: flex;
        align-items: center;
        gap: 8px;
        font-family: 'IBM Plex Mono', monospace;
        font-size: 13px;
        white-space: nowrap;
        padding: 4px 0;
        border-top: 2px solid transparent;
        border-bottom: 2px solid transparent;
        text-decoration: none;
        transition: opacity 0.2s ease;
    }

    .smn-market-item:hover { opacity: 0.75; }

    .smn-market-item.up {
        border-image: linear-gradient(90deg, transparent, var(--accent-green), transparent) 1;
    }

    .smn-market-item.down {
        border-image: linear-gradient(90deg, transparent, var(--accent-red), transparent) 1;
    }

    .smn-market-symbol {
        color: var(--text-primary);
        font-weight: 700;
    }

    .smn-market-price {
        color: var(--text-secondary);
        font-weight: 700;
    }

    .smn-market-change { font-weight: 700; }
    .smn-market-change.up { color: var(--accent-green); }
    .smn-market-change.down { color: var(--accent-red); }

    /* Header */
    .smn-header {
        border-bottom: 1px solid var(--border-color);
        background: var(--bg-primary);
        font-family: 'Inter', -apple-system, sans-serif;
    }

    .smn-header-content {
        max-width: 1200px;
        margin: 0 auto;
        padding: 14px 24px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }

    .smn-logo {
        display: flex;
        align-items: baseline;
        gap: 2px;
        text-decoration: none;
    }

    .smn-logo-seasonal {
        font-size: 20px;
        font-weight: 700;
        color: var(--accent-blue);
        letter-spacing: -0.5px;
    }

    .smn-logo-market {
        font-size: 20px;
        font-weight: 700;
        color: var(--text-primary);
        letter-spacing: -0.5px;
    }

    .smn-logo-news {
        font-size: 20px;
        font-weight: 400;
        color: var(--text-muted);
        letter-spacing: -0.5px;
    }

    .smn-header-nav {
        display: flex;
        align-items: center;
        gap: 20px;
    }

    .smn-header-nav a {
        font-size: 14px;
        color: var(--text-secondary);
        font-weight: 500;
        transition: color 0.2s;
    }

    .smn-header-nav a:hover {
        color: var(--accent-blue);
    }

    /* Back to home link */
    .smn-breadcrumb {
        max-width: 860px;
        margin: 0 auto;
        padding: 12px 20px 0;
        font-family: 'Inter', -apple-system, sans-serif;
        font-size: 13px;
    }

    .smn-breadcrumb a {
        color: var(--accent-blue);
        text-decoration: none;
    }

    .smn-breadcrumb a:hover {
        text-decoration: underline;
    }

    .smn-breadcrumb span {
        color: var(--text-muted);
    }

    /* Article CTA */
    .smn-article-cta {
        max-width: 860px;
        margin: 32px auto 0;
        padding: 28px 24px;
        background: linear-gradient(135deg, #f0f4ff 0%, #f8f9fa 100%);
        border: 1px solid var(--border-color);
        border-radius: 8px;
        text-align: center;
        font-family: 'Inter', -apple-system, sans-serif;
    }

    .smn-article-cta h3 {
        font-size: 18px;
        font-weight: 700;
        color: var(--text-primary);
        margin: 0 0 6px;
    }

    .smn-article-cta p {
        font-size: 14px;
        color: var(--text-secondary);
        margin: 0 0 16px;
        line-height: 1.5;
    }

    .smn-cta-row {
        display: flex;
        justify-content: center;
        align-items: center;
        gap: 10px;
        flex-wrap: wrap;
    }

    .smn-cta-email {
        padding: 10px 16px;
        border: 1px solid var(--border-color);
        border-radius: 6px;
        font-size: 14px;
        width: 240px;
        outline: none;
        font-family: 'Inter', -apple-system, sans-serif;
    }

    .smn-cta-email:focus {
        border-color: var(--accent-blue);
    }

    .smn-cta-btn {
        padding: 10px 20px;
        background: var(--accent-blue);
        color: #fff;
        border: none;
        border-radius: 6px;
        font-size: 14px;
        font-weight: 600;
        cursor: pointer;
        font-family: 'Inter', -apple-system, sans-serif;
        transition: background 0.2s;
    }

    .smn-cta-btn:hover {
        background: #0052a3;
    }

    .smn-cta-groups {
        display: flex;
        justify-content: center;
        gap: 16px;
        margin-top: 10px;
        font-size: 13px;
        color: var(--text-secondary);
    }

    .smn-cta-groups label {
        display: flex;
        align-items: center;
        gap: 4px;
        cursor: pointer;
    }

    .smn-cta-error {
        color: var(--accent-red);
        font-size: 12px;
        margin-top: 6px;
        display: none;
    }

    .smn-cta-success {
        color: var(--accent-green);
        font-weight: 600;
        font-size: 14px;
        display: none;
    }

    /* Share bar */
    .smn-share {
        display: flex;
        align-items: center;
        justify-content: center;
        flex-wrap: wrap;
        gap: 8px;
        padding: 18px 24px 6px;
        margin: 32px auto 0;
        max-width: 760px;
        border-top: 1px solid var(--border-color);
        font-family: 'Inter', -apple-system, sans-serif;
    }

    .smn-share-label {
        font-size: 13px;
        font-weight: 600;
        color: var(--text-secondary);
        margin-right: 4px;
    }

    .smn-share-btn {
        display: inline-block;
        padding: 7px 14px;
        border: 1px solid var(--border-color);
        border-radius: 999px;
        background: var(--bg-secondary);
        font-size: 13px;
        font-weight: 600;
        line-height: 1.2;
        color: var(--text-primary);
        text-decoration: none;
        cursor: pointer;
        font-family: 'Inter', -apple-system, sans-serif;
        transition: border-color 0.2s, color 0.2s;
    }

    .smn-share-btn:hover {
        border-color: var(--accent-blue);
        color: var(--accent-blue);
    }

    .smn-share-toast {
        flex-basis: 100%;
        text-align: center;
        font-size: 12px;
        font-weight: 600;
        color: var(--accent-green);
        min-height: 16px;
        opacity: 0;
        transition: opacity 0.3s;
    }

    /* Compact top variant: right-aligned in the breadcrumb band, CNBC-style */
    .smn-share--top {
        justify-content: flex-end;
        border-top: none;
        max-width: 860px;
        margin: 0 auto;
        padding: 6px 20px 0;
    }

    .smn-share--top .smn-share-btn {
        padding: 5px 11px;
        font-size: 12px;
    }

    .smn-share--top .smn-share-label {
        font-size: 12px;
    }

    .smn-share--top .smn-share-toast {
        min-height: 0;
        text-align: right;
    }

    /* Footer */
    .smn-footer {
        border-top: 1px solid var(--border-color);
        padding: 24px;
        background: var(--bg-secondary);
        margin-top: 40px;
        font-family: 'Inter', -apple-system, sans-serif;
    }

    .smn-footer-content {
        max-width: 1200px;
        margin: 0 auto;
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
        gap: 12px;
    }

    .smn-footer-left {
        font-size: 13px;
        color: var(--text-muted);
    }

    .smn-footer-left a {
        color: var(--text-muted);
        transition: color 0.2s;
    }

    .smn-footer-left a:hover {
        color: var(--text-secondary);
    }

    .smn-footer-links {
        display: flex;
        gap: 20px;
    }

    .smn-footer-links a {
        font-size: 13px;
        color: var(--text-muted);
        transition: color 0.2s;
    }

    .smn-footer-links a:hover {
        color: var(--text-secondary);
    }

    /* Responsive */
    @media (max-width: 768px) {
        .smn-market-bar {
            -webkit-overflow-scrolling: touch;
            scrollbar-width: none;
        }
        .smn-market-bar::-webkit-scrollbar { display: none; }
        .smn-market-bar-content {
            justify-content: flex-start;
            padding: 0 16px;
            gap: 16px;
        }
        .smn-header-nav a:not(:last-child) { display: none; }
        .smn-footer-content { flex-direction: column; text-align: center; }
        .smn-cta-row { flex-direction: column; align-items: center; }
        .smn-cta-email { width: 100%; max-width: 300px; }
    }
'''


def _site_market_bar_html():
    """Build market bar with JS that loads live prices from /assets/quotes.json.

    The JSON file is written by update_news_quotes.py every minute during market hours.
    This avoids having to inject into hundreds of article HTML files.
    """
    tickers = [
        {"short": "S&P 500", "symbol": "GSPC", "slug": "sp500"},
        {"short": "DOW",     "symbol": "DJI",  "slug": "dow"},
        {"short": "NASDAQ",  "symbol": "IXIC", "slug": "nasdaq"},
        {"short": "VIX",     "symbol": "VIX",  "slug": "vix"},
        {"short": "CRUDE",   "symbol": "CL",   "slug": "crude-oil"},
        {"short": "NAT GAS", "symbol": "NG",   "slug": "natural-gas"},
        {"short": "GOLD",    "symbol": "GC",   "slug": "gold"},
    ]

    items = ""
    for t in tickers:
        items += f'''
            <a href="/markets/{t['slug']}.html" class="smn-market-item flat" data-symbol="{t['symbol']}">
                <span class="smn-market-symbol">{t['short']}</span>
                <span class="smn-market-price">--</span>
            </a>'''

    return f'''
    <div class="smn-market-bar">
        <div class="smn-market-bar-content">
            {items}
        </div>
    </div>
    <script>
    (function() {{
        fetch('/assets/quotes.json?' + Date.now())
        .then(function(r) {{ return r.json(); }})
        .then(function(q) {{
            document.querySelectorAll('.smn-market-item').forEach(function(el) {{
                var sym = el.getAttribute('data-symbol');
                var d = q[sym];
                if (!d) return;
                var price = parseFloat(d.close);
                var chg = parseFloat(d.change_p);
                if (isNaN(price)) return;
                var dir = chg >= 0 ? 'up' : 'down';
                var sign = chg >= 0 ? '+' : '';
                el.className = 'smn-market-item ' + dir;
                el.querySelector('.smn-market-price').textContent = price.toLocaleString('en-US', {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
                var chgSpan = el.querySelector('.smn-market-change');
                if (!chgSpan) {{
                    chgSpan = document.createElement('span');
                    chgSpan.className = 'smn-market-change';
                    el.appendChild(chgSpan);
                }}
                chgSpan.className = 'smn-market-change ' + dir;
                chgSpan.textContent = sign + chg.toFixed(2) + '%';
            }});
        }}).catch(function() {{}});
    }})();
    </script>'''


def _site_header_html():
    """Return the SMN site header."""
    news_url = getattr(config, 'news_website_url', '').rstrip('/')
    home_href = f"{news_url}/" if news_url else "/"
    return f'''
    <div class="smn-header">
        <div class="smn-header-content">
            <a href="{home_href}" class="smn-logo">
                <span class="smn-logo-seasonal">Seasonal</span><span class="smn-logo-market">Market</span><span class="smn-logo-news">News</span>
            </a>
            <div class="smn-header-nav">
                <a href="{home_href}">Home</a>
                <a href="https://tradewave.ai" target="_blank">TradeWave</a>
            </div>
        </div>
    </div>'''


def _site_breadcrumb_html(symbol, title):
    """Return breadcrumb navigation."""
    news_url = getattr(config, 'news_website_url', '').rstrip('/')
    home_href = f"{news_url}/" if news_url else "/"
    short_title = title[:60] + "..." if len(title) > 60 else title
    return f'''
    <div class="smn-breadcrumb">
        <a href="{home_href}">Home</a> <span>/</span> <span>{short_title}</span>
    </div>'''


def _site_cta_html():
    """Return the email signup CTA for articles."""
    ml = _get_ml_group_ids_cached()
    smn_id = ml.get('SMN', '')
    smn_daily_id = ml.get('SMN-DAILY', '')
    smn_weekly_id = ml.get('SMN-WEEKLY', '')

    return f'''
    <div class="smn-article-cta">
        <h3>Get Daily Market Intelligence</h3>
        <p>AI-powered seasonal analysis delivered to your inbox. Free, no spam.</p>
        <form id="smnCtaForm" onsubmit="return false;">
            <div class="smn-cta-row">
                <input type="email" id="smnCtaEmail" class="smn-cta-email" placeholder="Enter your email" required autocomplete="email">
                <button type="submit" class="smn-cta-btn" id="smnCtaBtn">Subscribe</button>
            </div>
            <div class="smn-cta-groups">
                <label><input type="checkbox" id="smnChkDaily" checked> Daily Digest</label>
                <label><input type="checkbox" id="smnChkWeekly"> Weekly Summary</label>
            </div>
            <div class="smn-cta-error" id="smnCtaError">Please select at least one option.</div>
        </form>
        <div class="smn-cta-success" id="smnCtaSuccess">Thanks! Check your email to confirm.</div>
    </div>
    <script>
    (function() {{
        var form = document.getElementById('smnCtaForm');
        if (!form) return;
        form.addEventListener('submit', function(e) {{
            e.preventDefault();
            var email = document.getElementById('smnCtaEmail').value.trim();
            var daily = document.getElementById('smnChkDaily').checked;
            var weekly = document.getElementById('smnChkWeekly').checked;
            var err = document.getElementById('smnCtaError');
            if (!daily && !weekly) {{ err.style.display = 'block'; return; }}
            err.style.display = 'none';
            var btn = document.getElementById('smnCtaBtn');
            btn.disabled = true;
            btn.textContent = 'Subscribing...';
            var fd = new FormData();
            fd.append('fields[email]', email);
            fd.append('ml-submit', '1');
            fd.append('anticsrf', 'true');
            if ('{smn_id}') fd.append('groups[]', '{smn_id}');
            if (daily && '{smn_daily_id}') fd.append('groups[]', '{smn_daily_id}');
            if (weekly && '{smn_weekly_id}') fd.append('groups[]', '{smn_weekly_id}');
            fetch('{MAILERLITE_FORM_URL}', {{ method: 'POST', body: fd, mode: 'no-cors' }})
            .then(function() {{
                form.style.display = 'none';
                document.getElementById('smnCtaSuccess').style.display = 'block';
            }}).catch(function() {{
                form.style.display = 'none';
                document.getElementById('smnCtaSuccess').style.display = 'block';
            }});
        }});
    }})();
    </script>'''


def _site_share_html(symbol, title, article_url, compact=False):
    """
    Return a social share bar for article pages.

    X / StockTwits / LinkedIn open pre-filled compose windows via share-intent
    URLs baked server-side (works without JS). Substack has no compose URL, so
    that button copies the link and opens Notes; the pasted link unfurls via
    the page's existing OG tags. Copy-link uses the Clipboard API.

    compact=True renders the smaller top-of-article variant (breadcrumb row,
    right-aligned, no LinkedIn) and omits the shared click-handler script,
    which is emitted once with the full bottom bar and binds both by class.
    """
    from urllib.parse import quote
    import html as _htmlmod

    clean_title = _htmlmod.unescape(title or '').strip()
    sym = (symbol or '').strip().upper()
    cashtag = f"${sym}" if re.fullmatch(r'[A-Z.]{1,10}', sym) else ''

    x_text  = f"{clean_title} {cashtag}".strip()
    st_body = f"{cashtag} — {clean_title} {article_url}" if cashtag else f"{clean_title} {article_url}"

    x_href  = f"https://x.com/intent/post?text={quote(x_text, safe='')}&url={quote(article_url, safe='')}"
    st_href = f"https://api.stocktwits.com/widgets/share?body={quote(st_body, safe='')}"
    li_href = f"https://www.linkedin.com/sharing/share-offsite/?url={quote(article_url, safe='')}"
    fb_href = f"https://www.facebook.com/sharer/sharer.php?u={quote(article_url, safe='')}"
    em_href = (f"mailto:?subject={quote(clean_title, safe='')}"
               f"&body={quote(clean_title, safe='')}%0A%0A{quote(article_url, safe='')}")
    esc_url   = _htmlmod.escape(article_url, quote=True)
    esc_title = _htmlmod.escape(clean_title, quote=True)

    variant = ' smn-share--top' if compact else ''
    label   = 'Share:' if compact else 'Share this analysis:'
    # The compact top row stays at four items; the extended set (LinkedIn,
    # Facebook, Email, native mobile share) lives only in the bottom bar.
    extra_btns = '' if compact else (
        f'\n        <a class="smn-share-btn" href="{li_href}" target="_blank" rel="noopener noreferrer">LinkedIn</a>'
        f'\n        <a class="smn-share-btn" href="{fb_href}" target="_blank" rel="noopener noreferrer">Facebook</a>'
        f'\n        <a class="smn-share-btn" href="{em_href}">Email</a>'
    )
    native_btn = '' if compact else (
        '\n        <button type="button" class="smn-share-btn smn-share-native" style="display:none;">More&hellip;</button>'
    )

    bar = f'''
    <div class="smn-share{variant}" data-share-url="{esc_url}" data-share-title="{esc_title}">
        <span class="smn-share-label">{label}</span>
        <a class="smn-share-btn" href="{x_href}" target="_blank" rel="noopener noreferrer">X</a>
        <a class="smn-share-btn" href="{st_href}" target="_blank" rel="noopener noreferrer">StockTwits</a>
        <button type="button" class="smn-share-btn smn-share-substack">Substack</button>{extra_btns}
        <button type="button" class="smn-share-btn smn-share-copy">Copy link</button>{native_btn}
        <span class="smn-share-toast" role="status"></span>
    </div>'''

    if compact:
        return bar

    return bar + '''
    <script>
    (function() {
        function bindShare(bar) {
            var url = bar.getAttribute('data-share-url') || window.location.href;
            var toast = bar.querySelector('.smn-share-toast');
            var timer = null;
            function copyLink(msg) {
                function done() {
                    if (!toast) return;
                    toast.textContent = msg;
                    toast.style.opacity = '1';
                    clearTimeout(timer);
                    timer = setTimeout(function() { toast.style.opacity = '0'; }, 4000);
                }
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(url).then(done, done);
                } else {
                    var ta = document.createElement('textarea');
                    ta.value = url;
                    document.body.appendChild(ta);
                    ta.select();
                    try { document.execCommand('copy'); } catch (e) {}
                    document.body.removeChild(ta);
                    done();
                }
            }
            var copyBtn = bar.querySelector('.smn-share-copy');
            if (copyBtn) copyBtn.addEventListener('click', function() {
                copyLink('Link copied');
            });
            var ssBtn = bar.querySelector('.smn-share-substack');
            if (ssBtn) ssBtn.addEventListener('click', function() {
                copyLink('Link copied \\u2014 paste it into your Substack Note or post');
                window.open('https://substack.com/notes', '_blank', 'noopener');
            });
            var nativeBtn = bar.querySelector('.smn-share-native');
            if (nativeBtn && navigator.share) {
                nativeBtn.style.display = 'inline-block';
                nativeBtn.addEventListener('click', function() {
                    navigator.share({
                        title: bar.getAttribute('data-share-title') || document.title,
                        url: url
                    }).catch(function() {});
                });
            }
        }
        var bars = document.querySelectorAll('.smn-share');
        for (var i = 0; i < bars.length; i++) bindShare(bars[i]);
    })();
    </script>'''


def _site_footer_html():
    """Return the SMN site footer."""
    from datetime import datetime as _dt
    year = _dt.now().year
    return f'''
    <div class="smn-footer">
        <div class="smn-footer-content">
            <div class="smn-footer-left">
                &copy; {year} <a href="https://taradataresearch.com" target="_blank">Tara Data Research LLC</a>. All rights reserved.
            </div>
            <div class="smn-footer-links">
                <a href="https://tradewave.ai" target="_blank">TradeWave</a>
            </div>
        </div>
    </div>'''


def _strip_site_wrapper(html):
    """Remove existing site wrapper (CSS + top chrome + bottom chrome) so it can be re-injected.

    Uses the two marker comments as exact boundaries to avoid greedy regex issues.
    """
    marker = _SITE_WRAPPER_MARKER  # '<!-- smn-site-wrapper -->'

    # Remove the chrome CSS block from <head>
    html = re.sub(
        r'<style>\s*/\* .{0,5} SMN Site Chrome .{0,50}\*/.*?</style>\s*',
        '',
        html,
        flags=re.DOTALL,
        count=1
    )

    # The wrapper has exactly two markers:
    #   first marker ... top chrome ... (article content) ... bottom chrome ... second marker
    # Remove everything from first marker to the line after top chrome ends,
    # and from bottom chrome start to second marker.

    first = html.find(marker)
    second = html.rfind(marker)

    if first == -1 or second == -1 or first == second:
        return html

    # Remove bottom chrome: from just before the bottom <div class="smn-chrome"> to end of second marker
    # Find the bottom chrome start - it's the last <div class="smn-chrome"> before the second marker
    bottom_start = html.rfind('<div class="smn-chrome">', first + len(marker), second)
    if bottom_start != -1:
        end_of_second = second + len(marker)
        pre, post = html[:bottom_start], html[end_of_second:]
        # consume the newline padding added at injection so repeated
        # strip/re-inject cycles don't accumulate blank lines
        if pre.endswith('\n'):
            pre = pre[:-1]
        if post.startswith('\n'):
            post = post[1:]
        html = pre + post

    # Remove top chrome: from first marker to end of top chrome </div>
    # Top chrome ends with </div> (closing smn-chrome) followed by a newline before the article content
    first = html.find(marker)
    if first != -1:
        # Find the closing </div> of the top smn-chrome div
        # The top chrome structure is: marker + <div class="smn-chrome"> ... </div>
        # We need to find where this top smn-chrome div closes
        chrome_start = html.find('<div class="smn-chrome">', first)
        if chrome_start != -1:
            # Count div nesting to find the matching close
            depth = 0
            i = chrome_start
            while i < len(html):
                if html[i:i+4] == '<div':
                    depth += 1
                elif html[i:i+6] == '</div>':
                    depth -= 1
                    if depth == 0:
                        end_of_top = i + 6
                        # Remove from first marker through end of top chrome,
                        # consuming the injection's newline padding as above
                        pre, post = html[:first], html[end_of_top:]
                        if pre.endswith('\n'):
                            pre = pre[:-1]
                        if post.startswith('\n'):
                            post = post[1:]
                        html = pre + post
                        break
                i += 1

    return html


def _inject_site_wrapper(html, symbol, title, force=False):
    """
    Wrap article body content with SMN site chrome:
    market bar, header, breadcrumb (above) and email CTA, footer (below).
    Idempotent: skips if wrapper already present (unless force=True).
    """
    if _SITE_WRAPPER_MARKER in html:
        if not force:
            print("[WRAPPER] Site wrapper already present, skipping")
            return html
        # Strip old wrapper before re-injecting
        html = _strip_site_wrapper(html)

    # 1) Inject site chrome CSS into <head>
    chrome_css = f"<style>{_site_chrome_css()}</style>\n"
    idx_head_close = html.lower().find("</head>")
    if idx_head_close != -1:
        html = html[:idx_head_close] + chrome_css + html[idx_head_close:]

    # Share bars need the article's public URL and clean title; both live in
    # <head> by this point (canonical/og:url injected at step 9, og:title clean
    # of the <title> site suffix). Skip the bars if no URL is found.
    url_m = (re.search(r'<link rel="canonical" href="([^"]+)"', html)
             or re.search(r'<meta property="og:url" content="([^"]+)"', html))
    ogt_m = re.search(r'<meta property="og:title" content="([^"]*)"', html)
    share_title = ogt_m.group(1) if ogt_m else (title or symbol)
    share_html = _site_share_html(symbol, share_title, url_m.group(1)) if url_m else ''
    share_top  = _site_share_html(symbol, share_title, url_m.group(1), compact=True) if url_m else ''

    # 2) Build the wrapper HTML
    top_chrome = f'''{_SITE_WRAPPER_MARKER}
<div class="smn-chrome">
{_site_market_bar_html()}
{_site_header_html()}
{_site_breadcrumb_html(symbol, title or symbol)}
{share_top}
</div>'''

    bottom_chrome = f'''<div class="smn-chrome">
{share_html}
{_site_cta_html()}
{_site_footer_html()}
</div>
{_SITE_WRAPPER_MARKER}'''

    # 3) Insert top chrome right after <body> tag
    body_match = re.search(r'<body[^>]*>', html, re.IGNORECASE)
    if body_match:
        insert_pos = body_match.end()
        html = html[:insert_pos] + "\n" + top_chrome + "\n" + html[insert_pos:]

    # 4) Insert bottom chrome before </body>
    idx_body_close = html.lower().rfind("</body>")
    if idx_body_close != -1:
        html = html[:idx_body_close] + "\n" + bottom_chrome + "\n" + html[idx_body_close:]

    print("[WRAPPER] Injected SMN site wrapper (market bar, header, breadcrumb, share bar, email CTA, footer)")
    return html


# ---------- Helpers to make post-process idempotent ----------

_DATASET_LD_RE = re.compile(
    r'<script\s+type="application/ld\+json">\s*(\{.*?\})\s*</script>',
    re.IGNORECASE | re.DOTALL
)

# Asset type mapping based on exchange_mapping
def _get_subject_schema(resource_id: str, symbol: str) -> dict:
    """
    Build schema.org structured data for the article subject (company, ETF, commodity, etc.)
    Returns appropriate schema type based on resource_id.
    """
    exchange_type = config.exchange_mapping.get(str(resource_id), "US")
    company_name = get_company_name(resource_id, symbol) or symbol
    
    # US stocks and foreign stocks -> Corporation
    if exchange_type in ("US", "LSE", "TO", "KO", "KQ"):
        return {
            "@context": "https://schema.org",
            "@type": "Corporation",
            "name": company_name,
            "tickerSymbol": symbol
        }
    
    # ETFs -> InvestmentFund
    elif exchange_type == "ETF":
        return {
            "@context": "https://schema.org",
            "@type": "InvestmentFund",
            "name": company_name,
            "tickerSymbol": symbol
        }
    
    # Indices, commodities, forex, bonds, crypto -> Thing
    else:
        return {
            "@context": "https://schema.org",
            "@type": "Thing",
            "name": company_name,
            "identifier": symbol
        }


#---------------------------------------------------------------------------------------
def _strip_existing_related_articles_schema(html: str) -> str:
    """
    Remove existing related articles ItemList schema from <head>.
    Makes schema injection idempotent.
    """
    # Much simpler pattern: find any script tag with ItemList in it
    # Split, check each script tag individually
    result = html
    while True:
        # Find a script tag with type="application/ld+json"
        match = re.search(
            r'(<script\s+type=["\']application/ld\+json["\'][^>]*>)(.*?)(</script>)',
            result,
            re.DOTALL | re.IGNORECASE
        )
        if not match:
            break
        
        # Check if this script contains ItemList
        script_content = match.group(2)
        if '"@type"' in script_content and '"ItemList"' in script_content:
            # Remove this entire script tag
            result = result[:match.start()] + result[match.end():]
        else:
            # Skip this one, look for next starting after this one
            # Replace it temporarily so we don't match it again
            placeholder = f"<!--KEEPSCHEMA{match.start()}-->"
            result = result[:match.start()] + placeholder + result[match.end():]
    
    # Restore the kept schemas
    result = re.sub(r'<!--KEEPSCHEMA\d+-->', lambda m: html[html.find('<script type="application/ld+json">', html.find(m.group(0))):html.find('</script>', html.find(m.group(0))) + 9], result)
    
    # Simpler approach: just remove all ItemList schemas
    pattern = r'<script\s+type=["\']application/ld\+json["\'][^>]*>\s*\{[^}]*"@type"\s*:\s*"ItemList".*?</script>\s*'
    cleaned = re.sub(pattern, '', html, flags=re.DOTALL | re.IGNORECASE)
    
    return cleaned


def _strip_existing_dataset_ld(html: str) -> str:
    """
    Remove any <script type="application/ld+json"> blocks whose @type is "Dataset".
    Leave NewsArticle and any other JSON-LD untouched.
    """
    def repl(m):
        body = m.group(1)
        try:
            data = json.loads(body)
        except Exception:
            # If it is not parseable JSON, leave it alone
            return m.group(0)

        t = data.get("@type")
        if t == "Dataset" or (isinstance(t, list) and "Dataset" in t):
            # Strip this block
            return ""
        return m.group(0)

    return _DATASET_LD_RE.sub(repl, html)


_OG_TWITTER_META_RE = re.compile(
    r'\s*<meta\b[^>]+(?:property="og:[^"]*"|name="twitter:[^"]*")[^>]*>\s*',
    re.IGNORECASE
)

_CANONICAL_TAG_RE = re.compile(
    r'\s*<link\s+rel="canonical"[^>]*>\s*',
    re.IGNORECASE
)

_FAVICON_TAG_RE = re.compile(
    r'\s*<link\s+rel=["\'](?:icon|shortcut icon)["\'][^>]*>\s*',
    re.IGNORECASE
)

#---------------------------------------------------------------------------------------
def _strip_existing_favicon(html: str) -> str:
    """
    Remove existing favicon link tags so we can inject a clean one.
    """
    return _FAVICON_TAG_RE.sub("\n", html)

#---------------------------------------------------------------------------------------
def _strip_existing_og_twitter(html: str) -> str:
    """
    Remove all og:* and twitter:* meta tags AND canonical link tags
    so that we can re-inject a clean, single set of tags.
    """
    html = _OG_TWITTER_META_RE.sub("\n", html)
    html = _CANONICAL_TAG_RE.sub("\n", html)
    return html

#---------------------------------------------------------------------------------------
def _strip_existing_faqpage_ld(html: str) -> str:
    """Remove any FAQPage JSON-LD blocks for idempotency."""
    def repl(m):
        body = m.group(1)
        try:
            data = json.loads(body)
        except Exception:
            return m.group(0)
        if data.get("@type") == "FAQPage":
            return ""
        return m.group(0)
    return _DATASET_LD_RE.sub(repl, html)


#---------------------------------------------------------------------------------------
def _build_faqpage_ld(html: str) -> Optional[dict]:
    """
    Extract question H2s and their first answer paragraph from article HTML.
    Returns a FAQPage JSON-LD dict, or None if fewer than 2 Q&A pairs are found.
    """
    def strip_tags(text: str) -> str:
        text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    pairs = []
    h2_re = re.compile(r'<h2[^>]*>(.*?)</h2>', re.DOTALL | re.IGNORECASE)
    p_re  = re.compile(r'<p[^>]*>(.*?)</p>',   re.DOTALL | re.IGNORECASE)

    for h2_match in h2_re.finditer(html):
        q_text = strip_tags(h2_match.group(1)).strip()
        if not q_text.endswith('?'):
            continue

        # Find first non-empty <p> after this h2
        answer_text = ''
        for p_match in p_re.finditer(html, h2_match.end()):
            candidate = strip_tags(p_match.group(1)).strip()
            if len(candidate) >= 30:
                answer_text = candidate[:500]
                break

        if not answer_text:
            continue

        pairs.append({
            "@type": "Question",
            "name": q_text,
            "acceptedAnswer": {
                "@type": "Answer",
                "text": answer_text,
            }
        })

        if len(pairs) >= 4:  # cap at 4 Q&A pairs
            break

    if len(pairs) < 2:
        return None

    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": pairs,
    }


#---------------------------------------------------------------------------------------
def load_articles_catalog(catalog_path: str) -> List[Dict[str, Any]]:
    """
    Load the articles catalog JSON file.
    
    This is the single source of truth for all published articles.
    Used by related articles to find connections.
    
    Args:
        catalog_path: Path to posts.json catalog file
    
    Returns:
        List of article dicts
    """
    try:
        with open(catalog_path, 'r', encoding='utf-8') as f:
            articles = json.load(f)
        print(f"[INFO] Loaded {len(articles)} articles from catalog")
        return articles
    except FileNotFoundError:
        print(f"[WARNING] Catalog not found at {catalog_path}")
        return []
    except Exception as e:
        print(f"[ERROR] Failed to load catalog: {e}")
        return []

#---------------------------------------------------------------------------------------
def article_post_process(
    resource_id, 
    symbol, 
    date, 
    days, 
    years, 
    zero_last_year, 
    article_html,
    enable_related_articles: bool = True,
    articles_catalog_path: str = None,
    volume_csv_dir: str = None,
):
    """
    Post-process a GPT-generated article HTML:
      - Recompute cdata via get_opp_data
      - Build dataset JSON with per-year net/MFE/MAE
      - Write dataset JSON to the correct /news/datasets/... path
      - Inject Dataset JSON-LD into <head>
      - Inject OpenGraph + Twitter Card meta tags into <head>
      - (NEW) Compute and inject related articles HTML and schema
      
    Idempotent: re-running this will NOT accumulate duplicate tags/schema.
    
    Args:
        resource_id: Pattern resource ID
        symbol: Ticker symbol
        date: Pattern start date
        days: Trading days in pattern
        years: Lookback years
        zero_last_year: Whether to zero last year
        article_html: Original HTML from AI
        enable_related_articles: Whether to compute and inject related articles
        articles_catalog_path: Path to posts.json catalog (defaults to config)
        volume_csv_dir: Path to volume CSV files directory (defaults to config)
    
    Returns:
        Processed HTML string
    """
    # Resolve config paths if not provided
    if articles_catalog_path is None:
        articles_catalog_path = f"{config.news_root_folder}/posts.json"
    
    if volume_csv_dir is None:
        volume_csv_dir = getattr(config, 'volume_csv_dir', '/home/flask/blog/volume_lists')
    
    print("starting post process")

    # 0) Compute canonical article + dataset paths/URLs (no IO here)
    info = compute_article_paths_and_url(
        resource_id=resource_id,
        symbol=symbol,
        pattern_start_date=date,
        days=days,
        years=years,
        article_html=article_html,
    )

    article_url      = info["full_url"]
    dataset_dir      = Path(info["dataset_dir"])
    dataset_path     = Path(info["dataset_path"])
    dataset_url      = info["dataset_full_url"]

    # Ensure dataset directory exists
    dataset_dir.mkdir(parents=True, exist_ok=True)

    # 1) get structured data from appserver (single source of truth)
    cdata = get_opp_data(resource_id, date, symbol, days, years, zero_last_year)
    chart_rows = cdata["ChartData4"]
    stats = cdata["stats"]

    trade_dir = stats["Trade Dir"].strip().lower()  # "long" or "short"

    # 2) build per-year list from ChartData4
    years_data = []
    year_values = []

    for row in chart_rows:
        year = row["year"]
        pct_str = row["pct"]  # "net,pos,neg"
        net_str, pos_str, neg_str = pct_str.split(",")

        net = float(net_str.replace(",", ""))
        pos = float(pos_str.replace(",", ""))
        neg = float(neg_str.replace(",", ""))

        if trade_dir == "short":
            # bearish: MFE is 3rd, MAE is 2nd
            mfe = neg
            mae = pos
        else:
            # bullish: MFE is 2nd, MAE is 3rd
            mfe = pos
            mae = neg

        years_data.append(
            {
                "year": year,
                "net_return_pct": net,
                "mfe_pct": mfe,
                "mae_pct": mae,
            }
        )
        year_values.append(year)

    temporal_coverage = None
    if year_values:
        temporal_coverage = f"{min(year_values)}/{max(year_values)}"

    # 3) build top level stats dict
    # percent fields are guaranteed to end with '%', so slice last char; if that ever changes, everything is broken anyway
    percent_profitable = float(stats["Percent Profitable"].replace(",", "").rstrip("%"))
    avg_profit         = float(stats["Avg Profit"].replace(",", "").rstrip("%"))
    avg_profit_all     = float(stats["Avg Profit - All"].replace(",", "").rstrip("%"))
    median_profit      = float(stats["Median Profit"].replace(",", "").rstrip("%"))
    cumulative_return  = float(stats["Cumulative Return"].replace(",", "").rstrip("%"))
    annualized_return  = float(stats["Annualized Return"].replace(",", "").rstrip("%"))

    num_winners = int(stats["Num Winners"])
    num_losers = int(stats["Num Losers"])

    sharpe_ratio = float(stats["Sharpe Ratio"])  # no percent sign

    # When every year is a winner (no losers), "Avg Profit - All" is by definition equal to
    # "Avg Profit" -- the Key-Stats aside even states this and omits the separate row. The
    # upstream stat is sometimes a differently-rounded whole number (e.g. 6.58 vs 7.0), which
    # contradicts the aside; force exact equality so the Dataset JSON-LD stays consistent.
    if num_losers == 0 and avg_profit_all != avg_profit:
        print(f"[FIX] all-winners: avg_profit_all {avg_profit_all} -> {avg_profit}")
        avg_profit_all = avg_profit

    stats_payload = {
        "percent_profitable": percent_profitable,
        "num_winners": num_winners,
        "num_losers": num_losers,
        "avg_profit": avg_profit,
        "avg_profit_all": avg_profit_all,
        "median_profit": median_profit,
        "cumulative_return": cumulative_return,
        "annualized_return": annualized_return,
        "sharpe_ratio": sharpe_ratio,
        "tradewave_ratio": None,
    }

    # 4) dataset payload (this is what you dump to *_dataset.json)
    dataset = {
        "symbol": symbol,
        "resource_id": resource_id,
        "pattern_start_date": date,
        "window_calendar_days": days,
        "lookback": years,
        "trade_direction": trade_dir,
        "stats": stats_payload,
        "years": years_data,
        "temporal_coverage": temporal_coverage,
    }

    # 5) write dataset json to the canonical /news/datasets/... path
    print("dataset_dir,dataset_path=",dataset_dir,dataset_path)

    # Make sure the datasets folder exists
    dataset_dir.mkdir(parents=True, exist_ok=True)

    # 6) write dataset JSON to the canonical path
    with dataset_path.open("w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    print(f"[DATASET] wrote dataset json: {dataset_path}")

    # 7) URLs for JSON-LD / OG
    article_url = info["full_url"]               # canonical article URL
    dataset_url = info["dataset_full_url"]       # canonical dataset URL

    # 8) build Dataset JSON-LD (SEO schema) using real URLs
    description = (
        f"Historical seasonal performance statistics for {symbol} over a {years} lookback window, "
        f"including win rate, average profit, Sharpe ratio and per-year net returns with MFE and MAE."
    )

    variable_measured = [
        {"@type": "PropertyValue", "name": "Symbol", "value": symbol},
        {"@type": "PropertyValue", "name": "Trade direction", "value": trade_dir},
        {"@type": "PropertyValue", "name": "Window (calendar days)", "value": str(days)},
        {"@type": "PropertyValue", "name": "Lookback", "value": str(years)},
        {"@type": "PropertyValue", "name": "Percent profitable", "value": str(percent_profitable)},
        {"@type": "PropertyValue", "name": "Num winners", "value": str(num_winners)},
        {"@type": "PropertyValue", "name": "Num losers", "value": str(num_losers)},
        {"@type": "PropertyValue", "name": "Average profit", "value": str(avg_profit)},
        {"@type": "PropertyValue", "name": "Average profit - all years", "value": str(avg_profit_all)},
        {"@type": "PropertyValue", "name": "Median profit", "value": str(median_profit)},
        {"@type": "PropertyValue", "name": "Sharpe ratio", "value": str(sharpe_ratio)},
    ]

    dataset_ld = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": f"Seasonal performance dataset for {symbol}",
        "description": description,
        "url": article_url,
        "creator": {
            "@type": "Organization",
            "name": "TradeWave",
            "url": "https://tradewave.ai/",
        },
        "variableMeasured": variable_measured,
        "distribution": [
            {
                "@type": "DataDownload",
                "name": f"TradeWave seasonal dataset for {symbol}",
                "encodingFormat": "application/json",
                "contentUrl": dataset_url,
            }
        ],
    }

    if temporal_coverage:
        dataset_ld["temporalCoverage"] = temporal_coverage

    # ------------------------------------------------------------------
    # 9) Start from original HTML, strip old OG/Twitter + old Dataset LD + old related articles + old favicon
    # ------------------------------------------------------------------
    html_out = article_html
    html_out = _strip_existing_dataset_ld(html_out)
    html_out = _strip_existing_faqpage_ld(html_out)
    html_out = _strip_existing_og_twitter(html_out)
    html_out = _strip_existing_related_articles_schema(html_out)
    html_out = _strip_existing_favicon(html_out)
    
    # Strip old related articles section (makes this idempotent)
    if RELATED_ARTICLES_ENABLED:
        html_out = strip_existing_related_articles(html_out)
    
    #---------------------------------------------------------------------------------------
    # minimal helpers to pull title and description from the existing HTML head
    def _extract_between(h, start_tag, end_tag):
        start = h.find(start_tag)
        if start == -1:
            return None
        start += len(start_tag)
        end = h.find(end_tag, start)
        if end == -1:
            return None
        return h[start:end]

    title = _extract_between(html_out, "<title>", "</title>") or ""
    desc = ""
    desc_marker = 'name="description" content="'
    idx_desc = html_out.find(desc_marker)
    if idx_desc != -1:
        start = idx_desc + len(desc_marker)
        end = html_out.find('"', start)
        if end != -1:
            desc = html_out[start:end]

    # hero image src (for og:image / twitter:image)
    hero_src = None
    hero_marker = '<figure class="hero"><img src="'
    idx_img = html_out.find(hero_marker)
    if idx_img != -1:
        start = idx_img + len(hero_marker)
        end = html_out.find('"', start)
        if end != -1:
            hero_src = html_out[start:end]

    # build the OG + Twitter block
    og_twitter_block = []
    
    # Canonical URL (prevents duplicate content issues)
    og_twitter_block.append(f'<link rel="canonical" href="{article_url}">')

    og_twitter_block.append('<meta property="og:locale" content="en_US">')
    og_twitter_block.append('<meta property="og:type" content="article">')
    if title:
        og_twitter_block.append(f'<meta property="og:title" content="{title}">')
        og_twitter_block.append(f'<meta name="twitter:title" content="{title}">')
    if desc:
        og_twitter_block.append(f'<meta property="og:description" content="{desc}">')
        og_twitter_block.append(f'<meta name="twitter:description" content="{desc}">')
    og_twitter_block.append(f'<meta property="og:url" content="{article_url}">')
    og_twitter_block.append('<meta property="og:site_name" content="Seasonal Market News">')
    og_twitter_block.append('<meta name="twitter:card" content="summary_large_image">')
    if hero_src:
        og_twitter_block.append(f'<meta property="og:image" content="{hero_src}">')
        og_twitter_block.append(f'<meta name="twitter:image" content="{hero_src}">')

    # Additional article OG tags when SEO is enabled
    if getattr(config, 'seo_enabled', False):
        pub_date = _date.today().isoformat()
        og_twitter_block.append(f'<meta property="article:published_time" content="{pub_date}">')
        og_twitter_block.append(f'<meta property="article:modified_time" content="{pub_date}">')
        og_twitter_block.append('<meta property="article:author" content="Seasonal Market News">')
        og_twitter_block.append('<meta property="article:section" content="Market Analysis">')
        og_twitter_block.append(f'<meta property="article:tag" content="{symbol}">')
        og_twitter_block.append('<meta property="og:image:width" content="1280">')
        og_twitter_block.append('<meta property="og:image:height" content="720">')

    og_twitter_str = "\n  ".join(og_twitter_block) + "\n"

    # inject OG/Twitter right after the viewport meta if present, otherwise after <head>
    viewport_marker = '<meta name="viewport" content="width=device-width,initial-scale=1">'
    idx_viewport = html_out.find(viewport_marker)
    if idx_viewport != -1:
        insert_pos = idx_viewport + len(viewport_marker)
        html_out = html_out[:insert_pos] + "\n  " + og_twitter_str + html_out[insert_pos:]
        print("[SEO] Injected canonical tag and OG/Twitter meta tags")
    else:
        head_idx = html_out.lower().find("<head>")
        if head_idx != -1:
            insert_pos = head_idx + len("<head>")
            html_out = html_out[:insert_pos] + "\n  " + og_twitter_str + html_out[insert_pos:]
            print("[SEO] Injected canonical tag and OG/Twitter meta tags")

    # ------------------------------------------------------------------
    # 9b) Inject favicon into <head>
    # ------------------------------------------------------------------
    favicon_tag = f'<link rel="icon" type="image/png" href="{config.smn_favicon}">\n  '
    
    idx_head_close = html_out.lower().find("</head>")
    if idx_head_close != -1:
        html_out = html_out[:idx_head_close] + favicon_tag + html_out[idx_head_close:]
        print(f"[SEO] Injected favicon: {config.smn_favicon}")

    # ------------------------------------------------------------------
    # 10) inject Dataset JSON-LD into <head> (now with old Dataset LD stripped)
    # ------------------------------------------------------------------
    idx_head_close = html_out.lower().find("</head>")
    if idx_head_close == -1:
        raise RuntimeError("No </head> tag found in article_html")

    ld_str = json.dumps(dataset_ld, ensure_ascii=False, indent=2)
    script_tag = f'<script type="application/ld+json">\n{ld_str}\n</script>\n'

    html_out = html_out[:idx_head_close] + script_tag + html_out[idx_head_close:]


    # ------------------------------------------------------------------
    # 10b) Inject subject (company/ETF/asset) schema into <head>
    # ------------------------------------------------------------------
    subject_schema = _get_subject_schema(resource_id, symbol)
    subject_ld_str = json.dumps(subject_schema, ensure_ascii=False, indent=2)
    subject_script_tag = f'<script type="application/ld+json">\n{subject_ld_str}\n</script>\n'
    
    idx_head_close = html_out.lower().find("</head>")
    if idx_head_close != -1:
        html_out = html_out[:idx_head_close] + subject_script_tag + html_out[idx_head_close:]
        print(f"[SEO] Injected subject schema ({subject_schema.get('@type')}: {symbol})")



    # ------------------------------------------------------------------
    # 11) NEW: Compute and inject related articles
    # ------------------------------------------------------------------
    if enable_related_articles and RELATED_ARTICLES_ENABLED:
        print("[RELATED] Computing related articles...")
        
        # Build base article dict for related articles function
        base_article = {
            "url": article_url,
            "symbol": symbol,
            "title": title,
            "dek": desc,
            "market_family": "US",  # TODO: determine from data if needed
            "published_date": "",  # Will be set when article is saved to catalog
            "pattern_start_date": date,
            "pattern_days": days,
            "lookback_years": str(years),
        }
        
        # Load articles catalog
        all_articles = load_articles_catalog(articles_catalog_path)
        
        if all_articles:
            # Compute related articles
            related_articles = select_related_articles(
                base_article,
                all_articles,
                max_related=6,
                volume_csv_dir=volume_csv_dir
            )
            
            print(f"[RELATED] Found {len(related_articles)} related articles")
            
            if related_articles:
                # Inject HTML
                html_out = inject_related_articles_html(html_out, related_articles)
                print("[RELATED] Injected related articles HTML")
                
                # Optionally inject related articles schema into <head>
                # (This is separate from the main article schema for now)
                related_schema = generate_related_articles_schema(article_url, related_articles)
                if related_schema:
                    idx_head_close = html_out.lower().find("</head>")
                    if idx_head_close != -1:
                        related_ld_str = json.dumps(related_schema, ensure_ascii=False, indent=2)
                        related_script_tag = f'<script type="application/ld+json">\n{related_ld_str}\n</script>\n'
                        html_out = html_out[:idx_head_close] + related_script_tag + html_out[idx_head_close:]
                        print("[RELATED] Injected related articles schema")
        else:
            print("[RELATED] No articles catalog found, skipping related articles")
    
    # ------------------------------------------------------------------
    # 12) Inject methodology link into meta section
    # ------------------------------------------------------------------
    # Simple check: look for methodology link in the meta section
    meta_pattern = r'<div\s+class=["\']meta["\'][^>]*>.*?</div>'
    meta_match = re.search(meta_pattern, html_out, re.DOTALL | re.IGNORECASE)
    
    if meta_match and 'methodology.html' in meta_match.group(0):
        print("[METHODOLOGY] Link already present, skipping injection")
    else:
        methodology_url = f"{config.news_website_url.rstrip('/')}/methodology.html"
        methodology_span = f'<span><a href="{methodology_url}">Methodology</a></span>'
        
        # Find the closing </div> of the meta section
        match = re.search(meta_pattern, html_out, re.DOTALL | re.IGNORECASE)
        
        if match:
            # Insert methodology link before the closing </div>
            html_out = html_out[:match.end() - 6] + '\n          ' + methodology_span + '\n        ' + html_out[match.end() - 6:]
            print("[METHODOLOGY] Injected methodology link into meta section")
        else:
            print("[WARNING] Could not find meta section to inject methodology link")
    
    # ------------------------------------------------------------------
    # 13) Inject BreadcrumbList JSON-LD when seo_enabled
    # ------------------------------------------------------------------
    if getattr(config, 'seo_enabled', False):
        site_url = config.news_website_url.rstrip('/')
        breadcrumb_items = [
            ("Home", site_url + "/"),
            ("Articles", site_url + "/articles/"),
            (title or symbol, None),
        ]
        breadcrumb_tag = seo_helpers.breadcrumb_jsonld(breadcrumb_items)
        if breadcrumb_tag:
            idx_head_close = html_out.lower().find("</head>")
            if idx_head_close != -1:
                html_out = html_out[:idx_head_close] + breadcrumb_tag + "\n" + html_out[idx_head_close:]
                print("[SEO] Injected BreadcrumbList JSON-LD")

    # ------------------------------------------------------------------
    # 13a) Inject RSS auto-discovery link tag
    # ------------------------------------------------------------------
    rss_url = f"{config.news_website_url.rstrip('/')}/rss.xml"
    rss_link_tag = f'<link rel="alternate" type="application/rss+xml" title="Seasonal Market News" href="{rss_url}">\n'
    idx_head_close = html_out.lower().find("</head>")
    if idx_head_close != -1:
        html_out = html_out[:idx_head_close] + rss_link_tag + html_out[idx_head_close:]
        print(f"[SEO] Injected RSS auto-discovery link")

    # ------------------------------------------------------------------
    # 13b) Inject Organization + WebSite JSON-LD (E-E-A-T signals)
    # ------------------------------------------------------------------
    org_tag  = seo_helpers.organization_jsonld()
    site_tag = seo_helpers.website_jsonld()
    for schema_tag in (org_tag, site_tag):
        if schema_tag:
            idx_head_close = html_out.lower().find("</head>")
            if idx_head_close != -1:
                html_out = html_out[:idx_head_close] + schema_tag + "\n" + html_out[idx_head_close:]
    if org_tag:
        print("[SEO] Injected Organization + WebSite JSON-LD")

    # ------------------------------------------------------------------
    # 13c) Inject FAQPage JSON-LD (question H2s + answer paragraphs)
    # ------------------------------------------------------------------
    faq_ld = _build_faqpage_ld(html_out)
    if faq_ld:
        faq_ld_str = json.dumps(faq_ld, ensure_ascii=False, indent=2)
        faq_script_tag = f'<script type="application/ld+json">\n{faq_ld_str}\n</script>\n'
        idx_head_close = html_out.lower().find("</head>")
        if idx_head_close != -1:
            html_out = html_out[:idx_head_close] + faq_script_tag + html_out[idx_head_close:]
            print(f"[SEO] Injected FAQPage JSON-LD ({len(faq_ld['mainEntity'])} Q&A pairs)")
    else:
        print("[SEO] FAQPage skipped: fewer than 2 question H2s found")

    # ------------------------------------------------------------------
    # 13d) Inject max-snippet robots meta (lets Google show full rich snippets)
    # ------------------------------------------------------------------
    if 'max-snippet' not in html_out:
        robots_meta = '<meta name="robots" content="max-snippet:-1, max-image-preview:large, max-video-preview:-1">\n'
        idx_head_close = html_out.lower().find("</head>")
        if idx_head_close != -1:
            html_out = html_out[:idx_head_close] + robots_meta + html_out[idx_head_close:]
            print("[SEO] Injected max-snippet robots meta")

    # ------------------------------------------------------------------
    # 14) Inject Google Analytics snippet when seo_enabled
    # ------------------------------------------------------------------
    if getattr(config, 'seo_enabled', False):
        ga_tag = seo_helpers.ga_snippet()
        if ga_tag:
            idx_head_close = html_out.lower().find("</head>")
            if idx_head_close != -1:
                html_out = html_out[:idx_head_close] + ga_tag + "\n" + html_out[idx_head_close:]
                print("[SEO] Injected Google Analytics snippet")

    # ------------------------------------------------------------------
    # 15) Inject SMN site wrapper (market bar, header, footer, email CTA)
    # ------------------------------------------------------------------
    html_out = _inject_site_wrapper(html_out, symbol, title)

    # ------------------------------------------------------------------
    # 16) Inject paywall assets when news_paywall_enabled is True
    # ------------------------------------------------------------------
    if getattr(config, 'news_paywall_enabled', False):
        try:
            from paywall import inject_paywall_into_html
            html_out = inject_paywall_into_html(html_out)
        except Exception as pw_err:
            print(f"[PAYWALL] Injection failed (non-fatal): {pw_err}")

    # ------------------------------------------------------------------
    # 17) Deterministic consistency repairs (no LLM)
    # ------------------------------------------------------------------
    # 17a) Author spelling (the LLM sometimes corrupts the templated name)
    if "Moshirefi" in html_out:
        html_out = html_out.replace("Moshirefi", "Moshrefi")
        print("[FIX] Corrected author spelling: Moshirefi -> Moshrefi")

    # 17b) Force the NewsArticle JSON-LD 'headline' to equal the visible <h1>.
    # The H1 is finalized (incl. SEO-title rewrite) by the time post-process runs;
    # the model-authored JSON-LD headline often diverges. Parse-and-reserialize the
    # ld+json block (never a blind string replace) so we cannot corrupt the JSON.
    try:
        _h1m = re.search(r'<h1[^>]*>(.*?)</h1>', html_out, re.S | re.I)
        if _h1m:
            _h1 = re.sub(r'<[^>]+>', '', _h1m.group(1)).strip()

            def _sync_headline(m):
                try:
                    obj = json.loads(m.group(1))
                except Exception:
                    return m.group(0)  # not valid JSON -> leave untouched
                items = obj if isinstance(obj, list) else [obj]
                changed = False
                for o in items:
                    if (isinstance(o, dict) and o.get('@type') == 'NewsArticle'
                            and 'headline' in o and o['headline'] != _h1):
                        o['headline'] = _h1
                        changed = True
                if not changed:
                    return m.group(0)
                print("[FIX] Synced NewsArticle JSON-LD headline to <h1>")
                return ('<script type="application/ld+json">\n'
                        + json.dumps(obj, ensure_ascii=False, indent=2) + '\n</script>')

            html_out = re.sub(r'<script type="application/ld\+json">\s*(\{.*?\}|\[.*?\])\s*</script>',
                              _sync_headline, html_out, flags=re.S)
    except Exception as _he:
        print(f"[FIX] headline sync skipped (non-fatal): {_he}")

    # 17c) Price-finalize: enforce the current price + intraday range against the
    # authoritative EODHD quote (use_realtime=False); inject a dated "Price as of" label.
    # The model is never the source of a price digit that reaches a reader.
    try:
        import datetime as _dt
        from get_price_eod import get_quote_details as _gqd
        def _fmtd(d):
            try: return _dt.datetime.strptime(d, '%Y-%m-%d').strftime('%b %-d, %Y')
            except Exception: return d
        _ex = config.exchange_mapping.get(str(resource_id), "US")
        _q = _gqd(symbol, _ex, use_realtime=False)
        if _q and _q.get('close') is not None:
            _close = float(_q['close']); _lo = _q.get('low'); _hi = _q.get('high')
            # Precise as-of date + market-state from the EODHD quote: the EOD path carries a
            # settled close date; the real-time path carries a unix trade timestamp. A trade
            # stamped within the last 30 min means a live session (intraday); otherwise the
            # real-time endpoint is just echoing the prior close.
            _asof = _q.get('as_of_date'); _ts = _q.get('timestamp')
            _nowu = _dt.datetime.now(_dt.timezone.utc)
            _kind = 'last close'
            if _asof:
                _ld = _fmtd(_asof)
            elif _ts:
                try:
                    _tt = _dt.datetime.fromtimestamp(int(_ts), _dt.timezone.utc)
                    _ld = _tt.strftime('%b %-d, %Y')
                    if (_nowu - _tt).total_seconds() < 1800:
                        _kind = 'intraday'
                except Exception:
                    _ld = _nowu.strftime('%b %-d, %Y')
            else:
                _ld = _nowu.strftime('%b %-d, %Y')
            _P = lambda v: f"${float(v):,.2f}"
            _tol = lambda v: max(0.01, float(v) * 0.001)
            _fixed = [0]
            def _rc(m):
                try: sv = float(m.group(2).replace(',', ''))
                except Exception: return m.group(0)
                if abs(sv - _close) > _tol(_close):
                    _fixed[0] += 1; return f"{m.group(1)}{_P(_close)}"
                return m.group(0)
            # Allow an optional weekday between the verb and the price ("closed Thursday at $X")
            # so a second, unenforced price can't slip past the gate. Verb set kept narrow on
            # purpose: "ended/finished at $X" could be a historical window price, not the current one.
            html_out = re.sub(
                r'(\b(?:closed?|trading|trades|last\s+traded)\s+'
                r'(?:(?:on\s+)?(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day\s+)?(?:at\s+)?)'
                r'\$([\d,]+(?:\.\d+)?)',
                _rc, html_out, flags=re.I)
            if _lo is not None and _hi is not None:
                html_out = re.sub(r'(between\s+)\$([\d,]+(?:\.\d+)?)(\s+and\s+)\$([\d,]+(?:\.\d+)?)',
                                  lambda m: f"{m.group(1)}{_P(_lo)}{m.group(3)}{_P(_hi)}", html_out, flags=re.I)
                if not (float(_lo) <= _close <= float(_hi)):
                    print(f"[WARN] price-finalize: feed close {_close} outside [{_lo},{_hi}] for {symbol}")
            if 'class="price-asof"' not in html_out:
                _line = (f'<p class="price-asof" style="font-size:.9rem;color:#5a6b7a;margin:-6px 0 16px;">'
                         f'Price as of {_ld}: <strong>{_P(_close)}</strong> ({_kind}).</p>')
                html_out = re.sub(r'(<p class="dek">.*?</p>)', r'\1\n' + _line, html_out, count=1, flags=re.S)
            print(f"[FIX] price-finalize: {symbol} current price set to {_P(_close)} "
                  f"(src={_q.get('source')}); {_fixed[0]} prose value(s) corrected")
    except Exception as _pe:
        print(f"[FIX] price-finalize skipped (non-fatal): {_pe}")

    print("post process complete")

    return html_out

#---------------------------------------------------------------------------------------
def show_html_diff(original_path: str, processed_path: str, context_lines: int = 4):
    """
    Print a unified diff between two HTML files, with a small amount of context.
    Useful for verifying what article_post_process changed.
    """
    orig = Path(original_path)
    proc = Path(processed_path)

    if not orig.exists():
        raise FileNotFoundError(f"Original file not found: {orig}")
    if not proc.exists():
        raise FileNotFoundError(f"Processed file not found: {proc}")

    original_lines = orig.read_text(encoding="utf-8").splitlines(keepends=False)
    processed_lines = proc.read_text(encoding="utf-8").splitlines(keepends=False)

    diff = difflib.unified_diff(
        original_lines,
        processed_lines,
        fromfile=str(orig),
        tofile=str(proc),
        n=context_lines,
    )

    print("\n===== HTML DIFF =====")
    any_output = False
    for line in diff:
        any_output = True
        print(line)
    if not any_output:
        print("No differences found (files are identical).")
    print("===== END DIFF =====\n")

# =============================================================================
# BACKFILL: Apply site wrapper to all existing articles
# =============================================================================

def backfill_site_wrapper(force=False):
    """
    Apply the SMN site wrapper (market bar, header, footer, CTA) to all
    existing article HTML files.

    Usage:
      python article_post_process.py --backfill-wrapper          # skip already wrapped
      python article_post_process.py --backfill-wrapper --force   # strip + re-inject all
    """
    articles_root = Path(config.news_root_folder) / "articles"
    if not articles_root.exists():
        print(f"[BACKFILL] No articles directory at {articles_root}")
        return

    html_files = list(articles_root.rglob("*.html"))
    print(f"[BACKFILL] Found {len(html_files)} article files (force={force})")

    updated = 0
    skipped = 0
    for fpath in html_files:
        html = fpath.read_text("utf-8")

        if _SITE_WRAPPER_MARKER in html and not force:
            skipped += 1
            continue

        # Extract title and symbol from existing HTML
        title_match = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
        title = title_match.group(1).strip() if title_match else ""

        sym_match = re.search(r'<span>Symbol:\s*(\w+)</span>', html)
        symbol = sym_match.group(1) if sym_match else ""

        result = _inject_site_wrapper(html, symbol, title, force=force)
        fpath.write_text(result, "utf-8")
        updated += 1

    print(f"[BACKFILL] Done: {updated} updated, {skipped} skipped")


#--------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    import sys as _sys
    if "--backfill-wrapper" in _sys.argv:
        backfill_site_wrapper(force="--force" in _sys.argv)
        _sys.exit(0)

    """
    Smoketest for an already-published WYNN article.

    Behavior:
      - Uses load_article_from_folder() as the single source of truth for the filename.
      - On first run:
          * Reads the canonical HTML file.
          * Saves a backup as <name>_nonprocessed.html (if it does not already exist).
          * Overwrites the canonical file with the processed HTML.
      - On subsequent runs:
          * Reads the (already processed) canonical file.
          * Runs article_post_process() again.
          * Overwrites the same file.
        Because post-processing is idempotent, the output should be stable.
    """

    from pathlib import Path
    from publish_article import load_article_from_folder

    # ---- GOOG smoketest parameters ----
    resource_id     = "2"
    symbol          = "GOOG"
    date            = "2025-12-03"
    days            = 62
    years           = "10"
    zero_last_year  = True
    userid          = 22  # correct userid for production

    # 1) Load canonical article via metadata (Redis / posts.json)
    result = load_article_from_folder(resource_id, symbol, date, str(days), years, userid)

    if not result.get("found"):
        raise RuntimeError(f"Article not found for {symbol} {date} {days} {years}: {result.get('reason')}")

    html_path_str = result.get("file_path")
    if not html_path_str:
        raise RuntimeError("load_article_from_folder returned no file_path")

    html_path = Path(html_path_str)
    print(f"[INFO] Using canonical article file: {html_path}")

    if not html_path.exists():
        raise RuntimeError(f"Canonical article file does not exist on disk: {html_path}")

    # 2) Backup original once as *_nonprocessed.html (dev-only)
    backup_path = html_path.with_name(html_path.stem + "_nonprocessed.html")
    if not backup_path.exists():
        backup_path.write_text(html_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[INFO] Created backup of original HTML:\n    {backup_path}")
    else:
        print(f"[INFO] Backup already exists, not overwriting:\n    {backup_path}")

    # 3) Load current canonical HTML (may already be processed)
    original_html = html_path.read_text(encoding="utf-8")

    # 4) Post-process (idempotent)
    processed_html = article_post_process(
        resource_id,
        symbol,
        date,
        days,
        years,
        zero_last_year,
        original_html,
    )

    # 5) Overwrite the canonical file with processed HTML
    html_path.write_text(processed_html, encoding="utf-8")
    print(f"[SUCCESS] Overwrote canonical article with processed HTML:\n    {html_path}")
