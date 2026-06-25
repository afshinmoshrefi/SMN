#!/home/flask/venv/bin/python
"""
DEV-ONLY post-sync step.

Production's published HTML carries a related-articles bug: same-ticker articles
are uncapped, so a page's "Related Articles" fills with near-duplicate stories
about its own ticker. The fix lives in related_articles.py (SAME_TICKER_MAX).

After each site sync we re-render every page's related block here so the dev site
shows the diversified lists. Covers both catalog articles and orphan HTML files
(on disk but absent from posts.json). LOCAL ONLY — no article generation, no
external/TradeWave API calls.
"""
import sys, re, glob
sys.path.insert(0, "/home/flask")
import config
import refresh_related_articles as R

VOL = getattr(config, "volume_csv_dir", "/home/flask/blog/volume_lists")


def _meta(html, *pats):
    for pat in pats:
        m = re.search(pat, html, re.I)
        if m:
            return m.group(1).strip()
    return ""


def main():
    catalog = R._load_catalog()
    catalog_paths = {a.get("path") for a in catalog}

    ok = fail = 0
    for a in catalog:
        try:
            ok += 1 if R._update_article_html(a, catalog, VOL, dry_run=False) else 0
        except Exception as e:
            fail += 1
            print(f"[ERROR] {a.get('symbol')}: {e}")
    print(f"[CATALOG] updated={ok} failed={fail} of {len(catalog)}")

    # Orphans: HTML on disk but not in the catalog — rebuild from page metadata.
    root = config.news_root_folder.rstrip("/")
    orphans = [p for p in glob.glob(f"{root}/articles/**/*.html", recursive=True)
               if p not in catalog_paths]
    o_ok = 0
    for p in orphans:
        html = open(p, encoding="utf-8").read()
        fam = p.split("/articles/")[1].split("/")[0]
        base = {
            "path": p,
            "url": _meta(html, r'rel="canonical"\s+href="([^"]+)"', r'og:url"\s+content="([^"]+)"'),
            "symbol": _meta(html, r'article:tag"\s+content="([^"]+)"', r'"tickerSymbol":\s*"([^"]+)"'),
            "market_family": fam,
            "title": _meta(html, r'"headline":\s*"([^"]+)"', r"<title>([^<]+)</title>"),
            "dek": _meta(html, r'name="description"\s+content="([^"]+)"', r'og:description"\s+content="([^"]+)"'),
            "published_date": _meta(html, r'"datePublished":\s*"([^"]+)"'),
        }
        try:
            o_ok += 1 if R._update_article_html(base, catalog, VOL, dry_run=False) else 0
        except Exception as e:
            print(f"[ERROR] orphan {p}: {e}")
    print(f"[ORPHANS] updated={o_ok} of {len(orphans)}")


if __name__ == "__main__":
    main()
