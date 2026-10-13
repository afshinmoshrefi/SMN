"""Qualified artifacts outrank old failed attempts, with no fabricated visual pass."""
from datetime import datetime,timezone
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
import smn_daily,smn_recovery as recovery,smn_subscription_daily as controller
from subscription_writer import save_json,load_json

DAY='2026-10-13';NOW=datetime(2026,10,13,13,tzinfo=timezone.utc)
SYMBOLS=['AAA','BBB','CCC','DDD','EEE','FFF']

class QualifiedStateTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.edition=self.root/DAY/'chatgpt'
        self.day=smn_daily.Day(self.edition,DAY,roles={},profile='chatgpt',publication_origin=None)
        self.day.continuity=True;self.day.symbols=SYMBOLS
        self.hold={'utc':'2026-10-13T12:00:00Z','reason':'old research attempt did not complete'}
        self.day.state['articles']={s:{'finalized':False,'held':dict(self.hold)} for s in SYMBOLS}
        self.day.save()
    def proof(self,count=5):
        return {'complete':count==6,'verified_symbols':SYMBOLS[:count],
                'qualified_hashes':{s:{'article':'a'*64,'review':'b'*64,'html':'c'*64,'fallback':'d'*64} for s in SYMBOLS[:count]}}
    def test_five_qualified_one_pending_and_original_hold_preserved(self):
        with patch.object(recovery,'verify_generation',return_value=self.proof()),patch.object(self.day,'jobs_used',return_value=60):
            result=self.day.check()
        self.assertEqual(result['passed_count'],5);self.assertEqual(result['held'],['FFF'])
        for s in SYMBOLS[:5]:
            row=self.day.state['articles'][s]
            self.assertNotIn('held',row);self.assertFalse(row['finalized'])
            self.assertEqual(row['retained_holds'][0]['hold'],self.hold)
            self.assertEqual(result['articles'][s]['basis'],'current_source_bound_continuity_artifacts')
    def test_restart_and_repeat_do_not_duplicate_hold_history_or_change_state(self):
        with patch.object(recovery,'verify_generation',return_value=self.proof()):
            recovery.reconcile_qualified_articles(self.day);saved=self.day.state_path.read_bytes()
            resumed=smn_daily.Day(self.edition,DAY,roles={},profile='chatgpt',publication_origin=None)
            resumed.continuity=True
            recovery.reconcile_qualified_articles(resumed)
            self.assertEqual(resumed.state_path.read_bytes(),saved)
    def test_changed_or_incomplete_proof_cannot_reuse_stale_qualification(self):
        with patch.object(recovery,'verify_generation',return_value=self.proof()):recovery.reconcile_qualified_articles(self.day)
        with patch.object(recovery,'verify_generation',return_value={'verified_symbols':['AAA'],'qualified_hashes':{'AAA':{'article':'a'*64}}}):
            self.assertEqual(recovery.reconcile_qualified_articles(self.day),set())
        self.assertFalse(any(row.get('continuity_qualification') for row in self.day.state['articles'].values()))
    def test_supervisor_preserves_old_jobs_of_qualified_article_without_requeue(self):
        job=self.edition/'jobs/AAA-20261013-research';save_json(job/'job.json',{'stage':'research'})
        save_json(job/'state.json',{'status':'failed_needs_review','reason':'old failed attempt'})
        before=(job/'state.json').read_bytes()
        state=load_json(self.day.state_path);state['roles']=smn_daily.smn_models.load(None,'chatgpt');save_json(self.day.state_path,state)
        from contextlib import contextmanager
        @contextmanager
        def locked(*_):yield True
        with patch('production_continuity_schedule._lock',locked),patch.object(recovery,'watch_workers',return_value={}), \
                patch.object(recovery,'verify_generation',return_value=self.proof()),patch.object(recovery,'reconcile_private_job') as reconcile, \
                patch.object(recovery,'repair_held_articles',return_value=[]):
            result=recovery.supervise(self.root,DAY,NOW)
        reconcile.assert_not_called();self.assertEqual((job/'state.json').read_bytes(),before)
        self.assertEqual(result['jobs'][0]['status'],'retained_for_qualified_article')
        self.assertNotIn('held',load_json(self.day.state_path)['articles']['AAA'])

    def test_controller_skips_all_model_stages_for_verified_five_on_restart(self):
        save_json(self.edition/'production/posts.json',[{'symbol':s} for s in SYMBOLS])
        calls=[]
        class ExistingStages(smn_daily.Day):
            def __init__(inner,root,date,**kwargs):
                super().__init__(root,date,roles={},profile='chatgpt',publication_origin=kwargs.get('publication_origin'))
            def release_transient_holds(inner):pass
            def research(inner):calls.append(inner.symbols[0]);raise RuntimeError('Pending subject remains held')
            def articles(inner):raise AssertionError('No qualified article should be rewritten')
            def visual(inner):raise AssertionError('No qualified article should spend a visual job')
        state=load_json(self.day.state_path);state['publication_origin']=controller.ORIGIN;save_json(self.day.state_path,state)
        with patch.object(controller,'Day',ExistingStages),patch.object(controller,'authenticate'), \
                patch.object(controller,'freeze_inputs'),patch.object(controller,'release_login_holds'), \
                patch.object(recovery,'verify_generation',return_value=self.proof()), \
                patch.object(recovery,'checkpoint'),patch('smn_models.run') as model, \
                patch.object(smn_daily.Day,'jobs_used',return_value=60):
            first=controller.run_profile(self.edition,DAY,'chatgpt',self.root/'inputs',continuity=True)
            second=controller.run_profile(self.edition,DAY,'chatgpt',self.root/'inputs',continuity=True)
        self.assertEqual(first['passed_count'],5);self.assertEqual(second['held'],['FFF'])
        self.assertEqual(calls,['FFF','FFF']);model.assert_not_called()

    def test_old_failed_job_does_not_replace_pending_current_factual_hold(self):
        latest={'utc':'2026-10-13T13:00:00Z','reason':'Current essential source quote does not match'}
        state=load_json(self.day.state_path);state['roles']=smn_daily.smn_models.load(None,'chatgpt')
        state['articles']['FFF']['held']=latest;save_json(self.day.state_path,state)
        job=self.edition/'jobs/FFF-20261013-research';save_json(job/'job.json',{'stage':'research'})
        save_json(job/'state.json',{'status':'failed_needs_review','reason':'Old private turn failed'})
        from contextlib import contextmanager
        @contextmanager
        def locked(*_):yield True
        with patch('production_continuity_schedule._lock',locked),patch.object(recovery,'watch_workers',return_value={}), \
                patch.object(recovery,'verify_generation',return_value=self.proof()), \
                patch.object(recovery,'reconcile_private_job',return_value={'status':'failed_private_turn'}), \
                patch.object(recovery,'repair_held_articles',return_value=[]):
            recovery.supervise(self.root,DAY,NOW)
        self.assertEqual(load_json(self.day.state_path)['articles']['FFF']['held'],latest)

if __name__=='__main__':unittest.main()
