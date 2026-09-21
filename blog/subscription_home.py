"""Pure static renderers for the subscription archive.

The production wire presentation remains owned by ``rebuild_news_home.py``.
This module parses that file and compiles only an explicit allowlist of its
literal theme and presentation helpers.  It never imports the production module,
so its config, Redis, quote, and MailerLite setup cannot run here.
"""

from __future__ import annotations

import ast
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
import re
from types import MappingProxyType
from urllib.parse import urlsplit


_ORIGIN = "https://smn-dev.trxstat.com"
_TAGLINE = (
    "Daily, data-backed coverage of repeating market patterns. "
    "Institutional-grade research powered by TradeWave analytics."
)
_SOURCE = Path(__file__).with_name("rebuild_news_home.py")
_PURE_FUNCTIONS = frozenset({
    "_fmt_date", "_direction_to_sentiment", "_get_hero_image_url",
    "_extract_pattern_date_from_url", "_get_base_css", "_get_wire_template_css",
    "_get_header_html", "_get_footer_html",
    "_build_wire_lead", "_build_wire_headline", "_build_wire_card",
    "_build_wire_list_item", "_build_wire_template",
})


def _load_wire_primitives():
    """Compile only audited presentation AST nodes from the production source."""
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"), filename=str(_SOURCE))
    nodes = []
    themes = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "THEMES" for target in node.targets
        ):
            themes = ast.literal_eval(node.value)
        elif isinstance(node, ast.FunctionDef) and node.name in _PURE_FUNCTIONS:
            nodes.append(node)
    found = {node.name for node in nodes if isinstance(node, ast.FunctionDef)}
    if themes is None or found != _PURE_FUNCTIONS:
        raise RuntimeError("production wire presentation allowlist is incomplete")
    module = ast.Module(body=nodes, type_ignores=[])
    namespace = {
        "datetime": datetime,
        "timezone": timezone,
        "re": re,
        "SHOW_BADGES": True,
        "SHOW_DATA_BAR": False,
        "THEMES": themes,
    }
    exec(compile(module, str(_SOURCE), "exec"), namespace)
    return MappingProxyType(namespace)


_WIRE = _load_wire_primitives()


def _absolute_local_url(value: object) -> str:
    """Accept only faithful dev-origin or root-relative archive URLs."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    if parts.scheme or parts.netloc:
        if f"{parts.scheme}://{parts.netloc}" != _ORIGIN:
            raise ValueError("archive URLs must use the SMN dev origin")
        return raw
    if not raw.startswith("/"):
        raise ValueError("archive URLs must be root-relative or use the SMN dev origin")
    return _ORIGIN + raw


def _safe_post(post: object) -> dict[str, str]:
    """Escape every production-template interpolation without altering data values."""
    source = post if isinstance(post, dict) else {}
    safe = {}
    for key in ("title", "dek", "symbol", "direction", "pattern_days", "published_date"):
        safe[key] = escape(str(source.get(key, "")), quote=True)
    safe["url"] = escape(_absolute_local_url(source.get("url")), quote=True)
    safe["hero_image"] = escape(_absolute_local_url(source.get("hero_image")), quote=True)
    return safe


def _published_at(post: dict[str, str]) -> datetime | None:
    try:
        value = datetime.fromisoformat(post["published_date"].replace("Z", "+00:00"))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    except ValueError:
        return None


def _display_posts(posts, as_of: str) -> list[dict[str, str]]:
    try:
        today = date.fromisoformat(as_of)
    except (TypeError, ValueError) as exc:
        raise ValueError("as_of must be an ISO date (YYYY-MM-DD)") from exc
    eligible = []
    for post in posts:
        safe = _safe_post(post)
        published = _published_at(safe)
        if published is not None and 0 <= (today - published.date()).days <= 14:
            eligible.append((published, safe))
    eligible.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in eligible[:50]]


def _header() -> str:
    return _WIRE["_get_header_html"]()


def _hero() -> str:
    return f'''\
    <section class="hero">
        <h1>AI-Powered <span>Seasonal</span> Market Intelligence</h1>
        <p class="hero-subtitle">{_TAGLINE}</p>
    </section>'''


def _document(title: str, body: str, extra_css: str = "", script: str = "") -> str:
    theme = _WIRE["THEMES"]["light"]
    css = _WIRE["_get_base_css"](theme) + _WIRE["_get_wire_template_css"]()
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="robots" content="noindex, nofollow">
    <title>{title} | Seasonal Market News</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>{css}\n{extra_css}</style>
</head>
<body>
{_header()}
{body}
{script}
</body>
</html>'''


def render_home(posts, as_of: str) -> str:
    """Render the 14-day, maximum-50 article subscription home page."""
    items = _display_posts(posts, as_of)
    content = _WIRE["_build_wire_template"](items, _WIRE["THEMES"]["light"])
    older_link = '<p class="archive-link"><a href="search.html">Search all older coverage</a></p>'
    footer = _WIRE["_get_footer_html"](len(items), len(posts))
    return _document("Seasonal Market News", _hero() + content + older_link + footer,
                     ".archive-link { max-width: 1200px; margin: 0 auto 48px; padding: 0 24px; }")


_SEARCH_CSS = """
.search-page { max-width: 1200px; margin: 0 auto; padding: 40px 24px 56px; }
.search-page h1 { font-size: 28px; margin-bottom: 8px; }
.search-page > p { color: var(--text-muted); margin-bottom: 24px; }
.search-input { width: 100%; max-width: 620px; padding: 12px 14px; border: 1px solid var(--border-color); border-radius: 6px; font: inherit; }
.search-count { margin: 20px 0; font: 600 12px 'IBM Plex Mono', monospace; color: var(--text-muted); text-transform: uppercase; letter-spacing: .06em; }
.search-results .wire-list { grid-template-columns: 1fr; }
.search-results .wire-list-item { min-height: 100px; }
"""


_SEARCH_SCRIPT = """<script>
(() => {
  const input = document.getElementById('searchQuery');
  const results = document.getElementById('searchResults');
  const count = document.getElementById('searchCount');
  const text = value => String(value == null ? '' : value);
  const matches = (post, query) => !query || [post.symbol, post.title, post.dek]
    .some(value => text(value).toLowerCase().includes(query));
  function articleUrl(value) {
    try { const url = new URL(text(value), location.href); return 'https://smn-dev.trxstat.com' + url.pathname; }
    catch (_) { return 'https://smn-dev.trxstat.com/'; }
  }
  function draw(posts) {
    const query = input.value.trim().toLowerCase();
    const visible = posts.filter(post => matches(post, query));
    count.textContent = visible.length + ' article' + (visible.length === 1 ? '' : 's');
    results.replaceChildren();
    const list = document.createElement('div'); list.className = 'wire-list';
    visible.forEach(post => {
      const link = document.createElement('a'); link.className = 'wire-list-item'; link.dataset.archiveResult = 'true'; link.href = articleUrl(post.url);
      const content = document.createElement('div'); content.className = 'wire-list-content';
      const top = document.createElement('div'); top.className = 'wire-list-top';
      const symbol = document.createElement('span'); symbol.className = 'wire-list-symbol'; symbol.textContent = text(post.symbol);
      const heading = document.createElement('h4'); heading.textContent = text(post.title) || 'Untitled';
      const dek = document.createElement('p'); dek.className = 'wire-card-excerpt'; dek.textContent = text(post.dek);
      const meta = document.createElement('div'); meta.className = 'wire-list-meta'; meta.textContent = text(post.published_date).slice(0, 10);
      top.append(symbol); content.append(top, heading, dek, meta); link.append(content); list.append(link);
    });
    if (!visible.length) { const empty = document.createElement('p'); empty.textContent = 'No articles found.'; results.append(empty); }
    else results.append(list);
  }
  fetch('posts.json').then(response => { if (!response.ok) throw Error('catalog unavailable'); return response.json(); })
    .then(posts => { const catalog = Array.isArray(posts) ? posts : []; const query = new URLSearchParams(location.search).get('q'); if (query) input.value = query; input.addEventListener('input', () => draw(catalog)); draw(catalog); })
    .catch(() => { count.textContent = 'Catalog unavailable'; });
})();
</script>"""


def render_search() -> str:
    """Render the static, client-side archive search page."""
    body = '''<main class="search-page">
    <h1>Search Articles</h1>
    <p>Search the complete Seasonal Market News archive.</p>
    <input id="searchQuery" name="q" class="search-input" type="search" placeholder="Symbol, title, or topic" aria-label="Search articles" autofocus>
    <div id="searchCount" class="search-count">Loading articles…</div>
    <div id="searchResults" class="search-results"></div>
</main>'''
    footer = '''<footer><div class="footer-content"><div class="footer-left">© Seasonal Market News</div>
    <div class="footer-links"><a href="https://tradewave.ai" target="_blank">TradeWave</a></div></div></footer>'''
    return _document("Search", body + footer, _SEARCH_CSS, _SEARCH_SCRIPT)
