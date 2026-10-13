"""Freeze verified digest data and reserve only while its publication still binds."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date,datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib,json,os,re
from urllib.parse import urlsplit

CATALOG_LOCK=Path('/var/lib/smn-dashboard/posts.lock')
RELEASE_STATE=Path('/var/lib/tradewave/release-state')
# These are the identity and content fields consumed by the verified digest.
# Live quotes/volume metadata are intentionally absent: they are not email copy.
FIELDS=('url','slug','symbol','source_commit','edition_id','published_date','publish_status',
        'title','dek','meta_description','pattern_days','pattern_start_date','lookback_years',
        'direction','hero_status','hero_image')
def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def rows_json(rows):return encoded(sorted(rows,key=lambda row:row['url']))
def digest(raw):return hashlib.sha256(raw).hexdigest()

@contextmanager
def coordinated(root,day):
    date.fromisoformat(day)
    edition=Path(root)/day/'chatgpt'
    if not edition.is_dir() or edition.is_symlink():raise ValueError('Reader namespace missing or unsafe')
    from production_continuity import _lock
    # Same order as the rolling publisher: edition lease, then catalog lease.
    with _lock(edition):
        if CATALOG_LOCK.is_symlink():raise ValueError('Unsafe catalog lock')
        CATALOG_LOCK.parent.mkdir(parents=True,exist_ok=True)
        with CATALOG_LOCK.open('a+b') as handle:
            if os.name=='nt':
                import msvcrt
                handle.write(b'0');handle.flush();handle.seek(0)
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle,fcntl.LOCK_EX)
            try:yield
            finally:
                if os.name=='nt':
                    handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
                else:fcntl.flock(handle,fcntl.LOCK_UN)

@dataclass(frozen=True)
class ReaderSnapshot:
    root:Path
    day:str
    web:Path
    receipt_sha256:str
    manifest_sha256:str
    articles_json:str
    article_files_json:str
    @property
    def articles(self):return json.loads(self.articles_json)
    @property
    def urls(self):return {row['url'] for row in self.articles}
    @property
    def binding(self):
        return {'edition_date':self.day,'receipt_sha256':self.receipt_sha256,
                'manifest_sha256':self.manifest_sha256,
                'article_data_sha256':digest(self.articles_json.encode()),
                'article_data':self.articles,'article_files':json.loads(self.article_files_json)}

def _capture_locked(root,day,web,current):
    import operational_schedule as schedule
    bound=json.loads((root/day/'schedule-settings.json').read_text())
    setting=bound.get('weekday_newsletter') or schedule.operational_settings.for_date(day)['weekday_newsletter']
    local=current.astimezone(ZoneInfo(setting['timezone']))
    cutoff=datetime.combine(date.fromisoformat(day),datetime.strptime(setting['time'],'%H:%M').time(),ZoneInfo(setting['timezone']))
    if local.date().isoformat()!=day or local.weekday()>=5 or local<cutoff:
        raise ValueError('Verified digest cannot reserve before its same-day cutoff')
    urls=schedule.verified_reader_urls(root,day,web,current=current)
    if not urls:raise ValueError('Verified published reader coverage is not ready')
    path=root/day/'chatgpt/production-publication-receipt.json'
    if path.is_symlink():raise ValueError('Unsafe reader receipt')
    receipt_bytes=path.read_bytes();receipt=json.loads(receipt_bytes)
    transaction=Path(receipt.get('transaction_root',''))
    base=root/day/'chatgpt/publication-revisions'
    if (base.is_symlink() or transaction.is_symlink() or base.resolve() not in transaction.resolve().parents
            or any(parent.is_symlink() for parent in transaction.parents if parent!=root and root in parent.parents)):
        raise ValueError('Digest publication transaction is missing or unsafe')
    package=transaction/'publication-package';manifest_path=package/'manifest.json'
    if manifest_path.is_symlink():raise ValueError('Unsafe publication manifest')
    manifest_bytes=manifest_path.read_bytes();manifest=json.loads(manifest_bytes)
    ident=receipt.get('id','')
    if not isinstance(ident,str) or not re.fullmatch('smn-production-'+re.escape(day)+r'-[a-f0-9]{16}',ident):
        raise ValueError('Digest native publication identity is invalid')
    intent_path=RELEASE_STATE/ident/'transaction-intent.json'
    if intent_path.is_symlink():raise ValueError('Unsafe publication intent')
    intent=json.loads(intent_path.read_text())
    if (intent.get('id')!=ident or intent.get('source_commit')!=receipt['source_commit'] or
            intent.get('manifest_sha256')!=digest(manifest_bytes) or
            manifest.get('transaction_id')!=receipt.get('transaction_id') or
            manifest.get('source_commit')!=receipt['source_commit']):
        raise ValueError('Digest package is not bound to native publication intent')
    entries_path=package/'entries.json'
    if entries_path.is_symlink():raise ValueError('Unsafe publication entries')
    entries_bytes=entries_path.read_bytes()
    if digest(entries_bytes)!=manifest.get('files',{}).get('entries.json'):
        raise ValueError('Digest publication entries changed')
    entries=json.loads(entries_bytes)
    posts=json.loads((web/'posts.json').read_text())
    selected=[post for post in posts if post.get('url') in urls]
    if len(selected)!=len(urls):raise ValueError('Digest catalog rows are missing or ambiguous')
    files={}
    for post in selected:
        rel=urlsplit(post['url']).path.lstrip('/')
        if (post.get('source_commit')!=receipt['source_commit'] or
                post.get('edition_id')!='subscription-'+day or
                str(post.get('published_date',''))[:10]!=day or
                str(post.get('publish_status')).lower()!='true' or not post.get('slug')):
            raise ValueError('Digest catalog row no longer belongs to the verified publication')
        actual=digest((web/rel).read_bytes())
        if actual!=receipt.get('files',{}).get(rel):raise ValueError('Digest article bytes changed')
        original=[entry for entry in entries if entry.get('url')==post['url']]
        if len(original)!=1 or {key:post[key] for key in FIELDS if key in post}!={key:original[0][key] for key in FIELDS if key in original[0]}:
            raise ValueError('Digest catalog data differs from the immutable publication entries')
        files[rel]=actual
    rows=[{key:post[key] for key in FIELDS if key in post} for post in selected]
    return ReaderSnapshot(root,day,web,digest(receipt_bytes),digest(manifest_bytes),rows_json(rows),encoded(files))

def capture(root,day,web,*,current):
    root=Path(root).resolve();web=Path(web).resolve()
    with coordinated(root,day):return _capture_locked(root,day,web,current)

def reserve(snapshot,articles,persist,*,current):
    """Recheck and journal locally under publication locks; never calls a provider."""
    if type(snapshot) is not ReaderSnapshot or rows_json(articles)!=snapshot.articles_json:
        raise ValueError('Digest input differs from its immutable verified snapshot')
    with coordinated(snapshot.root,snapshot.day):
        latest=_capture_locked(snapshot.root,snapshot.day,snapshot.web,current)
        if latest!=snapshot:raise ValueError('Reader publication changed before campaign intent')
        return persist(snapshot.binding)
