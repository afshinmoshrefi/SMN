"""
seo_helpers.py
==============
Shared SEO utilities for all page generators.
Every function checks config.seo_enabled and returns empty string when disabled.
"""

import sys, json
sys.path.insert(0, '/home/flask')
import config


def _enabled():
    return getattr(config, 'seo_enabled', False)


def _site_url():
    return config.news_website_url.rstrip('/')


# ─── Google Analytics ────────────────────────────────────────────────

def ga_snippet():
    """Return GA4 gtag.js script block. Empty string if disabled."""
    if not _enabled():
        return ''
    mid = getattr(config, 'ga_measurement_id', '')
    if not mid:
        return ''
    return f'''
    <!-- Google Analytics -->
    <script async src="https://www.googletagmanager.com/gtag/js?id={mid}"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){{ dataLayer.push(arguments); }}
      gtag('js', new Date());
      gtag('config', '{mid}');
    </script>'''


# ─── Open Graph tags ─────────────────────────────────────────────────

def og_tags(title, description, url, og_type='website', image=None,
            image_width=None, image_height=None, site_name='Seasonal Market News',
            article_published=None, article_modified=None,
            article_author=None, article_section=None, article_tags=None):
    """Return OG meta tags. Empty string if SEO disabled."""
    if not _enabled():
        return ''
    tags = [
        f'<meta property="og:locale" content="en_US">',
        f'<meta property="og:title" content="{_esc(title)}">',
        f'<meta property="og:description" content="{_esc(description)}">',
        f'<meta property="og:url" content="{url}">',
        f'<meta property="og:type" content="{og_type}">',
        f'<meta property="og:site_name" content="{site_name}">',
    ]
    if image:
        img_url = image if image.startswith('http') else _site_url() + image
        tags.append(f'<meta property="og:image" content="{img_url}">')
        if image_width:
            tags.append(f'<meta property="og:image:width" content="{image_width}">')
        if image_height:
            tags.append(f'<meta property="og:image:height" content="{image_height}">')
    if article_published:
        tags.append(f'<meta property="article:published_time" content="{article_published}">')
    if article_modified:
        tags.append(f'<meta property="article:modified_time" content="{article_modified}">')
    if article_author:
        tags.append(f'<meta property="article:author" content="{article_author}">')
    if article_section:
        tags.append(f'<meta property="article:section" content="{article_section}">')
    if article_tags:
        for t in (article_tags if isinstance(article_tags, list) else [article_tags]):
            tags.append(f'<meta property="article:tag" content="{_esc(t)}">')
    return '\n    '.join(tags)


# ─── Twitter Card tags ───────────────────────────────────────────────

def twitter_tags(title, description, image=None, card='summary_large_image'):
    """Return Twitter card meta tags. Empty string if SEO disabled."""
    if not _enabled():
        return ''
    tags = [
        f'<meta name="twitter:card" content="{card}">',
        f'<meta name="twitter:title" content="{_esc(title)}">',
        f'<meta name="twitter:description" content="{_esc(description)}">',
    ]
    if image:
        img_url = image if image.startswith('http') else _site_url() + image
        tags.append(f'<meta name="twitter:image" content="{img_url}">')
    return '\n    '.join(tags)


# ─── JSON-LD: BreadcrumbList ─────────────────────────────────────────

def breadcrumb_jsonld(items):
    """
    Return BreadcrumbList JSON-LD script tag.
    items: list of (name, url) tuples. Last item url can be None (current page).
    """
    if not _enabled() or not items:
        return ''
    elements = []
    for i, (name, url) in enumerate(items, 1):
        el = {"@type": "ListItem", "position": i, "name": name}
        if url:
            el["item"] = url
        elements.append(el)
    ld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": elements,
    }
    return f'<script type="application/ld+json">\n    {json.dumps(ld)}\n    </script>'


# ─── JSON-LD: Organization ───────────────────────────────────────────

def organization_jsonld():
    """Return Organization JSON-LD for Seasonal Market News."""
    if not _enabled():
        return ''
    ld = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "Seasonal Market News",
        "url": _site_url(),
        "logo": _site_url() + "/smnfav.png",
        "description": "AI-powered seasonal market analysis and data-driven financial news.",
    }
    return f'<script type="application/ld+json">\n    {json.dumps(ld)}\n    </script>'


# ─── JSON-LD: WebSite ────────────────────────────────────────────────

def website_jsonld():
    """Return WebSite JSON-LD with SearchAction for Google Sitelinks Searchbox."""
    if not _enabled():
        return ''
    site_url = _site_url()
    ld = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "Seasonal Market News",
        "url": site_url + "/",
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": f"{site_url}/search.html?q={{search_term_string}}",
            },
            "query-input": "required name=search_term_string",
        },
    }
    return f'<script type="application/ld+json">\n    {json.dumps(ld)}\n    </script>'


# ─── Helpers ──────────────────────────────────────────────────────────

def _esc(s):
    """Escape HTML attribute content."""
    if not s:
        return ''
    return str(s).replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
