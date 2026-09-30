"""Canonical subscription inputs from the daily selector, never published prose.

Selection is read-only. Hero creation is a separate, explicit API-cost stage.
TradeWave evidence is captured by subscription_capture.engine unchanged.
"""
from __future__ import annotations
import base64
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from subscription_capture import Held, PRODUCTION_HOST, _date, _write_once


def _json(value):
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False).encode()


def _sha(blob):
    return hashlib.sha256(blob).hexdigest()


REMOTE_SELECTION = r'''import base64,contextlib,datetime,hashlib,io,json,os,sys
from pathlib import Path
date=datetime.date.fromisoformat(sys.argv[1]); blog=Path(os.environ.get('SMN_INPUT_SOURCE_BLOG','/home/flask/blog'))
sys.path.insert(0,'/home/flask')
sys.path.insert(0,str(blog))
with contextlib.redirect_stdout(io.StringIO()):
 import daily_article_queue as q
 q.TODAY=date
 csvpath=Path(q.CSV_DIR)/q.CSV_FILE_TPL.format(date.isoformat())
 if not csvpath.is_file(): sys.exit(75)
 blob=csvpath.read_bytes(); rows=q.filter_ideas(q.read_ideas(str(csvpath)))
 dynamic=bool(getattr(q.config,'dynamic_count_enabled',False))
 if dynamic: selected,reasons,backfilled=q.select_lineup(rows)
 else: selected=q.select_diverse_lineup(rows,q.ARTICLES_PER_DAY); reasons={}; backfilled=False
 for row in selected: row['featured_score']=q.featured_score(row)
 selected.sort(key=lambda r:r['featured_score'])
 families={str(r['pat_resource_id']):q.config.exchange_mapping[str(r['pat_resource_id'])] for r in selected}
 config={k:getattr(q,k) for k in ('USERID','ARTICLES_PER_DAY','MIN_SCORE','REQUIRE_IN_NEWS','ENSURE_EARNINGS','ENSURE_NON_STOCK','MAX_PER_SECTOR','MAX_INDICES','DYN_MIN_SCORE','DYN_CLIFF_DROP','DYN_CLIFF_CAP','DYN_MAX_DAYS_NEWSLESS','DYN_MAX_DAYS_ANY','DYN_SOFT_MIN','DYN_LEGACY_FLOOR','DYN_RVOL_PEG')}
print(json.dumps({'date':date.isoformat(),'csv_base64':base64.b64encode(blob).decode(),'rows':selected,'market_families':families,'selection':{'module_path':str(Path(q.__file__).resolve()),'module_sha256':hashlib.sha256(Path(q.__file__).read_bytes()).hexdigest(),'csv_path':str(csvpath),'dynamic_count_enabled':dynamic,'config':config,'reasons':reasons,'backfilled':backfilled}},allow_nan=False))
'''


def _remote(code, args=(), payload=None, timeout=120):
    encoded=base64.b64encode(code.encode()).decode()
    bootstrap="import base64;exec(base64.b64decode('"+encoded+"'))"
    env=None
    if os.environ.get('SMN_CAPTURE_LOCAL')=='1':
        env=dict(os.environ,SMN_INPUT_SOURCE_BLOG=str(Path(__file__).resolve().parent))
        cmd=[sys.executable,'-c',bootstrap,*args]
    else:
        source=os.environ.get('SMN_INPUT_SOURCE_BLOG','/home/flask/blog')
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+',source):
            raise Held('unsafe remote committed-source path')
        if code==REMOTE_HERO and source=='/home/flask/blog':
            raise Held('hero generation requires explicit committed candidate SMN_INPUT_SOURCE_BLOG')
        command="cd "+source+" && SMN_INPUT_SOURCE_BLOG="+source+" /home/flask/venv/bin/python -c \""+bootstrap+"\""
        command+=''.join(' '+a for a in args)
        cmd=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',*PRODUCTION_HOST,command]
    result=subprocess.run(cmd,input=payload,text=True,capture_output=True,timeout=timeout,env=env)
    if result.returncode==75:
        return None
    if result.returncode:
        raise Held('subscription input transport failed; inspect on-host without logging credentials')
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise Held('subscription input transport returned invalid evidence') from exc


def fetch_selection(edition):
    _date(edition)
    return _remote(REMOTE_SELECTION,(edition,))


def _selected_years(row, edition):
    """Preserve the existing workflow's PE metadata mapping at the frozen date."""
    years=str(row['pat_years']).strip().lower()
    mode=str(row.get('pat_mode') or 'consecutive').strip().lower()
    if mode not in ('pe','consecutive'):
        raise Held('unsupported selected pattern mode')
    phase=_date(edition).year % 4
    prefixed=re.fullmatch(r'pe([0-3])-(\d+)',years)
    if prefixed:
        if mode!='pe' or int(prefixed[1])!=phase or int(prefixed[2])<1:
            raise Held('selected PE phase and mode are inconsistent with edition')
        return years,mode
    if not years.isdigit() or int(years)<1:
        raise Held('invalid selected lookback')
    return (f'pe{phase}-{years}' if mode=='pe' else years),mode


def selection_posts(package, edition):
    """Map selected CSV identities to the native post contract, no financial math."""
    if package['date']!=edition:
        raise Held('selection edition differs')
    rows=package['rows']
    if not 1<=len(rows)<=6:
        raise Held('selection requires one to six subjects')
    posts=[]
    for index,row in enumerate(rows):
        symbol=str(row['ticker']).strip().upper()
        if not re.fullmatch(r'[A-Z0-9]{1,12}',symbol):
            raise Held('unsupported selected symbol')
        rid=str(row['pat_resource_id']).strip(); start=str(row['pat_start_date']).strip()
        _date(start); days=int(row['pat_days']); years,mode=_selected_years(row,edition)
        direction=str(row['pat_direction']).strip().lower()
        if days<1 or direction not in ('long','short') or not re.fullmatch(r'(?:\d+|pe[0-3]-\d+)',years):
            raise Held('invalid selected study')
        family=package['market_families'][rid]
        if not re.fullmatch(r'[A-Za-z0-9_-]+',family):
            raise Held('invalid native market family')
        identity={'resource_id':rid,'symbol':symbol,'pattern_start_date':start,
                  'pattern_days':days,'lookback_years':years,'direction':direction}
        aid=_sha(_json({'edition':edition,**identity}))[:16]
        directory=f'/articles/{family}/{start.replace("-","/")}'
        slug=f'{symbol.lower()}-{start}-{days}-{years}-{edition}'
        posts.append({**identity,'source_mode':'selected_inputs','company':row.get('company') or symbol,
                      'title':row.get('company') or symbol,'slug':slug,
                      'url':'https://seasonalmarketnews.com'+directory+'/'+slug+'.html',
                      'path':'/var/www/smn'+directory+'/'+slug+'.html','market_family':family,
                      'published_date':edition+f'T00:00:{index:02d}Z','tickers':[symbol],
                      'author_id':str(package['selection']['config']['USERID']),
                      'hero_image':'https://seasonalmarketnews.com'+directory+f'/hero_{symbol}_{aid}.jpg',
                      'article_id':aid,'publish_status':'true','featured_score':row.get('featured_score',0),
                      'pattern_mode':mode,'tags':[]})
    if len({p['symbol'] for p in posts})!=len(posts):
        raise Held('duplicate selected subject')
    return posts


def _verify(root, receipt):
    for name, expected in receipt['files'].items():
        path=root/name
        if path.is_symlink() or not path.is_file() or _sha(path.read_bytes())!=expected:
            raise Held('canonical subscription inputs changed')


def capture(root, edition, selection_fetcher=None):
    root=Path(root); _date(edition)
    if not root.is_absolute():
        raise Held('absolute input root required')
    if root.is_symlink():
        raise Held('unsafe canonical input root')
    receipt_path=root/'input-selection.json'
    if receipt_path.exists():
        receipt=json.loads(receipt_path.read_bytes())
        if receipt['date']!=edition:
            raise Held('input receipt edition differs')
        _verify(root,receipt)
        return {'status':'captured','symbols':receipt['symbols'],'idempotent':True,'production_writes':False}
    if (root/'production').exists():
        raise Held('partial or legacy inputs retained; use a fresh canonical root')
    package=(selection_fetcher or fetch_selection)(edition)
    if not package or not package.get('rows'):
        return {'status':'waiting_for_selection','date':edition,'production_writes':False}
    posts=selection_posts(package,edition)
    files={'production/posts.json':_json(posts),'production/selection.csv':base64.b64decode(package['csv_base64'],validate=True)}
    for post,row in zip(posts,package['rows']):
        files[f'production/{post["symbol"]}/selected-row.json']=_json(row)
    for name,blob in files.items():
        _write_once(root/name,blob)
    receipt={'schema_version':1,'date':edition,'source_mode':'selected_inputs','symbols':[p['symbol'] for p in posts],
             'selection':package['selection'],'files':{name:_sha(blob) for name,blob in files.items()},
             'published_articles_required':False,'paid_article_writer_called':False,'production_writes':False}
    _write_once(receipt_path,_json(receipt))
    return {'status':'captured','symbols':receipt['symbols'],'production_writes':False}


REMOTE_HERO = r'''import base64,contextlib,hashlib,io,json,os,sys
from pathlib import Path
blog=Path(os.environ['SMN_INPUT_SOURCE_BLOG'])
request=json.load(sys.stdin); post=request['post']; directory=Path('/var/tmp/smn-subscription-heroes')/request['date']/post['article_id']
directory.mkdir(parents=True,exist_ok=True); receipt=directory/'hero-receipt.json'
if receipt.exists():
 result=json.loads(receipt.read_text()); image=directory/result['filename']
 if hashlib.sha256(image.read_bytes()).hexdigest()!=result['sha256']: raise RuntimeError('hero evidence changed')
else:
 # A crash after an API request has uncertain cost: never regenerate blindly.
 started=directory/'generation-started.json'
 if started.exists(): raise RuntimeError('partial hero generation retained; inspect before retry')
 # The established workflow reads this non-code runtime asset by relative path.
 motif=Path('/home/flask/blog/ticker_motif_custom.json').read_bytes()
 (directory/'ticker_motif_custom.json').write_bytes(motif); os.chdir(directory)
 started.write_text(json.dumps({'article_id':post['article_id'],'date':request['date']}))
 with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
  sys.path.insert(0,'/home/flask'); import config
  config.news_root_folder=str(directory); config.article_images_folder=str(directory)
  config.web_root_dir=str(directory); config.news_website_url='https://seasonalmarketnews.com'
  sys.path.insert(0,str(blog)); import article_hero_image as hero
  output=hero.hero_image_workflow(resource_id=post['resource_id'],symbol=post['symbol'],date=post['pattern_start_date'],sentiment='bullish' if post['direction']=='long' else 'bearish',company_name=post['company'],article_id=post['article_id'])
 image=Path(output.get('image_path') or '')
 if not image.is_file() or directory.resolve() not in image.resolve().parents: raise RuntimeError('missing safe hero asset')
 # Persist the generated asset under the private transaction root for retry.
 image_bytes=image.read_bytes(); filename=Path(post['hero_image']).name
 target=directory/filename
 if target!=image: target.write_bytes(image_bytes)
 image=target
 result={'filename':filename,'sha256':hashlib.sha256(image_bytes).hexdigest(),'provider':'existing production hero_image_workflow','api_cost_stage':True,'usage_available':False,'cost_usd':None,'configured_models':{'prompt':'gpt-5-nano','image_primary':'gpt-image-2','check':getattr(hero,'HERO_CHECK_MODEL',None)},'source_module_sha256':hashlib.sha256(Path(hero.__file__).read_bytes()).hexdigest(),'motif_asset_sha256':hashlib.sha256(motif).hexdigest(),'prompt':output.get('prompt'),'concept':output.get('concept'),'hero_check':output.get('hero_check')}
 receipt.write_text(json.dumps(result))
result['image_base64']=base64.b64encode(image.read_bytes()).decode(); print(json.dumps(result))
'''


def generate_hero(post, edition):
    """Explicit API-cost operation; stages on production's private /var/tmp only."""
    return _remote(REMOTE_HERO,payload=json.dumps({'post':post,'date':edition}),timeout=900)


def prepare_heroes(root, edition, callback=None):
    root=Path(root); _date(edition)
    selection=json.loads((root/'input-selection.json').read_bytes()); _verify(root,selection)
    if selection['date']!=edition:
        raise Held('hero edition differs')
    receipt_path=root/'input-heroes.json'
    if receipt_path.exists():
        receipt=json.loads(receipt_path.read_bytes()); _verify(root,receipt)
        if receipt['date']!=edition or receipt['selection_sha256']!=_sha((root/'input-selection.json').read_bytes()):
            raise Held('hero selection binding changed')
        return {'status':'captured','idempotent':True,'symbols':list(receipt['heroes']),
                'api_cost_stage':receipt['api_cost_stage']}
    heroes={}; files={}
    for post in json.loads((root/'production/posts.json').read_bytes()):
        sym=post['symbol']; one=root/f'production/{sym}/hero-receipt.json'
        if one.exists():
            item=json.loads(one.read_bytes()); _verify(root,{'files':{item['path']:item['sha256']}})
        else:
            generated=(callback or generate_hero)(post,edition)
            blob=base64.b64decode(generated['image_base64'],validate=True)
            if _sha(blob)!=generated['sha256']:
                raise Held('hero asset hash differs')
            name=Path(post['hero_image']).name; path=f'production/{sym}/assets/{name}'
            _write_once(root/path,blob)
            item={k:v for k,v in generated.items() if k!='image_base64'}
            item.update(path=path,url=post['hero_image'])
            _write_once(one,_json(item))
        heroes[sym]=item; files[item['path']]=item['sha256']; files[one.relative_to(root).as_posix()]=_sha(one.read_bytes())
    receipt={'schema_version':1,'date':edition,'heroes':heroes,'files':files,
             'selection_sha256':_sha((root/'input-selection.json').read_bytes()),
             'api_cost_stage':any(item.get('api_cost_stage') is True for item in heroes.values()),
             'paid_article_writer_called':False,'public_runtime_writes':False}
    _write_once(receipt_path,_json(receipt))
    return {'status':'captured','symbols':list(heroes),'api_cost_stage':receipt['api_cost_stage']}
