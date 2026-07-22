"""angle_chrome.py — server-rendered fact chrome + slot-token assembly (Phase 2).

The fact/form split from ANGLE_ENGINE_DESIGN.md §1: every surface the reader
trusts (pattern-meta strip, key-stats box, figures, sources, methodology,
head/JSON-LD, CSS) is rendered HERE, by code, from the Angle Card — the writer
model never types these. The writer places them with slot tokens:

    {{HERO}} {{META_STRIP}} {{KEY_STATS}} {{FIG:price}} {{FIG:trend}}
    {{FIG:bars}} {{FIG:bars_mae_mfe}} {{FIG:cumulative}} {{SOURCES}}
    {{METHODOLOGY}}

assemble_article() substitutes chrome for tokens, applies deterministic
default placement for anything the writer forgot (never a model retry),
drops duplicates, renumbers citations to the cited-only sources list, and
wraps the result in the standard head/CSS. Numbers cannot drift in chrome
because chrome never passes through the model.
"""
from __future__ import annotations

import datetime
import json
import re
from typing import Any, Dict, List, Optional, Tuple

CHART_WIDTH_ATTR = 1600
CHART_HEIGHT_ATTR = 746
HERO_WIDTH_ATTR = 1536
HERO_HEIGHT_ATTR = 640

TOKEN_RE = re.compile(r"\{\{([A-Z_]+(?::[a-z_]+)?)\}\}")
SUP_RE = re.compile(r"<sup[^>]*>\s*\[(\d+)\]\s*</sup>", re.I)

FIGURE_VARIANTS = ("price", "trend", "bars", "bars_mae_mfe", "bars_mfe",
                   "bars_mae", "cumulative")

# Same look as the current article_prompt.py output — the visual identity of
# the site does not change with the angle redesign.
ARTICLE_CSS = """
    :root { --ink:#0a0a0a; --muted:#5a6b7a; --rule:#e6e9ee; --accent:#0059ff; }
    body { margin:0; font-family: Helvetica, Arial, sans-serif; color:var(--ink); background:#fff; }
    article { max-width:860px; margin: 40px auto; padding: 0 20px 64px; line-height:1.6; }
    h1 { font-size: 2.0rem; line-height:1.25; margin: 0 0 8px; }
    .dek { font-size:1.125rem; color:var(--muted); margin: 0 0 16px; }
    figure.hero img { margin-bottom: 24px; }
    .meta { font-size:.875rem; color:var(--muted); border-top:1px solid var(--rule); border-bottom:1px solid var(--rule); padding:8px 0; margin-bottom:24px; display:flex; gap:12px; flex-wrap:wrap; }
    h2 { font-size:1.25rem; margin:28px 0 8px; }
    p { margin: 0 0 14px; }
    blockquote.pull { margin: 18px 0; padding: 12px 16px; border-left: 4px solid var(--rule); color:#111; background:#fafbfe; font-style: italic; }
    aside.key-stats { border:1px solid var(--rule); background:#fafbfe; padding:12px 14px; margin: 20px 0; font-size:.95rem; }
    aside.key-stats h3 { margin:0 0 8px; font-size:1rem; }
    aside.key-stats .row { display:flex; justify-content:space-between; border-bottom:1px dashed var(--rule); padding:6px 0; }
    figure { margin: 20px 0; }
    figure img { width:100%; height:auto; display:block; }
    figcaption { font-size:.9rem; color:var(--muted); text-align:center; margin-top:6px; }
    .sources { border-top:1px solid var(--rule); margin-top:28px; padding-top:14px; }
    .sources h3 { margin:0 0 8px; font-size:1rem; }
    .sources ol { margin:0 0 0 18px; padding:0; }
    .sources li { margin:6px 0; }
    a { color:var(--accent); text-decoration:none; }
    a:hover { text-decoration:underline; }
    .pattern-meta { font-size:.9rem; color:var(--muted); border-top:1px solid var(--rule); border-bottom:1px solid var(--rule); padding:8px 0; margin:16px 0; display:flex; gap:12px; flex-wrap:wrap; }
    .chart-bridge { margin: 8px 0 6px; color: var(--muted); font-size:.95rem; }
    .key-takeaways-box ul { margin:0; padding-left:0; list-style:none; }
    .key-takeaways-box li { margin:6px 0; }
    .direct-answer { font-size:1.05rem; font-weight:600; color:var(--ink); border-left:3px solid var(--accent); padding-left:12px; margin:0 0 14px; }
    .methodology-note { border-top:1px solid var(--rule); margin-top:28px; padding-top:14px; font-size:.95rem; color:var(--muted); }
    .methodology-note h2 { font-size:1.1rem; color:var(--ink); margin:0 0 8px; }
""".strip("\n")


def _fmt_date(iso: str) -> str:
    d = datetime.date.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def _esc(text: Any) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def window_end_date(cell: Dict[str, Any]) -> str:
    """Last DATE inside the window.

    TradeWave counts the entry day as day 1, so a "30 day" window spans 30
    dates: start .. start + 29. The analytics engine works in daysOut = days-1
    (see angle_engine._cell_key / article_images), and the +1 is a DISPLAY
    convention on the count only -- it must never be added to the end date.
    """
    start = datetime.date.fromisoformat(cell["anchor_date"])
    return (start + datetime.timedelta(days=int(cell["days"]) - 1)).isoformat()


# ============================================================
# Chrome renderers — every reader-trusted surface
# ============================================================

def render_meta_strip(cell: Dict[str, Any]) -> str:
    """Pattern-meta strip: window identity + DERIVED historical bias.

    Matrix cells are computed under long accounting (Trade Dir is 'long' for
    every arbitrary window), so the reader-facing direction is the derived
    bias with its counts — never the accounting field.
    """
    k = cell["up_years"] if cell["direction"] == "bullish" else cell["down_years"]
    word = "higher" if cell["direction"] == "bullish" else "lower"
    lookback = (f"{cell['years']} years" if cell["mode"] == "cons"
                else _pe_label(cell["years"]))
    return (
        '<div class="pattern-meta">'
        f'<span>Symbol: {_esc(cell["symbol"])}</span>'
        f'<span>Window: {_fmt_date(cell["anchor_date"])} to '
        f'{_fmt_date(window_end_date(cell))} ({cell["days"]} calendar days)</span>'
        f'<span>Lookback: {_esc(lookback)}</span>'
        f'<span>Historical bias: {cell["direction"]} ({k} of {cell["n"]} years '
        f'closed {word})</span>'
        '</div>')


def _pe_label(years_code: str) -> str:
    m = re.match(r"pe([0-3])-(\d+)", str(years_code).lower())
    if not m:
        return f"{years_code} years"
    names = {"0": "presidential election years", "1": "post-election years",
             "2": "midterm election years", "3": "pre-election years"}
    return f"the last {m.group(2)} {names[m.group(1)]}"


KEY_STATS_ROWS = ("Percent Profitable", "Num Winners", "Num Losers",
                  "Avg Profit", "Avg Profit - All", "Median Profit",
                  "Avg Loss", "Std Dev", "Sharpe Ratio", "TradeWave Ratio")


def render_key_stats(cell: Dict[str, Any], cta_link: str = "",
                     methodology_url: str = "", book_url: str = "") -> str:
    """TradeWave key-stats box, values verbatim from the cell's stats block.

    'Percent Profitable' etc. are long-accounting figures ('years closed
    higher'); the sample-size row makes n explicit. No Trade Direction row
    for matrix cells — direction lives in the meta strip as derived bias.
    Omit 'Avg Profit - All' when there are no losing years (existing rule).
    """
    stats = dict(cell.get("stats_raw") or {})

    # ChartData4 may return Trade Dir = short, in which case its Winners/Losers
    # and profit figures are SHORT-accounted: "winners" are years the window
    # closed LOWER. The narrative is always derived from per-year nets, so a
    # short-accounted box contradicts it. Re-state the counts from the engine's
    # derived values and drop the raw figures that cannot be safely re-signed.
    short_acct = str(stats.get("Trade Dir", "")).strip().lower() == "short"
    if short_acct:
        up, dn, n = cell.get("up_years"), cell.get("down_years"), cell.get("n")
        if up is not None and dn is not None and n:
            stats["Num Winners"] = up
            stats["Num Losers"] = dn
            stats["Percent Profitable"] = f"{round(100.0 * up / n)}%"
        med = cell.get("median_net")
        if med is not None:
            stats["Median Profit"] = f"{med:.2f}%"
        for k in ("Avg Profit", "Avg Profit - All", "Avg Loss",
                  "Sharpe Ratio", "TradeWave Ratio"):
            stats.pop(k, None)

    rows: List[str] = [
        f'<div class="row"><span>Sample Size</span><span>{cell["n"]} years</span></div>'
    ]
    losers = str(stats.get("Num Losers", "")).strip()
    for label in KEY_STATS_ROWS:
        value = stats.get(label)
        if value in (None, ""):
            continue
        if label == "Avg Profit - All" and losers in ("0", ""):
            continue
        rows.append(f'<div class="row"><span>{_esc(label)}</span>'
                    f'<span>{_esc(value)}</span></div>')
    src = (f'<a href="{_esc(cta_link)}">TradeWave.ai</a>' if cta_link
           else 'TradeWave.ai')
    footer = (
        '<p style="margin-top:4px;font-size:.85rem;color:#5a6b7a;">'
        f'Source: {src} seasonal database. Figures use close-to-close accounting '
        'over the stated window: winners are years the window closed higher, '
        'losers are years it closed lower. Cumulative Return is '
        'compounded across the lookback years.</p>')
    if methodology_url or book_url:
        links = []
        if methodology_url:
            links.append(f'<a href="{_esc(methodology_url)}">Methodology</a>')
        if book_url:
            links.append(f'<a href="{_esc(book_url)}"><em>The 100-Year Pattern</em></a>')
        footer += ('<p style="margin-top:4px;font-size:.85rem;color:#5a6b7a;">'
                   + " · ".join(links) + '</p>')
    return ('<aside class="key-stats"><h3>TradeWave Key Stats</h3>'
            + "".join(rows) + footer + '</aside>')


def render_figure(variant: str, images: List[Dict[str, str]]) -> str:
    """One <figure> for a chart variant from the image manifest (caption and
    alt come from the manifest — already server-generated)."""
    img = next((i for i in images if i.get("variant") == variant), None)
    if img is None:
        return ""
    cap = img.get("caption") or img.get("alt") or variant
    return (f'<figure class="{variant}-chart">'
            f'<img src="{_esc(img.get("url", ""))}" alt="{_esc(img.get("alt", cap))}" '
            f'width="{CHART_WIDTH_ATTR}" height="{CHART_HEIGHT_ATTR}">'
            f'<figcaption>{_esc(cap)}</figcaption></figure>')


def render_hero(hero_url: str, symbol: str, company: str) -> str:
    if not hero_url:
        return ""
    alt = f"{company} ({symbol}) market analysis and seasonal trends"
    return (f'<figure class="hero"><img src="{_esc(hero_url)}" '
            f'width="{HERO_WIDTH_ATTR}" height="{HERO_HEIGHT_ATTR}" '
            f'alt="{_esc(alt)}"></figure>')


def render_methodology(cell: Dict[str, Any], methodology_url: str = "",
                       book_url: str = "") -> str:
    lookback = (f"{cell['years']} years" if cell["mode"] == "cons"
                else _pe_label(cell["years"]))
    extra = ""
    if methodology_url:
        extra += f' Read the full <a href="{_esc(methodology_url)}">data methodology</a>'
        if book_url:
            extra += (f' or the book <a href="{_esc(book_url)}">'
                      f'<em>The 100-Year Pattern</em></a> by Afshin Moshrefi (2026 edition)')
        extra += "."
    return (
        '<section id="methodology" class="methodology-note">'
        '<h2>About this seasonal analysis</h2>'
        f'<p>Seasonal pattern data is sourced from '
        f'<a href="https://tradewave.ai/">TradeWave.ai</a>. This analysis covers a '
        f'{cell["days"]} calendar-day window with {_esc(lookback)} of observations.'
        f'{extra} Past performance of seasonal patterns does not guarantee future '
        'results. This article is for informational purposes only and does not '
        'constitute investment advice.</p></section>')


def render_sources(cited_ids: List[int], research: Optional[Dict[str, Any]]) -> str:
    """Cited-only sources list (DECIDED 2026-07-20): exactly the sources the
    prose cites, in order of first appearance."""
    if not cited_ids or not isinstance(research, dict):
        return ""
    by_id = {s.get("id"): s for s in research.get("sources", [])
             if isinstance(s, dict)}
    items = []
    for cid in cited_ids:
        src = by_id.get(cid)
        if not src:
            continue
        pub = src.get("publisher") or ""
        title = src.get("title") or ""
        label = f"{pub} - {title}" if pub and title else (title or pub or "Source")
        items.append(f'<li><a href="{_esc(src.get("url", ""))}">{_esc(label)}</a></li>')
    if not items:
        return ""
    return ('<section class="sources"><h3>Sources</h3><ol>'
            + "".join(items) + '</ol></section>')


def build_json_ld(headline: str, description: str, image_url: str,
                  company: str, symbol: str, date_iso: str) -> str:
    payload = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": headline,
        "description": description,
        "datePublished": date_iso,
        "dateModified": date_iso,
        "author": {"@type": "Organization", "name": "TradeWave.ai",
                   "url": "https://tradewave.ai/"},
        "publisher": {"@type": "Organization", "name": "TradeWave.ai",
                      "logo": {"@type": "ImageObject",
                               "url": "https://tradewave.ai/logo.png"}},
        "image": image_url,
        "about": {"@type": "Thing", "name": company, "tickerSymbol": symbol},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ============================================================
# Assembly
# ============================================================

def build_chrome(card: Dict[str, Any], *, images: Optional[List[Dict[str, str]]] = None,
                 hero_url: str = "", research: Optional[Dict[str, Any]] = None,
                 company: str = "", cta_link: str = "", methodology_url: str = "",
                 book_url: str = "") -> Dict[str, str]:
    """All chrome fragments for one article, keyed by token name."""
    cell = card["story_cell"]
    company = company or card["symbol"]
    images = images or []
    chrome = {
        "HERO": render_hero(hero_url, card["symbol"], company),
        "META_STRIP": render_meta_strip(cell),
        "KEY_STATS": render_key_stats(cell, cta_link, methodology_url, book_url),
        "METHODOLOGY": render_methodology(cell, methodology_url, book_url),
    }
    for variant in FIGURE_VARIANTS:
        chrome[f"FIG:{variant}"] = render_figure(variant, images)
    # SOURCES is rendered during assembly (needs the cited-id order).
    chrome["SOURCES"] = ""
    # lowercase sentinels are unmatchable by TOKEN_RE; used for head assembly
    chrome["_company"] = company
    chrome["_symbol"] = card["symbol"]
    return chrome


def renumber_citations(prose: str, research: Optional[Dict[str, Any]]
                       ) -> Tuple[str, List[int]]:
    """Writer cites research source ids directly (<sup>[7]</sup> = Research
    id 7). Renumber to 1..k in order of first appearance and return the
    cited research ids in display order — the cited-only sources list."""
    if not isinstance(research, dict):
        return prose, []
    valid_ids = {s.get("id") for s in research.get("sources", [])
                 if isinstance(s, dict)}
    order: List[int] = []

    def _sub(match: re.Match) -> str:
        rid = int(match.group(1))
        if rid not in valid_ids:
            return ""                      # dead citation: drop, gate flags coverage
        if rid not in order:
            order.append(rid)
        return f"<sup>[{order.index(rid) + 1}]</sup>"

    return SUP_RE.sub(_sub, prose), order


DEFAULT_ORDER = ("HERO", "META_STRIP", "KEY_STATS")


def assemble_article(prose: str, chrome: Dict[str, str], *,
                     research: Optional[Dict[str, Any]] = None,
                     planned_figs: Optional[List[str]] = None,
                     byline: str = "", ai_disclosure: str = "",
                     favicon_url: str = "") -> Dict[str, Any]:
    """Substitute chrome for slot tokens and wrap into a full HTML document.

    Deterministic repairs (never a model retry):
      - duplicate token -> first kept, rest dropped
      - missing required token -> default placement (HERO after dek;
        META_STRIP after first <h2>; KEY_STATS after META_STRIP; planned
        figures after KEY_STATS; SOURCES/METHODOLOGY appended)
      - unknown/leftover tokens -> stripped
    Returns {html, warnings, cited_ids, used_tokens}.
    """
    warnings: List[str] = []
    planned_figs = [f for f in (planned_figs or []) if chrome.get(f"FIG:{f}")]

    prose, cited_ids = renumber_citations(prose, research)
    chrome = dict(chrome)
    chrome["SOURCES"] = render_sources(cited_ids, research)

    # Substitute, tracking usage; drop duplicates.
    used: List[str] = []

    def _sub(match: re.Match) -> str:
        name = match.group(1)
        if name not in chrome:
            warnings.append(f"unknown token {{{{{name}}}}} stripped")
            return ""
        if name in used:
            warnings.append(f"duplicate token {{{{{name}}}}} dropped")
            return ""
        used.append(name)
        return chrome[name]

    body = TOKEN_RE.sub(_sub, prose)

    # Default placements for anything required that the writer forgot.
    def _insert_after(html: str, anchor_re: str, fragment: str) -> Tuple[str, bool]:
        m = re.search(anchor_re, html, re.I | re.S)
        if not m:
            return html, False
        idx = m.end()
        return html[:idx] + fragment + html[idx:], True

    required = [name for name in DEFAULT_ORDER if chrome.get(name)]
    required += [f"FIG:{v}" for v in planned_figs]
    for name in required:
        if name in used or not chrome.get(name):
            continue
        fragment = chrome[name]
        if name == "HERO":
            body, ok = _insert_after(body, r"<p\b[^>]*class=[\"'][^\"']*dek[^\"']*[\"'][^>]*>.*?</p>", fragment)
        elif name == "META_STRIP":
            body, ok = _insert_after(body, r"<h2\b[^>]*>.*?</h2>", fragment)
        elif name == "KEY_STATS":
            body, ok = (body.replace(chrome["META_STRIP"],
                                     chrome["META_STRIP"] + fragment, 1), True) \
                if chrome.get("META_STRIP", "") in body else (body, False)
        else:
            anchor = chrome.get("KEY_STATS", "")
            body, ok = ((body.replace(anchor, anchor + fragment, 1), True)
                        if anchor and anchor in body else (body, False))
        if not ok:
            body += fragment
        used.append(name)
        warnings.append(f"token {{{{{name}}}}} missing; default placement applied")

    for tail in ("SOURCES", "METHODOLOGY"):
        if tail not in used and chrome.get(tail):
            body += chrome[tail]
            used.append(tail)

    # Head assembly: title = H1 text, description = dek text.
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.I | re.S)
    dek = re.search(r"<p\b[^>]*class=[\"'][^\"']*dek[^\"']*[\"'][^>]*>(.*?)</p>",
                    body, re.I | re.S)
    strip_tags = lambda s: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()
    title = strip_tags(h1.group(1)) if h1 else ""
    description = strip_tags(dek.group(1))[:160] if dek else ""
    if not title:
        warnings.append("no <h1> found in prose")
    hero_m = re.search(r'<figure class="hero"><img src="([^"]+)"', body)
    first_img = re.search(r"<img src=\"([^\"]+)\"", body)
    image_url = hero_m.group(1) if hero_m else (first_img.group(1) if first_img else "")
    today_iso = datetime.date.today().isoformat()

    meta_bits = []
    if byline:
        meta_bits.append(f"<span>{_esc(byline)}</span>")
    if ai_disclosure:
        meta_bits.append(f"<span>{_esc(ai_disclosure)}</span>")
    if meta_bits and '<div class="meta">' not in body:
        frag = '<div class="meta">' + "".join(meta_bits) + "</div>"
        body, ok = _insert_after(body, r"<p\b[^>]*class=[\"'][^\"']*dek[^\"']*[\"'][^>]*>.*?</p>", frag)
        if not ok:
            body = frag + body

    favicon = (f'<link rel="icon" type="image/png" href="{_esc(favicon_url)}">'
               if favicon_url else "")
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  {favicon}
  <title>{_esc(title)}</title>
  <meta name="description" content="{_esc(description)}">
  <script type="application/ld+json">
{build_json_ld(title, description, image_url,
               chrome.get('_company', '') or title.split('(')[0].strip(),
               chrome.get('_symbol', ''), today_iso)}
  </script>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
{ARTICLE_CSS}
  </style>
</head>
<body>
<article>
{body}
</article>
</body>
</html>"""
    leftovers = TOKEN_RE.findall(html)
    if leftovers:
        warnings.append(f"leftover tokens stripped: {leftovers}")
        html = TOKEN_RE.sub("", html)
    return {"html": html, "warnings": warnings, "cited_ids": cited_ids,
            "used_tokens": used}
