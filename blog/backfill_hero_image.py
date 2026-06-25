"""
backfill_hero_image.py
======================
Backfills the hero_image field in posts.json by reading the actual hero <img src>
from each article's HTML file. This is the authoritative source — it contains
the exact filename that was generated, including the article_id suffix.

Run dry-run first (default), then --apply to write:
    python backfill_hero_image.py
    python backfill_hero_image.py --apply

Add --force to overwrite already-set values (needed if old backfill stored wrong URLs):
    python backfill_hero_image.py --apply --force
"""
import sys
import re
import json
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, '/home/flask')
import config
import redis

redis_client = redis.Redis(host=config.webserver_ip, port=6379, db=3)


def _extract_hero_url(article_path):
    """Extract hero image URL from the article HTML's <figure class="hero"> tag."""
    try:
        html = Path(article_path).read_text(encoding='utf-8')
        match = re.search(
            r'<figure[^>]+class=["\']hero["\'][^>]*>\s*<img[^>]+src=["\']([^"\']+)["\']',
            html
        )
        if match:
            return match.group(1)
    except Exception:
        pass
    return None


def _normalize_url(url):
    """Re-anchor URL to current config.news_website_url (handles dev→prod moves)."""
    if not url:
        return url
    path = urlparse(url).path
    base = config.news_website_url.rstrip('/')
    return base + path


def run(dry_run=True, force=False):
    news_root = Path(config.news_root_folder).resolve()
    posts_json = news_root / 'posts.json'

    posts = json.loads(posts_json.read_text(encoding='utf-8'))

    updated = 0
    not_found = 0
    already_set = 0

    for p in posts:
        if p.get('hero_image') and not force:
            already_set += 1
            continue

        article_path = p.get('path', '')
        if not article_path or not Path(article_path).exists():
            not_found += 1
            print(f'[MISS] {p.get("symbol")} — path not found: {article_path}')
            continue

        hero_url = _extract_hero_url(article_path)
        if not hero_url:
            not_found += 1
            print(f'[MISS] {p.get("symbol")} — no hero img in HTML: {article_path}')
            continue

        hero_url = _normalize_url(hero_url)

        if dry_run:
            print(f'[DRY] {p.get("symbol")} → {hero_url}')
        else:
            p['hero_image'] = hero_url
            print(f'[SET] {p.get("symbol")} → {hero_url}')
        updated += 1

    print(f'\nTotal: {updated} to update, {already_set} already set, {not_found} not found')

    if not dry_run and updated > 0:
        posts_json.write_text(json.dumps(posts, indent=2, ensure_ascii=False), encoding='utf-8')
        print(f'[OK] posts.json saved.')

    # --- Sync hero_image into Redis ---
    # Build a slug→hero_image lookup from the (now updated) posts list
    slug_to_hero = {p.get('slug'): p.get('hero_image', '') for p in posts if p.get('slug')}

    redis_updated = 0
    for key in redis_client.scan_iter(match='*'):
        raw = redis_client.get(key)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        entry = payload.get('entry', {})
        slug = entry.get('slug', '')
        hero = slug_to_hero.get(slug, '')
        if not hero:
            continue
        if entry.get('hero_image') == hero and not force:
            continue
        if dry_run:
            print(f'[DRY-REDIS] {slug} → {hero}')
        else:
            entry['hero_image'] = hero
            payload['entry'] = entry
            redis_client.set(key, json.dumps(payload, ensure_ascii=False))
            print(f'[REDIS] {slug} → {hero}')
        redis_updated += 1

    print(f'Redis: {redis_updated} entries updated')


if __name__ == '__main__':
    dry_run = '--apply' not in sys.argv
    force = '--force' in sys.argv
    if dry_run:
        print('[DRY RUN] Pass --apply to write changes.\n')
    if force:
        print('[FORCE] Overwriting already-set hero_image values.\n')
    run(dry_run=dry_run, force=force)
