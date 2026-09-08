"""Apply a reviewed static SMN Dev package with a brief lock and local rollback.

This installer is deliberately bound to 192.168.1.180; it cannot publish to production.
No model calls, Redis, email, SEO pings, scheduler or service activation.
"""
from pathlib import Path
import datetime,hashlib,json,os,re,shutil,socket,subprocess,sys,uuid

ROOT=Path('/var/www/smn')
LOCK=Path('/var/lib/tradewave/release-state/dev-activation.lock')
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def hashfile(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def atomic(p,data):
    tmp=p.with_name(p.name+'.subscription-new')
    with tmp.open('wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    os.chmod(tmp,0o644);os.chown(tmp,33,33);os.replace(tmp,p)
def encode(v):return (json.dumps(v,ensure_ascii=False,separators=(',',':'))+'\n').encode()

def install(package):
    package=Path(package).resolve();m=read(package/'manifest.json')
    ips=subprocess.check_output(['hostname','-I'],text=True).split()
    if (socket.gethostname()!='SMN' or '192.168.1.180' not in ips or ROOT.resolve()!=ROOT or
        m.get('target_origin')!='https://smn-dev.trxstat.com' or m.get('target_root')!=str(ROOT) or
        m.get('production_allowed') is not False):raise ValueError('Exact SMN Dev host/root required')
    date=m['edition_date']
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date):raise ValueError('Invalid edition date')
    prefix='editions/'+date+'/'
    entries=read(package/'entries.json')
    if len(entries)!=6 or len({e['symbol'] for e in entries})!=6:raise ValueError('Six distinct subjects required')
    if any(not e['url'].startswith('https://smn-dev.trxstat.com/'+prefix) for e in entries):raise ValueError('Dev article URLs required')
    for rel,expected in m['files'].items():
        p=package/rel
        if p.resolve()!=p or package not in p.resolve().parents or p.is_symlink() or hashfile(p)!=expected:raise ValueError('Unsafe or changed package file')
        if not (rel.startswith(prefix) or rel in {'entries.json','home-section.html'}):raise ValueError('Unexpected package path')
    LOCK.parent.mkdir(parents=True,exist_ok=True);LOCK.mkdir()  # Never remove another task's lock.
    owner={'task':'SMN subscription edition '+date,'pid':os.getpid(),'source_commit':m['source_commit'],'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    backup=LOCK.parent/('subscription-'+date+'-'+datetime.datetime.now(datetime.timezone.utc).strftime('%H%M%S')+'-'+uuid.uuid4().hex[:8])
    written=[];previous={}
    try:
        (LOCK/'owner.json').write_bytes(encode(owner));backup.mkdir()
        catalog_state={name:hashfile(ROOT/name) if (ROOT/name).exists() else None
                       for name in ('posts.json','search_index.json','suggest.json','index.html')}
        files={rel:(package/rel).read_bytes() for rel in m['files'] if rel.startswith(prefix)}
        posts=read(ROOT/'posts.json')
        replacements={(e['symbol'],e['pattern_start_date'],e['pattern_days'],e['published_date'][:10]) for e in entries}
        def replaced(p):return (p.get('symbol'),p.get('pattern_start_date'),p.get('pattern_days'),p.get('published_date','')[:10]) in replacements or p.get('edition_id')==m['edition_id']
        posts=[p for p in posts if not replaced(p)]+entries
        posts.sort(key=lambda p:p.get('published_date',''),reverse=True)
        files['posts.json']=encode(posts)
        search=read(ROOT/'search_index.json')
        oldurls={p.get('url') for p in read(ROOT/'posts.json') if replaced(p)}|{e['url'] for e in entries}
        search=[p for p in search if p.get('url') not in oldurls]
        for e in entries:
            record={k:e[k] for k in ('title','url','symbol','market_family','dek','published_date','tags')}
            record.update(month=e['published_date'][:7],q=' '.join(str(e[k]) for k in ('symbol','title','dek','market_family')).lower())
            search.append(record)
        search.sort(key=lambda p:p.get('published_date',''),reverse=True)
        files['search_index.json']=encode(search)
        files['suggest.json']=encode([{k:p.get(k,[] if k=='tickers' else '') for k in ('title','url','symbol','tickers','published_date')} for p in posts])
        home=(ROOT/'index.html').read_text(encoding='utf-8')
        start='<!-- SMN SUBSCRIPTION EDITION START -->';end='<!-- SMN SUBSCRIPTION EDITION END -->'
        block=start+'\n'+(package/'home-section.html').read_text(encoding='utf-8')+'\n'+end
        if start in home:
            home=re.sub(re.escape(start)+r'.*?'+re.escape(end),lambda _:block,home,flags=re.S)
        else:
            marker='    <!-- Hero Section -->'
            if home.count(marker)!=1:raise ValueError('Existing homepage anchor changed')
            home=home.replace(marker,block+'\n'+marker,1)
        home=re.sub(r'<meta name="robots" content="[^"]*">','<meta name="robots" content="noindex,nofollow">',home,count=1)
        files['index.html']=home.encode()
        if any((hashfile(ROOT/name) if (ROOT/name).exists() else None)!=value for name,value in catalog_state.items()):
            raise ValueError('Dev catalogs changed during preparation; nothing installed')
        # Snapshot only affected paths, then install assets/articles before catalogs/home.
        for rel in files:
            dest=ROOT/rel
            if ROOT not in dest.resolve().parents or dest.is_symlink():raise ValueError('Destination escaped static dev root')
            previous[rel]=hashfile(dest) if dest.exists() else None
            if dest.exists():
                b=backup/rel;b.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(dest,b)
        (backup/'previous.json').write_bytes(encode(previous))
        for rel,data in files.items():
            dest=ROOT/rel;dest.parent.mkdir(parents=True,exist_ok=True)
            atomic(dest,data);written.append(rel)
        for rel,data in files.items():
            if hashfile(ROOT/rel)!=hashlib.sha256(data).hexdigest():raise ValueError('Installed bytes do not match package')
        receipt={**owner,'target':'SMN Dev only','backup':str(backup),'files':{rel:hashfile(ROOT/rel) for rel in files},
                 'urls':[e['url'] for e in entries],'status':'installed_pending_live_browser_check','production_written':False}
        (backup/'receipt.json').write_bytes(encode(receipt));print(json.dumps(receipt))
    except Exception:
        for rel in reversed(written):
            dest=ROOT/rel
            if previous[rel] is None:dest.unlink(missing_ok=True)
            else:atomic(dest,(backup/rel).read_bytes())
        raise
    finally:
        (LOCK/'owner.json').unlink(missing_ok=True);LOCK.rmdir()

if __name__=='__main__':install(sys.argv[1])
