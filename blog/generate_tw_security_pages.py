#!/usr/bin/env python3
"""
generate_tw_security_pages.py
==============================
Generates dark-themed security detail pages for TradeWave from data
already collected by generate_security_pages.py.

Reads _page_data.json (saved by the SMN generator), copies chart images,
and outputs dark-themed HTML to TradeWave's _static/markets/ directory.

Run AFTER generate_security_pages.py:
  python generate_security_pages.py
  python generate_tw_security_pages.py
"""

import os, sys, json, shutil
from pathlib import Path
from datetime import datetime

sys.path.insert(0, '/home/flask')
sys.path.insert(0, '/home/flask/blog')
import config
from get_price_eod import get_quote_details

# Import the heavy lifting from the SMN generator
import generate_security_pages as gsp

# =============================================================================
# CONFIGURATION
# =============================================================================

# Input: data exported by generate_security_pages.py
SMN_MARKETS_DIR = Path(config.news_root_folder) / "markets"
SMN_CHARTS_DIR = SMN_MARKETS_DIR / "charts"
PAGE_DATA_FILE = SMN_MARKETS_DIR / "_page_data.json"

# Output: TradeWave static directory
TW_ROOT = Path(config.web_root_dir) / "_static"
TW_MARKETS_DIR = TW_ROOT / "markets"
TW_CHARTS_DIR = TW_MARKETS_DIR / "charts"

DOMAIN_ROOT = config.domain_root
MAILERLITE_FORM_URL = "https://assets.mailerlite.com/jsonp/489451/forms/173861813170996648/subscribe"

# Save original functions BEFORE any patching
_original_base_css = gsp._build_base_css


# =============================================================================
# DARK THEME OVERRIDES
# =============================================================================

def _dark_base_css():
    """Dark theme CSS variables matching TradeWave homepage."""
    t = {
        "bg_primary": "rgb(15,10,21)",
        "bg_secondary": "rgb(25,22,35)",
        "bg_tertiary": "#111936",
        "text_primary": "#ffffff",
        "text_secondary": "#9ca3af",
        "text_muted": "#6b7280",
        "accent_blue": "#6366f1",
        "accent_green": "rgb(100,220,140)",
        "accent_red": "#ef4444",
        "accent_amber": "#f59e0b",
        "border_color": "#1f2937",
        "card_shadow": "0 1px 3px rgba(0,0,0,0.3)",
        "card_hover_shadow": "0 4px 12px rgba(0,0,0,0.4)",
        "badge_bullish_bg": "rgba(100, 220, 140, 0.12)",
        "badge_bearish_bg": "rgba(239, 68, 68, 0.12)",
    }
    # Get the original CSS template and just swap the variables
    original = _original_base_css()
    # Replace the :root block with dark values
    css = f"""
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
    """
    # Extract everything after the :root block from original
    idx = original.find('}')
    if idx > 0:
        # Skip past the :root closing brace
        rest = original[idx + 1:]
        css += rest
    return css


def _dark_header_html():
    """TradeWave branded header."""
    return f'''
    <header>
        <div class="header-content">
            <a href="{DOMAIN_ROOT}" class="logo" style="font-size:30px;font-weight:800;background:linear-gradient(135deg,#6366f1 0%,#8b5cf6 50%,#a855f7 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;text-decoration:none;">TradeWave</a>
            <div class="header-right">
                <nav>
                    <a href="{DOMAIN_ROOT}">Home</a>
                    <a href="{DOMAIN_ROOT}wave-viewer">Wave Viewer</a>
                    <a href="{DOMAIN_ROOT}scorecard">Track Record</a>
                </nav>
            </div>
        </div>
    </header>'''


def _get_daily_ai_pick_group_id():
    """Fetch the DAILY_AI_PICK MailerLite group ID."""
    try:
        headers = {
            'Authorization': f'Bearer {config.mailerlite_token}',
            'Accept': 'application/json',
        }
        r = requests.get('https://connect.mailerlite.com/api/groups?limit=100',
                         headers=headers, timeout=10)
        for g in r.json().get('data', []):
            if g['name'] == 'DAILY_AI_PICK':
                return g['id']
    except Exception:
        pass
    return ''

import requests

def _dark_cta_html():
    """TradeWave daily AI pick signup CTA."""
    group_id = _get_daily_ai_pick_group_id()

    return f'''
    <div class="security-cta">
        <h3>Get the Daily AI Pick Before Market Open</h3>
        <p>ML-scored seasonal patterns delivered to your inbox every morning. Free.</p>
        <form id="secCtaForm" onsubmit="return false;">
            <div class="cta-row">
                <input type="email" id="secCtaEmail" class="cta-email" placeholder="Enter your email" required autocomplete="email">
                <button type="submit" class="cta-btn" id="secCtaBtn">Subscribe</button>
            </div>
            <div class="cta-error" id="secCtaError">Please enter a valid email.</div>
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
            var err = document.getElementById('secCtaError');
            if (!email) {{ err.style.display = 'block'; return; }}
            err.style.display = 'none';
            var btn = document.getElementById('secCtaBtn');
            btn.disabled = true;
            btn.textContent = 'Subscribing...';
            var fd = new FormData();
            fd.append('fields[email]', email);
            fd.append('ml-submit', '1');
            fd.append('anticsrf', 'true');
            if ('{group_id}') fd.append('groups[]', '{group_id}');
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


def _dark_footer_html():
    """TradeWave footer."""
    year = datetime.now().year
    return f'''
    <footer>
        <div class="footer-content">
            <p class="footer-copyright">{year} TradeWave AI. All rights reserved.</p>
            <p class="footer-disclaimer">
                TradeWave is a research platform. It is not a brokerage and does not execute trades.
                All data is based on historical analysis and is provided for informational and educational purposes only.
                Past performance does not guarantee future results.
            </p>
        </div>
    </footer>'''


def _dark_market_bar_html(current_slug=None, all_quotes=None):
    """Market bar with links pointing to TradeWave dark pages."""
    items_html = ""
    for sec in gsp.SECURITY_PAGES:
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
            price_fmt = ""
            chg_fmt = ""
            direction = "flat"

        short_labels = {"Dow Jones Industrial Average": "DOW", "NASDAQ Composite": "NASDAQ",
                        "CBOE Volatility Index": "VIX", "Crude Oil (WTI)": "CRUDE",
                        "Natural Gas": "NAT GAS"}
        short_label = short_labels.get(sec["label"], sec["label"])
        is_current = sec["slug"] == current_slug
        active_cls = " current" if is_current else ""

        href = f'{DOMAIN_ROOT}_static/markets/{sec["slug"]}.html'
        items_html += f'''
            <a href="{href}" class="market-item {direction}{active_cls}">
                <span class="market-symbol">{short_label}</span>
                {f'<span class="market-price">{price_fmt}</span>' if price_fmt else ''}
                {"<span class='market-change " + direction + "'>" + chg_fmt + "</span>" if chg_fmt else ""}
            </a>'''

    return f'''
    <div class="market-bar">
        <div class="market-bar-content">
            {items_html}
        </div>
    </div>'''


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("[TW SECURITY PAGES] Starting dark theme generation")

    # Check for page data
    if not PAGE_DATA_FILE.exists():
        print("  ERROR: %s not found. Run generate_security_pages.py first." % PAGE_DATA_FILE)
        return

    # Load exported data
    with open(str(PAGE_DATA_FILE), 'r') as f:
        page_data = json.load(f)
    print("  Loaded page data for %d securities" % len(page_data))

    # Create output dirs
    TW_MARKETS_DIR.mkdir(parents=True, exist_ok=True)
    TW_CHARTS_DIR.mkdir(parents=True, exist_ok=True)

    # Copy chart images from SMN to TW
    if SMN_CHARTS_DIR.exists():
        chart_count = 0
        for chart_file in SMN_CHARTS_DIR.glob("*.jpg"):
            dest = TW_CHARTS_DIR / chart_file.name
            shutil.copy2(str(chart_file), str(dest))
            chart_count += 1
        print("  Copied %d chart images" % chart_count)

    # Fetch quotes for market bar
    print("  Fetching quotes for market bar...")
    all_quotes = {}
    for sec in gsp.SECURITY_PAGES:
        q = get_quote_details(sec["symbol"], sec["exchange"])
        if q:
            all_quotes[sec["symbol"]] = q

    # Monkey-patch the display functions to use dark theme
    original_base_css = gsp._build_base_css
    original_header = gsp._build_header_html
    original_cta = gsp._build_cta_html
    original_footer = gsp._build_footer_html
    original_market_bar = gsp._build_market_bar_html

    gsp._build_base_css = _dark_base_css
    gsp._build_header_html = _dark_header_html
    gsp._build_cta_html = _dark_cta_html
    gsp._build_footer_html = _dark_footer_html
    gsp._build_market_bar_html = _dark_market_bar_html

    # Also patch the site URL references
    original_site_url = gsp.SITE_URL
    gsp.SITE_URL = DOMAIN_ROOT.rstrip('/')

    # Also patch favicon
    original_favicon_line = None  # handled in page assembly

    # Generate dark pages
    generated = []
    for sec in gsp.SECURITY_PAGES:
        slug = sec["slug"]
        pd = page_data.get(slug)
        if not pd:
            print("  WARNING: No data for %s, skipping" % slug)
            continue

        # Use dark chart URLs, falling back to light if missing
        dark_urls = pd.get("dark_chart_urls", {})
        source_urls = dark_urls if dark_urls else pd.get("chart_urls", {})
        tw_chart_urls = {}
        for key, url in source_urls.items():
            # Original: /markets/charts/dark_GSPC_2026-03-17_10_30.jpg
            # TW: /_static/markets/charts/dark_GSPC_2026-03-17_10_30.jpg
            filename = url.split("/")[-1]
            tw_chart_urls[key] = "/_static/markets/charts/%s" % filename

        html = gsp.build_security_page(
            pd["sec"], pd.get("quote", {}), [], [],  # no news, no related for TW
            tw_chart_urls, pd.get("max_pe", 0),
            projection_targets=pd.get("projection_targets", {}),
            projection_stats=pd.get("projection_stats", {}),
            ai_analysis=pd.get("ai_analysis"),
            usage_guide=pd.get("usage_guide"),
            all_quotes=all_quotes,
            all_ai_snippets={},
            price_history={},
        )

        # Replace SMN favicon with TW favicon
        html = html.replace(config.smn_favicon, config.tw_favicon)

        # Replace SMN title branding
        html = html.replace("| Seasonal Market News", "| TradeWave")
        html = html.replace("Seasonal Market News", "TradeWave")

        # Fix chart paths in the HTML (charts reference /markets/charts/)
        html = html.replace('"/markets/charts/', '"/_static/markets/charts/')
        html = html.replace("'/markets/charts/", "'/_static/markets/charts/")

        # Fix related card links and any remaining /markets/ links
        html = html.replace('href="/markets/', 'href="/_static/markets/')

        out_path = TW_MARKETS_DIR / ("%s.html" % slug)
        out_path.write_text(html, "utf-8")
        print("  -> Wrote %s (%s bytes)" % (out_path, format(len(html), ',')))
        generated.append(slug)

    # Restore originals
    gsp._build_base_css = original_base_css
    gsp._build_header_html = original_header
    gsp._build_cta_html = original_cta
    gsp._build_footer_html = original_footer
    gsp._build_market_bar_html = original_market_bar
    gsp.SITE_URL = original_site_url

    print("\n[TW SECURITY PAGES] Done. Generated %d dark pages: %s" % (
        len(generated), ", ".join(generated)))


def inject_tw_security_prices(quotes_by_symbol=None):
    """Update prices in TradeWave dark security pages.

    Same regex approach as the SMN injector but targets TW_MARKETS_DIR
    and uses the dark market bar.
    """
    import re

    if not TW_MARKETS_DIR.exists():
        print("[TW PRICES] No TW markets directory, skipping")
        return

    if quotes_by_symbol is None:
        quotes_by_symbol = {}
        for sec in gsp.SECURITY_PAGES:
            q = get_quote_details(sec["symbol"], sec["exchange"])
            if q:
                quotes_by_symbol[sec["symbol"]] = q

    updated = 0
    for sec in gsp.SECURITY_PAGES:
        slug = sec["slug"]
        page_path = TW_MARKETS_DIR / ("%s.html" % slug)
        if not page_path.exists():
            continue

        quote = quotes_by_symbol.get(sec["symbol"])
        if not quote:
            continue

        html = page_path.read_text("utf-8")

        # 1) Update hero price
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

        chg_str, direction = gsp._fmt_change(change, change_p)

        html = re.sub(
            r'(<span class="price-main">)[^<]*(</span>)',
            r'\g<1>%s\2' % gsp._fmt_price(close_val),
            html
        )
        html = re.sub(
            r"<span class=['\"]price-change [^'\"]*['\"]>[^<]*</span>",
            "<span class='price-change %s'>%s</span>" % (direction, chg_str),
            html
        )

        # 2) Update quote details
        detail_map = {"Open": "open", "High": "high", "Low": "low", "Prev Close": "previousClose"}
        for label, key in detail_map.items():
            val = quote.get(key)
            try:
                val = float(val) if val is not None else None
            except (ValueError, TypeError):
                val = None
            html = re.sub(
                r'(<span class="quote-detail-label">%s</span>'
                r'<span class="quote-detail-value">)[^<]*(</span>)' % re.escape(label),
                r'\g<1>%s\2' % gsp._fmt_price(val),
                html
            )

        # Volume
        vol = quote.get("volume")
        if vol:
            try:
                vol_fmt = "%s" % format(float(vol), ",.0f")
            except (ValueError, TypeError):
                vol_fmt = ""
            html = re.sub(
                r'(<span class="quote-detail-label">Volume</span>'
                r'<span class="quote-detail-value">)[^<]*(</span>)',
                r'\g<1>%s\2' % vol_fmt,
                html
            )

        # 3) Update timestamp
        ts = quote.get("timestamp")
        if ts:
            try:
                from datetime import timezone
                ts_str = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%b %d, %Y %I:%M %p UTC")
                display_symbol = sec["appserver_symbol"]
                html = re.sub(
                    r'(<div class="security-meta">)[^<]*(</div>)',
                    r'\g<1>%s &middot; %s\2' % (display_symbol, ts_str),
                    html
                )
            except Exception:
                pass

        # 4) Update market bar with dark version
        new_bar = _dark_market_bar_html(current_slug=slug, all_quotes=quotes_by_symbol)
        html = re.sub(
            r'<div class="market-bar">.*?</div>\s*</div>',
            new_bar.strip(),
            html,
            flags=re.DOTALL,
            count=1
        )

        page_path.write_text(html, "utf-8")
        updated += 1
        print("  [TW PRICES] Updated %s: %s %s" % (slug, gsp._fmt_price(close_val), chg_str))

    print("[TW PRICES] Updated %d pages" % updated)


if __name__ == "__main__":
    main()
