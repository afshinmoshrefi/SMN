"""Package reviewed subscription articles for SMN Dev. No config or network imports."""
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime, timezone
import hashlib,html,json,re,shutil

DEV='https://smn-dev.trxstat.com'
CHECKS={'facts_and_sources','why_now_and_opening','reader_value','history_and_numeric_meaning',
        'smn_identity_and_visuals','source_allowances','michael_brevity_and_clarity'}

def digest_bytes(data):return hashlib.sha256(data).hexdigest()
def sha256_equal(actual,expected):
    return (isinstance(actual,str) and isinstance(expected,str) and
            re.fullmatch(r'[0-9a-fA-F]{64}',actual) is not None and
            re.fullmatch(r'[0-9a-fA-F]{64}',expected) is not None and
            actual.casefold()==expected.casefold())
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def require_dev(url):
    u=urlparse(url)
    if u.scheme!='https' or u.netloc!='smn-dev.trxstat.com' or u.username or u.query or u.fragment:
        raise ValueError('Publication is restricted to the exact SMN Dev origin')

def digest(value):
    from visual_evidence import digest as d
    return d(value)

def reviewed(result,review_path):
    a=read(result/'article.json');m=read(result/'mechanical-checks.json');r=read(review_path)
    if not m.get('passed') or not sha256_equal(digest(a),m.get('article_sha256')):raise ValueError('Changed or mechanically held article')
    if (r.get('passed') is not True or set(r.get('checks',{}))!=CHECKS or
        any(v.get('passed') is not True for v in r['checks'].values()) or
        any(v.get('severity') in {'major','blocker'} for v in r.get('issues',[]))):
        raise ValueError('Independent review has not passed')
    binding=read(result/'review-binding.json')
    if (not sha256_equal(digest(a),binding.get('article_sha256')) or
        not sha256_equal(digest_bytes(Path(review_path).read_bytes()),binding.get('review_sha256'))):
        raise ValueError('Independent review is not bound to this exact article')
    bundle=read(result/'bundle.json')
    if m.get('evidence_sha256')!=bundle.get('evidence_sha256'):
        raise ValueError('Mechanical review uses different evidence')
    if m.get('copyedit_receipt'):
        ledger=result/m['copyedit_receipt']
        if (ledger.parent!=result or not sha256_equal(digest_bytes(ledger.read_bytes()),m.get('copyedit_receipt_sha256')) or
            not sha256_equal(read(ledger).get('article_sha256'),digest(a))):
            raise ValueError('Editorial change record differs from final copy')
    if bundle.get('seasonal_contract',{}).get('price_path_required'):
        from engine_seasonal import verify_assets, figure_html as native_figure
        native=read(result/'seasonal-manifest.json')
        path=native.get('price_path') or {}
        verify_assets(native,result)
        figure_html=lambda value:native_figure(value,'price_projection')
        if not sha256_equal(binding.get('price_path_sha256'),path.get('evidence_sha256')):
            raise ValueError('Independent review is not bound to the added price path')
        if not sha256_equal(binding.get('price_path_figure_sha256'),digest_bytes(figure_html(native).encode())):
            raise ValueError('Independent review is not bound to the final price-path wording')
        if figure_html(native) not in (result/'article.html').read_text(encoding='utf-8'):
            raise ValueError('Required price-path figure missing or changed')
        im=next(i for i in native['images'] if i['variant']=='price_projection')
        for file,sha in ((im['url'],im['sha256']),(im['mobile_url'],im['mobile_sha256']),
                         ('assets/tradewave-price-path.csv',native['price_path_csv_sha256'])):
            if not sha256_equal(digest_bytes((result/file).read_bytes()),sha):
                raise ValueError('Price-path asset differs from reviewed evidence')
    qa=read(result/'visual-checks.json')
    if not qa.get('passed') or not sha256_equal(qa.get('article_html_sha256'),digest_bytes((result/'article.html').read_bytes())):
        raise ValueError('Rendered-page review missing or stale')
    screenshots=qa.get('inspected_images')
    if screenshots is not None and (not isinstance(screenshots,dict) or any(
        not isinstance(name,str) or Path(name).name!=name or not (result/name).is_file() or
        not sha256_equal(digest_bytes((result/name).read_bytes()),expected)
        for name,expected in screenshots.items())):
        raise ValueError('Rendered-page screenshots missing or stale')
    return a

CSS='''
:root{--ink:#183140;--muted:#627781;--accent:#0066cc;--border:#dfe6e7;--soft:#f6f8fa}*{box-sizing:border-box}body{margin:0;font:16px/1.6 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--ink);background:#fff}header{border-bottom:1px solid var(--border);background:#fff}.header-content{max-width:1200px;margin:0 auto;padding:16px 24px;display:flex;justify-content:space-between;align-items:center;gap:24px}.logo{display:flex;align-items:baseline;gap:2px;text-decoration:none}.logo-seasonal,.logo-market{font-size:22px;font-weight:700;letter-spacing:-.5px}.logo-seasonal{color:var(--accent)}.logo-market{color:var(--ink)}.logo-news{font-size:22px;font-weight:400;letter-spacing:-.5px;color:var(--muted)}nav{display:flex;gap:28px}nav a{color:#526873;text-decoration:none;font-size:14px;font-weight:500}nav a:hover,.edition-card h3 a:hover{text-decoration:underline}.smn-edition{max-width:1200px;margin:0 auto;padding:42px 24px 54px}.section-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;padding-bottom:12px;border-bottom:2px solid var(--ink)}.section-title{font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase}.edition-intro{color:#526873;margin:0 0 24px}.edition-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}.edition-card{display:block;border:1px solid var(--border);background:#fff;color:inherit;text-decoration:none;transition:box-shadow .2s ease,transform .2s ease}.edition-card:hover{box-shadow:0 8px 24px #15334418;transform:translateY(-2px)}.edition-card img{width:100%;aspect-ratio:16/9;object-fit:cover;display:block;background:var(--soft)}.edition-copy{padding:18px}.edition-meta{font:12px/1.4 "IBM Plex Mono",monospace;color:var(--muted);text-transform:uppercase}.edition-card h3{font-size:20px;line-height:1.3;letter-spacing:-.3px;margin:8px 0}.edition-card h3 a{color:var(--ink);text-decoration:none}.edition-copy p{font-size:14px;line-height:1.55;color:#526873;margin:0 0 14px}.read-more{color:var(--accent);font-size:14px;font-weight:600}.edition-proof{margin-top:14px;font-size:12px;color:var(--muted)}.edition-proof summary{cursor:pointer}.edition-proof a{color:var(--muted)}footer{border-top:1px solid var(--border);padding:28px 24px;background:var(--soft)}.footer-content{max-width:1200px;margin:0 auto;display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}.footer-left,.footer-links a{font-size:13px;color:var(--muted);text-decoration:none}.footer-links{display:flex;gap:24px}@media(max-width:750px){.header-content{padding:16px 20px}.edition-grid{grid-template-columns:1fr}.smn-edition{padding:30px 20px 42px}.footer-content{flex-direction:column;text-align:center}.logo-seasonal,.logo-market,.logo-news{font-size:20px}}
'''

def edition_section(entries,date):
    esc=html.escape;cards=[]
    day=datetime.strptime(date,'%Y-%m-%d');label=day.strftime('%B')+' '+str(day.day)
    for p in entries:
        require_dev(p['url']);require_dev(p['hero_image'])
        cards.append('<article class="edition-card"><a href="'+esc(p['url'],quote=True)+'"><img src="'+esc(p['hero_image'],quote=True)+'" alt="'+esc(p['hero_alt'],quote=True)+'"></a><div class="edition-copy"><span class="edition-meta">'+esc(p['symbol'])+' · '+esc(p['market_family'])+'</span><h3><a href="'+esc(p['url'],quote=True)+'">'+esc(p['title'])+'</a></h3><p>'+esc(p['dek'])+'</p><a class="read-more" href="'+esc(p['url'],quote=True)+'">Read Analysis →</a><details class="edition-proof"><summary>Article details</summary><a href="'+esc(p['production_original'],quote=True)+'" target="_blank" rel="noopener">Original publication</a></details></div></article>')
    return '<main class="smn-edition" id="smn-subscription-edition"><div class="section-header"><span class="section-title">Market Analysis</span></div><h1>'+esc(label)+' market analysis</h1><p class="edition-intro">Data-backed coverage of seasonal market patterns and the current context around them.</p><div class="edition-grid">'+''.join(cards)+'</div></main>'

def package(edition_root,date,source_commit,review_stages):
    root=Path(edition_root).resolve()
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date) or not re.fullmatch(r'[0-9a-f]{40}',source_commit):raise ValueError('Dated, committed edition required')
    target=root/'publication-package'
    if any((root/name).exists() for name in ('dev-stage.json','dev-activation.json','dev-publication-receipt.json')):
        raise ValueError('Preserve prior publication package; use a new reviewed package')
    if (target.exists() or target.is_symlink()) and (not target.is_dir() or target.is_symlink() or any(target.iterdir())):
        raise ValueError('Preserve prior publication package; use a new reviewed package')
    entries=[];prepared=[];asof=datetime.now(timezone.utc).isoformat()
    for sym,stage in review_stages.items():
        if not re.fullmatch('[A-Z0-9]{1,12}',sym):raise ValueError('Invalid symbol')
        result=root/'results'/sym
        review=root/'jobs'/(sym+'-'+date.replace('-','')+'-'+stage)/'output.json'
        a=reviewed(result,review);commission=read(result/'commission.json');original=commission['production_article']
        rel=Path('editions')/date/sym
        b=read(result/'bundle.json');hero=read(result/'hero-asset.json')
        htmltext=(result/'article.html').read_text(encoding='utf-8')
        if 'Private draft · Editorial review has not passed.' in htmltext:raise ValueError('Unfinalized page')
        if 'Development preview · Not published' in htmltext:raise ValueError('Unfinalized footer')
        if 'name="robots" content="noindex,nofollow"' not in htmltext:raise ValueError('Dev search-engine exclusion missing')
        assets=[]
        # Copy ONLY public article assets. Never audit, jobs, credentials or source prompts.
        for folder,extensions in [('assets',{'.png','.jpg','.jpeg','.webp','.svg','.csv'}),('evidence',{'.json'})]:
            for f in (result/folder).iterdir():
                if not f.is_file() or f.is_symlink() or f.suffix.lower() not in extensions:continue
                assets.append((f,Path(folder)/f.name))
        url=DEV+'/'+rel.as_posix()+'/article.html'
        entry={k:original.get(k) for k in ('resource_id','symbol','tickers','market_family','pattern_start_date','pattern_days','author_id','direction')}
        entry.update(title=a['title'],dek=a['dek'],slug=sym.lower()+'-subscription-'+date,
            url=url,path='/var/www/smn/'+rel.as_posix()+'/article.html',lookback_years=original['lookback_years'],
            published_date=original['published_date'],updated_date=asof,tags=['subscription-edition'],
            hero_image=DEV+'/'+rel.as_posix()+'/'+hero['url'],hero_alt=hero['alt'],
            seo_title=a['title'],meta_description=a['dek'][:155],publish_status='true',
            production_original=original['url'],edition_id='subscription-'+date,source_commit=source_commit,
            production_release_allowed=False,history_validation=commission['history_status'])
        if (result/'generation.json').exists():
            generation=read(result/'generation.json')
            if not sha256_equal(digest(a),generation.get('article_sha256')) or generation['summary'].get('api_fallback') is not False:
                raise ValueError('Generation provenance is not bound to this exact article')
            if 'name="smn-generation"' not in htmltext:raise ValueError('Generation metadata missing from page')
            entry['generation']=generation['summary']
        entries.append(entry)
        prepared.append((rel,htmltext,assets))
    if len(entries)!=6:raise ValueError('This requested daily edition must contain all six reviewed subjects')
    if not target.exists(): target.mkdir()
    for rel,htmltext,assets in prepared:
        dest=target/rel;dest.mkdir(parents=True)
        (dest/'article.html').write_text(htmltext,encoding='utf-8')
        for source,relative in assets:
            d=dest/relative;d.parent.mkdir(exist_ok=True);shutil.copy2(source,d)
    entries.sort(key=lambda p:p['published_date'],reverse=True)
    section=edition_section(entries,date)
    landing='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Seasonal Market News</title><style>'+CSS+'</style></head><body><header><div class="header-content"><a href="/" class="logo"><span class="logo-seasonal">Seasonal</span><span class="logo-market">Market</span><span class="logo-news">News</span></a><nav><a href="https://tradewave.ai" target="_blank" rel="noopener">TradeWave</a></nav></div></header>'+section+'<footer><div class="footer-content"><div class="footer-left">© '+str(datetime.now().year)+' <a href="https://taradataresearch.com" target="_blank" rel="noopener">Tara Data Research LLC</a>. All rights reserved.</div><div class="footer-links"><a href="https://tradewave.ai" target="_blank" rel="noopener">TradeWave</a></div></div></footer></body></html>'
    (target/'editions'/date/'index.html').write_text(landing,encoding='utf-8')
    write(target/'entries.json',entries)
    if (root/'archive-seed.json').exists():
        shutil.copy2(root/'archive-seed.json',target/'archive-seed.json')
    (target/'home-section.html').write_text('<style>'+CSS+'</style>'+section,encoding='utf-8')
    manifest={'schema_version':1,'target_origin':DEV,'target_root':'/var/www/smn','edition_date':date,
      'edition_id':'subscription-'+date,'source_commit':source_commit,'production_allowed':False,
      'files':{p.relative_to(target).as_posix():digest_bytes(p.read_bytes()) for p in target.rglob('*') if p.is_file()}}
    write(target/'manifest.json',manifest);return manifest
