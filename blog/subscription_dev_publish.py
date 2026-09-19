"""Dev-only stage, activate, finish and rollback for a reviewed SMN edition."""
from __future__ import annotations
import argparse, base64, hashlib, json, os, queue, re, subprocess, tarfile, threading
from pathlib import Path

HOST='root@192.168.1.176'
REMOTE_BASE='/var/tmp'
STATE='/var/lib/tradewave/release-state'
PYTHON='/home/flask/venv/bin/python'
SOURCE_FILES=(
 'blog/tradewave_engine_export.py','blog/install_smn_recovery_edition.py','blog/chartkit.py',
 'blog/visual_evidence.py','blog/visual_editorial.py','blog/visual_charts.py','blog/seasonal_price_path.py',
 'blog/seasonal_edition.py','blog/subscription_writer.py','blog/subscription_publication.py',
 'blog/subscription_edition.py','blog/engine_seasonal.py','blog/engine_edition_workflow.py',
 'blog/schemas/subscription_article.schema.json','blog/schemas/subscription_review.schema.json',
 'blog/SUBSCRIPTION_DEV_PUBLICATION.md','blog/SUBSCRIPTION_DAILY_RUNBOOK.md','blog/subscription_daily.py','blog/subscription_capture.py',
 'blog/subscription_dev_publish.py','blog/subscription_layout.cjs','blog/subscription_live.cjs')

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path,obj): Path(path).write_text(json.dumps(obj,indent=2)+'\n',encoding='utf-8')
def run(args, **kw): return subprocess.run(args, check=True, text=True, **kw)

def clean_main(repo):
    repo=Path(repo).resolve()
    run(['git','-C',str(repo),'fetch','origin','main'])
    if subprocess.check_output(['git','-C',str(repo),'status','--porcelain'],text=True):
        raise ValueError('Source repository must be clean')
    head=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    main=subprocess.check_output(['git','-C',str(repo),'rev-parse','origin/main'],text=True).strip()
    if head != main: raise ValueError('Source repository must be current pushed origin/main')
    return repo,head

def review_stages(root):
    stages={}
    for result in sorted((root/'results').iterdir()):
        if not result.is_dir(): continue
        binding=read(result/'review-binding.json'); stage=binding.get('review_stage')
        if not re.fullmatch(r'[a-z-]+',str(stage or '')): raise ValueError('Final review binding has no valid stage')
        stages[result.name]=stage
    if len(stages)!=6: raise ValueError('Exactly six final review bindings required')
    return stages

def edition_date(root):
    state=root/'daily-state.json'
    date=read(state).get('date') if state.exists() else root.name
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',str(date)): raise ValueError('Edition root must declare an ISO edition date')
    return date

def source_tree(root,repo,commit):
    dest=root/'committed-source'
    if dest.exists(): raise ValueError('Preserve existing staged source; use a new edition root')
    files={}
    for rel in SOURCE_FILES:
        data=subprocess.check_output(['git','-C',str(repo),'show',commit+':'+rel])
        target=dest/rel; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data); files[rel]=sha(target)
    write(dest/'source-provenance.json',{'source_commit':commit,'git_origin':subprocess.check_output(['git','-C',str(repo),'remote','get-url','origin'],text=True).strip(),'files':files,'production_allowed':False})
    return dest

def archive(source,target,name):
    with tarfile.open(target,'w') as tar: tar.add(source,arcname=name)

def stage(root,repo):
    root=Path(root).resolve(); repo,commit=clean_main(repo)
    from subscription_publication import package
    stages=review_stages(root)
    date=edition_date(root); manifest=package(root,date,commit,stages)
    source=source_tree(root,repo,commit)
    source_tar=root/'committed-source.tar'; package_tar=root/'publication-package.tar'
    archive(source,source_tar,'source'); archive(root/'publication-package',package_tar,'publication-package')
    record=STATE+'/smn-edition-'+date.replace('-','')+'-'+commit[:10]
    remote=REMOTE_BASE+'/smn-edition-'+date+'-'+commit[:10]
    receipt={'status':'staged','source_commit':commit,'record':record,'remote':remote,'review_stages':stages,
             'source_tar_sha256':sha(source_tar),'package_tar_sha256':sha(package_tar),'manifest':manifest}
    write(root/'dev-stage.json',receipt); return receipt

def ssh(command, **kw): return run(['ssh','-o','BatchMode=yes',HOST,command],**kw)
def remote_call(command, **kw): return subprocess.check_output(['ssh','-o','BatchMode=yes',HOST,command],text=True,**kw)
def installer(receipt, action, record=None):
    record=record or receipt['record']
    return remote_call(PYTHON+' '+receipt['remote']+'/source/blog/install_smn_recovery_edition.py '+action+' '+record)

def remote_state(receipt):
    code="""import json,sys
from pathlib import Path
remote=Path(sys.argv[1]); p=Path(sys.argv[2])/'receipt.json'
print(json.dumps({'state':('partial' if remote.exists() else 'missing')} if not p.is_file() else {'state':json.loads(p.read_text()).get('status'),'record':str(p.parent)}))
"""
    encoded=base64.b64encode(code.encode()).decode()
    return json.loads(remote_call(PYTHON+" -c \"import base64;exec(base64.b64decode('"+encoded+"'))\" "+receipt['remote']+' '+receipt['record']))

def remote_prepare(root,receipt):
    remote=receipt['remote']; root=Path(root)
    for name,key in (('committed-source.tar','source_tar_sha256'),('publication-package.tar','package_tar_sha256')):
        if sha(root/name) != receipt.get(key):
            raise ValueError('Staged archive hash differs; stage again before remote preparation')
    state=remote_state(receipt)
    if state['state'] != 'missing':
        raise RuntimeError('Interrupted remote activation is '+state['state']+'; inspect or rollback explicitly')
    ssh('mkdir -m 700 '+remote)
    for name in ('committed-source.tar','publication-package.tar'):
        run(['scp',str(root/name),HOST+':'+remote+'/'+name])
    extract="import tarfile;from pathlib import Path;p=Path(%r);[tarfile.open(p/n).extractall(p) for n in ('committed-source.tar','publication-package.tar')]" % remote
    ssh(PYTHON+' -',input=extract)
    return remote_call(PYTHON+' '+remote+'/source/blog/install_smn_recovery_edition.py prepare '+remote+'/publication-package --source '+remote+'/source')

def activation_wrapper():
    return """from pathlib import Path
import json,sys
remote,record,expected=sys.argv[1:4]
sys.path.insert(0,str(Path(remote)/'source/blog'))
import install_smn_recovery_edition as install
class MainCheckedLock:
 def __init__(self,path): self.path=path
 def __truediv__(self,name): return self.path/name
 def exists(self): return self.path.exists()
 def rmdir(self): return self.path.rmdir()
 def mkdir(self):
  self.path.mkdir()
  print('DEV_LOCK_ACQUIRED_RECHECK_MAIN',flush=True)
  if sys.stdin.readline().strip()!=expected:
   self.path.rmdir(); raise RuntimeError('main acknowledgement missing')
install.LOCK=MainCheckedLock(install.LOCK)
print(json.dumps(install.activate(Path(record))),flush=True)
"""

def rollback_remote(receipt,record):
    try: installer(receipt,'rollback',record)
    except Exception: pass

def read_marker(process, timeout=60):
    lines=queue.Queue()
    threading.Thread(target=lambda:lines.put(process.stdout.readline()),daemon=True).start()
    try: return lines.get(timeout=timeout).strip()
    except queue.Empty:
        if process.poll() is None: process.communicate('ABORT_MAIN_CHANGED\n',timeout=60)
        raise RuntimeError('timed out waiting for Dev lock marker; no activation acknowledgement sent')

def activate(root,repo,node=None,playwright=None):
    if not node: raise ValueError('--node is required for live Dev verification')
    root=Path(root).resolve(); receipt=read(root/'dev-stage.json')
    record=json.loads(remote_prepare(root,receipt))
    wrapper=activation_wrapper(); compile(wrapper,'remote_main_gate.py','exec')
    encoded=base64.b64encode(wrapper.encode()).decode(); remote=receipt['remote']
    cmd=['ssh','-o','BatchMode=yes',HOST,PYTHON+" -c \"import base64;exec(base64.b64decode('"+encoded+"'))\" "+remote+' '+record+' '+receipt['source_commit']]
    p=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    marker=read_marker(p)
    if marker != 'DEV_LOCK_ACQUIRED_RECHECK_MAIN':
        p.communicate('ABORT_MAIN_CHANGED\n'); raise RuntimeError('activation did not acquire the expected Dev lock')
    try:
        _,main=clean_main(repo)
        acknowledgement=receipt['source_commit'] if main==receipt['source_commit'] else 'ABORT_MAIN_CHANGED'
        output,error=p.communicate(acknowledgement+'\n',timeout=60)
        if p.returncode or acknowledgement != receipt['source_commit']:
            raise RuntimeError('origin/main changed while Dev lock was held')
        json.loads(output)
    except BaseException:
        if p.poll() is None: p.communicate('ABORT_MAIN_CHANGED\n',timeout=60)
        rollback_remote(receipt,record); raise
    active={'record':record,'source_commit':receipt['source_commit'],'status':'active_pending_live_verification'}
    try:
        env={**os.environ,'SMN_PLAYWRIGHT':str(playwright)} if playwright else None
        run([str(node),str(root/'committed-source/blog/subscription_live.cjs'),str(root)],env=env)
        proof=read(root/'live-browser-verification.json')
        proof['public_hash_proof']=bool(proof.get('public_files')) and all(p.get('passed') for p in proof['public_files'])
        write(root/'live-verification.json',proof)
        write(root/'dev-activation.json',active);return active
    except BaseException:
        rollback_remote(receipt,record)
        raise

def finish(root,repo):
    root=Path(root).resolve(); active=read(root/'dev-activation.json'); _,main=clean_main(repo)
    if main != active['source_commit']:
        rollback_remote(read(root/'dev-stage.json'),active['record'])
        raise ValueError('origin/main moved; Dev edition rolled back')
    proof=read(root/'live-verification.json')
    pixel_path=root/'live-landing-visual-checks.json'
    if not pixel_path.is_file(): raise ValueError('Fresh inspected landing receipt required')
    pixels=read(pixel_path)
    inspected=pixels.get('inspected_images',{})
    if (not proof.get('passed') or proof.get('source_commit') != main or not proof.get('public_hash_proof')
        or not pixels.get('passed') or not inspected or any(sha(root/name)!=digest for name,digest in inspected.items())):
        raise ValueError('Fresh landing receipts and public hash proof required')
    receipt=read(root/'dev-stage.json'); remote=receipt['remote']; write(root/'live-verification.json',{**proof,'origin_main':main})
    run(['scp',str(root/'live-verification.json'),HOST+':'+active['record']+'/live-verification.json'])
    verify="""import json,sys
from pathlib import Path
root=Path(sys.argv[1]); record=Path(sys.argv[2]); sys.path.insert(0,str(root/'source/blog'))
import install_smn_recovery_edition as i
r=i.read(record/'receipt.json'); p=i.read(Path(r['candidate_code'])/'source-provenance.json'); proof=i.read(record/'live-verification.json')
assert r['source_commit']==p['source_commit']==proof['source_commit']==proof['origin_main']
assert str(i.CURRENT.resolve())==r['candidate_web'] and str((i.CODE/'current').resolve())==r['candidate_code']
for rel,expected in p['files'].items(): assert i.sha(Path(r['candidate_code'])/rel)==expected,rel
for item in proof['public_files']: assert item['passed'] and i.sha(Path(r['candidate_web'])/item['rel'])==item['sha256'],item['rel']
print(json.dumps({'source_files':len(p['files']),'public_files':len(proof['public_files'])}))
"""
    encoded=base64.b64encode(verify.encode()).decode()
    try:
        remote_call(PYTHON+" -c \"import base64;exec(base64.b64decode('"+encoded+"'))\" "+remote+' '+active['record'])
        out=installer(receipt,'finalize',active['record'])
    except BaseException:
        rollback_remote(receipt,active['record'])
        raise
    result=json.loads(out);write(root/'dev-publication-receipt.json',result);return result

def rollback(root):
    active=read(Path(root)/'dev-activation.json'); return json.loads(installer(read(Path(root)/'dev-stage.json'),'rollback',active['record']))

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('stage','activate','finish','rollback'));p.add_argument('--root',required=True,type=Path);p.add_argument('--repo',required=True,type=Path);p.add_argument('--node');p.add_argument('--playwright');a=p.parse_args()
    result=globals()[a.action](a.root,a.repo,a.node,a.playwright) if a.action=='activate' else globals()[a.action](a.root,a.repo) if a.action in ('stage','finish') else rollback(a.root)
    print(json.dumps(result))
if __name__=='__main__': main()
