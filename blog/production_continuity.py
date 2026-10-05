"""Source-bound production rolling publication of independently qualified coverage."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tempfile

from subscription_publication import read, selected_lineup, qualified_html, package, validate_staged_reviews


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json_sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _write(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as temp:
        json.dump(value,temp,indent=2,ensure_ascii=False);temp.write('\n');name=temp.name
    os.replace(name,path)


@contextmanager
def _lock(root):
    path=root/'.publication-continuity.lock'
    with path.open('a+b') as handle:
        handle.seek(0);handle.write(b'0');handle.flush();handle.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try: yield
        finally:
            if os.name=='nt': msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else: fcntl.flock(handle,fcntl.LOCK_UN)


def _eligible(root,date,expected):
    """A failed or incomplete subject stays pending; no approval is inferred."""
    stages={};hashes={}
    for symbol in expected:
        result=root/'results'/symbol
        try:
            binding=next(p for p in (result/'review-binding.json',result/'review-binding.held.json') if p.is_file())
            stage=read(binding)['review_stage']
            if not isinstance(stage,str) or not stage.replace('-','').isalpha():
                continue
            review=root/'jobs'/(symbol+'-'+date.replace('-','')+'-'+stage)/'output.json'
            html,proof,_=qualified_html(result,review,continuity=True)
            stages[symbol]=stage
            hashes[symbol]={'html':hashlib.sha256(html.encode()).hexdigest(),
                            'article':_sha(result/'article.json'),'review':_sha(review),
                            'fallback':_json_sha(proof) if proof else None}
        except (OSError,ValueError,KeyError,StopIteration,TypeError,json.JSONDecodeError):
            continue
    return stages,hashes


def _copy_subject(source,target,symbol,date,stage):
    shutil.copytree(source/'results'/symbol,target/'results'/symbol)
    names=list((source/'jobs').glob(symbol+'-'+date.replace('-','')+'-*'))
    if not (source/'jobs'/(symbol+'-'+date.replace('-','')+'-'+stage)) in names:
        raise ValueError('Bound review job missing from subject snapshot')
    for job in names:
        if job.is_dir() and not job.is_symlink():
            (target/'jobs').mkdir(exist_ok=True)
            shutil.copytree(job,target/'jobs'/job.name)
    if (source/'primary').is_dir():
        (target/'primary').mkdir(exist_ok=True)
        for path in (source/'primary').glob(symbol+'*'):
            if path.is_file(): shutil.copy2(path,target/'primary'/path.name)


def _snapshot(root,target,date,stages,prior,*,selection_frozen):
    target.mkdir(parents=True)
    for name in ('input-selection.json','shared-inputs.json','daily-state.json','smn-daily-state.json'):
        if name in ('input-selection.json','shared-inputs.json') and not selection_frozen:
            if (root/name).is_file():shutil.copy2(root/name,target/('rejected-'+name))
            continue
        if name in ('daily-state.json','smn-daily-state.json') and (root/name).is_file() and read(root/name).get('date')!=date:
            shutil.copy2(root/name,target/('rejected-'+name))
            continue
        if (root/name).is_file(): shutil.copy2(root/name,target/name)
    if (root/'production/posts.json').is_file():
        (target/'production').mkdir(exist_ok=True)
        shutil.copy2(root/'production/posts.json',target/'production/posts.json')
    for symbol,stage in stages.items():
        source=root
        if prior and prior.get('review_stages',{}).get(symbol)==stage and symbol not in _eligible(root,date,[symbol])[0]:
            source=Path(prior['transaction_root'])
        _copy_subject(source,target,symbol,date,stage)
    if (root/'archive-seed.json').is_file(): shutil.copy2(root/'archive-seed.json',target/'archive-seed.json')


def _archive_unstaged(root,tx):
    """Retain an interrupted local preparation before rebuilding the same attempt."""
    root=root.resolve();tx=tx.resolve()
    if root not in tx.parents or tx.name.startswith('incomplete-'):
        raise ValueError('Unsafe continuity preparation path')
    archive=tx.with_name('incomplete-'+tx.name+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
    if root not in archive.resolve().parents:
        raise ValueError('Unsafe continuity archive path')
    tx.rename(archive)


def require_policy(commit=None):
    import install_smn_primary_edition as installer
    installer.configure_production()
    installer.guard()
    activation=read('/etc/SMN/subscription-primary.json')
    if activation.get('publication_policy')!='continuity-v1' or (commit and activation.get('source_commit')!=commit):
        raise ValueError('Source-bound operator continuity-v1 opt-in required')
    repo=Path(__file__).resolve().parent.parent
    if subprocess.check_output(['git','-C',str(repo),'status','--porcelain','--untracked-files=no'],text=True).strip():
        raise ValueError('Production continuity requires unchanged committed source')
    return activation


def reconcile_legacy(root,date):
    receipt=read(root/'production-publication-receipt.json')
    expected=selected_lineup(root,date,required=True)
    urls={'https://seasonalmarketnews.com/editions/'+date+'/'+s+'/article.html' for s in expected}
    if receipt.get('status')!='live_verified' or receipt.get('edition_date')!=date or set(receipt.get('urls',[]))!=urls:
        raise ValueError('Legacy completed edition receipt differs from frozen selection')
    from urllib.parse import urlsplit
    import install_smn_primary_edition as installer
    for rel,digest in receipt.get('files',{}).items():
        if rel.startswith('editions/'+date+'/') and _sha(installer.WEB/rel)!=digest:
            raise ValueError('Legacy published article changed')
    if not any(rel.endswith('/article.html') for rel in receipt.get('files',{})):
        raise ValueError('Legacy receipt lacks public article hashes')
    return {**receipt,'complete':True,'reused_legacy':True}


def _remote_status(stage):
    return read(Path(stage['record'])/'receipt.json')


def _resume_or_publish(tx,repo,node,playwright):
    import install_smn_primary_edition as installer
    stage=read(tx/'production-stage.json');record=Path(stage['record'])
    current=_remote_status(stage)
    if current.get('transaction_id')!=stage['transaction_id']:
        raise ValueError('Local transaction identity changed')
    if current['status']=='live_verified': return current
    require_policy(current['source_commit'])
    if current['status']=='activating':
        installer.rollback(record)
        raise ValueError('Interrupted activation rolled back')
    if current['status']=='rolled_back': raise ValueError('Retain rolled-back transaction')
    try:
        if current['status']=='prepared': current=installer.activate(record)
        if current['status']!='active_pending_live_verification': raise ValueError('Unexpected publication state')
        _write(tx/'primary-activation.json',current)
        env={**os.environ,'SMN_PLAYWRIGHT':str(playwright)} if playwright else None
        subprocess.run([str(node),str(repo/'blog/subscription_primary_live.cjs'),str(tx)],check=True,env=env)
        proof=read(tx/'live-verification.json')
        if proof.get('origin')!='https://seasonalmarketnews.com' or not proof.get('deterministic_landing'):
            raise ValueError('Production deterministic browser proof missing')
        _write(record/'live-verification.json',proof)
        return installer.finish(record)
    except BaseException:
        if _remote_status(stage)['status'] in ('activating','active_pending_live_verification'):
            installer.rollback(record)
        raise


def publish_available(root: Path, date: str, target: str, *, repo: Path | None = None,
                      max_jobs: int = 40, node: str = 'node', playwright: str | None = None,
                      candidate_base: str | None = None) -> dict:
    """Publish the qualified subset or an honest notice; never creates model jobs."""
    if target!='production': raise ValueError('Production continuity target required')
    require_policy()
    if not 0<=max_jobs<=40: raise ValueError('Normal daily job cap is 40')
    datetime.strptime(date,'%Y-%m-%d')
    root=Path(root).resolve();repo=Path(repo or Path(__file__).resolve().parent.parent).resolve()
    root.mkdir(parents=True,exist_ok=True)
    with _lock(root):
        try:
            expected=selected_lineup(root,date,required=False) or []
        except ValueError:
            selection_path=root/'input-selection.json'
            if not selection_path.is_file() or read(selection_path).get('date')==date:
                raise
            expected=[]
        prior_path=root/'production-publication-receipt.json'
        prior=read(prior_path) if prior_path.is_file() else None
        if prior and prior.get('publication_policy')!='continuity-v1':
            return reconcile_legacy(root,date)
        if prior and prior.get('status')!='live_verified':
            raise ValueError('Prior publication is unverified')
        if prior and prior.get('expected_symbols') and expected!=prior['expected_symbols']:
            raise ValueError('Frozen selected lineup changed after publication')
        if prior and prior.get('selection_status')=='frozen' and (
                not expected or _sha(root/'input-selection.json')!=prior.get('selection_fingerprint')):
            raise ValueError('Frozen selected evidence changed after publication')
        selected_stages,hashes=_eligible(root,date,expected)
        stages=dict(prior.get('review_stages',{})) if prior else {}
        stages.update(selected_stages)
        if not set(stages)<=set(expected): raise ValueError('Prior published subject outside frozen lineup')
        for symbol in stages:
            if symbol not in hashes and prior:
                old=Path(prior['transaction_root'])
                _,saved=_eligible(old,date,[symbol])
                if symbol not in saved: raise ValueError('Previously published evidence changed')
                hashes[symbol]=saved[symbol]
        commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
        selection=_sha(root/'input-selection.json') if expected else None
        identity=_json_sha({'date':date,'selection':selection,'source':commit,'articles':hashes})
        if prior and prior.get('content_sha256')==identity:
            return prior
        revision=(prior.get('revision',0)+1) if prior else 1
        family=root/'publication-revisions'/identity
        for attempt in range(1,4):
            tx=family/('attempt-'+str(attempt))
            if not tx.exists():
                _snapshot(root,tx,date,stages,prior,selection_frozen=bool(expected))
                break
            stage_file=tx/'production-stage.json'
            if stage_file.is_file():
                saved=read(stage_file)
                if saved.get('candidate_base_main')!=candidate_base:
                    raise ValueError('Existing attempt has a different source qualification mode')
                state=_remote_status(saved)['status']
                if state in ('rolled_back','activating'):
                    if state=='activating':
                        import install_smn_primary_edition as installer
                        installer.rollback(Path(saved['record']))
                    continue
                break
            if (tx/'publication-package/manifest.json').is_file():
                # Resume immutable preparation if the local stage write was interrupted.
                break
            _archive_unstaged(root,tx)
            _snapshot(root,tx,date,stages,prior,selection_frozen=bool(expected))
            break
        else:
            raise ValueError('Three publication attempts exhausted; retain evidence and investigate')
        transaction_id=_json_sha({'content_sha256':identity,'attempt':attempt})
        frozen=selected_lineup(tx,date,required=False) or []
        if frozen!=expected or (_sha(tx/'input-selection.json') if expected else None)!=selection:
            raise ValueError('Selection changed while preparing continuity transaction')
        snap_stages,snap_hashes=_eligible(tx,date,list(stages))
        if snap_stages!=stages or snap_hashes!=hashes:
            raise ValueError('Qualified article evidence changed while preparing transaction')
        if not (tx/'production-stage.json').is_file():
            import install_smn_primary_edition as installer
            if not (tx/'publication-package/manifest.json').is_file():
                package(tx,date,commit,stages,target_origin='https://seasonalmarketnews.com',
                        continuity={'revision':revision,'revision_id':identity,'transaction_id':transaction_id})
            validate_staged_reviews(tx)
            _write(tx/'production-stage.json',installer.prepare(tx/'publication-package'))
        verified=_resume_or_publish(tx,repo,node,playwright)
        manifest=read(tx/'publication-package/manifest.json')
        if verified.get('status')!='live_verified' or verified.get('revision_id')!=identity or verified.get('transaction_id')!=transaction_id:
            raise ValueError('Public verification did not establish this revision')
        receipt={**verified,'production_written':True,'publication_policy':'continuity-v1','revision':revision,'revision_id':identity,
                 'transaction_id':transaction_id,'content_sha256':identity,
                 'selection_fingerprint':manifest['selection_sha256'],'selection_status':manifest['selection_status'],
                 'expected_symbols':manifest['expected_symbols'],'published_symbols':manifest['published_symbols'],
                 'pending_symbols':manifest['pending_symbols'],'coverage_status':manifest['coverage_status'],
                 'complete':manifest['complete'],'review_stages':stages,'transaction_root':str(tx),
                 'latest_update':datetime.now(timezone.utc).isoformat()}
        _write(tx/'coverage-receipt.json',receipt)
        _write(prior_path,receipt)
        return receipt
