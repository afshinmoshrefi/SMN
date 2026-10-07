"""Failure injection for actual recovery, with all providers/side effects mocked."""
from datetime import datetime,timedelta,timezone
import hashlib
import io
import json
from pathlib import Path
import socket
import tempfile
import types
import unittest
from unittest.mock import Mock,patch

import smn_recovery as recovery
import smn_daily
import smn_subscription_daily as controller
import production_continuity_schedule as schedule
import subscription_inputs as inputs
import subscription_writer as writer
import smn_visual
import smn_operational_alerts as alerts
from subscription_writer import save_json,load_json,sha256

NOW=datetime.now(timezone.utc)
DAY=NOW.date().isoformat()


class PrivateCompletion(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.job=writer.prepare_job(self.root/'jobs','ALB-'+DAY.replace('-','')+'-write',
            'Use only prepared evidence',{'type':'object','required':['text'],'properties':{'text':{'type':'string'}},'additionalProperties':False},
            as_of=DAY,valid_until=(NOW+timedelta(hours=1)).isoformat(),evidence_sha256='a'*64)
        self.manifest=load_json(self.job/'job.json')
        save_json(self.job/'output.json',{'text':'Completed private draft'})
        save_json(self.job/'usage-before.json',{'utc':(NOW-timedelta(seconds=5)).isoformat(),'auth_type':'chatgpt',
            'probe_generated_model_turns':0,'plan_type':'pro','model_catalog':[{'model':'gpt-6-astra',
            'supportedReasoningEfforts':[{'reasoningEffort':'xhigh'}]}]})
        self.argv=['codex','exec','--ephemeral','--sandbox','read-only','--model','gpt-6-astra',
                   '-c','model_reasoning_effort="xhigh"']
        save_json(self.job/'invocation.json',{'argv':self.argv,'timeout_seconds':900})
        (self.job/'events.jsonl').write_text(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':2}})+'\n')
        self.execution={'job_id':self.job.name,'job_sha256':sha256((self.job/'job.json').read_bytes()),
            'returncode':0,'finished_utc':NOW.isoformat(),'seconds':5,
            'output_sha256':sha256((self.job/'output.json').read_bytes()),
            'events_sha256':sha256((self.job/'events.jsonl').read_bytes()),
            'invocation_sha256':sha256((self.job/'invocation.json').read_bytes()),
            'input_hashes':self.manifest['input_hashes'],'evidence_sha256':self.manifest['evidence_sha256']}
        save_json(self.job/'execution.json',self.execution)
        save_json(self.job/'state.json',{'status':'failed_needs_review','reason':'account retrieval temporarily failed after completion'})

    def claim(self,pid=99999999,host=None,**extra):
        save_json(self.job/'.claim/owner.json',{'pid':pid,'host':host or socket.gethostname(),
                   'utc':(NOW-timedelta(minutes=20)).isoformat(),**extra})

    def test_post_turn_account_failure_recovers_real_checkpoint_without_model_or_auth_call(self):
        with patch.object(writer,'account_snapshot',side_effect=AssertionError('account call')),patch('smn_models.run',side_effect=AssertionError('model call')):
            result=recovery.reconcile_private_job(self.job,NOW)
        receipt=load_json(self.job/'receipt.json')
        self.assertEqual(result['status'],'verified_completed')
        self.assertEqual(receipt['status'],'output_ready_for_smn_validation')
        self.assertFalse(receipt['usage_after_available']);self.assertFalse(receipt['publish'])
        self.assertEqual(recovery.attempts(self.job),1)
        self.assertEqual(recovery.reconcile_private_job(self.job,NOW)['status'],'verified_completed')

    def test_private_timeout_checkpoints_actual_failed_exit_before_retry(self):
        (self.job/'execution.json').unlink();save_json(self.job/'state.json',{'status':'ready'})
        before=load_json(self.job/'usage-before.json')
        proc=Mock(pid=99999999,returncode=-9)
        proc.communicate.side_effect=writer.subprocess.TimeoutExpired('private mocked CLI',900)
        with patch.object(writer,'account_snapshot',return_value=before),patch.object(writer,'codex_defaults',return_value=[]),patch.object(writer.subprocess,'Popen',return_value=proc),patch.object(recovery,'process_identity',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'timed out'):writer.run_job(self.job,Path('mock-codex'))
        proc.kill.assert_called_once();proc.wait.assert_called_once()
        self.assertEqual(load_json(self.job/'execution.json')['returncode'],-9)
        self.assertFalse((self.job/'receipt.json').exists());self.assertFalse((self.job/'.claim').exists())
        observed=recovery.reconcile_private_job(self.job,NOW)
        self.assertEqual(observed['status'],'failed_private_turn');self.assertTrue(observed['retry_allowed'])

    def test_completed_receipt_reused_after_assignment_expiry_without_new_turn(self):
        self.manifest['valid_until']=(NOW-timedelta(seconds=1)).isoformat();save_json(self.job/'job.json',self.manifest)
        self.execution.update(job_sha256=sha256((self.job/'job.json').read_bytes()),finished_utc=(NOW-timedelta(seconds=2)).isoformat())
        save_json(self.job/'execution.json',self.execution)
        self.assertEqual(recovery.reconcile_private_job(self.job,NOW)['status'],'verified_completed')

    def test_changed_checkpoint_output_input_model_or_invocation_never_creates_receipt(self):
        for name in ('output.json','prompt.txt','events.jsonl','invocation.json','job.json'):
            path=self.job/name;before=path.read_bytes();path.write_bytes(before+b' ')
            with self.assertRaises(ValueError,msg=name):recovery.reconcile_private_job(self.job,NOW)
            self.assertFalse((self.job/'receipt.json').exists());path.write_bytes(before)

    def test_duplicate_completion_or_unexpected_tool_is_security_hold(self):
        for events in ([{'type':'turn.completed'},{'type':'turn.completed'}],
                       [{'type':'turn.completed'},{'type':'item.completed','item':{'type':'command_execution'}}]):
            (self.job/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
            self.execution['events_sha256']=sha256((self.job/'events.jsonl').read_bytes());save_json(self.job/'execution.json',self.execution)
            with self.assertRaises(ValueError):recovery.reconcile_private_job(self.job,NOW)
            self.assertFalse((self.job/'receipt.json').exists())

    def test_completion_after_expiry_is_not_faked(self):
        self.execution['finished_utc']=(NOW+timedelta(hours=2)).isoformat();save_json(self.job/'execution.json',self.execution)
        with self.assertRaisesRegex(ValueError,'timestamp'):recovery.reconcile_private_job(self.job,NOW)

    def test_dead_claim_and_verified_completion_recover_without_retry(self):
        self.claim()
        with patch.object(recovery,'alive',return_value=False):result=recovery.reconcile_private_job(self.job,NOW)
        self.assertEqual(result['status'],'verified_completed');self.assertFalse((self.job/'.claim').exists())
        self.assertEqual(len(list(self.job.glob('retained-dead-claim-*'))),1)

    def test_dead_parent_without_completion_checkpoint_preserves_uncertainty(self):
        self.claim();(self.job/'execution.json').unlink()
        with patch.object(recovery,'alive',return_value=False):result=recovery.reconcile_private_job(self.job,NOW)
        self.assertEqual(result['status'],'uncertain_completion');self.assertFalse(result['retry_allowed'])
        self.assertTrue((self.job/'.claim').exists());self.assertFalse((self.job/'receipt.json').exists())

    def test_live_foreign_and_unrecognized_claims_are_not_retired(self):
        self.claim(pid=123)
        with patch.object(recovery,'alive',return_value=True),patch.object(recovery.os,'kill') as kill:
            self.assertEqual(recovery.reconcile_private_job(self.job,NOW)['status'],'running_or_uncertain');kill.assert_not_called()
        owner=load_json(self.job/'.claim/owner.json');owner['host']='another host';save_json(self.job/'.claim/owner.json',owner)
        self.assertEqual(recovery.reconcile_private_job(self.job,NOW)['claim_status'],'foreign')
        (self.job/'.claim/peer.txt').write_text('Retain peer evidence')
        self.assertEqual(recovery.reconcile_private_job(self.job,NOW)['claim_status'],'uncertain')

    def test_only_exact_overdue_private_child_is_signaled_and_receipt_is_not_faked(self):
        identity={'pid':777,'start_ticks':'42','cwd':str(self.job),'argv_sha256':sha256(b'\0'.join(a.encode() for a in self.argv)+b'\0')}
        self.claim(child_pid=777,child_identity=identity,invocation_sha256=sha256((self.job/'invocation.json').read_bytes()))
        running={777:True,99999999:False}
        def terminate(pid,_signal):running[pid]=False
        with patch.object(recovery,'alive',side_effect=lambda pid:running.get(pid)),patch.object(recovery,'process_identity',return_value=identity),patch.object(recovery.os,'kill',side_effect=terminate) as kill:
            self.assertTrue(recovery.terminate_stalled_private_child(self.job,NOW));kill.assert_called_once()
        self.assertTrue((self.job/'managed-termination.json').is_file());self.assertFalse((self.job/'receipt.json').exists())

    def test_reused_pid_or_unavailable_birth_identity_is_never_signaled(self):
        identity={'pid':777,'start_ticks':'42','cwd':str(self.job),'argv_sha256':sha256(b'\0'.join(a.encode() for a in self.argv)+b'\0')}
        self.claim(child_pid=777,child_identity=identity,invocation_sha256=sha256((self.job/'invocation.json').read_bytes()))
        for observed in (None,{**identity,'start_ticks':'43'}):
            with patch.object(recovery,'alive',return_value=True),patch.object(recovery,'process_identity',return_value=observed),patch.object(recovery.os,'kill') as kill:
                self.assertFalse(recovery.terminate_stalled_private_child(self.job,NOW));kill.assert_not_called()


class TargetedRecovery(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)/'day/chatgpt'
        self.day=smn_daily.Day(self.root,DAY,roles={},profile='chatgpt',publication_origin=None)
        self.day.continuity=True
        self.job=self.root/'jobs'/('ALB-'+DAY.replace('-','')+'-write')
        save_json(self.job/'job.json',{'stage':'write','publish':False})
        save_json(self.job/'state.json',{'status':'failed_needs_review','reason':'provider overloaded'})
        self.day.state['articles']={'ALB':{'held':{'reason':'provider overloaded'}},'SPX':{'finalized':True}}
        self.day.save()
        self.good=self.root/'results/SPX/article.html';self.good.parent.mkdir(parents=True);self.good.write_text('Passed evidence is retained')

    def test_transient_repair_requeues_only_failed_stage_preserving_approved_article(self):
        before=self.good.read_bytes();result=recovery.repair_held_articles(self.day,NOW)
        self.assertEqual(result[0]['status'],'targeted_resume_prepared')
        self.assertNotIn('held',self.day.state['articles']['ALB']);self.assertEqual(self.good.read_bytes(),before)
        self.assertEqual(self.day.jobs_used(),2);self.assertEqual(load_json(self.job/'state.json')['status'],'ready')
        self.assertTrue(result[0]['original_hold'])

    def test_per_job_and_edition_budget_exhaustion_preserve_failure(self):
        for n in range(3):(self.job/('transient-attempt-'+str(n))).mkdir()
        result=recovery.repair_held_articles(self.day,NOW)
        self.assertEqual(result[0]['status'],'safe_paths_exhausted');self.assertIn('held',self.day.state['articles']['ALB'])
        self.assertEqual(load_json(self.job/'state.json')['status'],'failed_needs_review')
        self.day.max_jobs=4
        with self.assertRaises(smn_daily.Hold):self.day._archive(self.job,'overloaded','transient-attempt')

    def test_exhausted_transient_subject_does_not_abort_release_of_next_subject(self):
        for n in range(3):(self.job/('transient-attempt-'+str(n))).mkdir()
        self.day.state['articles']['BBB']={'held':{'reason':'infrastructure temporarily unavailable'}}
        self.day.release_transient_holds()
        self.assertIn('held',self.day.state['articles']['ALB'])
        self.assertNotIn('held',self.day.state['articles']['BBB'])
        self.assertEqual(self.good.read_bytes(),b'Passed evidence is retained')

    def test_authorization_and_security_failures_do_not_auto_dispatch(self):
        for reason in ('login required','Prepared source changed','Unexpected tools in model job'):
            self.day.state['articles']['ALB']['held']={'reason':reason}
            save_json(self.job/'state.json',{'status':'failed_needs_review','reason':reason})
            with patch('smn_models.run') as model:result=recovery.repair_held_articles(self.day,NOW)
            self.assertEqual(result[0]['status'],'authorization_or_evidence_required');model.assert_not_called()
            self.assertIn('held',self.day.state['articles']['ALB'])

    def test_backoff_is_durable_and_no_immediate_retry_or_sleep(self):
        save_json(self.job/'state.json',{'status':'ready'})
        def fail(*_):save_json(self.job/'state.json',{'status':'failed_needs_review','reason':'provider overloaded'});raise RuntimeError('provider overloaded')
        with patch('smn_models.run',side_effect=fail) as model,patch.object(smn_daily.time,'sleep') as sleep:
            with self.assertRaisesRegex(smn_daily.Hold,'backoff'):self.day.run_job(self.job)
        model.assert_called_once();sleep.assert_not_called();self.assertEqual(self.day.jobs_used(),2)
        self.assertEqual(recovery.repair_held_articles(self.day,NOW)[0]['status'],'waiting_backoff')
        self.assertIn('held',self.day.state['articles']['ALB'])

    def test_one_factual_repair_uses_existing_source_backed_method_and_keeps_warning(self):
        self.day.state['articles']['ALB']['held']={'reason':'ALB failed review twice: unsupported method'}
        save_json(self.job/'state.json',{'status':'ready'})
        with patch.object(self.day,'prepare_editorial_recovery') as repair:
            first=recovery.repair_held_articles(self.day,NOW);second=recovery.repair_held_articles(self.day,NOW)
        repair.assert_called_once_with('ALB',controller_owned=True)
        self.assertEqual(first[0]['status'],'source_backed_repair_prepared');self.assertEqual(second[0]['status'],'safe_paths_exhausted')
        self.assertIn('unsupported',first[0]['original_hold']['reason'])

    def test_failed_repair_for_one_subject_does_not_stop_next_subject(self):
        self.day.state['articles']['ALB']['held']={'reason':'Unsupported source'}
        self.day.state['articles']['BBB']={'held':{'reason':'local infrastructure failure'}}
        save_json(self.job/'state.json',{'status':'ready'})
        with patch.object(self.day,'prepare_editorial_recovery',side_effect=smn_daily.Hold('Source gate still fails')):
            rows=recovery.repair_held_articles(self.day,NOW)
        self.assertEqual(rows[0]['status'],'safe_paths_exhausted');self.assertEqual(rows[1]['status'],'targeted_resume_prepared')

    def test_stage_checkpoint_cannot_claim_success_without_artifact_bytes(self):
        recovery.checkpoint(self.day,'ALB','research','completed')
        row=load_json(self.root/'stage-checkpoints.json')['stages']['ALB:research']
        self.assertEqual(row['status'],'pending');self.assertEqual(row['artifact_sha256'],{})

    def test_lone_timer_success_is_not_generation_completion(self):
        self.assertFalse(recovery.verify_generation(self.root.parent.parent,DAY)['complete'])

    def test_failed_first_article_yields_immediately_to_independent_subject(self):
        root=self.root/'isolated-controller'
        save_json(root/'production/posts.json',[{'symbol':'ALB'},{'symbol':'SPX'}])
        calls=[]
        class RealStages(smn_daily.Day):
            def __init__(inner,root,date,**kwargs):
                super().__init__(root,date,roles={},profile='chatgpt',publication_origin=kwargs.get('publication_origin'))
                inner.state['articles']={}
            def research(inner):calls.append(('research',inner.symbols[0]))
            def articles(inner):
                symbol=inner.symbols[0];calls.append(('articles',symbol))
                if symbol=='ALB':raise RuntimeError('Infrastructure temporarily unavailable')
                inner.state['articles'][symbol]={'finalized':True};inner.save()
            def visual(inner):calls.append(('visual',inner.symbols[0]))
            def check(inner):return inner.state
        with patch.object(controller,'Day',RealStages),patch.object(controller,'authenticate'),patch.object(controller,'freeze_inputs'),patch.object(controller,'release_login_holds'):
            result=controller.run_profile(root,DAY,'chatgpt',root/'inputs',continuity=True)
        self.assertTrue(result['articles']['SPX']['finalized'])
        self.assertIn('temporarily',result['articles']['ALB']['held']['reason'])
        self.assertEqual(calls,[('research','ALB'),('articles','ALB'),('research','SPX'),('articles','SPX'),('visual','SPX')])

    def test_essential_asset_failure_cannot_be_replaced_by_presentation_success(self):
        self.day.state['articles']['ALB']['held']={'reason':'missing essential chart asset'}
        save_json(self.job/'state.json',{'status':'ready'})
        with patch('presentation_fallback.prepare_fallback',side_effect=ValueError('TradeWave chart/source custody failed')):
            rows=recovery.repair_held_articles(self.day,NOW)
        self.assertEqual(rows[0]['status'],'safe_paths_exhausted')
        self.assertIn('held',self.day.state['articles']['ALB']);self.assertEqual(self.good.read_bytes(),b'Passed evidence is retained')


class IndependentSupervision(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)

    def test_malformed_worker_journal_is_observed_without_disabling_other_ticks(self):
        path=self.root/DAY/'continuity-workers/reconcile.json'
        path.parent.mkdir(parents=True);path.write_text('{truncated')
        result=recovery.watch_workers(self.root,DAY,NOW)
        self.assertEqual(result['conditions'][0]['kind'],'worker_evidence_invalid')
        self.assertFalse(result['completion_inferred_from_timer'])

    def test_repair_backoff_does_not_escalate_before_safe_paths_are_exhausted(self):
        settings={'daily_generation':{'timezone':'UTC','start_time':'05:30','no_start_grace_minutes':15}}
        path=self.root/DAY/'chatgpt/automatic-recovery.json'
        save_json(path,{'articles':{'ALB':{'status':'waiting_backoff'}}})
        self.assertEqual(alerts._recovery_incidents(self.root,NOW,settings,'production'),[])
        save_json(path,{'articles':{'ALB':{'status':'safe_paths_exhausted','failure':{'reason':'budget exhausted'}}}})
        incidents=alerts._recovery_incidents(self.root,NOW,settings,'production')
        self.assertEqual(len(incidents),1);self.assertIn('budget exhausted',incidents[0]['detail'])

    def test_repeated_supervisor_failure_has_an_independent_incident(self):
        settings={'daily_generation':{'timezone':'UTC','start_time':'05:30','no_start_grace_minutes':15}}
        save_json(self.root/DAY/'continuity-supervision.json',{'conditions':[{'phase':'reconcile','kind':'worker_failed'}]})
        worker=self.root/DAY/'continuity-workers/reconcile.json'
        save_json(worker,{'consecutive_failures':2})
        self.assertEqual(alerts._recovery_incidents(self.root,NOW,settings,'production'),[])
        save_json(worker,{'consecutive_failures':3})
        self.assertEqual(len(alerts._recovery_incidents(self.root,NOW,settings,'production')),1)

    def test_other_tick_detects_stalled_and_failed_recovery_worker(self):
        path=self.root/DAY/'continuity-workers/reconcile.json'
        save_json(path,{'phase':'reconcile','status':'running','updated_utc':(NOW-timedelta(minutes=3)).isoformat(),'lease_seconds':90,'pid':99999999})
        with patch.object(recovery,'alive',return_value=False):watch=recovery.watch_workers(self.root,DAY,NOW)
        self.assertEqual(watch['conditions'][0]['phase'],'reconcile');self.assertEqual(watch['conditions'][0]['kind'],'worker_stalled')
        record=load_json(path);record['status']='held';save_json(path,record)
        self.assertEqual(recovery.watch_workers(self.root,DAY,NOW)['conditions'][0]['kind'],'worker_failed')

    def test_worker_finish_records_pending_not_inferred_success(self):
        path=recovery.worker_start(self.root,DAY,'reconcile',NOW)
        recovery.worker_finish(path,{'status':'held','reason':'Recovery failed'},NOW)
        record=load_json(path);self.assertEqual(record['status'],'held')
        self.assertFalse(record['process_return_is_completion_proof'])

    def test_supervisor_busy_preserves_live_controller_and_still_watches(self):
        from contextlib import contextmanager
        @contextmanager
        def busy(*_):yield False
        with patch.object(schedule,'_lock',busy),patch('smn_models.run') as model:
            result=recovery.supervise(self.root,DAY,NOW)
        self.assertEqual(result['status'],'busy');model.assert_not_called()
        self.assertTrue((self.root/DAY/'continuity-supervision.json').is_file())

    def test_mail_get_failure_does_not_disable_recovery_supervision(self):
        mail=types.SimpleNamespace(poll_pending_campaigns=Mock(side_effect=RuntimeError('mail provider unavailable')))
        argv=['schedule','reconcile','--enable-production-continuity','--root',str(self.root),'--date',DAY]
        with patch.object(schedule,'require_production_host'),patch('sys.argv',argv),patch.dict('sys.modules',{'send_smn_emails':mail}),patch('builtins.print'):
            self.assertEqual(schedule.main(),0)
        worker=load_json(self.root/DAY/'continuity-workers/reconcile.json')
        self.assertEqual(worker['result']['mail_reconciliation']['status'],'held')
        self.assertEqual(worker['status'],'waiting_for_reader')

    def test_failure_classifier_and_essential_checks_are_retained(self):
        cases={'rate limit 429':'transient','login required':'authorization','budget exhausted':'budget',
               'missing hero asset':'missing_asset','unsupported metric':'factual','opening style':'editorial',
               'CLI not installed':'infrastructure','source changed':'security'}
        for reason,expected in cases.items():
            row=recovery.classify(reason);self.assertEqual(row['category'],expected);self.assertTrue(row['factual_checks_required'])


class OptionalAssetRecovery(unittest.TestCase):
    def test_neutral_hero_for_missing_assets_has_zero_paid_calls_and_real_raster(self):
        from test_subscription_inputs import package
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);inputs.capture(root,'2026-09-30',lambda _:package())
            with patch.object(inputs,'generate_hero',side_effect=AssertionError('paid API')) as paid:
                first=inputs.prepare_heroes(root,'2026-09-30',nonessential_fallback=True)
                second=inputs.prepare_heroes(root,'2026-09-30',nonessential_fallback=True)
            paid.assert_not_called();self.assertFalse(first['api_cost_stage']);self.assertTrue(second['idempotent'])
            receipt=load_json(root/'input-heroes.json');item=receipt['heroes']['VIX']
            self.assertEqual(item['asset_kind'],'neutral_placeholder');self.assertFalse(item['paid_generation_retried'])
            with Image.open(root/item['path']) as image:self.assertEqual(image.size,(1200,675));self.assertEqual(image.format,'JPEG')

    def test_changed_retained_hero_custody_is_never_silently_replaced(self):
        from test_subscription_inputs import package
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);inputs.capture(root,'2026-09-30',lambda _:package())
            inputs.prepare_heroes(root,'2026-09-30',nonessential_fallback=True)
            receipt=load_json(root/'input-heroes.json');asset=root/receipt['heroes']['VIX']['path'];asset.write_bytes(b'Changed asset')
            with self.assertRaisesRegex(inputs.Held,'changed'):inputs.prepare_heroes(root,'2026-09-30',nonessential_fallback=True)
            self.assertEqual(asset.read_bytes(),b'Changed asset')

    def test_report_only_hero_budget_failure_does_not_hold_an_article(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);out=root/'results/ALB';out.mkdir(parents=True);image=out/'hero.png';image.write_bytes(b'fixture')
            save_json(out/'hero-asset.json',{'path':str(image),'url':'hero.png'})
            save_json(root/'sources.json',{'ALB':{'company':'Albemarle'}})
            with patch.object(smn_visual,'_job',side_effect=smn_daily.Hold('budget exhausted')):
                record=smn_visual.hero(root,DAY,'ALB',{}, {},None)
            self.assertTrue(record['report_only']);self.assertIn('error',record);self.assertNotIn('passed',record)


if __name__=='__main__':unittest.main()
