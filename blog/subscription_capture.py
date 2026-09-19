"""Read-only production and TradeWave engine evidence capture."""
from __future__ import annotations
import argparse, base64, datetime as dt, hashlib, io, json, re, subprocess, tarfile
from pathlib import Path

PRODUCTION_HOST = ["-p", "4369", "root@209.182.216.112"]
ENGINE_HOST = ["-p", "4369", "root@194.113.195.141"]
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.=-]{0,15}$")

class Held(RuntimeError):
    pass

def _write_once(path: Path, data: bytes) -> None:
    if path.is_symlink(): raise Held('unsafe capture path')
    if path.exists():
        if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(data).digest():
            raise Held("existing capture differs; refusing overwrite")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as f: f.write(data)

def _date(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value): raise Held('invalid date')
    return dt.date.fromisoformat(value)

def _posts(root, edition):
    posts=json.loads((root/'production/posts.json').read_text())
    if len(posts)!=6 or len({p.get('symbol') for p in posts})!=6 or any(
        not re.fullmatch(r'[A-Z0-9]{1,12}',str(p.get('symbol',''))) or
        p.get('published_date','')[:10]!=edition for p in posts):
        raise Held('six complete records for the requested date required')
    return posts

def _hashes(root):
    if root.is_symlink() or any(p.is_symlink() for p in root.rglob('*')):
        raise Held('symlink in captured evidence')
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}

def _safe_extract(blob: bytes, dest: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for m in tar.getmembers():
            if '\\' in m.name or Path(m.name).is_absolute() or Path(m.name).drive:
                raise Held("production archive contains unsafe path")
            target = (dest / m.name).resolve()
            if dest.resolve() not in target.parents and target != dest.resolve():
                raise Held("production archive contains unsafe path")
            if m.issym() or m.islnk() or not (m.isfile() or m.isdir()):
                raise Held("production archive contains unsupported entry")
        tar.extractall(dest, filter='data')

def _remote_capture(edition: str) -> bytes:
    _date(edition)
    code = r'''import io,json,tarfile,sys,re
from pathlib import Path
from urllib.parse import urlparse
from html.parser import HTMLParser
date=sys.argv[1]; root=Path('/var/www/smn'); posts=json.loads((root/'posts.json').read_text())
items=[x for x in posts if str(x.get('published_date','')).startswith(date)]
if len(items)!=6: raise RuntimeError('published-date record count is not six')
class I(HTMLParser):
 def __init__(s): super().__init__(); s.u=[]
 def handle_starttag(s,t,a):
  d=dict(a)
  if t in ('img','source'):
   for k in ('src','srcset'):
    if d.get(k): s.u.append(d[k].split(',')[0].split()[0])
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as tar:
 b=json.dumps(items,indent=2).encode(); i=tarfile.TarInfo('posts.json'); i.size=len(b); tar.addfile(i,io.BytesIO(b))
 for x in items:
  sym=x.get('symbol','');
  if not re.fullmatch(r'[A-Z0-9]{1,12}',sym): raise RuntimeError('invalid symbol')
  p=Path(x['path']).resolve()
  if root not in p.parents: raise RuntimeError('article path outside root')
  tar.add(p,arcname=sym+'/article.html')
  ds=f"{sym}_{x['pattern_start_date']}_{x['pattern_days']}_{x['lookback_years']}_dataset.json"; dp=root/'datasets'/ds
  if (root/'datasets') not in dp.resolve().parents or not dp.is_file(): raise RuntimeError('missing or unsafe dataset')
  tar.add(dp,arcname=sym+'/dataset.json'); q=I(); q.feed(p.read_text()); urls=set(q.u+[x.get('hero_image','')])
  for u in urls:
   z=urlparse(u)
   if z.netloc and z.netloc not in ('seasonalmarketnews.com','www.seasonalmarketnews.com'): continue
   a=(root/z.path.lstrip('/')).resolve() if z.path.startswith('/') else (p.parent/z.path).resolve()
   if root in a.parents and a.is_file(): tar.add(a,arcname=sym+'/assets/'+a.name)
  aid=Path(urlparse(x.get('hero_image','')).path).stem.rsplit('_',1)[-1]
  aa=list(Path('/home/flask/blog/audit') .glob(date.replace('-','/')+'/'+sym+'_*_'+aid))
  if len(aa)!=1: raise RuntimeError('missing audit')
  for n in ('manifest.json','research.json','research_context.txt','research_tavily_raw.json','prompt.txt','publish_result.json'):
   f=aa[0]/n
   if f.is_file(): tar.add(f,arcname=sym+'/audit/'+n)
 tar.add('/home/flask/blog/article_ideas/article_queue_'+date+'.csv',arcname='selection.csv')
'''
    cmd=["ssh","-o","BatchMode=yes","-o","ConnectTimeout=10",*PRODUCTION_HOST,"python3 -",edition]
    p=subprocess.run(cmd,input=code.encode(),capture_output=True,timeout=900)
    if p.returncode: raise Held("production capture held; remote read failed")
    return p.stdout

def production(root: Path, edition: str) -> dict:
    _date(edition)
    if not root.is_absolute(): raise ValueError("root must be absolute")
    root.mkdir(parents=True,exist_ok=True); out=root/'production'; tarpath=root/f'production-capture-{edition}.tar.gz'
    hashpath=root/f'production-capture-{edition}-hashes.json'
    if tarpath.exists() and hashpath.exists() and out.exists():
        if _hashes(out)!=json.loads(hashpath.read_text()): raise Held('capture inputs changed')
        posts=_posts(root,edition)
        return {'status':'captured','symbols':[p['symbol'] for p in posts],'production_writes':False,'idempotent':True}
    if out.exists() or tarpath.exists() or hashpath.exists(): raise Held('partial capture retained; use a new attempt')
    blob=_remote_capture(edition); _write_once(tarpath,blob); _safe_extract(blob,out)
    posts=_posts(root,edition)
    for prompt in out.glob('*/audit/prompt.txt'):
        for line in prompt.read_text(errors='replace').splitlines():
            if line.startswith('{"meta":'):
                _write_once(prompt.parent.parent/'engine-payload.json',line.encode()); break
    payloads=list(out.glob('*/engine-payload.json'))
    if len(payloads)!=6: raise Held("engine payload inputs incomplete")
    hashes=_hashes(out)
    _write_once(root/f'production-capture-{edition}-hashes.json',json.dumps(hashes,indent=2).encode())
    return {'status':'captured','symbols':[p['symbol'] for p in posts],'files':len(hashes),'production_writes':False}

def engine(root: Path, edition: str, mode: str) -> dict:
    end=_date(edition)-dt.timedelta(days=1); posts=_posts(root,edition)
    target=root/'production-engine-export.json'; receipt=root/'engine-capture.json'
    request_hash=hashlib.sha256((root/'production/posts.json').read_bytes()).hexdigest()
    if target.exists() and receipt.exists():
        proof=json.loads(receipt.read_text())
        if target.is_symlink() or proof!={'date':edition,'posts_sha256':request_hash,'export_sha256':hashlib.sha256(target.read_bytes()).hexdigest()}:
            raise Held('engine evidence changed')
        return {'status':'captured','idempotent':True}
    if target.exists() or receipt.exists(): raise Held('partial engine capture retained; use a new attempt')
    try: start=end.replace(year=end.year-1)
    except ValueError: start=end.replace(year=end.year-1,day=28)
    studies=[]
    for p in posts:
        if not SYMBOL.fullmatch(p['symbol']): raise Held('invalid symbol')
        studies.append({k:p[k] for k in ('symbol','resource_id','pattern_start_date','pattern_days','lookback_years','direction')})
        studies[-1]['comparison_years']=['10','20'] if str(p['lookback_years']).startswith('pe') else ['20','pe2-10']
    req={'studies':studies,'price_start':start.isoformat(),'price_end':end.isoformat()}
    exporter=Path(__file__).with_name('tradewave_engine_export.py'); enc=base64.b64encode(exporter.read_bytes()).decode()
    remote="set -a; . /etc/tradewave/secrets.env; set +a; /home/flask/venv/bin/python -c \"import base64;exec(base64.b64decode('"+enc+"'))\""
    cmd=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',*ENGINE_HOST,remote]
    p=subprocess.run(cmd,input=json.dumps(req),text=True,capture_output=True,timeout=900)
    if p.returncode: raise Held('TradeWave engine export held')
    try: data=json.loads(p.stdout)
    except json.JSONDecodeError: raise Held('TradeWave engine export returned invalid evidence')
    if 'error' in data: raise Held('TradeWave engine export held')
    if {x['identity']['symbol'] for x in data.get('studies',[])}!={p['symbol'] for p in posts}: raise Held('engine study set differs')
    _write_once(target,json.dumps(data,indent=2).encode())
    _write_once(receipt,json.dumps({'date':edition,'posts_sha256':request_hash,'export_sha256':hashlib.sha256(target.read_bytes()).hexdigest()}).encode())
    return {'status':'captured','symbols':[x['identity']['symbol'] for x in data['studies']],'owner':data.get('owner')}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('mode',choices=['production','engine']); ap.add_argument('--root',required=True,type=Path); ap.add_argument('--date',required=True); a=ap.parse_args(); dt.date.fromisoformat(a.date)
    print(json.dumps(production(a.root,a.date) if a.mode=='production' else engine(a.root,a.date,a.mode)))
if __name__=='__main__': main()
