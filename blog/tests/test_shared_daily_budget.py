"""Shared daily admission/accounting using temporary files and mocked providers.

The concurrency test uses the actual atomic directory lease on this filesystem;
it does not substitute a fake flock, dispatch a model or contact a host.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch
import hashlib,json,os,sys,unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import daily_budget as budget
import model_job_evidence
import smn_daily
import smn_models

DATE='2026-10-09'
def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes((json.dumps(value,sort_keys=True)+'\n').encode())

class SharedBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.pool=Path(self.temp.name);self.date=self.pool/DATE
        self.normal=self.date/'chatgpt';self.other=self.date/'claude'
        self.normal.mkdir(parents=True);self.other.mkdir()
        self.env=patch.dict(os.environ,{},clear=False);self.env.start();self.addCleanup(self.env.stop)
        os.environ.pop(budget.SCAN_ROOT_ENV,None)
        self.dispatch=patch.object(smn_models,'run',side_effect=AssertionError('No provider calls in budget tests'))
        self.dispatch.start();self.addCleanup(self.dispatch.stop)
        self.roles={'review':{'provider':'codex','model':'gpt-6-sol','effort':'medium'}}
    def pending(self,edition,count,prefix='existing'):
        for n in range(count):(edition/'jobs'/f'{prefix}-{n}').mkdir(parents=True)
    def prepare(self,edition,job_id):
        # Use the real file-only bridge through the owned shared admission API.
        return smn_models.prepare(self.roles,'review',edition/'jobs',job_id,'SYNTHETIC',{'type':'object'},
            as_of=DATE,valid_until=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
            evidence_sha256='synthetic-budget-only',stage='review')
    def complete(self,edition,job_id,token):
        job=edition/'jobs'/job_id
        m={'job_id':job_id,'input_hashes':{'prompt.txt':token},'evidence_sha256':'SYNTHETIC'}
        r={**m,'token':token}
        save(job/'job.json',m);save(job/'receipt.json',r);return job
    def test_prepare60_allowed_and61_refused_across_profiles(self):
        self.pending(self.normal,30);self.pending(self.other,29)
        self.assertEqual(model_job_evidence.jobs_used(self.normal),59)
        last=self.prepare(self.other,'last-review')
        self.assertTrue((last/'job.json').exists())
        with self.assertRaisesRegex(budget.BudgetEvidenceError,'budget of 60'):
            self.prepare(self.normal,'excess-review')
        self.assertFalse((self.normal/'jobs/excess-review').exists())
        self.assertEqual(smn_daily.Day(self.normal,DATE,roles={}).jobs_used(),60)
    def test_concurrent_prepares_at59_admit_exactly_one_without61_race(self):
        self.pending(self.normal,59);start=Barrier(8)
        def worker(number):
            start.wait()
            try:self.prepare(self.other,f'parallel-{number}');return 'admitted'
            except budget.BudgetEvidenceError:return 'held'
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(worker,range(8)))
        self.assertEqual(results.count('admitted'),1);self.assertEqual(results.count('held'),7)
        self.assertEqual(budget.jobs_used(self.normal,DATE),60)
        self.assertFalse((budget.scope(self.normal,DATE)[2]/'.reservation-lease').exists())
    def test_concurrent_archive_and_prepare_at59_share_one_atomic_slot(self):
        self.pending(self.normal,59)
        job=self.normal/'jobs/existing-0';save(job/'state.json',{'status':'failed_needs_review','reason':'503'})
        day=smn_daily.Day(self.normal,DATE,roles={});start=Barrier(2)
        def archive():
            start.wait()
            try:day._archive(job,'503','transient-attempt');return 'admitted'
            except (budget.BudgetEvidenceError,smn_daily.Hold):return 'held'
        def prepare():
            start.wait()
            try:self.prepare(self.other,'parallel-review');return 'admitted'
            except budget.BudgetEvidenceError:return 'held'
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(archive),pool.submit(prepare)];results=[f.result() for f in futures]
        self.assertEqual(results.count('admitted'),1);self.assertEqual(budget.jobs_used(self.normal,DATE),60)
    def test_failed_preparation_remains_pending_and_resume_does_not_refund(self):
        self.pending(self.normal,59)
        def partial(*a,**k):
            (Path(a[0])/a[1]).mkdir(parents=True);raise RuntimeError('Synthetic interrupted persistence')
        with patch.object(smn_models.subscription_writer,'prepare_job',side_effect=partial):
            with self.assertRaises(RuntimeError):self.prepare(self.other,'partial-review')
        self.assertEqual(budget.jobs_used(self.normal,DATE),60)
        with self.assertRaises(budget.BudgetEvidenceError):self.prepare(self.normal,'extra-review')
        with self.assertRaises(budget.BudgetEvidenceError):self.prepare(self.other,'partial-review')
        self.assertEqual(smn_daily.Day(self.other,DATE,roles={}).jobs_used(),60)
    def test_matching_orphan_at60_is_refused_without_job_or_reservation_effect(self):
        self.pending(self.normal,59)
        registry=budget.scope(self.other,DATE)[2];target=self.other/'jobs/reserved-review'
        save(registry/'contract.json',{'immutable':'SYNTHETIC recovery contract'})
        record=registry/'reservations/reserved-review/reservation.json'
        save(record,{'job_id':'reserved-review','slot_cost':1,'job_path':str(target.resolve())})
        before={p:p.read_bytes() for p in [record,registry/'contract.json']}
        self.assertEqual(budget.jobs_used(self.normal,DATE),60)
        with self.assertRaisesRegex(budget.BudgetEvidenceError,'explicit attributable recovery'):
            self.prepare(self.other,'reserved-review')
        self.assertFalse(target.exists());self.assertEqual(budget.jobs_used(self.normal,DATE),60)
        for p,raw in before.items():self.assertEqual(p.read_bytes(),raw)
    def test_matching_orphan_below_cap_is_also_refused_and_retained(self):
        registry=budget.scope(self.other,DATE)[2];target=self.other/'jobs/unknown-review'
        record=registry/'reservations/unknown-review/reservation.json'
        save(record,{'job_id':'unknown-review','slot_cost':1,'job_path':str(target.resolve())})
        before=record.read_bytes()
        with self.assertRaisesRegex(budget.BudgetEvidenceError,'explicit attributable recovery'):
            self.prepare(self.other,'unknown-review')
        self.assertFalse(target.exists());self.assertEqual(record.read_bytes(),before)
        self.assertEqual(budget.jobs_used(self.normal,DATE),1)
    def test_foreign_orphan_is_never_refunded_or_rebound(self):
        self.pending(self.normal,59);registry=budget.scope(self.normal,DATE)[2]
        save(registry/'reservations/foreign/reservation.json',{'job_id':'foreign','slot_cost':1,'job_path':str(self.other/'jobs/foreign')})
        self.assertEqual(budget.jobs_used(self.normal,DATE),60)
        with self.assertRaises(budget.BudgetEvidenceError):self.prepare(self.normal,'unrelated-review')
        self.assertTrue((registry/'reservations/foreign/reservation.json').exists())
    def test_exact_completed_receipt_copy_dedup_but_distinct_receipts_count_twice(self):
        first=self.complete(self.normal,'same-job','one');second=self.other/'jobs/same-job';second.mkdir(parents=True)
        for name in ['job.json','receipt.json']:(second/name).write_bytes((first/name).read_bytes())
        self.assertEqual(budget.jobs_used(self.normal,DATE),1)
        self.complete(self.other,'same-job','two')
        self.assertEqual(budget.jobs_used(self.normal,DATE),2)
    def test_copied_receipt_conflicting_dispatch_evidence_fails_closed(self):
        first=self.complete(self.normal,'same-job','one');second=self.other/'jobs/same-job';second.mkdir(parents=True)
        for name in ['job.json','receipt.json']:(second/name).write_bytes((first/name).read_bytes())
        save(first/'execution.json',{'dispatch':'one'});save(second/'execution.json',{'dispatch':'different'})
        with self.assertRaises(budget.BudgetEvidenceError):self.prepare(self.normal,'new-review')
    def test_retry_prefixes_across_recovery_namespaces_remain_counted_at60(self):
        self.pending(self.normal,57);job=self.normal/'jobs/existing-0'
        for prefix in budget.PREFIXES:(job/(prefix+'1')).mkdir()
        self.assertEqual(budget.jobs_used(self.other,DATE),60)
        with self.assertRaises(budget.BudgetEvidenceError):self.prepare(self.other,'new-review')
        self.assertEqual(sum(p.is_dir() for p in job.iterdir()),3)
    def test_prepared_job_at60_stays_dispatchable_under_existing_day_guard(self):
        self.pending(self.other,59);job=self.prepare(self.normal,'ready-review')
        with patch.object(smn_models,'run',return_value={'synthetic_dispatch':True}) as run:
            self.assertEqual(smn_daily.Day(self.normal,DATE,roles={}).run_job(job),{'synthetic_dispatch':True})
        run.assert_called_once();self.assertEqual(budget.jobs_used(self.normal,DATE),60)
    def test_explicit_configured_root_includes_separate_date_namespaces(self):
        second=self.pool/'recovery'/DATE/'reader';second.mkdir(parents=True)
        self.pending(second,59)
        with patch.dict(os.environ,{budget.SCAN_ROOT_ENV:str(self.pool)}):
            self.assertEqual(budget.jobs_used(self.normal,DATE),59)
            self.prepare(self.normal,'last-review')
            with self.assertRaises(budget.BudgetEvidenceError):self.prepare(second,'extra-review')
            self.assertEqual(budget.scope(self.normal,DATE)[2],self.pool/'daily-budget60'/DATE)
        # Unconfigured local fixture is strictly scoped to its nearest DATE.
        self.assertEqual(budget.jobs_used(self.normal,DATE),1)
    def test_foreign_dates_and_workspace_siblings_are_not_scanned(self):
        self.pending(self.pool/'2026-10-08'/'chatgpt',61)
        self.pending(self.pool/'unrelated-workspace',61)
        self.assertEqual(budget.jobs_used(self.normal,DATE),0)
        with patch.dict(os.environ,{budget.SCAN_ROOT_ENV:str(self.pool)}):
            self.assertEqual(budget.jobs_used(self.normal,DATE),0)
    def test_live_or_interrupted_recovery_lease_is_never_stolen(self):
        registry=budget.scope(self.normal,DATE)[2];lock=registry/'.reservation-lease';lock.mkdir(parents=True)
        with self.assertRaisesRegex(budget.BudgetEvidenceError,'lease'):self.prepare(self.normal,'new-review')
        self.assertTrue(lock.is_dir());self.assertFalse((self.normal/'jobs/new-review').exists())
    def test_invalid_receipt_reservation_and_symlink_custody_fail_closed(self):
        job=self.complete(self.normal,'bad-job','one');save(job/'receipt.json',{'job_id':'other'})
        with self.assertRaises(budget.BudgetEvidenceError):self.prepare(self.other,'new-review')
        (job/'receipt.json').write_bytes(b'{not-json')
        with self.assertRaises(budget.BudgetEvidenceError):self.prepare(self.other,'new-review')
        # Symlink rejection is also asserted without needing Windows privilege.
        original=Path.is_symlink
        with patch.object(Path,'is_symlink',lambda p:p==self.date or original(p)):
            with self.assertRaises(budget.BudgetEvidenceError):budget.jobs_used(self.normal,DATE)
    def test_global_failed60th_stops_archive_and_preserves_resume_evidence(self):
        self.pending(self.other,59);job=self.normal/'jobs/failed-last';job.mkdir(parents=True)
        save(job/'state.json',{'status':'failed_needs_review','reason':'503 overloaded'})
        day=smn_daily.Day(self.normal,DATE,roles={})
        with self.assertRaisesRegex(smn_daily.Hold,'cumulative'):day._archive(job,'503 overloaded','transient-attempt')
        self.assertFalse(list(job.glob('transient-attempt-*')))
        self.assertEqual(budget.jobs_used(self.other,DATE),60)
        prior=(job/'retry-exhausted.json').read_bytes()
        with self.assertRaisesRegex(smn_daily.Hold,'retained terminal'):
            smn_daily.Day(self.normal,DATE,roles={}).run_job(job)
        self.assertEqual((job/'retry-exhausted.json').read_bytes(),prior)
    def test_invalid_identity_and_outside_configured_root_fail_before_prepare(self):
        with self.assertRaises(budget.BudgetEvidenceError):self.prepare(self.normal,'../escape')
        with patch.dict(os.environ,{budget.SCAN_ROOT_ENV:str(self.pool/'other-root')}):
            with self.assertRaises(budget.BudgetEvidenceError):self.prepare(self.normal,'new-review')
        self.assertFalse((self.normal/'jobs/new-review').exists())

    def abandoned_normal_owner(self,active=False):
        import normal_budget_lease as ownership
        registry=budget.scope(self.normal,DATE)[2];registry.mkdir(parents=True,exist_ok=True)
        with ownership.kernel_lock(registry) as kernel:
            lock=registry/'.reservation-lease';lock.mkdir()
            proof=dict(ownership.process_identity(os.getpid()))
            if not active:
                if proof['platform']=='linux':proof['start_ticks']='0'
                else:proof['creation_filetime']=1
            owner={'kind':'smn-normal-budget-lease-v1','token':'a'*32,'pid':os.getpid(),
                'process_identity':proof,'registry':str(registry),'directory_identity':ownership.identity(lock),'kernel_identity':kernel}
            save(lock/'normal-owner.json',owner)
        return registry,lock
    def test_recognized_dead_normal_lease_retained_then_preparation_resumes(self):
        registry,lock=self.abandoned_normal_owner()
        self.prepare(self.normal,'after-interruption')
        self.assertTrue((registry/'normal-lease-history'/('a'*32+'-interrupted')/'normal-owner.json').exists())
        self.assertFalse(lock.exists());self.assertEqual(budget.jobs_used(self.normal,DATE),1)
    def test_active_normal_owner_even_without_kernel_lock_is_not_retired(self):
        registry,lock=self.abandoned_normal_owner(active=True)
        with self.assertRaisesRegex(budget.BudgetEvidenceError,'Active normal'):self.prepare(self.normal,'new-review')
        self.assertTrue(lock.exists());self.assertFalse((registry/'normal-lease-history').exists())
    def test_foreign_normal_kernel_identity_is_not_retired(self):
        registry,lock=self.abandoned_normal_owner();p=lock/'normal-owner.json'
        owner=json.loads(p.read_text(encoding='utf-8'));owner['kernel_identity']['st_ino']+=1;save(p,owner)
        with self.assertRaises(budget.BudgetEvidenceError):self.prepare(self.normal,'new-review')
        self.assertTrue(lock.exists());self.assertFalse((registry/'normal-lease-history').exists())
    def test_malformed_normal_owner_is_retained_without_retirement(self):
        registry,lock=self.abandoned_normal_owner();save(lock/'normal-owner.json',[])
        with self.assertRaises(budget.BudgetEvidenceError):self.prepare(self.normal,'new-review')
        self.assertTrue(lock.exists());self.assertFalse((registry/'normal-lease-history').exists())
    def test_malformed_process_identity_payload_cannot_authorize_retirement(self):
        registry,lock=self.abandoned_normal_owner();p=lock/'normal-owner.json'
        owner=json.loads(p.read_text(encoding='utf-8'));original=json.loads(json.dumps(owner))
        key='start_ticks' if owner['process_identity']['platform']=='linux' else 'creation_filetime'
        for bad in (None,{},True,-1):
            owner=json.loads(json.dumps(original));owner['process_identity'][key]=bad;save(p,owner)
            with self.subTest(payload=bad),self.assertRaises(budget.BudgetEvidenceError):self.prepare(self.normal,'new-review')
            self.assertTrue(lock.exists());self.assertFalse((registry/'normal-lease-history').exists())
        for bad_pid in (0,-1,True):
            owner=json.loads(json.dumps(original));owner['pid']=bad_pid;save(p,owner)
            with self.subTest(pid=bad_pid),self.assertRaises(budget.BudgetEvidenceError):self.prepare(self.normal,'new-review')
        if key=='start_ticks':
            owner=json.loads(json.dumps(original));owner['process_identity']['boot_id']=None;save(p,owner)
            with self.assertRaises(budget.BudgetEvidenceError):self.prepare(self.normal,'new-review')
    def test_real_local_kernel_lock_excludes_second_normal_owner(self):
        import normal_budget_lease as ownership
        registry=budget.scope(self.normal,DATE)[2];registry.mkdir(parents=True,exist_ok=True)
        with ownership.kernel_lock(registry):
            with self.assertRaisesRegex(budget.BudgetEvidenceError,'kernel lock'):
                with ownership.kernel_lock(registry):self.fail('Second native kernel owner admitted')
    def test_unreadable_receipt_fails_closed_before_preparation(self):
        job=self.complete(self.normal,'unreadable','one');original=Path.read_bytes
        def unreadable(p):
            if p==job/'receipt.json':raise PermissionError('Synthetic unreadable evidence')
            return original(p)
        with patch.object(Path,'read_bytes',unreadable):
            with self.assertRaises(budget.BudgetEvidenceError):self.prepare(self.other,'new-review')
        self.assertFalse((self.other/'jobs/new-review').exists())

if __name__=='__main__':unittest.main(verbosity=2)
