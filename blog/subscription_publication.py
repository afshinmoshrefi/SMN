"""Package reviewed subscription articles for SMN Dev. No config or network imports."""
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime, timezone
import hashlib,html,json,re,shutil

DEV='https://smn-dev.trxstat.com'
CHECKS={'facts_and_sources','why_now_and_opening','reader_value','history_and_numeric_meaning',
        'smn_identity_and_visuals','source_allowances','michael_brevity_and_clarity'}

def digest_bytes(data):return hashlib.sha256(data).hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def require_dev(url):
    u=urlparse(url)
    if u.scheme!='https' or u.netloc!='smn-dev.trxstat.com' or u.username or u.query or u.fragment:
        raise ValueError('Publication is restricted to the exact SMN Dev origin')

def reviewed(result,review_path):
    from visual_evidence import digest
    a=read(result/'article.json');m=read(result/'mechanical-checks.json');r=read(review_path)
    if not m.get('passed') or m['article_sha256']!=digest(a):raise ValueError('Changed or mechanically held article')
    if (r.get('passed') is not True or set(r.get('checks',{}))!=CHECKS or
        any(v.get('passed') is not True for v in r['checks'].values()) or
        any(v.get('severity') in {'major','blocker'} for v in r.get('issues',[]))):
        raise ValueError('Independent review has not passed')
    binding=read(result/'review-binding.json')
    if binding.get('article_sha256')!=digest(a) or binding.get('review_sha256')!=digest_bytes(Path(review_path).read_bytes()):
        raise ValueError('Independent review is not bound to this exact article')
    qa=read(result/'visual-checks.json')
    if not qa.get('passed') or qa.get('article_html_sha256')!=digest_bytes((result/'article.html').read_bytes()):
        raise ValueError('Rendered-page review missing or stale')
    return a

CSS='''
.smn-edition{max-width:1160px;margin:28px auto 38px;padding:22px;font:16px/1.55 system-ui;color:#153344;background:#f4f7fa;border-top:4px solid #145d68}.smn-edition h2{font-size:28px;line-height:1.2;margin:0 0 8px}.smn-edition .edition-intro{margin:0 0 22px}.smn-edition .edition-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:22px}.smn-edition .edition-card{background:#fff;border:1px solid #dce4e6;overflow:hidden;border-radius:5px}.smn-edition .edition-card img{width:100%;height:160px;object-fit:cover;display:block}.smn-edition .edition-copy{padding:16px}.smn-edition .edition-copy h3{margin:6px 0 12px;font-size:20px;line-height:1.3}.smn-edition a{color:#145d68;text-decoration:none}.smn-edition a:hover{text-decoration:underline}.smn-edition .edition-copy p{font-size:14px;line-height:1.55;margin:0 0 14px}.smn-edition .edition-meta{font-size:12px;color:#526873}.smn-edition .edition-original{font-size:12px;display:block;margin-top:12px}@media(max-width:750px){.smn-edition .edition-grid{grid-template-columns:1fr}.smn-edition{margin:20px 12px;padding:16px}.smn-edition .edition-card img{height:auto;aspect-ratio:2.33}.smn-edition h2{font-size:25px}}
'''

def edition_section(entries,date):
    esc=html.escape;cards=[]
    day=datetime.strptime(date,'%Y-%m-%d');label=day.strftime('%B')+' '+str(day.day)
    for p in entries:
        require_dev(p['url']);require_dev(p['hero_image'])
        cards.append('<div class="edition-card"><a href="'+esc(p['url'],quote=True)+'"><img src="'+esc(p['hero_image'],quote=True)+'" alt="'+esc(p['hero_alt'],quote=True)+'"></a><div class="edition-copy"><span class="edition-meta">'+esc(p['symbol'])+' · '+esc(p['market_family'])+'</span><h3><a href="'+esc(p['url'],quote=True)+'">'+esc(p['title'])+'</a></h3><p>'+esc(p['dek'])+'</p><a href="'+esc(p['url'],quote=True)+'">Read article →</a><a class="edition-original" href="'+esc(p['production_original'],quote=True)+'">Compare with production original</a></div></div>')
    return '<section class="smn-edition" id="smn-subscription-edition"><h2>'+esc(label)+': The Updated SMN Edition</h2><p class="edition-intro">Today’s six production subjects, recreated with the updated editorial workflow for SMN Dev. TradeWave analysis, current context and additional visuals.</p><div class="edition-grid">'+''.join(cards)+'</div></section>'

def package(edition_root,date,source_commit,review_stages):
    root=Path(edition_root).resolve()
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date) or not re.fullmatch(r'[0-9a-f]{40}',source_commit):raise ValueError('Dated, committed edition required')
    target=root/'publication-package'
    if target.exists():raise ValueError('Preserve prior publication package; use a new reviewed package')
    target.mkdir();entries=[];asof=datetime.now(timezone.utc).isoformat()
    for sym,stage in review_stages.items():
        if not re.fullmatch('[A-Z0-9]{1,12}',sym):raise ValueError('Invalid symbol')
        result=root/'results'/sym
        review=root/'jobs'/(sym+'-'+date.replace('-','')+'-'+stage)/'output.json'
        a=reviewed(result,review);commission=read(result/'commission.json');original=commission['production_article']
        rel=Path('editions')/date/sym;dest=target/rel;dest.mkdir(parents=True)
        b=read(result/'bundle.json');hero=read(result/'hero-asset.json')
        htmltext=(result/'article.html').read_text(encoding='utf-8')
        if 'Private draft · Editorial review has not passed.' in htmltext:raise ValueError('Unfinalized page')
        if 'Development preview · Not published' in htmltext:raise ValueError('Unfinalized footer')
        if 'name="robots" content="noindex,nofollow"' not in htmltext:raise ValueError('Dev search-engine exclusion missing')
        (dest/'article.html').write_text(htmltext,encoding='utf-8')
        # Copy ONLY public article assets. Never audit, jobs, credentials or source prompts.
        for folder,extensions in [('assets',{'.png','.jpg','.jpeg','.webp','.svg','.csv'}),('evidence',{'.json'})]:
            for f in (result/folder).iterdir():
                if not f.is_file() or f.is_symlink() or f.suffix.lower() not in extensions:continue
                d=dest/folder/f.name;d.parent.mkdir(exist_ok=True);shutil.copy2(f,d)
        url=DEV+'/'+rel.as_posix()+'/article.html'
        entry={k:original.get(k) for k in ('resource_id','symbol','tickers','market_family','pattern_start_date','pattern_days','author_id','direction')}
        entry.update(title=a['title'],dek=a['dek'],slug=sym.lower()+'-subscription-'+date,
            url=url,path='/var/www/smn/'+rel.as_posix()+'/article.html',lookback_years='20',
            published_date=original['published_date'],updated_date=asof,tags=['subscription-edition'],
            hero_image=DEV+'/'+rel.as_posix()+'/'+hero['url'],hero_alt=hero['alt'],
            seo_title=a['title'],meta_description=a['dek'][:155],publish_status='true',
            production_original=original['url'],edition_id='subscription-'+date,source_commit=source_commit,
            production_release_allowed=False,history_validation=commission['history_status'])
        entries.append(entry)
    if len(entries)!=6:raise ValueError('This requested daily edition must contain all six reviewed subjects')
    entries.sort(key=lambda p:p['published_date'],reverse=True)
    section=edition_section(entries,date)
    landing='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>'+date+' · SMN Dev Edition</title><style>body{margin:0;background:#fff}header{padding:20px 24px;font:700 22px system-ui;border-bottom:1px solid #dce4e6}header a{color:#153344;text-decoration:none}'+CSS+'</style></head><body><header><a href="/">Seasonal Market News · Dev</a></header>'+section+'</body></html>'
    (target/'editions'/date/'index.html').write_text(landing,encoding='utf-8')
    write(target/'entries.json',entries)
    (target/'home-section.html').write_text('<style>'+CSS+'</style>'+section,encoding='utf-8')
    manifest={'schema_version':1,'target_origin':DEV,'target_root':'/var/www/smn','edition_date':date,
      'edition_id':'subscription-'+date,'source_commit':source_commit,'production_allowed':False,
      'files':{p.relative_to(target).as_posix():digest_bytes(p.read_bytes()) for p in target.rglob('*') if p.is_file()}}
    write(target/'manifest.json',manifest);return manifest
