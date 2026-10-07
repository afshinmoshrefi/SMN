"""Bounded recovery of the existing private SMN workflow, never a second workflow.

No model, upload, mail or publication dispatch lives here. Only verified saved
private turns can recover a missing receipt. Live/foreign/uncertain effects are
preserved. The reconcile tick performs repairs; the other ticks watch its lease.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import time

from subscription_writer import load_json, save_json, sha256

POLICY='continuity-recovery-v1'
MAX_JOB_ATTEMPTS=4
BACKOFF_MINUTES=(5,15,30)
WORKER_LEASE_SECONDS={'progress':1100,'deliver':300,'reconcile':90}


def classify(error):
    text=str(error).lower()
    if re.search(r'unexpected tool|unsafe|symlink|different inputs|receipt.*changed|custody.*changed|hash.*differ|source.*changed',text):
        category='security'
    elif re.search(r'oauth.*(?:race|refresh)|rate.?limit|\b429\b|overload|temporar|econnreset|etimedout|timed? ?out|backoff',text):
        category='transient'
    elif re.search(r'budget|quota|usage limit',text):category='budget'
    elif re.search(r'login|unauthori|authentication|permission denied|\b401\b|\b403\b',text):category='authorization'
    elif re.search(r'hero|decorative|screenshot|layout|asset.*missing|missing.*asset|no layout',text):category='missing_asset'
    elif re.search(r'fact|primary|source|evidence|unsupported|metric|numeric|mechanical|expired|quote|review twice',text):category='factual'
    elif re.search(r'editorial|opening|framing|style|brevity',text):category='editorial'
    else:category='infrastructure'
    return {'category':category,'reason':str(error)[:500],
            'automatic_retry_allowed':category in {'transient','infrastructure'},
            'factual_checks_required':True,'policy':POLICY}


def attempts(job):
    from model_job_evidence import ATTEMPT_PREFIXES
    return 1+sum(p.is_dir() and p.name.startswith(ATTEMPT_PREFIXES) for p in Path(job).iterdir())


def process_identity(pid):
    """Linux birth identity, not PID alone. Unavailable identity forbids termination."""
    try:
        root=Path('/proc')/str(pid)
        stat=(root/'stat').read_text();fields=stat[stat.rindex(')')+2:].split()
        return {'pid':pid,'start_ticks':fields[19],
                'argv_sha256':sha256((root/'cmdline').read_bytes()),
                'cwd':str((root/'cwd').resolve())}
    except (OSError,ValueError,IndexError):return None


def alive(pid):
    if not isinstance(pid,int) or pid<=1:return None
    try:os.kill(pid,0)
    except ProcessLookupError:return False
    except (PermissionError,OSError):return None
    return True


def claim_state(job):
    claim=Path(job)/'.claim'
    if not claim.exists():return {'status':'absent'}
    if claim.is_symlink() or set(p.name for p in claim.iterdir())!={'owner.json'}:
        return {'status':'uncertain','reason':'Unrecognized claim; preserve its owner'}
    owner=load_json(claim/'owner.json')
    if owner.get('host')!=socket.gethostname():return {'status':'foreign','owner':owner}
    parent=alive(owner.get('pid'));child=alive(owner.get('child_pid')) if owner.get('child_pid') else False
    return {'status':'dead' if parent is False and child is False else 'live' if parent or child else 'uncertain',
            'owner':owner,'parent_alive':parent,'child_alive':child}


def terminate_stalled_private_child(job,current):
    """Retire only this workflow's overdue read-only CLI with exact birth/argv/cwd.

    Never signals the parent, a reused PID, a foreign process, an upload or sender.
    This is called only under the existing controller lock or by its own worker.
    """
    job=Path(job).resolve();state=claim_state(job);owner=state.get('owner',{})
    if state.get('child_alive') is not True:return False
    manifest=load_json(job/'job.json');invocation=load_json(job/'invocation.json')
    started=datetime.fromisoformat(owner['utc'].replace('Z','+00:00'))
    argv=invocation.get('argv',[]);identity=owner.get('child_identity')
    if (manifest.get('publish') is not False or manifest.get('provider')!='openai' or
            invocation.get('timeout_seconds')!=900 or '--ephemeral' not in argv or
            '--sandbox' not in argv or argv[argv.index('--sandbox')+1]!='read-only' or
            identity is None or identity!=process_identity(owner.get('child_pid')) or
            identity.get('cwd')!=str(job) or
            owner.get('invocation_sha256')!=sha256((job/'invocation.json').read_bytes()) or
            started.tzinfo is None or current<started+timedelta(seconds=1020)):
        return False
    pid=owner['child_pid']
    os.kill(pid,signal.SIGTERM)
    deadline=time.monotonic()+5
    while alive(pid) is True and time.monotonic()<deadline:time.sleep(.1)
    if alive(pid) is True:
        if process_identity(pid)!=identity:raise ValueError('Managed child identity changed; do not signal another process')
        os.kill(pid,signal.SIGKILL)
    save_json(job/'managed-termination.json',{'policy':POLICY,'utc':current.isoformat(),
              'child_identity':identity,'reason':'own private CLI exceeded timeout plus grace',
              'model_attempt_already_reserved':True,'side_effects_reconciled':False})
    return True


def clear_dead_claim(job):
    job=Path(job).resolve();claim=job/'.claim'
    if claim_state(job).get('status')!='dead':return False
    name=job/('retained-dead-claim-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
    if job not in claim.resolve().parents or job not in name.resolve().parents:
        raise ValueError('Unsafe recovery claim path')
    claim.rename(name)
    return True


def reconcile_private_job(job,current=None):
    """No replay until completion/ownership evidence has been reconciled."""
    job=Path(job);current=current or datetime.now(timezone.utc)
    from model_job_evidence import completed_receipt
    if (job/'receipt.json').exists():
        completed_receipt(job)
        return {'status':'verified_completed','job':job.name,'new_dispatches':0}
    claimed=claim_state(job)
    if claimed['status'] in {'foreign','uncertain','live'}:
        retired=terminate_stalled_private_child(job,current) if claimed['status']=='live' else False
        return {'status':'awaiting_process_exit' if retired else 'running_or_uncertain',
                'job':job.name,'retry_allowed':False,'claim_status':claimed['status']}
    if (job/'execution.json').is_file():
        execution=load_json(job/'execution.json')
        if execution.get('returncode')==0:
            from subscription_writer import reconcile_completed_job
            receipt=reconcile_completed_job(job)
            if claimed['status']=='dead':clear_dead_claim(job)
            return {'status':'verified_completed','job':job.name,'new_dispatches':0,
                    'recovered_receipt':receipt['output_sha256']}
    # A parent dying before its dispatch completion checkpoint is uncertain.
    # A directory without a claim is not evidence that a model/process finished.
    state=load_json(job/'state.json') if (job/'state.json').is_file() else {}
    if claimed['status']=='dead':
        execution=load_json(job/'execution.json') if (job/'execution.json').is_file() else {}
        if execution.get('returncode') is None:
            return {'status':'uncertain_completion','job':job.name,'retry_allowed':False}
        clear_dead_claim(job)
    if state.get('status')=='failed_needs_review':
        return {'status':'failed_private_turn','job':job.name,'failure':classify(state.get('reason','')),
                'retry_allowed':classify(state.get('reason',''))['automatic_retry_allowed']}
    return {'status':'ready' if state.get('status')=='ready' else 'uncertain_completion',
            'job':job.name,'retry_allowed':False}


def checkpoint(day,symbol,stage,status,reason=None):
    path=day.root/'stage-checkpoints.json'
    record=load_json(path) if path.is_file() else {'date':day.date,'policy':POLICY,'stages':{}}
    key=symbol+':'+stage
    prior=record['stages'].get(key,{})
    entry={**prior,'symbol':symbol,'stage':stage,'status':status,
           'updated_utc':datetime.now(timezone.utc).isoformat()}
    artifacts={}
    names=({'research':['research/'+symbol+'.json','primary/'+symbol+'.txt','primary/'+symbol+'.receipt.json'],
            'article':['results/'+symbol+'/'+name for name in ('article.json','bundle.json','mechanical-checks.json','review-binding.json','generation.json')],
            'visual':['results/'+symbol+'/'+name for name in ('layout-checks.json','visual-checks.json','completion-check.json')]}.get(stage,[]))
    for name in names:
        artifact=day.root/name
        if artifact.is_file() and not artifact.is_symlink():artifacts[name]=sha256(artifact.read_bytes())
    if status=='completed' and len(artifacts)!=len(names):
        status='pending';entry['status']=status
    entry['artifact_sha256']=artifacts
    if reason is not None:entry['failure']=classify(reason)
    # Never claims stage verification from a process exit; verifier remains downstream.
    entry['verification']='existing immutable artifact/source gates' if status=='completed' else 'pending'
    record['stages'][key]=entry;save_json(path,record)
    worker=day.root.parent/'continuity-workers/progress.json'
    if worker.is_file():
        owner=load_json(worker)
        if owner.get('pid')==os.getpid() and owner.get('host')==socket.gethostname():
            owner['updated_utc']=entry['updated_utc'];owner['checkpoint']=key;save_json(worker,owner)


def worker_start(root,day,phase,current):
    path=Path(root)/day/'continuity-workers'/(phase+'.json')
    prior=load_json(path) if path.is_file() else {}
    record={'date':day,'phase':phase,'status':'running','pid':os.getpid(),'host':socket.gethostname(),
            'started_utc':current.isoformat(),'updated_utc':current.isoformat(),
            'lease_seconds':WORKER_LEASE_SECONDS[phase],
            'attempts':prior.get('attempts',0)+1,'prior_status':prior.get('status'),
            'consecutive_failures':prior.get('consecutive_failures',0)+(prior.get('status')=='running')}
    save_json(path,record);return path


def worker_finish(path,result,current):
    record=load_json(path)
    if record.get('pid')!=os.getpid() or record.get('host')!=socket.gethostname():
        raise ValueError('Continuity worker ownership changed')
    status=result.get('status','unknown')
    record.update(status=status,updated_utc=current.isoformat(),result=result,
                  process_return_is_completion_proof=False,
                  consecutive_failures=record.get('consecutive_failures',0)+1 if status in {'held','failed','needs_attention'} else 0)
    save_json(path,record)


def watch_workers(root,day,current):
    """Other independently scheduled phases observe even a failed recovery worker."""
    conditions=[]
    for phase in WORKER_LEASE_SECONDS:
        path=Path(root)/day/'continuity-workers'/(phase+'.json')
        if not path.is_file():
            if phase=='reconcile':conditions.append({'phase':phase,'kind':'worker_not_observed','recovery_policy':POLICY})
            continue
        try:
            worker=load_json(path)
            stamp=datetime.fromisoformat(worker['updated_utc'].replace('Z','+00:00'))
            if stamp.tzinfo is None or not isinstance(worker.get('lease_seconds'),int):
                raise ValueError('Worker lease missing')
        except (OSError,ValueError,KeyError,TypeError,AttributeError):
            conditions.append({'phase':phase,'kind':'worker_evidence_invalid','recovery_policy':POLICY})
            continue
        if worker.get('status')=='running' and current>stamp+timedelta(seconds=worker['lease_seconds']):
            conditions.append({'phase':phase,'kind':'worker_stalled','pid':worker.get('pid'),
                               'owner_alive':alive(worker.get('pid')),'recovery_policy':POLICY})
        elif worker.get('status') in {'held','failed','needs_attention'}:
            conditions.append({'phase':phase,'kind':'worker_failed','recovery_policy':POLICY})
    record={'date':day,'updated_utc':current.isoformat(),'conditions':conditions,
            'recovery_worker_observed':(Path(root)/day/'continuity-workers/reconcile.json').is_file(),
            'completion_inferred_from_timer':False}
    save_json(Path(root)/day/'continuity-supervision.json',record)
    return record


def repair_held_articles(day,current):
    """Prepare existing targeted stages; no paid/model call or fabricated approval."""
    ledger_path=day.root/'automatic-recovery.json'
    ledger=load_json(ledger_path) if ledger_path.is_file() else {'date':day.date,'policy':POLICY,'articles':{}}
    results=[]
    for symbol,state in day.state['articles'].items():
        if not state.get('held'):continue
        original_hold=dict(state['held'])
        failure=classify(original_hold.get('reason',''));prior=ledger['articles'].get(symbol,{})
        row={'symbol':symbol,'failure':failure,'status':'preserved', 'new_model_calls':0}
        jobs=list((day.root/'jobs').glob(symbol+'-'+day.date.replace('-','')+'-*'))
        reconciled=[]
        try:
            for job in jobs:reconciled.append(reconcile_private_job(job,current))
            waiting=any((job/'state.json').is_file() and load_json(job/'state.json').get('retry_after_utc') and
                        current<datetime.fromisoformat(load_json(job/'state.json')['retry_after_utc']) for job in jobs)
            if waiting:
                row['status']='waiting_backoff'
            elif any(r['status'] in {'running_or_uncertain','uncertain_completion','awaiting_process_exit'} for r in reconciled):
                row['status']='awaiting_effect_reconciliation'
            elif failure['category'] in {'transient','infrastructure'}:
                retry_jobs=[(job,r) for job,r in zip(jobs,reconciled) if r.get('retry_allowed')]
                for job,result in retry_jobs:
                    if attempts(job)>=MAX_JOB_ATTEMPTS:raise ValueError('Per-job recovery attempts exhausted')
                    day._archive(job,result['failure']['reason'],'transient-attempt')
                if retry_jobs or any(r['status']=='verified_completed' for r in reconciled):
                    state.pop('held');day.save();row['status']='targeted_resume_prepared'
                elif prior.get('attempts',0)<2:
                    # Deterministic local/capture stage resumes its saved inputs.
                    state.pop('held');day.save();row['status']='targeted_resume_prepared'
                else:row['status']='safe_paths_exhausted'
            elif failure['category'] in {'factual','editorial'}:
                if not prior.get('editorial_repair_prepared') and day.jobs_used()+2<=day.max_jobs:
                    day.prepare_editorial_recovery(symbol,controller_owned=True)
                    row['status']='source_backed_repair_prepared';row['editorial_repair_prepared']=True
                else:row['status']='safe_paths_exhausted'
            elif failure['category']=='missing_asset':
                # Existing publisher's deterministic fallback revalidates factual
                # content and essential chart assets; it never changes a reviewer.
                from presentation_fallback import prepare_fallback
                _,proof=prepare_fallback(day.root/'results'/symbol)
                save_json(day.root/'results'/symbol/'recovery-presentation.json',proof)
                row['status']='verified_presentation_alternative_ready'
            else:row['status']='authorization_or_evidence_required'
        except Exception as exc:
            row.update(status='safe_paths_exhausted',repair_error=str(exc)[:500])
        row['original_hold']=original_hold
        row['utc']=current.isoformat();row['reconciliation']=reconciled
        row['attempts']=prior.get('attempts',0)+(row['status'] in {'targeted_resume_prepared','source_backed_repair_prepared'})
        row['editorial_repair_prepared']=row.get('editorial_repair_prepared',prior.get('editorial_repair_prepared',False))
        ledger['articles'][symbol]=row;results.append(row)
        save_json(ledger_path,ledger)
    return results


def verify_generation(root,day):
    """All selected subjects must have source-qualified bytes, not a success flag."""
    from production_continuity import _eligible
    from subscription_publication import selected_lineup
    edition=Path(root)/day/'chatgpt'
    try:
        expected=selected_lineup(edition,day,required=True)
        stages,hashes=_eligible(edition,day,expected)
        return {'complete':bool(expected) and set(stages)==set(expected),
                'verified_symbols':list(stages),'pending_symbols':[s for s in expected if s not in stages],
                'qualified_hashes':hashes}
    except (OSError,ValueError,KeyError,TypeError):
        return {'complete':False,'verified_symbols':[],'reason':'Selected source-bound artifact proof is incomplete'}


def supervise(root,day,current):
    """Existing reconcile phase repairs saved reader work under controller ownership."""
    root=Path(root);watch=watch_workers(root,day,current)
    from production_continuity_schedule import _lock
    with _lock(root/'controller.lock') as acquired:
        if not acquired:return {'status':'busy','reason':'Live controller retained','supervision':watch}
        edition=root/day/'chatgpt';state_path=edition/'smn-daily-state.json'
        if not state_path.is_file():return {'status':'waiting_for_reader','supervision':watch}
        state=load_json(state_path)
        from smn_daily import Day
        reader=Day(edition,day,profile='chatgpt',
                   publication_origin=state.get('publication_origin'),max_jobs=40)
        reader.continuity=True
        observations=[]
        for job in sorted((edition/'jobs').glob('*')):
            if not job.is_dir() or not (job/'job.json').is_file():continue
            try:
                manifest=load_json(job/'job.json')
                observed=reconcile_private_job(job,current);observations.append(observed)
                symbol=job.name.split('-')[0]
                article=reader.state['articles'].get(symbol,{})
                if (observed['status'] not in {'ready','verified_completed'} and manifest.get('stage')!='hero-check' and
                        not article.get('finalized')):
                    reader.state['articles'].setdefault(symbol,{})['held']={'utc':current.isoformat(),
                        'reason':load_json(job/'state.json').get('reason','Private job completion requires reconciliation')}
                    reader.save()
            except Exception as exc:
                observations.append({'job':job.name,'status':'evidence_hold','reason':str(exc)[:500]})
        repaired=repair_held_articles(reader,current)
        result={'date':day,'status':'recovery_prepared' if any(r['status'].endswith('prepared') for r in repaired) else
                'needs_attention' if any(r['status'] in {'safe_paths_exhausted','authorization_or_evidence_required'} for r in repaired) else 'observed',
                'articles':repaired,'jobs':observations,'supervision':watch,'new_model_calls':0}
        save_json(root/day/'continuity-recovery.json',result)
        return result
