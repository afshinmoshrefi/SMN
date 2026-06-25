"""
inject_seo_backfill.py
======================
Inject new SEO elements into all existing SMN article HTML files.
Safe to re-run: each injection is idempotent (checks before injecting).

New items injected (if not already present):
  1. RSS auto-discovery <link> tag
  2. Organization JSON-LD
  3. WebSite JSON-LD
  4. FAQPage JSON-LD (rebuilt from question H2s each run)
  5. og:locale meta tag
  6. og:image fallback (site-wide OG image for articles with no hero)

Usage:
    python inject_seo_backfill.py              # dry run - shows what would change
    python inject_seo_backfill.py --apply      # actually modify files
    python inject_seo_backfill.py --apply --verbose   # show per-file detail
"""

import os, sys, glob, json, re

sys.path.insert(0, '/home/flask')
import config
import seo_helpers

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ARTICLES_DIR = os.path.join(config.news_root_folder, config.articles_subfolder)
SITE_URL     = config.news_website_url.rstrip('/')
RSS_URL      = f"{SITE_URL}/rss.xml"
OG_IMAGE_FALLBACK = SITE_URL + config.smn_og_image  # full absolute URL


# ---------------------------------------------------------------------------
# Helpers shared with article_post_process.py
# ---------------------------------------------------------------------------

_DATASET_LD_RE = re.compile(
    r'<script\s+type=["\']application/ld\+json["\'][^>]*>\s*(.*?)\s*</script>',
    re.DOTALL | re.IGNORECASE,
)


def _strip_existing_faqpage_ld(html: str) -> str:
    """Remove any existing FAQPage JSON-LD block (so we can rebuild it)."""
    def repl(m):
        try:
            data = json.loads(m.group(1))
        except Exception:
            return m.group(0)
        if data.get('@type') == 'FAQPage':
            return ''
        return m.group(0)
    return _DATASET_LD_RE.sub(repl, html)


def _build_faqpage_ld(html: str):
    """Extract question H2s + first answer paragraphs. Returns dict or None."""
    def strip_tags(text):
        text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        return re.sub(r'\s+', ' ', text).strip()

    pairs = []
    h2_re = re.compile(r'<h2[^>]*>(.*?)</h2>', re.DOTALL | re.IGNORECASE)
    p_re  = re.compile(r'<p[^>]*>(.*?)</p>',   re.DOTALL | re.IGNORECASE)

    for h2_m in h2_re.finditer(html):
        q = strip_tags(h2_m.group(1)).strip()
        if not q.endswith('?'):
            continue
        answer = ''
        for p_m in p_re.finditer(html, h2_m.end()):
            candidate = strip_tags(p_m.group(1)).strip()
            if len(candidate) >= 30:
                answer = candidate[:500]
                break
        if not answer:
            continue
        pairs.append({
            '@type': 'Question',
            'name': q,
            'acceptedAnswer': {'@type': 'Answer', 'text': answer},
        })
        if len(pairs) >= 4:
            break

    if len(pairs) < 2:
        return None
    return {'@context': 'https://schema.org', '@type': 'FAQPage', 'mainEntity': pairs}


def _has_jsonld_type(html: str, schema_type: str) -> bool:
    """Return True if html already contains a JSON-LD block with the given @type."""
    for m in _DATASET_LD_RE.finditer(html):
        try:
            data = json.loads(m.group(1))
            if data.get('@type') == schema_type:
                return True
        except Exception:
            pass
    return False


def _inject_before_head_close(html: str, snippet: str) -> str:
    """Insert snippet immediately before </head>."""
    idx = html.lower().find('</head>')
    if idx == -1:
        return html
    return html[:idx] + snippet + html[idx:]


# ---------------------------------------------------------------------------
# Per-file injection
# ---------------------------------------------------------------------------

def process_file(filepath: str, dry_run: bool, verbose: bool) -> dict:
    """
    Inspect and (if not dry_run) update a single HTML file.
    Returns a dict summarising what was done.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        html = f.read()

    changes = []

    # 1. RSS auto-discovery
    if 'application/rss+xml' not in html:
        rss_tag = (
            f'<link rel="alternate" type="application/rss+xml" '
            f'title="Seasonal Market News" href="{RSS_URL}">\n'
        )
        html = _inject_before_head_close(html, rss_tag)
        changes.append('rss-link')

    # 2. Organization JSON-LD
    if not _has_jsonld_type(html, 'Organization'):
        org_tag = seo_helpers.organization_jsonld()
        if org_tag:
            html = _inject_before_head_close(html, org_tag + '\n')
            changes.append('org-jsonld')

    # 3. WebSite JSON-LD
    if not _has_jsonld_type(html, 'WebSite'):
        site_tag = seo_helpers.website_jsonld()
        if site_tag:
            html = _inject_before_head_close(html, site_tag + '\n')
            changes.append('website-jsonld')

    # 4. FAQPage JSON-LD  (strip old + rebuild)
    html = _strip_existing_faqpage_ld(html)
    faq_ld = _build_faqpage_ld(html)
    if faq_ld:
        faq_str = json.dumps(faq_ld, ensure_ascii=False, indent=2)
        faq_tag = f'<script type="application/ld+json">\n{faq_str}\n</script>\n'
        html = _inject_before_head_close(html, faq_tag)
        changes.append(f'faq-jsonld({len(faq_ld["mainEntity"])}qa)')

    # 5. og:locale
    if 'og:locale' not in html:
        locale_tag = '<meta property="og:locale" content="en_US">\n'
        html = _inject_before_head_close(html, locale_tag)
        changes.append('og:locale')

    # 6. og:image fallback (only if article has no og:image at all)
    if 'og:image' not in html:
        fallback_tag = f'<meta property="og:image" content="{OG_IMAGE_FALLBACK}">\n'
        html = _inject_before_head_close(html, fallback_tag)
        changes.append('og:image-fallback')

    # 7. max-snippet robots meta
    if 'max-snippet' not in html:
        robots_meta = '<meta name="robots" content="max-snippet:-1, max-image-preview:large, max-video-preview:-1">\n'
        html = _inject_before_head_close(html, robots_meta)
        changes.append('max-snippet')

    if not changes:
        return {'file': filepath, 'status': 'ok', 'changes': []}

    if not dry_run:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html)

    return {'file': filepath, 'status': 'dry' if dry_run else 'updated', 'changes': changes}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    dry_run = '--apply' not in sys.argv
    verbose = '--verbose' in sys.argv

    if dry_run:
        print('=== DRY RUN (pass --apply to modify files) ===\n')

    pattern = os.path.join(ARTICLES_DIR, '**', '*.html')
    files = sorted(glob.glob(pattern, recursive=True))

    if not files:
        print(f'No HTML files found in {ARTICLES_DIR}')
        return

    total = len(files)
    updated = 0
    already_ok = 0
    change_counts = {}

    for filepath in files:
        result = process_file(filepath, dry_run, verbose)
        if result['changes']:
            updated += 1
            for c in result['changes']:
                key = c.split('(')[0]   # normalise faq-jsonld(3qa) -> faq-jsonld
                change_counts[key] = change_counts.get(key, 0) + 1
            if verbose or dry_run:
                action = 'WOULD UPDATE' if dry_run else 'UPDATED'
                fname = os.path.basename(filepath)
                print(f'  {action}: {fname}  [{", ".join(result["changes"])}]')
        else:
            already_ok += 1

    print(f'\nScanned {total} file(s): {updated} need updates, {already_ok} already up-to-date.')
    if change_counts:
        print('Injection summary:')
        for k, v in sorted(change_counts.items()):
            print(f'  {k}: {v} file(s)')
    if dry_run and updated > 0:
        print('\nRun with --apply to apply all changes.')


if __name__ == '__main__':
    main()
