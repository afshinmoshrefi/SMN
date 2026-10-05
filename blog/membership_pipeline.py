"""Private-store-first publication adapters; callers hold the catalog lock."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit
import uuid

import membership_publication as publication
from article_content_store import ContentError
from visual_evidence import digest


def read(path):
    return json.loads(Path(path).read_text('utf-8'))


def write(path, value):
    publication._write(Path(path), value)


def safe_file(root, relative):
    root = Path(root).resolve()
    relative = Path(relative)
    path = root / relative
    if (relative.is_absolute() or '..' in relative.parts or not path.is_file()
            or any(p.is_symlink() for p in (path, *path.parents)) or root not in path.resolve().parents):
        raise ContentError('Unsafe retained publication source')
    return path


def capture_sources(root, target, date, stages, *, fallbacks=None):
    """Retain reviewer inputs privately; never add them to public manifest files."""
    sources, files = {}, {}
    fallbacks=fallbacks or {}
    root, target = Path(root), Path(target)
    base = '.membership-sources/' + date

    def copy(source, relative):
        source = safe_file(root, source.relative_to(root))
        dest = target / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        files[relative] = publication.sha(dest.read_bytes())

    for name in ('daily-state.json', 'smn-daily-state.json'):
        if (root / name).is_file():
            copy(root/name, base + '/' + name)
    for symbol, stage in stages.items():
        article = root / 'results' / symbol
        review = root / 'jobs' / (symbol + '-' + date.replace('-', '') + '-' + stage)
        # Ungated legacy packages retain their existing behavior. Gated target
        # preparation requires a complete capsule and never invents approval.
        if not (article / 'source.json').is_file() or not (review / 'job.json').is_file():
            continue
        for path in article.rglob('*'):
            if path.is_symlink():
                raise ContentError('Retained article source cannot contain symlinks')
            if path.is_file():
                copy(path, base + '/results/' + symbol + '/' + path.relative_to(article).as_posix())
        for path in (root/'primary').glob(symbol + '*'):
            if path.is_file():
                copy(path, base + '/primary/' + path.name)
        visual_path=article/'visual-checks.json'
        if not visual_path.is_file() and symbol not in fallbacks:
            raise ContentError('Final article visual proof missing')
        visual = read(visual_path).get('inspector', {}).get('job_id') if visual_path.is_file() else None
        job_names = {review.name}
        if visual:
            if Path(visual).name != visual or not visual.startswith(symbol + '-'):
                raise ContentError('Invalid retained visual review job')
            job_names.add(visual)
        for job_name in job_names:
            job = root/'jobs'/job_name
            names = {'output.json', 'receipt.json', 'job.json'} | set(read(job/'job.json').get('input_hashes', {}))
            if (job/'editorial-context.json').is_file():
                names.add('editorial-context.json')
            for name in sorted(names):
                source = safe_file(job, name)
                copy(source, base + '/jobs/' + job_name + '/' + Path(name).as_posix())
        sources[symbol] = {'article': base + '/results/' + symbol, 'review': base + '/jobs/' + review.name}
    return sources, files


def retain_capsule(package, record, package_manifest, entries):
    """Verify all capsule bytes, re-run the original editorial gate on target."""
    from subscription_publication import reviewed
    root = publication.store().root / 'qualified-inputs' / publication.sha(str(Path(record).absolute()).encode())
    declared = package_manifest.get('private_files', {})
    expected = package_manifest.get('membership_sources', {})
    if (not declared and entries) or set(expected) != {e['symbol'] for e in entries}:
        raise ContentError('Every gated article requires retained source and independent reviewer evidence')
    for relative, expected_hash in declared.items():
        if not relative.startswith('.membership-sources/'):
            raise ContentError('Invalid private capsule path')
        source = safe_file(package, relative)
        if publication.sha(source.read_bytes()) != expected_hash:
            raise ContentError('Retained reviewer/source capsule changed')
        dest = root / relative
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        dest.write_bytes(source.read_bytes())
        os.chmod(dest, 0o600)
    private_owner(root)
    result = {}
    for entry in entries:
        source = expected[entry['symbol']]
        base = '.membership-sources/' + package_manifest['edition_date']
        if (source['article'] != base + '/results/' + entry['symbol']
                or not str(source['review']).startswith(base + '/jobs/' + entry['symbol'] + '-' + package_manifest['edition_date'].replace('-', '') + '-')
                or '..' in Path(source['review']).parts):
            raise ContentError('Reviewer capsule belongs to another article')
        article, review = root / source['article'], root / source['review']
        fallback = package_manifest.get('presentation_fallbacks', {}).get(entry['symbol'])
        if fallback:
            if (package_manifest.get('continuity_policy') != 1 or
                    package_manifest.get('publication_policy') != 'continuity-v1' or
                    package_manifest.get('target_origin') != 'https://smn-dev.trxstat.com' or
                    package_manifest.get('production_allowed') is not False):
                raise ContentError('Presentation fallback requires explicit Dev continuity policy')
            from presentation_fallback import verify_fallback
            raw = safe_file(package, urlsplit(entry['url']).path.lstrip('/')).read_text('utf-8')
            verify_fallback(article, raw, fallback)
            approved = read(article/'article.json')
        else:
            approved = reviewed(article, review / 'output.json')
        raw = safe_file(package, urlsplit(entry['url']).path.lstrip('/')).read_bytes()
        if (not fallback and raw != safe_file(article, 'article.html').read_bytes()) or approved['title'] != entry['title']:
            raise ContentError('Incoming publication differs from retained approved article')
        result[entry['slug']] = {'article': str(article), 'review': str(review)}
    return result


def describe(post, manifest=None):
    store = publication.store()
    manifest = manifest or store.resolve(publication.path_for(post))
    if manifest['revision'] != post.get('membership_revision'):
        raise ContentError('Catalog/private revision mismatch')
    full = store._private_file(manifest, 'full.html')
    if publication.sha(full.read_bytes()) != manifest['full_html_sha256']:
        raise ContentError('Retained private full article changed')
    assets = []
    from reader_app import asset_url
    for name, item in manifest['assets'].items():
        file = store._private_file(manifest, 'assets/' + name)
        if publication.sha(file.read_bytes()) != item['sha256']:
            raise ContentError('Retained private article asset changed')
        assets.append({'name': name, 'sha256': item['sha256'], 'public': item['public'],
                       'url': asset_url(publication.path_for(post), manifest['revision'], name, public=item['public'])})
    return {'canonical': publication.path_for(post), 'url': post['url'], 'slug': post['slug'],
            'revision': manifest['revision'], 'full_html_sha256': manifest['full_html_sha256'],
            'preview_sha256': digest(manifest['preview']['content']), 'preview_content': manifest['preview']['content'], 'assets': assets}


def prepare_batch(entries, package, record, package_manifest):
    import article_index
    sources = retain_capsule(package, record, package_manifest, entries)
    previous = {publication.path_for(p):p for p in read(article_index.POSTS_JSON)}
    updated, revisions = [], []
    for entry in entries:
        raw = safe_file(package, urlsplit(entry['url']).path.lstrip('/')).read_text('utf-8')
        old = previous.get(publication.path_for(entry)) if package_manifest.get('continuity_policy') == 1 else None
        new, manifest = publication.prepare(entry, raw, previous=old, source_root=package)
        private_owner(publication.store().revisions / manifest['storage'])
        updated.append(new)
        revisions.append(dict(describe(new, manifest), expected_revision=old.get('membership_revision') if old else None,
                              source=sources[new['slug']],
                              raw_asset_urls=[urlsplit(new['url']).scheme + '://' + urlsplit(new['url']).netloc + '/' + rel
                                  for rel in package_manifest['files']
                                  if rel.startswith(urlsplit(new['url']).path.lstrip('/').rsplit('/', 1)[0] + '/')
                                  and rel != urlsplit(new['url']).path.lstrip('/')]))
    journal = publication.store().root / 'membership-editions' / (publication.sha(str(Path(record).absolute()).encode()) + '.json')
    intent = {'id': Path(record).name, 'state': 'prepared', 'record': str(Path(record).absolute()),
              'new_posts': updated, 'revisions': revisions}
    if journal.exists() and read(journal) != intent:
        raise ContentError('Edition publication intent already exists')
    write(journal, intent)
    capsule = publication.store().root / 'qualified-inputs' / publication.sha(str(Path(record).absolute()).encode())
    return {'journal': str(journal), 'capsule_files':{str(capsule/relative):expected for relative,expected in package_manifest.get('private_files', {}).items()}, 'new': revisions,
            'retained': [describe(p) for p in previous.values() if publication.path_for(p) not in {r['canonical'] for r in revisions}]}


def _current(store, canonical):
    with store.database() as db:
        row = db.execute('SELECT revision FROM articles WHERE path=?', (canonical,)).fetchone()
        return row['revision'] if row else None


def activate_batch(membership):
    verify_capsule(membership)
    store = publication.store()
    journal = Path(membership['journal'])
    intent = read(journal)
    if intent['state'] not in {'prepared', 'activating', 'active'}:
        raise ContentError('Edition intent is not activatable')
    # Validate the whole batch before any change; a peer publication is never overwritten.
    for row in intent['revisions']:
        if _current(store, row['canonical']) not in {row['expected_revision'], row['revision']}:
            raise ContentError('Edition private revision changed')
    intent['state'] = 'activating'; write(journal, intent)
    for row in sorted(intent['revisions'], key=lambda r: r['canonical']):
        store.activate_revision(row['canonical'], row['revision'],
            intent['id'] + '/' + publication.sha(row['canonical'].encode()) + '/activate', row['expected_revision'])
    intent['state'] = 'active'; write(journal, intent)


def rollback_batch(membership):
    store = publication.store(); journal = Path(membership['journal']); intent = read(journal)
    for row in intent['revisions']:
        if _current(store, row['canonical']) not in {row['expected_revision'], row['revision']}:
            raise ContentError('Peer changed private edition; targeted recovery required')
    for row in sorted(intent['revisions'], key=lambda r: r['canonical']):
        if _current(store, row['canonical']) == row['revision']:
            tx = intent['id'] + '/' + publication.sha(row['canonical'].encode()) + '/rollback'
            if row['expected_revision'] is None:
                withdraw_revision(store, row['canonical'], row['revision'], tx)
            else:
                store.activate_revision(row['canonical'], row['expected_revision'], tx, row['revision'])
    intent['state'] = 'rolled_back'; write(journal, intent)


def register_sources(membership):
    """Register only byte-bound qualified evidence; jobs remain private drafts."""
    verify_capsule(membership)
    from public_derivative import prepare_derivative
    import promotion_jobs
    store = publication.store()
    jobs, holds = [], []
    for row in membership['new']:
        manifest = store.resolve(row['canonical'])
        if manifest['revision'] != row['revision'] or manifest['full_html_sha256'] != row['full_html_sha256']:
            raise ContentError('Edition source is no longer the active approved revision')
        article, review = Path(row['source']['article']), Path(row['source']['review'])
        mapping = {'canonical_id': row['url'], 'source_original_id': read(article/'source.json')['card']['production_original'],
                   'article_sha256': digest(read(article/'article.json')), 'revision': row['revision']}
        mapping['binding_sha256'] = digest(mapping)
        native = read(article/'seasonal-manifest.json')
        from engine_seasonal import verify_assets
        verify_assets(native, article)
        charts = {im['variant']: {'path': str(safe_file(article, im['url'])), 'sha256': im['sha256']}
                  for im in native['images']}
        try:
            prepared = prepare_derivative(article, review, row['url'], row['revision'], publication_identity=mapping)
        except ValueError as exc:
            holds.append({'slug':row['slug'], 'reason':str(exc), 'status':'held'})
            continue
        record = {'canonical': row['canonical'], 'active_revision': row['revision'], 'prepared': prepared,
                  'publication_identity': mapping, 'native_charts': charts, 'title': prepared['article']['title'],
                  'retained_input_hashes': membership.get('capsule_files', {})}
        path = store.root / 'qualified-sources' / (publication.sha(row['slug'].encode()) + '.json')
        if path.exists():
            prior = read(path)
            if prior.get('active_revision') == row['revision']:
                if prior.get('prepared') != prepared or prior.get('native_charts') != charts:
                    raise ContentError('Qualified source record changed during publication')
                record = prior
        write(path, record)
        private_owner(path)
        job = promotion_jobs.create(store.root/'promotion', 'derivative',
            {'article_id': row['url'], 'source_revision': row['revision'],
             'source_hash': prepared['provenance']['article_sha256'], 'payload_sha256':digest(prepared)}, 'qualified-edition-publisher')
        jobs.append(job)
        private_owner(promotion_jobs._path(store.root/'promotion', job['id']))
    return {'jobs':jobs, 'holds':holds}


def verify_private(membership):
    verify_capsule(membership)
    import article_index
    posts = {publication.path_for(p): p for p in read(article_index.POSTS_JSON)}
    for row in membership['new'] + membership['retained']:
        if describe(posts[row['canonical']]) != {k:v for k,v in row.items() if k not in {'expected_revision', 'source', 'raw_asset_urls'}}:
            raise ContentError('Private edition or retained article changed')


@contextmanager
def private_processing(config):
    """Post-processing datasets are produced privately, with canonical URLs intact."""
    store = publication.store()
    with tempfile.TemporaryDirectory(prefix='legacy-', dir=store.root) as stage:
        prior = config.news_root_folder
        config.news_root_folder = stage
        try:
            yield Path(stage)
        finally:
            config.news_root_folder = prior


def materialize_existing_assets(stage, raw, hero, public_root):
    # Existing generation artifacts are read into this isolated staging root;
    # newly generated dataset files already exist there and are never public.
    for value in re.findall(r'(?:https://(?:smn-dev\.trxstat\.com|(?:www\.)?seasonalmarketnews\.com))?/(?:articles|editions|datasets)/[^\s\"\'<>),;]+', raw + ' ' + hero):
        parsed = urlsplit(value)
        if parsed.query or parsed.fragment or '%' in parsed.path or '\\' in parsed.path or '..' in Path(parsed.path).parts:
            raise ContentError('Unsafe referenced publication asset')
        if parsed.path.endswith('.html'):
            continue
        relative = parsed.path.lstrip('/')
        dest = Path(stage)/relative
        if not dest.exists():
            ingress = getattr(_catalog, 'ingress', None)
            source_root = ingress if ingress and (Path(ingress)/relative).is_file() else public_root
            source = safe_file(source_root, relative)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(source.read_bytes())


def private_owner(path):
    """Root-only installer preserves the private store service owner's access."""
    root = publication.store().root
    path = Path(path)
    if root not in path.resolve().parents or any(p.is_symlink() for p in (path, *path.parents)):
        raise ContentError('Private ownership path escaped storage')
    owner = root.stat()
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        for item in [path, *path.rglob('*')] if path.is_dir() else [path]:
            if item.is_symlink():
                raise ContentError('Private ownership cannot follow symlinks')
            os.chown(item, owner.st_uid, owner.st_gid)
            os.chmod(item, 0o700 if item.is_dir() else 0o600)
        # Newly created ancestors must also be traversable by the same owner.
        for parent in path.parents:
            if parent == root:
                break
            os.chown(parent, owner.st_uid, owner.st_gid)
            os.chmod(parent, 0o700)


def withdraw_revision(store, canonical, expected, transaction):
    payload = {'action': 'withdraw', 'path': canonical}
    with store.database() as db:
        if store._transaction(db, transaction, payload):
            return
        row = db.execute('SELECT revision FROM articles WHERE path=?', (canonical,)).fetchone()
        if not row or row['revision'] != expected:
            raise ContentError('Private revision changed before withdrawal')
        db.execute('UPDATE articles SET revision=NULL WHERE path=?', (canonical,))
        db.execute('INSERT INTO transactions VALUES (?,?)', (transaction, json.dumps(payload)))


from functools import wraps
import threading
_catalog = threading.local()
_configuration_lock = threading.RLock()


def catalog_transaction(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not publication.configured() or getattr(_catalog, 'held', False):
            return function(*args, **kwargs)
        import article_index
        with _configuration_lock, article_index.posts_lock():
            publication.recover()
            _catalog.held = True
            try:
                return function(*args, **kwargs)
            finally:
                _catalog.held = False
    return wrapped


def load_configuration(path):
    """Read only publication settings; never evaluate shell or load service keys."""
    required = {'SMN_READER_PRIVATE_ROOT', 'SMN_NEWS_ROOT', 'SMN_READER_ENV', 'SMN_DASHBOARD_STATE'}
    allowed = required | {'SMN_MEMBERSHIP_PYTHON'}
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise ContentError('Protected membership publication configuration is unavailable')
    values = {}
    for line in path.read_text('utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = line.split('=', 1)
        if name not in allowed:
            continue
        if value[:1] in {'"', "'"}:
            if value[-1:] != value[:1]:
                raise ContentError('Malformed membership publication configuration')
            value = value[1:-1]
        if not value or '\x00' in value or name in values:
            raise ContentError('Invalid membership publication configuration')
        values[name] = value
    if not required <= set(values) or values['SMN_READER_ENV'] != 'dev':
        raise ContentError('Explicit complete Dev membership publication configuration required')
    os.environ.update(values)



def generation_scope(function):
    """A legacy queue attempt retains its exact engine assets outside nginx."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not publication.configured() and Path('/etc/SMN/membership.env').is_file():
            load_configuration('/etc/SMN/membership.env')
        if not publication.configured() or getattr(_catalog, 'ingress', None):
            return function(*args, **kwargs)
        import config
        with _configuration_lock:
            if Path(config.news_root_folder).resolve() != Path(os.environ['SMN_NEWS_ROOT']).resolve():
                raise ContentError('Legacy queue publication root differs from the reader gate')
            stage = publication.store().root / 'legacy-ingress' / uuid.uuid4().hex
            stage.mkdir(parents=True, mode=0o700)
            _catalog.ingress = stage
            try:
                result = function(*args, **kwargs)
                write(stage/'generation.json', {'id':stage.name, 'status':result.get('status') if isinstance(result, dict) else 'completed'})
                return result
            except BaseException:
                write(stage/'generation.json', {'id':stage.name, 'status':'failed'})
                raise
            finally:
                _catalog.ingress = None
                private_owner(stage)
    return wrapped


def asset_generation(function):
    """Only chart/hero writes use ingress; catalog/title/research paths stay canonical."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not publication.configured():
            return function(*args, **kwargs)
        stage = getattr(_catalog, 'ingress', None)
        if stage is None:
            raise ContentError('Private engine asset generation requires a queue attempt context')
        import config
        with _configuration_lock:
            previous = config.news_root_folder
            config.news_root_folder = str(stage)
            try:
                return function(*args, **kwargs)
            finally:
                config.news_root_folder = previous
    return wrapped



def verify_capsule(membership):
    root = publication.store().root
    for name, expected in membership.get('capsule_files', {}).items():
        path = Path(name)
        if (root not in path.resolve().parents or any(p.is_symlink() for p in (path, *path.parents))
                or not path.is_file() or publication.sha(path.read_bytes()) != expected):
            raise ContentError('Retained approved publication input changed')
