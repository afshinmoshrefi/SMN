"""Install reviewed subscription editions on the primary .180 Dev site only.

Uses the dashboard's catalog lock and native renderer; never copies application
code over Claude's live dashboard or changes pins, services, or production.
"""
from pathlib import Path
import argparse
import contextlib
import html
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
import membership_publication as membership
import membership_pipeline as pipeline

if os.environ.get('SMN_MEMBERSHIP_ENV_FILE'):
    pipeline.load_configuration(os.environ['SMN_MEMBERSHIP_ENV_FILE'])
    interpreter = os.environ.get('SMN_MEMBERSHIP_PYTHON')
    if interpreter:
        target = Path(interpreter)
        if not target.is_absolute() or not target.is_file() or target.name != 'python':
            raise ValueError('Explicit membership interpreter is unavailable')
        if Path(sys.prefix).absolute() != target.parent.parent.absolute():
            os.execv(str(target), [str(target), *sys.argv])

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


def merge_posts(previous, incoming, *, continuity=False):
    merged = {p['url']: p for p in previous}
    if len(merged) != len(previous):
        raise ValueError('Duplicate existing catalog URLs')
    for entry in incoming:
        if entry['url'] in merged and not continuity:
            raise ValueError('Edition already present; inspect receipt instead of republishing')
        if entry['url'] in merged and (merged[entry['url']].get('symbol') != entry.get('symbol') or
                                      merged[entry['url']].get('edition_id') != entry.get('edition_id')):
            raise ValueError('Continuity revision conflicts with retained article')
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
            search[post['url']] = {k:v for k,v in dict(post, month=post.get('published_date','')[:7]).items() if k != 'path'}
    write(candidate/'search_index.json', list(search.values()))


def prepare(package):
    guard()
    package = Path(package).resolve()
    manifest, entries = validate_package(package, ORIGIN, PRODUCTION)
    if PRODUCTION:
        from smn_runtime_assets import preflight
        preflight()
        # Resolve native feed imports before creating a publication transaction.
        import publish_article
    continuity = manifest.get('continuity_policy') == 1
    ident = ('smn-production-' if PRODUCTION else 'smn-primary-')+manifest['edition_date']+'-'+(manifest['transaction_id'][:16] if continuity else manifest['source_commit'][:10])
    record = STATE/ident
    intent = {'id':ident,'source_commit':manifest['source_commit'],
              'manifest_sha256':sha(package/'manifest.json')}
    if (record/'receipt.json').exists():
        prior = read(record/'receipt.json')
        if prior.get('transaction_id') != manifest.get('transaction_id') or prior.get('source_commit') != manifest['source_commit']:
            raise ValueError('Existing publication transaction differs')
        return {'record':str(record), **prior}
    if record.exists():
        if record.is_symlink() or not record.is_dir():
            raise ValueError('Unsafe existing publication record')
        marker=record/'transaction-intent.json'
        if marker.is_file():
            if read(marker)!=intent: raise ValueError('Interrupted publication intent differs')
        elif any(record.iterdir()):
            raise ValueError('Unowned interrupted publication record')
    else:
        record.mkdir(parents=True, mode=0o700)
    atomic(record/'transaction-intent.json',(json.dumps(intent,sort_keys=True)+'\n').encode())
    candidate = record/'candidate'
    if candidate.exists():
        archive=record/('candidate.incomplete.'+str(os.getpid()))
        if archive.exists(): raise ValueError('Interrupted candidate archive already exists')
        candidate.rename(archive)
    candidate.mkdir()
    with catalog_lock():
        previous = read(WEB/'posts.json')
        before = {n: sha(WEB/n) if (WEB/n).exists() else None for n in GENERATED}
        helpers = {n:(sha(BLOG/n) if (BLOG/n).exists() else None)
                   for n in (('rebuild_news_home.py','pin_store.py','article_index.py') +
                             (('membership_publication.py','article_content_store.py','reader_app.py','membership_pipeline.py') if membership.configured() else ()))}
        if helpers['rebuild_news_home.py'] is None or (not PRODUCTION and None in helpers.values()):
            raise ValueError('Required native renderer missing')
        pin_hash = sha(DASH/'pins.json') if (DASH/'pins.json').exists() else None
        gated = membership.configured()
        private = None
        if gated:
            import article_index
            if article_index.POSTS_JSON.resolve() != (WEB/'posts.json').resolve():
                raise ValueError('Membership publication catalog mismatch')
            membership.recover()
            previous = read(WEB/'posts.json')
            before = {n: sha(WEB/n) if (WEB/n).exists() else None for n in GENERATED}
            # All prior posts must already be valid private revisions.
            private = pipeline.prepare_batch(entries, package, record, manifest)
            entries = pipeline.read(private['journal'])['new_posts']
            articles = {}
        else:
            articles = {str(local_path(p['url']).relative_to(WEB)): sha(local_path(p['url'])) for p in previous}
        heroes = {}
        for p in previous:
            url = p.get('hero_image')
            if url and url.startswith(ORIGIN+'/') and not (gated and '/member/public-assets/' in url):
                path = local_path(url)
                heroes[str(path.relative_to(WEB))] = sha(path)
        posts = merge_posts(previous, entries, continuity=continuity)
        write(candidate/'posts.json', posts)
    for rel in manifest['files']:
        if rel.startswith('editions/') and not gated:
            dest = candidate/rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(package/rel, dest)
    date = manifest['edition_date']
    if not gated:
        write(candidate/'editions'/date/'entries.json', entries)
        write(candidate/'editions'/date/'provenance.json', {'source_commit':manifest['source_commit'],
              'publication_target':TARGET_NAME, 'engine_authority':'TradeWave'})
    render(candidate)
    if continuity:
        status = {k:manifest[k] for k in ('edition_date','revision','revision_id','transaction_id','selection_sha256','selection_status',
            'expected_symbols','published_symbols','pending_symbols','coverage_status','complete','source_commit')}
        write(candidate/'coverage-status.json',status)
        published = len(status['published_symbols'])
        total = len(status['expected_symbols'])
        message = (f'{date} morning coverage: {published} of {total} articles available.' if published else
                   f'{date} coverage is being prepared. Previous articles retain their original dates.' if not total else
                   f'{date} morning coverage is pending. Earlier articles retain their original dates.')
        if status['pending_symbols']:
            message += ' Pending: '+', '.join(status['pending_symbols'])+'.'
        notice = '<section id="coverage-status" role="status" style="padding:14px 24px;background:#f3f7fa;color:#183140"><strong>'+html.escape(message)+'</strong></section>'
        index = (candidate/'index.html').read_text(encoding='utf-8')
        if '<body' not in index.lower():
            raise ValueError('Native homepage has no body for coverage notice')
        index = re.sub(r'(<body[^>]*>)',lambda match:match.group(1)+notice,index,count=1,flags=re.I)
        (candidate/'index.html').write_text(index,encoding='utf-8')
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
    if not gated:
        for e in entries:
            path = urlsplit(e['url']).path.lstrip('/')
            articles[path] = sha(candidate/path)
    home = {'source_commit':manifest['source_commit'], 'edition_date':date,
            'article_count':len(posts), 'previous_article_urls':[p['url'] for p in previous],
            'articles':articles, 'files':{n:sha(candidate/n) for n in GENERATED if n != 'home-manifest.json'}}
    if continuity:
        home['coverage'] = read(candidate/'coverage-status.json')
    write(candidate/'home-manifest.json', home)
    files = {str(p.relative_to(candidate)):sha(p) for p in candidate.rglob('*') if p.is_file()}
    receipt = {'id':ident, 'status':'prepared', 'source_commit':manifest['source_commit'],
               'expected_symbols':manifest.get('expected_symbols'),
               'editorial_gate_version':manifest['editorial_gate_version'],
               'edition_date':date, 'before':before, 'runtime_helpers':helpers, 'pins_sha256':pin_hash,
               'expected_pins':read(DASH/'pins.json').get('pins',[]) if pin_hash else [], 'files':files,
               'retained_articles':{k:v for k,v in articles.items() if not k.startswith('editions/'+date+'/')},
               'retained_heroes':heroes, 'production_written':False, 'target_host':HOST_IP, 'urls':[p['url'] for p in entries]}
    if continuity:
        receipt.update({k:manifest[k] for k in ('revision','revision_id','transaction_id','selection_sha256','selection_status','published_symbols',
            'pending_symbols','coverage_status','complete','publication_policy')})
        receipt['continuity_policy'] = 1
        receipt['overwritten'] = {rel:sha(WEB/rel) for rel in files if rel not in GENERATED and (WEB/rel).is_file()}
    if gated:
        receipt['membership_publication'] = private
        home['membership_articles'] = [{k:v for k,v in row.items() if k not in {'source', 'expected_revision', 'raw_asset_urls'}} for row in private['new'] + private['retained']]
        write(candidate/'home-manifest.json', home)
        receipt['files']['home-manifest.json'] = sha(candidate/'home-manifest.json')
    write(record/'receipt.json', receipt)
    return {'record':str(record), **receipt}


def unchanged(receipt):
    if receipt.get('membership_publication'):
        # Incoming revisions are prepared but inactive; retained revisions must remain current.
        import article_index
        posts = {membership.path_for(p):p for p in read(article_index.POSTS_JSON)}
        for row in receipt['membership_publication']['retained']:
            if pipeline.describe(posts[row['canonical']]) != row:
                raise ValueError('Retained private article changed')
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
    if r.get('editorial_gate_version') != 1:
        raise ValueError('Prepared publication predates required editorial completion gate')
    if r['status'] != 'prepared':
        raise ValueError('Edition not prepared')
    if r.get('continuity_policy') == 1:
        if PRODUCTION or r.get('complete') is not (bool(r['expected_symbols']) and set(r['published_symbols']) == set(r['expected_symbols'])):
            raise ValueError('Invalid Dev continuity activation')
        if set(r['urls']) != {ORIGIN+'/editions/'+r['edition_date']+'/'+s+'/article.html' for s in r['published_symbols']}:
            raise ValueError('Continuity URLs differ from declared subset')
    elif PRODUCTION or r.get('expected_symbols') is not None:
        symbols=r.get('expected_symbols')
        if (not isinstance(symbols,list) or not 1<=len(symbols)<=6 or len(set(symbols))!=len(symbols) or
            r.get('urls') is None or len(r['urls'])!=len(symbols) or set(r['urls'])!={
                ORIGIN+'/editions/'+r['edition_date']+'/'+s+'/article.html' for s in symbols}):
            raise ValueError('Prepared publication lacks the complete selected lineup')
    lock = STATE/LOCK_NAME
    lock.mkdir()
    write(lock/'owner.json', {'task':r['id'], 'pid':os.getpid()})
    try:
        with catalog_lock():
            unchanged(r)
            for rel, digest in r['files'].items():
                if sha(record/'candidate'/rel) != digest:
                    raise ValueError('Candidate changed')
                if rel not in GENERATED and (WEB/rel).exists() and rel not in r.get('overwritten',{}):
                    raise ValueError('Edition path already exists: '+rel)
            backup = record/'backup'
            backup.mkdir()
            for n in GENERATED:
                if (WEB/n).exists(): shutil.copy2(WEB/n, backup/n)
            for rel, digest in r.get('overwritten',{}).items():
                if sha(WEB/rel) != digest:
                    raise ValueError('Earlier public revision changed: '+rel)
                dest=backup/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(WEB/rel,dest)
            r['status'] = 'activating'
            write(record/'receipt.json', r)
            if r.get('membership_publication'):
                pipeline.activate_batch(r['membership_publication'])
                qualification = pipeline.register_sources(r['membership_publication'])
                r['derivative_jobs'] = qualification['jobs']
                r['derivative_qualification_holds'] = qualification['holds']
                write(record/'receipt.json', r)
            # Private registry (or legacy files) first, then catalog, then homepage.
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
        if r.get('membership_publication'):
            pipeline.rollback_batch(r['membership_publication'])
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
    if r.get('membership_publication'):
        pipeline.verify_private(r['membership_publication'])
        verify_membership_proof(r['membership_publication'], proof)
    r['status'] = 'live_verified'
    write(record/'receipt.json', r)
    release_lock(r)
    return r


def verify_membership_proof(private, proof):
    rows = {row.get('canonical'): row for row in proof.get('membership_articles', [])}
    selected = private['new'] + sorted(private['retained'], key=lambda row: row['canonical'])[:RETAINED_SAMPLE]
    for expected in selected:
        actual = rows.get(expected['canonical'], {})
        if (actual.get('revision') != expected['revision'] or actual.get('preview_sha256') != expected['preview_sha256']
                or actual.get('full_html_sha256') != expected['full_html_sha256']
                or actual.get('public_preview_passed') is not True or actual.get('member_full_passed') is not True
                or actual.get('anonymous_full_absent') is not True):
            raise ValueError('Membership public/member verification missing: ' + expected['canonical'])
        raw = {a.get('url'):a.get('denied') for a in actual.get('raw_assets', [])}
        if any(raw.get(url) is not True for url in expected.get('raw_asset_urls', [])):
            raise ValueError('Anonymous raw engine asset denial missing')
        assets = {a.get('name'): a for a in actual.get('assets', [])}
        for asset in expected['assets']:
            result = assets.get(asset['name'], {})
            if result.get('sha256') != asset['sha256'] or result.get('member_passed') is not True:
                raise ValueError('Protected native asset verification missing: ' + asset['name'])
            if asset['public']:
                if result.get('public_passed') is not True:
                    raise ValueError('Approved public hero verification missing')
            elif result.get('anonymous_denied') is not True:
                raise ValueError('Anonymous protected asset denial missing')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare','activate','rollback','finish'])
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    print(json.dumps(globals()[args.action](args.path)))
