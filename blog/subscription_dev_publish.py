"""Dev-only stage, activate, finish and rollback for a reviewed SMN edition."""
from __future__ import annotations
import argparse, base64, hashlib, json, os, re, subprocess, tarfile
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
 'blog/SUBSCRIPTION_DEV_PUBLICATION.md','blog/subscription_daily.py','blog/subscription_capture.py',
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
def remote_prepare(root,receipt):
    remote=receipt['remote']; root=Path(root)
    ssh('mkdir -m 700 '+remote)
    for name in ('committed-source.tar','publication-package.tar'):
        run(['scp',str(root/name),HOST+':'+remote+'/'+name])
    extract="import tarfile;from pathlib import Path;p=Path(%r);[tarfile.open(p/n).extractall(p) for n in ('committed-source.tar','publication-package.tar')]" % remote
    ssh(PYTHON+' -',input=extract)
    return subprocess.check_output(['ssh','-o','BatchMode=yes',HOST,PYTHON+' '+remote+'/source/blog/install_smn_recovery_edition.py prepare '+remote+'/publication-package --source '+remote+'/source'],text=True)

def activate(root,repo,node=None,playwright=None):
    root=Path(root).resolve(); receipt=read(root/'dev-stage.json'); _,main=clean_main(repo)
    if main != receipt['source_commit']: raise ValueError('origin/main moved after stage; stage again')
    record=json.loads(remote_prepare(root,receipt))
    # Installer acquires the actual lock.  Its wrapper receives our fresh main acknowledgement.
    wrapper="""from pathlib import Path\nimport sys,json\np=Path(sys.argv[1]);sys.path.insert(0,str(p/'source/blog'));import install_smn_recovery_edition as i\nexpected=sys.argv[3]\nclass L:\n def __init__(s,x):s.x=x\n def __truediv__(s,n):return s.x/n\n def exists(s):return s.x.exists()\n def rmdir(s):return s.x.rmdir()\n def mkdir(s):\n  s.x.mkdir();print('LOCK');\n  if sys.stdin.readline().strip()!=expected:s.x.rmdir();raise RuntimeError('main acknowledgement missing')\ni.LOCK=L(i.LOCK);print(json.dumps(i.activate(Path(sys.argv[2])))\n"""
    remote=receipt['remote']; encoded=base64.b64encode(wrapper.encode()).decode()
    cmd=['ssh','-o','BatchMode=yes',HOST,PYTHON+" -c \"import base64;exec(base64.b64decode('"+encoded+"'))\" "+remote+' '+record+' '+receipt['source_commit']]
    p=subprocess.run(cmd,input=receipt['source_commit']+'\n',text=True,capture_output=True)
    if p.returncode: raise RuntimeError('activation failed before live verification: '+p.stderr)
    active={'record':record,'source_commit':receipt['source_commit'],'status':'active_pending_live_verification'}
    try:
        if node:
            env={**os.environ,'SMN_PLAYWRIGHT':str(playwright)} if playwright else None
            run([str(node),str(root/'committed-source/blog/subscription_live.cjs'),str(root)],env=env)
            proof=read(root/'live-browser-verification.json')
            proof['public_hash_proof']=bool(proof.get('public_files')) and all(p.get('passed') for p in proof['public_files'])
            write(root/'live-verification.json',proof)
        write(root/'dev-activation.json',active);return active
    except BaseException:
        subprocess.run(['ssh','-o','BatchMode=yes',HOST,PYTHON+' '+remote+'/source/blog/install_smn_recovery_edition.py rollback '+record],text=True)
        raise

def finish(root,repo):
    root=Path(root).resolve(); active=read(root/'dev-activation.json'); _,main=clean_main(repo)
    if main != active['source_commit']: raise ValueError('origin/main differs; rollback required')
    proof=read(root/'live-verification.json')
    pixel_path=root/'live-landing-visual-checks.json'
    if not pixel_path.is_file(): raise ValueError('Fresh inspected landing receipt required')
    pixels=read(pixel_path)
    inspected=pixels.get('inspected_images',{})
    if (not proof.get('passed') or proof.get('source_commit') != main or not proof.get('public_hash_proof')
        or not pixels.get('passed') or not inspected or any(sha(root/name)!=digest for name,digest in inspected.items())):
        raise ValueError('Fresh landing receipts and public hash proof required')
    remote=read(root/'dev-stage.json')['remote']; write(root/'live-verification.json',{**proof,'origin_main':main})
    run(['scp',str(root/'live-verification.json'),HOST+':'+active['record']+'/live-verification.json'])
    out=subprocess.check_output(['ssh','-o','BatchMode=yes',HOST,PYTHON+' '+remote+'/source/blog/install_smn_recovery_edition.py finalize '+active['record']],text=True)
    result=json.loads(out);write(root/'dev-publication-receipt.json',result);return result

def rollback(root):
    active=read(Path(root)/'dev-activation.json'); out=subprocess.check_output(['ssh','-o','BatchMode=yes',HOST,PYTHON+' '+read(Path(root)/'dev-stage.json')['remote']+'/source/blog/install_smn_recovery_edition.py rollback '+active['record']],text=True); return json.loads(out)

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('stage','activate','finish','rollback'));p.add_argument('--root',required=True,type=Path);p.add_argument('--repo',required=True,type=Path);p.add_argument('--node');p.add_argument('--playwright');a=p.parse_args()
    result=globals()[a.action](a.root,a.repo,a.node,a.playwright) if a.action=='activate' else globals()[a.action](a.root,a.repo) if a.action in ('stage','finish') else rollback(a.root)
    print(json.dumps(result))
if __name__=='__main__': main()
