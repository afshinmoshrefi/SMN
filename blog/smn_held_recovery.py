"""Plan, then explicitly resume only named held work in a separate retained copy.

The default cap is cumulative, including every job copied from the failed run.
This command does not publish or send email. Publication uses its normal gates.
"""
import argparse
import json
import shutil
from pathlib import Path

from smn_daily import Day, Hold, now
from smn_subscription_daily import lock
from subscription_publication import selected_lineup, read, write, digest_bytes
from model_job_evidence import DAILY_JOB_LIMIT

PUBLICATION_FILES={'publication-package','production-stage.json','production-publication-receipt.json',
    'primary-stage.json','primary-activation.json','primary-activation-attempt.json',
    'dev-stage.json','dev-activation.json','dev-publication-receipt.json',
    'live-verification.json','live-landing-visual-checks.json','live-landing-visual-failed.json',
    'committed-source','committed-source.tar','publication-package.tar'}


def hashes(root):
    root=Path(root);result={}
    for p in root.rglob('*'):
        if p.is_symlink():raise Hold('Recovery refuses symlinked evidence: '+str(p))
        if p.is_file():result[p.relative_to(root).as_posix()]=digest_bytes(p.read_bytes())
    return result


def plan(root,retry_source=(),rereview=(),max_jobs=DAILY_JOB_LIMIT):
    root=Path(root).resolve();state=read(root/'smn-daily-state.json');date=state['date']
    expected=selected_lineup(root,date,required=True)
    requested=list(retry_source)+list(rereview)
    if not requested or len(set(requested))!=len(requested) or not set(requested)<=set(expected):
        raise Hold('Name distinct held subjects from the frozen selection')
    day=Day(root,date,profile=state['profile'],roles=state['roles'],max_jobs=max_jobs,
            publication_origin=state.get('publication_origin'))
    approved={};minimum=1  # A new publication needs its own landing inspection.
    for sym in expected:
        row=state['articles'].get(sym,{})
        if sym not in requested:
            if row.get('held') or not row.get('finalized'):
                raise Hold('Recovery omits an unfinished selected subject: '+sym)
            approved[sym]=day._approved_snapshot(sym)
            continue
        if not row.get('held') or row.get('finalized'):
            raise Hold('Recovery cannot alter a completed subject: '+sym)
        if sym in retry_source:
            if ((root/'research'/(sym+'.json')).exists() or
                'fewer than two accessible primary pages' not in row['held']['reason']):
                raise Hold('Source retry requires a held primary-page collection: '+sym)
            minimum+=5  # Research, writing, review, hero, article pixels; discovery reused.
        else:
            if row.get('mechanical_ok') is not True or row.get('review_stage')!='rereview':
                raise Hold('Reinspection requires a mechanically valid held second review: '+sym)
            minimum+=3  # Fresh independent review, hero, article pixels; draft unchanged.
    used=day.jobs_used()
    return {'date':date,'source_root':str(root),'retry_source':list(retry_source),'rereview':list(rereview),
            'jobs_used':used,'minimum_new_jobs':minimum,'minimum_total_jobs':used+minimum,
            'max_jobs':max_jobs,'budget_sufficient':used+minimum<=max_jobs,
            'approved_articles':approved,'expected_symbols':expected,
            'original_files':hashes(root)}


def recover(root,destination,retry_source=(),rereview=(),max_jobs=DAILY_JOB_LIMIT):
    root=Path(root).resolve();destination=Path(destination).resolve()
    if destination.parent!=root.parent or destination==root:
        raise Hold('Recovery must use a separate sibling of the original edition')
    with lock(root.parent.parent):
        ledger_path=destination/'held-recovery.json'
        if destination.exists():
            if not ledger_path.is_file():raise Hold('Unrecorded recovery directory; preserve and inspect it')
            ledger=read(ledger_path)
            if (ledger['source_root']!=str(root) or ledger['retry_source']!=list(retry_source) or
                ledger['rereview']!=list(rereview) or ledger['max_jobs']!=max_jobs or
                ledger['original_files']!=hashes(root)):
                raise Hold('Recovery request or original evidence changed')
        else:
            ledger=plan(root,retry_source,rereview,max_jobs)
            if not ledger['budget_sufficient']:
                raise Hold('Recovery needs at least %d cumulative jobs; authorized cap is %d' %
                           (ledger['minimum_total_jobs'],max_jobs))
            for sym in ledger['approved_articles']:
                from editorial_gate import verify_complete
                verify_complete(root/'results'/sym)
            shutil.copytree(root,destination,ignore=lambda folder,names:
                            list(set(names)&PUBLICATION_FILES) if Path(folder)==root else [])
            ledger.update(utc=now(),status='prepared')
            write(ledger_path,ledger)
        state=read(destination/'smn-daily-state.json')
        day=Day(destination,ledger['date'],profile=state['profile'],roles=state['roles'],
                max_jobs=max_jobs,publication_origin=state.get('publication_origin'))
        if ledger['status']=='prepared':
            for sym in retry_source:day.state['articles'][sym].pop('held',None)
            for sym in rereview:
                row=day.state['articles'][sym]
                row.pop('held',None);row['review_stage']='reinspect-review'
            day.save();ledger['status']='running';write(ledger_path,ledger)
        day.symbols=ledger['expected_symbols']
        try:
            day.research()
            day.symbols=list(retry_source)+list(rereview)
            day.articles();day.visual()
            day.symbols=ledger['expected_symbols']
            result=day.check()
            ledger.update(status='ready_for_publication' if result['passed'] else 'held',result=result)
            return result
        finally:
            for sym,expected in ledger['approved_articles'].items():
                if day._approved_snapshot(sym)!=expected:raise Hold('Approved evidence changed: '+sym)
            if hashes(root)!=ledger['original_files']:raise Hold('Original failed run changed during recovery')
            ledger['jobs_used_after']=day.jobs_used();write(ledger_path,ledger)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--retry-source',nargs='*',default=[])
    parser.add_argument('--rereview',nargs='*',default=[])
    parser.add_argument('--max-jobs',type=int,default=DAILY_JOB_LIMIT)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    if args.execute:
        if not args.destination:parser.error('--execute requires --destination')
        result=recover(args.root,args.destination,args.retry_source,args.rereview,args.max_jobs)
    else:
        result=plan(args.root,args.retry_source,args.rereview,args.max_jobs)
        result={k:v for k,v in result.items() if k not in {'original_files','approved_articles'}}
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
