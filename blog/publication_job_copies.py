"""Audited publication copies are read-only evidence, never new model effects."""
from pathlib import Path
from datetime import datetime
import hashlib, json, os, re

VERSION = 'publication-job-copy-v1'

def layout(job):
    job=Path(job).absolute()
    if (job.parent.name!='jobs' or not re.fullmatch(r'attempt-[1-3]',job.parent.parent.name)
            or not re.fullmatch(r'[0-9a-f]{64}',job.parent.parent.parent.name)
            or job.parent.parent.parent.parent.name!='publication-revisions'):
        return None
    reader=job.parent.parent.parent.parent.parent
    if not re.fullmatch(r'20\d\d-\d\d-\d\d',reader.parent.name):return None
    return reader,reader/'jobs'/job.name,job.parent.parent

def prevent_dispatch(job):
    if layout(job):
        raise ValueError('Publication snapshot jobs are read-only evidence; dispatch the canonical job')

def tree(folder):
    folder=Path(folder)
    if any(p.is_symlink() for p in (folder,*folder.parents)):
        raise ValueError('Linked publication-copy custody path')
    if not folder.is_dir():raise ValueError('Publication-copy custody directory missing')
    result={}
    for current,dirs,files in os.walk(folder,followlinks=False):
        here=Path(current)
        if any((here/name).is_symlink() for name in dirs+files):
            raise ValueError('Linked publication-copy evidence')
        for name in files:
            p=here/name;result[p.relative_to(folder).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    return result

def load(path):return json.loads(Path(path).read_text(encoding='utf8'))
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def evidence(job,date):
    shaped=layout(job)
    if not shaped:raise ValueError('Not a bound publication snapshot job')
    reader,canonical,tx=shaped
    if reader.parent.name!=date:raise ValueError('Publication-copy date differs')
    copied=tree(job);original=tree(canonical)
    if not copied.get('job.json') or copied['job.json']!=original.get('job.json'):
        raise ValueError('Publication-copy immutable contract differs')
    if load(Path(job)/'job.json').get('job_id')!=Path(job).name:
        raise ValueError('Publication-copy job identity differs')
    receipt=tx/'coverage-receipt.json';r=load(receipt)
    if (r.get('edition_date')!=date or r.get('status')!='live_verified'
            or r.get('production_written') is not True
            or r.get('transaction_root')!=str(tx)
            or Path(job).name.split('-')[0] not in r.get('published_symbols',[])):
        raise ValueError('Publication snapshot has no verified matching coverage receipt')
    if copied==original:
        if (canonical/'.claim').exists() or load(canonical/'state.json').get('status')=='running':
            raise ValueError('Canonical effect still running; preserve its custody')
        mode='exact_retained_tree'
    else:
        from model_job_evidence import completed_receipt
        completed_receipt(canonical)
        for name in ('invocation.json','job.json'):
            if not copied.get(name) or copied[name]!=original.get(name):
                raise ValueError('Historical copied dispatch identity differs')
        argv=load(canonical/'invocation.json').get('argv',[])
        if '--cd' not in argv or argv[argv.index('--cd')+1]!=str(canonical):
            raise ValueError('Historical copied invocation is not canonical')
        prefix=(Path(job)/'events.jsonl').read_bytes();full=(canonical/'events.jsonl').read_bytes()
        if not prefix or not full.startswith(prefix):
            raise ValueError('Historical copied turn events differ')
        started=[json.loads(line) for line in prefix.splitlines() if line.strip()]
        if (not started or started[0].get('type')!='thread.started'
                or not started[0].get('thread_id')):
            raise ValueError('Historical copied turn has no unique thread identity')
        mutable={'state.json','events.jsonl','.claim/owner.json'}
        if any(original.get(name)!=value for name,value in copied.items() if name not in mutable):
            raise ValueError('Historical copied immutable evidence differs')
        if load(Path(job)/'state.json').get('status')!='running':
            raise ValueError('Historical copied state is not the captured running effect')
        mode='same_completed_turn_prefix'
    return {'version':VERSION,'date':date,'copied_job_path':str(Path(job).absolute()),
            'canonical_job_path':str(canonical),'job_id':Path(job).name,'mode':mode,
            'copied_files':copied,'canonical_files':original,
            'coverage_receipt_sha256':digest(receipt)}

def proof_path(registry,job):
    identity=hashlib.sha256(str(Path(job).absolute()).encode()).hexdigest()
    return Path(registry)/'publication-copy-proofs'/(identity+'.json')

def verified_copy(job,date,registry,scan_root):
    path=proof_path(registry,job)
    if not path.exists():return None
    if any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('Linked publication-copy audit proof')
    proof=load(path)
    stamp=datetime.fromisoformat(proof.get('observed_utc','').replace('Z','+00:00'))
    if not proof.get('actor') or stamp.tzinfo is None:
        raise ValueError('Publication-copy audit is not attributable')
    current=evidence(job,date)
    if any(proof.get(k)!=v for k,v in current.items()):
        raise ValueError('Publication-copy audit custody changed')
    root=Path(scan_root).resolve();canonical=Path(current['canonical_job_path']).resolve()
    if root!=canonical and root not in canonical.parents:
        raise ValueError('Publication-copy canonical job is outside the shared inventory')
    for reservation in (Path(registry)/'reservations').glob('*/reservation.json'):
        if load(reservation).get('job_path')==current['copied_job_path']:
            raise ValueError('Publication copy has its own real reservation; keep it charged')
    return {**current,'audit_proof_path':str(path),'audit_proof_sha256':digest(path)}
