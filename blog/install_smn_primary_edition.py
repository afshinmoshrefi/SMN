"""Install reviewed subscription editions on the primary .180 Dev site only.

Uses the dashboard's catalog lock and native renderer; never copies application
code over Claude's live dashboard or changes pins, services, or production.
"""
from pathlib import Path
import argparse
import contextlib
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import types
from urllib.parse import urlsplit
from install_smn_recovery_edition import validate_package, sha, read, write, atomic

WEB = Path('/var/www/smn')
STATE = Path('/var/lib/tradewave/release-state')
DASH = Path('/var/lib/smn-dashboard')
BLOG = Path('/home/flask/blog')
ORIGIN = 'https://smn-dev.trxstat.com'
GENERATED = ('posts.json', 'index.html', 'suggest.json', 'home-manifest.json', 'search_index.json', 'search.html')
PRODUCTION = False
HOST_IP = '192.168.1.180'
LOCK_NAME = 'dev-activation.lock'
TARGET_NAME = 'SMN primary Dev'


def configure_production():
    """Explicit operator-installed activation; the default installer stays Dev-only."""
    global ORIGIN, PRODUCTION, HOST_IP, LOCK_NAME, TARGET_NAME, GENERATED
    activation = read('/etc/SMN/subscription-primary.json')
    commit = subprocess.check_output(['git', '-C', str(Path(__file__).resolve().parent.parent),
                                      'rev-parse', 'HEAD'], text=True).strip()
    if activation.get('reader_provider') != 'chatgpt' or activation.get('source_commit') != commit:
        raise ValueError('Human-installed production activation must match this committed release')
    ORIGIN, PRODUCTION = 'https://seasonalmarketnews.com', True
    HOST_IP, LOCK_NAME, TARGET_NAME = '209.182.216.112', 'smn-production-activation.lock', 'SMN production'
    GENERATED = tuple(dict.fromkeys(GENERATED + ('sitemap.xml', 'sitemap-news.xml', 'rss.xml', 'robots.txt', 'llms.txt')))


RETAINED_SAMPLE = 40  # retained files each publish verifies through the public site


def guard():
    if HOST_IP not in subprocess.check_output(['hostname', '-I'], text=True).split():
        raise ValueError('Publication host mismatch')
    conf = Path('/etc/nginx/sites-enabled/smn.conf').read_text()
    if urlsplit(ORIGIN).netloc not in conf or 'root /var/www/smn;' not in conf:
        raise ValueError('Publication nginx root changed')
    if WEB.is_symlink() or not (WEB/'posts.json').is_file():
        raise ValueError('Expected primary Dev catalog')


@contextlib.contextmanager
def catalog_lock():
    DASH.mkdir(parents=True, exist_ok=True)
    with (DASH/'posts.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def local_path(url):
    u = urlsplit(url)
    if u.netloc not in ({'seasonalmarketnews.com','www.seasonalmarketnews.com'} if PRODUCTION else {'smn-dev.trxstat.com'}) or u.scheme != 'https' or u.query or u.fragment:
        raise ValueError('Catalog URL outside publication origin')
    path = WEB/u.path.lstrip('/')
    if WEB not in path.resolve().parents or path.is_symlink() or not path.is_file():
        raise ValueError('Missing/unsafe archived article: '+u.path)
    return path


def merge_posts(previous, incoming):
    merged = {p['url']: p for p in previous}
    if len(merged) != len(previous):
        raise ValueError('Duplicate existing catalog URLs')
    for entry in incoming:
        if entry['url'] in merged:
            raise ValueError('Edition already present; inspect receipt instead of republishing')
        item = dict(entry)
        item.setdefault('slug', item['symbol'].lower()+'-subscription-'+str(item['edition_id']))
        merged[item['url']] = item
    return sorted(merged.values(), key=lambda p: (p.get('published_date',''), p['url']), reverse=True)


def noindex_html(html):
    """Keep the native page intact while replacing any robots indexing policy."""
    html = re.sub(r"<meta\b(?=[^>]*\bname\s*=\s*['\"]robots['\"])[^>]*>", '', html, flags=re.I)
    html, count = re.subn(r'</head\s*>', '<meta name="robots" content="noindex,nofollow">\n</head>', html, count=1, flags=re.I)
    if count != 1:
        raise ValueError('Native page has no head closing tag')
    return html


def render(candidate):
    # Use the installed dashboard template, with output paths scoped to candidate.
    # The two legacy security-page price updaters are outside this publication.
    sys.path.insert(0, '/home/flask')
    sys.path.insert(0, str(BLOG))
    import rebuild_news_home as home
    home.NEWS_ROOT = candidate
    home.POSTS_JSON = candidate/'posts.json'
    home.INDEX_HTML = candidate/'index.html'
    home.SUGGEST_JSON = candidate/'suggest.json'
    for name, func in [('generate_security_pages','inject_security_prices'),
                       ('generate_tw_security_pages','inject_tw_security_prices')]:
        module = types.ModuleType(name)
        setattr(module, func, lambda: None)
        sys.modules[name] = module
    with contextlib.redirect_stdout(sys.stderr):
        home.build_home()
    # Native search consumes search_index.json, while autocomplete uses suggest.
    posts = read(candidate/'posts.json')
    prior = read(WEB/'search_index.json') if (WEB/'search_index.json').exists() else []
    search = {p['url']:p for p in prior}
    for post in posts:
        if post['url'] not in search:
            search[post['url']] = {**post, 'month':post.get('published_date','')[:7]}
    write(candidate/'search_index.json', list(search.values()))


def prepare(package):
    guard()
    package = Path(package).resolve()
    manifest, entries = validate_package(package, ORIGIN, PRODUCTION)
    ident = ('smn-production-' if PRODUCTION else 'smn-primary-')+manifest['edition_date']+'-'+manifest['source_commit'][:10]
    record = STATE/ident
    record.mkdir(parents=True)
    candidate = record/'candidate'
    candidate.mkdir()
    with catalog_lock():
        previous = read(WEB/'posts.json')
        before = {n: sha(WEB/n) if (WEB/n).exists() else None for n in GENERATED}
        helpers = {n:(sha(BLOG/n) if (BLOG/n).exists() else None)
                   for n in ('rebuild_news_home.py','pin_store.py','article_index.py')}
        if helpers['rebuild_news_home.py'] is None or (not PRODUCTION and None in helpers.values()):
            raise ValueError('Required native renderer missing')
        pin_hash = sha(DASH/'pins.json') if (DASH/'pins.json').exists() else None
        articles = {str(local_path(p['url']).relative_to(WEB)): sha(local_path(p['url'])) for p in previous}
        heroes = {}
        for p in previous:
            url = p.get('hero_image')
            if url and url.startswith(ORIGIN+'/'):
                path = local_path(url)
                heroes[str(path.relative_to(WEB))] = sha(path)
        posts = merge_posts(previous, entries)
        write(candidate/'posts.json', posts)
    for rel in manifest['files']:
        if rel.startswith('editions/'):
            dest = candidate/rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(package/rel, dest)
    date = manifest['edition_date']
    write(candidate/'editions'/date/'entries.json', entries)
    write(candidate/'editions'/date/'provenance.json', {'source_commit':manifest['source_commit'],
          'publication_target':TARGET_NAME, 'engine_authority':'TradeWave'})
    render(candidate)
    for name in ('index.html', 'search.html'):
        source = candidate/name if name == 'index.html' else WEB/name
        text = source.read_text(encoding='utf-8')
        (candidate/name).write_text(text if PRODUCTION else noindex_html(text), encoding='utf-8')
    if PRODUCTION:
        # Reuse native sitemap/feed generation against the candidate, before activation.
        import config
        import publish_article
        previous_root = config.news_root_folder
        config.news_root_folder = str(candidate)
        try:
            with contextlib.redirect_stdout(sys.stderr):
                for name in ('generate_sitemap','generate_news_sitemap','generate_rss_feed','generate_llms_txt'):
                    getattr(publish_article, name)()
        finally:
            config.news_root_folder = previous_root
        shutil.copy2(WEB/'robots.txt', candidate/'robots.txt')
        if any(not (candidate/n).is_file() for n in GENERATED):
            # home-manifest is written below.
            missing = [n for n in GENERATED if n != 'home-manifest.json' and not (candidate/n).is_file()]
            if missing: raise ValueError('Native production outputs missing: '+str(missing))
    for e in entries:
        path = urlsplit(e['url']).path.lstrip('/')
        articles[path] = sha(candidate/path)
    home = {'source_commit':manifest['source_commit'], 'edition_date':date,
            'article_count':len(posts), 'previous_article_urls':[p['url'] for p in previous],
            'articles':articles, 'files':{n:sha(candidate/n) for n in GENERATED if n != 'home-manifest.json'}}
    write(candidate/'home-manifest.json', home)
    files = {str(p.relative_to(candidate)):sha(p) for p in candidate.rglob('*') if p.is_file()}
    receipt = {'id':ident, 'status':'prepared', 'source_commit':manifest['source_commit'],
               'edition_date':date, 'before':before, 'runtime_helpers':helpers, 'pins_sha256':pin_hash,
               'expected_pins':read(DASH/'pins.json').get('pins',[]) if pin_hash else [], 'files':files,
               'retained_articles':{k:v for k,v in articles.items() if not k.startswith('editions/'+date+'/')},
               'retained_heroes':heroes, 'production_written':False, 'target_host':HOST_IP, 'urls':[p['url'] for p in entries]}
    write(record/'receipt.json', receipt)
    return {'record':str(record), **receipt}


def unchanged(receipt):
    for name, digest in receipt['runtime_helpers'].items():
        if (sha(BLOG/name) if (BLOG/name).exists() else None) != digest:
            raise ValueError('Dashboard renderer changed during preparation')
    for n, digest in receipt['before'].items():
        if (sha(WEB/n) if (WEB/n).exists() else None) != digest:
            raise ValueError('Catalog/template changed during preparation: '+n)
    if (sha(DASH/'pins.json') if (DASH/'pins.json').exists() else None) != receipt['pins_sha256']:
        raise ValueError('Dashboard pins changed during preparation')
    for rel, digest in {**receipt['retained_articles'], **receipt.get('retained_heroes',{})}.items():
        if sha(WEB/rel) != digest:
            raise ValueError('Archived article changed: '+rel)


def activate(record):
    guard()
    record = Path(record)
    r = read(record/'receipt.json')
    if r['status'] != 'prepared':
        raise ValueError('Edition not prepared')
    lock = STATE/LOCK_NAME
    lock.mkdir()
    write(lock/'owner.json', {'task':r['id'], 'pid':os.getpid()})
    try:
        with catalog_lock():
            unchanged(r)
            for rel, digest in r['files'].items():
                if sha(record/'candidate'/rel) != digest:
                    raise ValueError('Candidate changed')
                if rel not in GENERATED and (WEB/rel).exists():
                    raise ValueError('Edition path already exists: '+rel)
            backup = record/'backup'
            backup.mkdir()
            for n in GENERATED:
                if (WEB/n).exists(): shutil.copy2(WEB/n, backup/n)
            r['status'] = 'activating'
            write(record/'receipt.json', r)
            # Articles first, then catalog, then homepage: no link precedes its file.
            order = sorted(r['files'], key=lambda n: n in GENERATED)
            for rel in order:
                dest = WEB/rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                atomic(dest, (record/'candidate'/rel).read_bytes())
            r['status'] = 'active_pending_live_verification'
            write(record/'receipt.json', r)
    except BaseException:
        if r['status'] == 'activating':
            rollback(record)
        else:
            (lock/'owner.json').unlink(); lock.rmdir()
        raise
    return r


def release_lock(r):
    lock = STATE/LOCK_NAME
    if read(lock/'owner.json')['task'] != r['id']:
        raise ValueError('Activation lock owner changed')
    (lock/'owner.json').unlink()
    lock.rmdir()


def rollback(record):
    guard()
    record = Path(record)
    r = read(record/'receipt.json')
    with catalog_lock():
        # Preserve peer edits instead of reverting the whole directory blindly.
        for rel, digest in r['files'].items():
            current = WEB/rel
            old = record/'backup'/rel
            if current.exists() and sha(current) not in {digest, sha(old) if old.exists() else None}:
                raise ValueError('Peer changed published file; targeted recovery required: '+rel)
        for rel in r['files']:
            old = record/'backup'/rel
            if old.exists(): atomic(WEB/rel, old.read_bytes())
            elif (WEB/rel).exists(): (WEB/rel).unlink()
        r['status'] = 'rolled_back'
        write(record/'receipt.json', r)
        release_lock(r)
    return r


def finish(record):
    guard()
    record = Path(record)
    r = read(record/'receipt.json')
    proof = read(record/'live-verification.json')
    if r['status'] != 'active_pending_live_verification' or not proof.get('passed'):
        raise ValueError('Live verification required')
    if proof.get('source_commit') != r['source_commit']:
        raise ValueError('Verification source mismatch')
    if (sha(DASH/'pins.json') if (DASH/'pins.json').exists() else None) != r['pins_sha256']:
        raise ValueError('Pin state changed during publication')
    verified = {p['rel']:p['sha256'] for p in proof.get('public_files',[]) if p.get('passed')}
    # Every file of this edition must be verified through the public site. Retained archive files
    # must still match on disk, and a sample of them through the public site: hashing all ~1,400
    # publicly ran ~20 h and exhausted Dev (Sept 29-30); this publish does not modify them.
    for rel, digest in r['files'].items():
        if sha(WEB/rel) != digest or verified.get(rel) != digest:
            raise ValueError('Public verification missing/changed: '+rel)
    for group in ('retained_articles', 'retained_heroes'):
        retained = r.get(group, {})
        for rel, digest in retained.items():
            if sha(WEB/rel) != digest or (rel in verified and verified[rel] != digest):
                raise ValueError('Public verification missing/changed: '+rel)
        if sum(rel in verified for rel in retained) < min(RETAINED_SAMPLE, len(retained)):
            raise ValueError('Public verification missing/changed: too few %s sampled' % group)
    r['status'] = 'live_verified'
    write(record/'receipt.json', r)
    release_lock(r)
    return r


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare','activate','rollback','finish'])
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    print(json.dumps(globals()[args.action](args.path)))
