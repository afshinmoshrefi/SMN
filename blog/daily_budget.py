"""Conservative all-namespace daily accounting; no model calls or financial math."""
from pathlib import Path
from contextlib import contextmanager
from datetime import date as calendar_date
import hashlib,json,os,re
PREFIXES=("failed-attempt-","transient-attempt-","authentication-retry-")
class BudgetEvidenceError(Exception):pass
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text(encoding="utf8"))
def snapshot(scan_root,date,registry,date_root=False):
    try:return _snapshot(scan_root,date,registry,date_root)
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as exc:
        raise BudgetEvidenceError("Unreadable or invalid daily inventory evidence:"+str(exc))
def _snapshot(scan_root,date,registry,date_root=False):
    original_scan=Path(scan_root);original_registry=Path(registry)
    if original_scan.is_symlink() or original_registry.is_symlink():raise BudgetEvidenceError("Symlink daily scan/registry root")
    scan_root=original_scan.resolve();registry=original_registry.resolve()
    if not scan_root.is_dir():raise BudgetEvidenceError("Daily scan root missing")
    receipts={};receipt_paths={};pending={};archives={};running=[];namespaces=[]
    def walk_error(exc):raise BudgetEvidenceError("Unreadable daily namespace:"+str(exc))
    for current,dirs,files in os.walk(str(scan_root),followlinks=False,onerror=walk_error):
        here=Path(current)
        # os.walk(followlinks=False) silently skips linked directories. Refuse
        # them before filtering so linked jobs/namespaces cannot hide attempts.
        for name in dirs+files:
            entry=here/name
            if entry.is_symlink() and (name in dirs or name=="jobs" or date in here.parts):
                raise BudgetEvidenceError("Linked daily namespace/input is not countable safely:"+str(entry))
        dirs[:]=[d for d in dirs if not re.fullmatch(r"20\d\d-\d\d-\d\d",d) or d==date]
        if here.name!="jobs":continue
        dirs[:]=[]
        if not date_root and date not in here.parts:continue
        if here.is_symlink():raise BudgetEvidenceError("Symlink daily job namespace")
        namespaces.append(str(here))
        for job in sorted(here.iterdir()):
            if not job.is_dir():continue
            if job.is_symlink():raise BudgetEvidenceError("Symlink daily job directory")
            if any(p.is_symlink() for p in job.iterdir()):raise BudgetEvidenceError("Linked job evidence or archive is not countable safely")
            path=str(job.resolve());receipt=job/"receipt.json";manifest=job/"job.json"
            if receipt.exists():
                if receipt.is_symlink():raise BudgetEvidenceError("Symlink receipt")
                h=sha(receipt);r=read(receipt)
                if r.get("job_id")!=job.name:raise BudgetEvidenceError("Receipt identity differs from job directory")
                evidence={name:sha(job/name) for name in ["job.json","execution.json","invocation.json","events.jsonl"] if (job/name).is_file()}
                if manifest.exists():
                    m=read(manifest)
                    if m.get("job_id")!=r.get("job_id") or m.get("input_hashes")!=r.get("input_hashes") or m.get("evidence_sha256")!=r.get("evidence_sha256"):
                        raise BudgetEvidenceError("Completed receipt/manifest custody differs")
                if h in receipts:
                    prior=receipts[h]["dispatch_evidence"]
                    if any(prior[k]!=v for k,v in evidence.items() if k in prior):
                        raise BudgetEvidenceError("Copied receipt has differing manifest/dispatch evidence")
                    prior.update(evidence);receipts[h]["paths"].append(path)
                else:receipts[h]={"job_id":job.name,"paths":[path],"dispatch_evidence":evidence}
                receipt_paths[path]=h
            else:
                pending[path]={"job_id":job.name,"manifest_sha256":sha(manifest) if manifest.is_file() else None,"classification":"conservative_prepared_or_uncertain; not claimed actual model call"}
                state=job/"state.json"
                if (job/".claim").exists() or (state.is_file() and read(state).get("status")=="running"):
                    running.append(path)
            for child in job.iterdir():
                if child.is_dir() and child.name.startswith(PREFIXES):
                    if child.is_symlink():raise BudgetEvidenceError("Symlink retry archive")
                    archives[str(child.resolve())]={"prefix":next(p for p in PREFIXES if child.name.startswith(p)),"classification":"conservative additional attempt; no archive deletion or exclusion"}
    orphans={};reservations={}
    for p in sorted((registry/"reservations").glob("*/reservation.json")):
        if p.is_symlink() or p.parent.is_symlink():raise BudgetEvidenceError("Linked reservation evidence")
        r=read(p)
        if r.get("job_id")!=p.parent.name or r.get("slot_cost")!=1:raise BudgetEvidenceError("Malformed daily reservation")
        reservations[p.parent.name]=r
        target=r.get("job_path")
        if target in pending and pending[target]["job_id"]!=r["job_id"]:
            raise BudgetEvidenceError("Reservation target belongs to another pending job identity")
        if target in receipt_paths and receipts[receipt_paths[target]]["job_id"]!=r["job_id"]:
            raise BudgetEvidenceError("Reservation target belongs to another completed job identity")
        if target and Path(target).name!=r["job_id"]:
            raise BudgetEvidenceError("Reservation target path does not name its own job identity")
        if not target or (target not in pending and target not in receipt_paths):
            orphans[p.parent.name]={"classification":"durable reservation without certain matching job; never refunded","job_path":target}
    count=len(receipts)+len(pending)+len(archives)+len(orphans)
    return {"consumed_conservative":count,"completed_unique_receipts":len(receipts),"prepared_or_uncertain_paths":len(pending),"archived_attempt_paths":len(archives),"orphan_reservations":len(orphans),"namespaces":namespaces,"receipt_identity_union":receipts,"pending_paths":pending,"retry_archive_paths":archives,"reservation_without_job":orphans,"all_reservations":reservations,"running_or_claimed_without_receipt":running,"copied_receipts_deduplicated_by":"exact receipt SHA plus matching overlapping manifest/dispatch evidence; never job ID alone","retirement_exclusions":[],"retirement_policy":"No unproven never-dispatched preparation is excluded; genuine retirement proof requires a new explicit versioned input."}


# Native normal runs share the recovery registry and its exact mkdir lease.
# Preparation is local persistence only: the lease covers the complete prepare
# call, and any partially created directory continues to reserve its attempt.
# No existing job/receipt/contract/dispatch provenance is rewritten.
DEFAULT_SCAN_ROOT = Path('/var/lib/tradewave/smn-daily')
SCAN_ROOT_ENV = 'SMN_DAILY_BUDGET_ROOT'


def _unlinked(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise BudgetEvidenceError('Linked daily custody path: ' + str(path))
    return path


def scope(edition, date=None):
    """Configured SMN root across profiles, or nearest edition DATE in fixtures.

    An undated isolated fixture stays inside its supplied edition directory.
    It never scans arbitrary workspace/temp siblings. Real editions have DATE
    ancestry (or an explicit configured SMN root and date).
    """
    edition = _unlinked(edition).resolve()
    dated = next((p for p in (edition, *edition.parents)
                  if re.fullmatch(r'20\d\d-\d\d-\d\d', p.name)), None)
    if dated:
        if date is not None and str(date)[:10] != dated.name:
            raise BudgetEvidenceError('Edition date differs from daily custody path')
        date = dated.name
    elif date is None:
        state = edition/'smn-daily-state.json'
        if state.exists(): date = read(state).get('date')
    if date is not None:
        date = str(date)[:10]
        try: calendar_date.fromisoformat(date)
        except ValueError as exc: raise BudgetEvidenceError('Invalid daily budget date') from exc
    else:
        date = 'undated-fixture'
    configured = os.environ.get(SCAN_ROOT_ENV)
    if configured:
        scan = _unlinked(configured).resolve()
        if not scan.is_dir() or edition != scan and scan not in edition.parents:
            raise BudgetEvidenceError('Job lies outside configured SMN daily root')
        if date == 'undated-fixture':
            raise BudgetEvidenceError('Configured shared accounting requires edition date')
        date_root = False
    elif edition == DEFAULT_SCAN_ROOT or DEFAULT_SCAN_ROOT in edition.parents:
        scan = _unlinked(DEFAULT_SCAN_ROOT).resolve();date_root = False
        if date == 'undated-fixture':
            raise BudgetEvidenceError('Production shared accounting requires edition date')
    else:
        scan = dated or edition;date_root = True
    registry = _unlinked(scan/'daily-budget60'/date)
    return scan, date, registry, date_root


def inventory(edition, date=None):
    scan, date, registry, date_root = scope(edition, date)
    if not scan.exists():
        return {'consumed_conservative': 0, 'reservation_without_job': {},
                'all_reservations': {}, 'pending_paths': {}, 'receipt_identity_union': {}}
    # glob would otherwise silently follow an intermediate reservation link.
    if registry.exists():
        for here, dirs, files in os.walk(registry, followlinks=False,
                                        onerror=lambda exc: (_ for _ in ()).throw(exc)):
            if any((Path(here)/name).is_symlink() for name in dirs+files):
                raise BudgetEvidenceError('Linked shared daily reservation custody')
    return snapshot(scan, date, registry, date_root=date_root)


def jobs_used(edition, date=None):
    return inventory(edition, date)['consumed_conservative']


@contextmanager
def reservation_lease(edition, date):
    """Recovery-compatible mkdir lease with attributable normal crash recovery."""
    from normal_budget_lease import lease
    _, _, registry, _ = scope(edition, date)
    registry.mkdir(parents=True, exist_ok=True)
    with lease(registry): yield


def prepare(edition, date, job_id, persist, maximum=60):
    """Atomically admit one normal preparation; never consume an orphan slot."""
    if type(maximum) is not int or not 0 <= maximum <= 60:
        raise BudgetEvidenceError('Daily model-job cap is 60')
    if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}', job_id):
        raise BudgetEvidenceError('Invalid model job identity')
    edition = _unlinked(edition).resolve();target = edition/'jobs'/job_id
    with reservation_lease(edition, date):
        before = inventory(edition, date)
        # A matching orphan can represent a lost/uncertain dispatch. Normal
        # preparation has no attributable never-dispatched recovery proof and
        # must never consume or replace that reservation, even below the cap.
        matching = [r for r in before['all_reservations'].values()
                    if r.get('job_path') == str(target) and r.get('job_id') == job_id]
        if matching:
            raise BudgetEvidenceError('Orphan reservation requires explicit attributable recovery; normal preparation refused')
        if target.exists():
            raise BudgetEvidenceError('Existing model job custody retained')
        if before['consumed_conservative'] + 1 > maximum:
            raise BudgetEvidenceError('Shared daily model-job budget of %d exhausted' % maximum)
        return persist()
