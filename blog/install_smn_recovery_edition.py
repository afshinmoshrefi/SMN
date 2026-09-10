"""Publish a reviewed edition to the ALREADY ACTIVE SMN Dev recovery host.

Explicitly bound to .176, the existing recovery nginx root, and the Dev origin.
This does not restore .180, change DNS, or activate any TradeWave service/queue.
Activation retains the dev lock until the caller verifies and finalizes, or rolls
back. All build/copy work happens before that short activation window.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess

BASE=Path('/var/www/smn-dev-recovery')
CURRENT=BASE/'current'
NGINX=Path('/etc/nginx/sites-enabled/smn-dev')
STATE=Path('/var/lib/tradewave/release-state')
LOCK=STATE/'dev-activation.lock'
CODE=Path('/var/lib/tradewave/smn-editorial')
ORIGIN='https://smn-dev.trxstat.com'


def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,obj):Path(p).write_text(json.dumps(obj,indent=2)+'\n')
def atomic(p,data):
    tmp=p.with_name(p.name+'.smn-new');tmp.write_bytes(data);os.chmod(tmp,0o644);os.replace(tmp,p)
def pointer(path,target):
    tmp=path.with_name(path.name+'.smn-new');tmp.symlink_to(target);os.replace(tmp,path)


def validate_package(package):
    package=Path(package).resolve();m=read(package/'manifest.json')
    if m.get('target_origin')!=ORIGIN or m.get('production_allowed') is not False:
        raise ValueError('Dev-only package required')
    date=m['edition_date'];commit=m['source_commit']
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date) or not re.fullmatch('[a-f0-9]{40}',commit):
        raise ValueError('Exact date and source commit required')
    prefix='editions/'+date+'/'
    entries=read(package/'entries.json')
    if len(entries)!=6 or len({e['symbol'] for e in entries})!=6:
        raise ValueError('Six distinct reviewed articles required')
    for e in entries:
        if e['url']!=ORIGIN+'/'+prefix+e['symbol']+'/article.html' or e['source_commit']!=commit:
            raise ValueError('Article destination/provenance mismatch')
    for rel,expected in m['files'].items():
        p=package/rel
        if p.is_symlink() or package not in p.resolve().parents or not p.is_file() or sha(p)!=expected:
            raise ValueError('Changed or unsafe package')
        if not (rel.startswith(prefix) or rel in {'entries.json','home-section.html'}):
            raise ValueError('Unexpected public path')
    return m,entries


def guard():
    ips=subprocess.check_output(['hostname','-I'],text=True).split()
    if '192.168.1.176' not in ips or not CURRENT.is_symlink() or CURRENT.resolve().parent!=BASE:
        raise ValueError('Exact temporary SMN Dev recovery host/root required')
    conf=NGINX.read_text()
    if 'server_name smn-dev.trxstat.com;' not in conf or 'root /var/www/smn-dev-recovery/current;' not in conf:
        raise ValueError('SMN recovery is not the active nginx target')
    if NGINX.is_symlink():raise ValueError('Recovery nginx ownership changed; inspect before activation')
    return conf


def prepare(package,source):
    package=Path(package).resolve();source=Path(source).resolve()
    m,entries=validate_package(package);conf=guard();commit=m['source_commit']
    if read(source/'source-provenance.json')['source_commit']!=commit:
        raise ValueError('Generator source and edition differ')
    for rel,expected in read(source/'source-provenance.json')['files'].items():
        p=source/rel
        if p.is_symlink() or source not in p.resolve().parents or sha(p)!=expected:
            raise ValueError('Generator source changed')
    ident=m['edition_date'].replace('-','')+'-'+commit[:10]
    record=STATE/('smn-edition-'+ident);record.mkdir(parents=True)
    web=BASE/ident
    if web.exists():raise ValueError('Preserve prior candidate; use a new committed package')
    old=CURRENT.resolve();shutil.copytree(old,web)
    for rel in m['files']:
        if rel.startswith('editions/'):
            dest=web/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(package/rel,dest)
    write(web/'editions'/m['edition_date']/'provenance.json',{'source_commit':commit,'engine_authority':'TradeWave','publication_target':'SMN Dev recovery','symbols':[e['symbol'] for e in entries]})
    code=CODE/'releases'/commit;code.parent.mkdir(parents=True,exist_ok=True)
    if code.exists():raise ValueError('Source candidate exists; inspect rather than overwrite')
    shutil.copytree(source,code)
    for p in web.rglob('*'):
        os.chmod(p,0o755 if p.is_dir() else 0o644)
    newconf,n=re.subn(r'(location = /\s*\{\s*return 302 )/editions/\d{4}-\d{2}-\d{2}/(;\s*\})',
                     lambda x:x[1]+'/editions/'+m['edition_date']+'/'+x[2],conf)
    if n!=1:raise ValueError('Recovery home redirect changed')
    (record/'nginx-before').write_text(conf);(record/'nginx-after').write_text(newconf)
    previous_code=str((CODE/'current').resolve()) if (CODE/'current').is_symlink() else None
    receipt={'id':ident,'source_commit':commit,'edition_date':m['edition_date'],'previous_web':str(old),
        'candidate_web':str(web),'previous_code':previous_code,'candidate_code':str(code),
        'package':str(package),'urls':[e['url'] for e in entries],
        'status':'prepared','production_written':False,'primary_smn_dev_restored':False}
    write(record/'receipt.json',receipt);return str(record)


def activate(record):
    record=Path(record);r=read(record/'receipt.json');guard()
    if r['status']!='prepared':raise ValueError('Candidate state changed')
    if str(CURRENT.resolve())!=r['previous_web'] or NGINX.read_bytes()!=(record/'nginx-before').read_bytes():
        raise ValueError('Active recovery changed during build; re-integrate')
    LOCK.mkdir();write(LOCK/'owner.json',{'task':r['id'],'pid':os.getpid(),'source_commit':r['source_commit'],'utc':datetime.now(timezone.utc).isoformat()})
    try:
        if str(CURRENT.resolve())!=r['previous_web'] or NGINX.read_bytes()!=(record/'nginx-before').read_bytes():
            unlock(r)
            raise ValueError('Recovery changed before lock acquisition')
        pointer(CURRENT,r['candidate_web']);pointer(CODE/'current',r['candidate_code'])
        atomic(NGINX,(record/'nginx-after').read_bytes())
        subprocess.run(['nginx','-t'],check=True,capture_output=True)
        subprocess.run(['systemctl','reload','nginx'],check=True)
        r['status']='active_pending_live_verification';write(record/'receipt.json',r)
    except Exception:
        if LOCK.exists():rollback(record)
        raise
    return r


def unlock(r):
    if read(LOCK/'owner.json')['task']!=r['id']:raise ValueError('Not this activation lock')
    (LOCK/'owner.json').unlink();LOCK.rmdir()


def rollback(record):
    record=Path(record);r=read(record/'receipt.json')
    if read(LOCK/'owner.json')['task']!=r['id']:raise ValueError('Not this activation lock')
    pointer(CURRENT,r['previous_web'])
    if r['previous_code']:pointer(CODE/'current',r['previous_code'])
    elif (CODE/'current').is_symlink():(CODE/'current').unlink()
    atomic(NGINX,(record/'nginx-before').read_bytes())
    subprocess.run(['nginx','-t'],check=True,capture_output=True);subprocess.run(['systemctl','reload','nginx'],check=True)
    r['status']='rolled_back';write(record/'receipt.json',r);unlock(r)
    return r


def finalize(record):
    record=Path(record);r=read(record/'receipt.json')
    if r['status']!='active_pending_live_verification':raise ValueError('Not awaiting verification')
    proof=read(record/'live-verification.json')
    if proof.get('passed') is not True or proof.get('source_commit')!=r['source_commit'] or proof.get('origin_main')!=r['source_commit']:
        raise ValueError('Live behavior and main parity proof required')
    if str(CURRENT.resolve())!=r['candidate_web'] or str((CODE/'current').resolve())!=r['candidate_code']:
        raise ValueError('Active pointers differ')
    r['status']='live_verified';write(record/'receipt.json',r);unlock(r);return r


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','activate','rollback','finalize'])
    p.add_argument('path');p.add_argument('--source');a=p.parse_args()
    result=prepare(a.path,a.source) if a.action=='prepare' else globals()[a.action](a.path)
    print(json.dumps(result))
