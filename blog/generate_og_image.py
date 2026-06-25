"""
generate_og_image.py
====================
Generates the OG social sharing image for seasonalmarketnews.com.
Output: /var/www/smn/smn-og-image.jpg (1200x630)

Run: python generate_og_image.py
"""

import sys, math, random
sys.path.insert(0, '/home/flask')
import config
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ─── Dimensions ───────────────────────────────────────────────────────
W, H = 1200, 630

# ─── Brand colors (dark theme) ───────────────────────────────────────
BG_PRIMARY    = (10, 10, 11)       # #0a0a0b
BG_SECONDARY  = (17, 17, 19)      # #111113
ACCENT_BLUE   = (10, 132, 255)    # #0a84ff
ACCENT_GREEN  = (48, 209, 88)     # #30d158
ACCENT_RED    = (255, 69, 58)     # #ff453a
TEXT_PRIMARY   = (245, 245, 247)  # #f5f5f7
TEXT_SECONDARY = (161, 161, 166)  # #a1a1a6
TEXT_MUTED     = (110, 110, 115)  # #6e6e73
BORDER_COLOR   = (44, 44, 46)    # #2c2c2e

# ─── Fonts ────────────────────────────────────────────────────────────
FONT_BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
FONT_REGULAR = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
FONT_MONO = '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'


def _draw_gradient_bg(img):
    """Draw a dark gradient background with subtle blue tint."""
    draw = ImageDraw.Draw(img)
    for y in range(H):
        r = int(10 + (y / H) * 8)
        g = int(10 + (y / H) * 12)
        b = int(14 + (y / H) * 18)
        draw.line([(0, y), (W, y)], fill=(r, g, b))


def _draw_grid(draw):
    """Draw a subtle grid pattern like a financial chart background."""
    grid_color = (25, 25, 30, 60)
    # We can't use alpha directly on RGB, so use a very dark shade
    grid_color_rgb = (20, 20, 24)
    spacing = 40
    for x in range(0, W, spacing):
        draw.line([(x, 0), (x, H)], fill=grid_color_rgb, width=1)
    for y in range(0, H, spacing):
        draw.line([(0, y), (W, y)], fill=grid_color_rgb, width=1)


def _generate_wave_data(n=120, base=300, amplitude=60):
    """Generate realistic-looking price data with trend and volatility."""
    random.seed(42)  # reproducible
    prices = []
    price = base
    trend = 0.15
    for i in range(n):
        # Upward trending with realistic volatility
        noise = random.gauss(0, 1.2)
        cycle = math.sin(i / 20) * 8 + math.sin(i / 7) * 4
        price += trend + noise + cycle * 0.3
        prices.append(price)
    return prices


def _draw_chart_line(draw, prices, x_start, x_end, y_base, y_height, color, width=3):
    """Draw a smooth price line chart."""
    n = len(prices)
    p_min, p_max = min(prices), max(prices)
    p_range = p_max - p_min or 1

    points = []
    for i, p in enumerate(prices):
        x = x_start + (i / (n - 1)) * (x_end - x_start)
        y = y_base + y_height - ((p - p_min) / p_range) * y_height
        points.append((x, y))

    # Draw the line
    for i in range(len(points) - 1):
        draw.line([points[i], points[i + 1]], fill=color, width=width)

    return points


def _draw_chart_glow(img, points, color, y_bottom):
    """Draw a gradient fill under the chart line for a glow effect."""
    if len(points) < 2:
        return
    overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)

    # Create polygon from line to bottom
    poly = list(points) + [(points[-1][0], y_bottom), (points[0][0], y_bottom)]

    # Draw with decreasing opacity bands
    for offset, alpha in [(0, 35), (5, 25), (10, 15), (20, 8)]:
        shifted = [(x, y + offset) for x, y in poly]
        od.polygon(shifted, fill=(*color, alpha))

    img.paste(Image.alpha_composite(Image.new('RGBA', img.size, (0, 0, 0, 0)), overlay), mask=overlay)


def _draw_projection_zone(draw, points, proj_start_idx, color):
    """Draw dashed projection line and zone from a point forward."""
    if proj_start_idx >= len(points):
        return

    # Dashed line for projection portion
    for i in range(proj_start_idx, len(points) - 1):
        if i % 3 != 0:  # create dash effect
            draw.line([points[i], points[i + 1]], fill=color, width=2)

    # Vertical dashed line at projection start
    px, py = points[proj_start_idx]
    for y in range(int(py) - 30, int(py) + 80, 6):
        draw.line([(px, y), (px, y + 3)], fill=BORDER_COLOR, width=1)


def _draw_stat_badge(draw, x, y, value, label, color):
    """Draw a floating stat badge."""
    font_val = ImageFont.truetype(FONT_MONO, 18)
    font_lbl = ImageFont.truetype(FONT_REGULAR, 11)

    # Background pill
    vw, vh = draw.textsize(value, font=font_val)
    lw, lh = draw.textsize(label, font=font_lbl)
    pw = max(vw, lw) + 24
    ph = 52

    draw.rectangle([(x, y), (x + pw, y + ph)], fill=(17, 17, 22), outline=BORDER_COLOR)
    draw.text((x + 12, y + 8), value, fill=color, font=font_val)
    draw.text((x + 12, y + 30), label, fill=TEXT_MUTED, font=font_lbl)
    return pw


def _draw_market_ticker(draw, x, y, symbol, price, change, is_up):
    """Draw a mini market ticker item."""
    font_sym = ImageFont.truetype(FONT_BOLD, 13)
    font_price = ImageFont.truetype(FONT_MONO, 13)
    font_chg = ImageFont.truetype(FONT_MONO, 11)

    color_chg = ACCENT_GREEN if is_up else ACCENT_RED
    arrow = "+" if is_up else ""

    draw.text((x, y), symbol, fill=TEXT_SECONDARY, font=font_sym)
    draw.text((x, y + 18), price, fill=TEXT_PRIMARY, font=font_price)
    draw.text((x, y + 34), f"{arrow}{change}", fill=color_chg, font=font_chg)


def generate_og_image(output_path=None):
    """Generate the OG social sharing image."""
    if output_path is None:
        assets_dir = Path(config.news_root_folder) / 'assets'
        assets_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(assets_dir / 'smn-og-image.jpg')

    # Create base image
    img = Image.new('RGB', (W, H), BG_PRIMARY)
    _draw_gradient_bg(img)
    draw = ImageDraw.Draw(img)

    # Subtle grid
    _draw_grid(draw)

    # ── Chart area (right side, behind text) ──
    prices_main = _generate_wave_data(100, base=280, amplitude=50)
    chart_x_start = 480
    chart_x_end = W - 40
    chart_y_base = 140
    chart_y_height = 320

    # Draw chart glow first (needs RGBA compositing)
    img_rgba = img.convert('RGBA')
    points = []
    n = len(prices_main)
    p_min, p_max = min(prices_main), max(prices_main)
    p_range = p_max - p_min or 1
    for i, p in enumerate(prices_main):
        x = chart_x_start + (i / (n - 1)) * (chart_x_end - chart_x_start)
        y = chart_y_base + chart_y_height - ((p - p_min) / p_range) * chart_y_height
        points.append((x, y))

    _draw_chart_glow(img_rgba, points[:75], ACCENT_BLUE, chart_y_base + chart_y_height + 40)
    _draw_chart_glow(img_rgba, points[74:], ACCENT_GREEN, chart_y_base + chart_y_height + 40)
    img = img_rgba.convert('RGB')
    draw = ImageDraw.Draw(img)

    # Draw the actual chart lines
    # Historical portion (blue)
    for i in range(len(points[:75]) - 1):
        draw.line([points[i], points[i + 1]], fill=ACCENT_BLUE, width=3)

    # Projection portion (green, slightly dashed feel)
    for i in range(74, len(points) - 1):
        if i % 2 == 0:
            draw.line([points[i], points[i + 1]], fill=ACCENT_GREEN, width=3)
        else:
            draw.line([points[i], points[i + 1]], fill=ACCENT_GREEN, width=2)

    # Vertical separator at projection start
    px, py = points[74]
    for y_tick in range(int(chart_y_base), int(chart_y_base + chart_y_height), 8):
        draw.line([(px, y_tick), (px, y_tick + 4)], fill=(60, 60, 65), width=1)

    # "NOW" label at projection line
    font_tiny = ImageFont.truetype(FONT_REGULAR, 10)
    draw.text((px - 12, chart_y_base + chart_y_height + 8), "NOW", fill=TEXT_MUTED, font=font_tiny)

    # "PROJECTED" label
    font_tiny2 = ImageFont.truetype(FONT_REGULAR, 10)
    proj_mid_x = (px + chart_x_end) / 2 - 28
    draw.text((proj_mid_x, chart_y_base - 18), "SEASONAL PROJECTION", fill=ACCENT_GREEN, font=font_tiny2)

    # ── Floating stat badges ──
    bx = 520
    w1 = _draw_stat_badge(draw, bx, 480, "73%", "WIN RATE", ACCENT_GREEN)
    _draw_stat_badge(draw, bx + w1 + 12, 480, "+4.2%", "AVG RETURN", ACCENT_BLUE)
    _draw_stat_badge(draw, bx + w1 + 12 + 110, 480, "10yr", "HISTORY", TEXT_SECONDARY)

    # ── Branding (left side) ──
    # Site name
    font_seasonal = ImageFont.truetype(FONT_BOLD, 38)
    font_market = ImageFont.truetype(FONT_BOLD, 38)
    font_news = ImageFont.truetype(FONT_BOLD, 38)

    y_brand = 80
    x_brand = 52

    draw.text((x_brand, y_brand), "Seasonal", fill=TEXT_PRIMARY, font=font_seasonal)
    # Get width of "Seasonal"
    sw, _ = draw.textsize("Seasonal", font=font_seasonal)
    draw.text((x_brand + sw + 6, y_brand), "Market", fill=ACCENT_BLUE, font=font_market)
    mw, _ = draw.textsize("Market", font=font_market)
    draw.text((x_brand + sw + 6 + mw + 6, y_brand), "News", fill=ACCENT_GREEN, font=font_news)

    # Tagline
    font_tagline = ImageFont.truetype(FONT_REGULAR, 17)
    draw.text((x_brand, y_brand + 52), "AI-Powered Seasonal Market Intelligence", fill=TEXT_SECONDARY, font=font_tagline)

    # ── Accent line under brand ──
    line_y = y_brand + 82
    # Gradient-like accent line
    for i in range(200):
        alpha = max(0, 255 - int(i * 1.3))
        r = int(ACCENT_BLUE[0] + (ACCENT_GREEN[0] - ACCENT_BLUE[0]) * i / 200)
        g = int(ACCENT_BLUE[1] + (ACCENT_GREEN[1] - ACCENT_BLUE[1]) * i / 200)
        b = int(ACCENT_BLUE[2] + (ACCENT_GREEN[2] - ACCENT_BLUE[2]) * i / 200)
        draw.line([(x_brand + i, line_y), (x_brand + i, line_y + 2)],
                  fill=(r, g, b))

    # ── Value propositions ──
    font_feature = ImageFont.truetype(FONT_REGULAR, 14)
    features = [
        "Seasonal price projections for indices & commodities",
        "Presidential election cycle analysis",
        "AI-generated market analysis, updated daily",
        "Historical win rates, returns & pattern data",
    ]
    y_feat = 200
    for feat in features:
        # Bullet dot
        draw.ellipse([(x_brand + 2, y_feat + 5), (x_brand + 8, y_feat + 11)], fill=ACCENT_BLUE)
        draw.text((x_brand + 18, y_feat), feat, fill=TEXT_SECONDARY, font=font_feature)
        y_feat += 26

    # ── Mini market bar at bottom ──
    bar_y = 555
    draw.line([(0, bar_y - 1), (W, bar_y - 1)], fill=BORDER_COLOR, width=1)
    # Dark bar background
    draw.rectangle([(0, bar_y), (W, H)], fill=(12, 12, 16))

    tickers = [
        ("S&P 500", "5,740", "+1.33%", True),
        ("DOW", "42,501", "+0.94%", True),
        ("NASDAQ", "18,387", "+1.59%", True),
        ("VIX", "19.49", "-4.17%", False),
        ("CRUDE", "67.27", "+0.35%", True),
        ("NAT GAS", "3.82", "-1.41%", False),
        ("GOLD", "3,081", "+0.63%", True),
    ]

    ticker_spacing = (W - 60) // len(tickers)
    for i, (sym, price, chg, up) in enumerate(tickers):
        tx = 30 + i * ticker_spacing
        _draw_market_ticker(draw, tx, bar_y + 10, sym, price, chg, up)

    # ── URL above the market bar ──
    font_url = ImageFont.truetype(FONT_MONO, 12)
    url_text = "seasonalmarketnews.com"
    url_w, _ = draw.textsize(url_text, font=font_url)
    draw.text((W - url_w - 30, bar_y - 20), url_text, fill=TEXT_MUTED, font=font_url)

    # ── Powered by TradeWave ──
    font_pw = ImageFont.truetype(FONT_REGULAR, 12)
    draw.text((x_brand, 500), "Powered by", fill=TEXT_MUTED, font=font_pw)
    font_tw = ImageFont.truetype(FONT_BOLD, 13)
    pb_w, _ = draw.textsize("Powered by ", font=font_pw)
    draw.text((x_brand + pb_w, 499), "TradeWave.ai", fill=ACCENT_BLUE, font=font_tw)

    # ── Save ──
    img.save(output_path, 'JPEG', quality=95, optimize=True)
    print(f"[OG IMAGE] Generated: {output_path} ({W}x{H})")
    return output_path


if __name__ == '__main__':
    generate_og_image()
    # Also save a copy in the blog folder for reference
    generate_og_image('/home/flask/blog/smn-og-image.jpg')
