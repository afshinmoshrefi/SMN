"""Controller routes require current approvals and bound the correction loop."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import smn_daily
from subscription_writer import save_json, sha256


class CompletionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.day=smn_daily.Day(tmp.name,'2026-09-30');self.day.symbols=['AMD']
        self.day.state['articles']={'AMD':{}}
        self.ed=MagicMock()
        self.ed.result.side_effect=lambda sym:Path(tmp.name)/'results'/sym
        self.ed.job.side_effect=lambda sym,stage:Path(tmp.name)/'jobs'/(sym+'-'+stage)
        self.ed.receive.return_value={'passed':True}
        def run(job):
            job.mkdir(parents=True,exist_ok=True)
            save_json(job/'output.json',{'passed':True})
        self.day.run_job=run

    def test_factual_failure_repaired_once_and_fresh_review_required(self):
        with patch('smn_daily.editorial_review_problems',side_effect=[['minor factual error'],[]]) as review:
            self.day._article(self.ed,'AMD')
        self.assertEqual(review.call_count,2)
        self.ed.repair.assert_called_once()
        self.assertEqual(self.ed.repair.call_args.args[2],'repair-two')
        self.ed.finalize.assert_called_once_with('AMD','rereview')
        state=self.day.state['articles']['AMD']
        self.assertTrue(state['editorially_finalized'])
        self.assertFalse(state['finalized'])  # Still needs screenshots and completion gate.

    def test_failed_second_review_holds_without_finalizing(self):
        with patch('smn_daily.editorial_review_problems',return_value=['unverified cause']):
            with self.assertRaisesRegex(smn_daily.Hold,'failed review twice'):
                self.day._article(self.ed,'AMD')
        self.ed.repair.assert_called_once();self.ed.finalize.assert_not_called()
        self.assertFalse(self.day.state['articles']['AMD'].get('finalized',False))

    def test_resume_and_daily_check_reject_stale_finalized_flag(self):
        self.day.state['articles']['AMD']={'finalized':True}
        with patch('editorial_gate.verify_complete',side_effect=ValueError('stale review')):
            with self.assertRaisesRegex(ValueError,'stale review'):
                self.day._article(self.ed,'AMD')
            result=self.day.check()
        self.assertFalse(result['passed']);self.assertEqual(result['held'],['AMD'])
        self.assertFalse(self.day.state['articles']['AMD']['finalized'])
        self.ed.prepare.assert_not_called()

    def test_crash_after_repair_receipt_resumes_fresh_review(self):
        def receive(sym,stage):
            if stage=='repair-two':raise RuntimeError('interrupted after replacing draft')
            return {'passed':True}
        self.ed.receive.side_effect=receive
        with patch('smn_daily.editorial_review_problems',return_value=['factual error']):
            with self.assertRaisesRegex(RuntimeError,'interrupted'):
                self.day._article(self.ed,'AMD')
        state=self.day.state['articles']['AMD']
        self.assertEqual(state['draft'],'repair-two')
        self.assertEqual(state['review_stage'],'rereview')
        self.assertFalse(state['mechanical_ok'])
        self.ed.receive.side_effect=None
        with patch('smn_daily.editorial_review_problems',return_value=[]):
            self.day._article(self.ed,'AMD')
        self.ed.repair.assert_called_once()
        self.ed.finalize.assert_called_once_with('AMD','rereview')

    def test_invalid_primary_capture_holds_before_spending_research_turn(self):
        with patch('smn_daily.smn_primary_sources.collect'), \
             patch('editorial_gate.primary_sources',side_effect=ValueError('primary bytes changed')), \
             patch.object(self.day,'_research_job') as model:
            with self.assertRaisesRegex(smn_daily.Hold,'research failed'):
                self.day.research()
        model.assert_not_called()
        self.assertIn('primary bytes changed',self.day.state['articles']['AMD']['held']['reason'])

    def test_held_flag_never_counts_complete(self):
        self.day.state['articles']['AMD']={'finalized':True,'held':{'reason':'missing source'}}
        result=self.day.check()
        self.assertEqual(result['passed_count'],0)
        self.assertFalse(self.day.state['articles']['AMD']['finalized'])

    def test_manual_finalize_requires_review_gate_before_writing(self):
        from engine_edition_workflow import Edition
        out=self.ed.result('AMD');out.mkdir(parents=True)
        for filename in ('bundle.json','article.json'):save_json(out/filename,{})
        job=self.ed.job('AMD','review');job.mkdir(parents=True)
        save_json(job/'output.json',{'passed':True})
        with patch('editorial_gate.verify_review',side_effect=ValueError('minor factual correction')):
            with self.assertRaisesRegex(ValueError,'minor factual correction'):
                Edition.finalize(self.ed,'AMD','review')
        self.assertFalse((out/'review-binding.json').exists())

    def failed_mechanical(self):
        return {'passed':False,'structure':{'passed':True},'source_words':{'issuer':{
            'passed':False,'total':201,'prose':171,'chart':20,'headings_and_citation':10,'maximum':200}}}

    def test_editorial_repair_over_cap_gets_one_correction_and_fresh_review(self):
        self.ed.receive.side_effect=lambda sym,stage:self.failed_mechanical() if stage=='repair-two' else {'passed':True}
        with patch('smn_daily.editorial_review_problems',side_effect=[['unsupported cause'],[]]) as review:
            self.day._article(self.ed,'AMD')
        self.assertEqual([call.args[2] for call in self.ed.repair.call_args_list],['repair-two','repair-three'])
        self.assertEqual(review.call_count,2)
        self.ed.finalize.assert_called_once_with('AMD','rereview')
        self.assertEqual(self.day.state['articles']['AMD']['draft'],'repair-three')

    def test_editorial_mechanical_correction_crash_resumes_without_extra_attempt(self):
        def receive(sym,stage):
            if stage=='repair-two':return self.failed_mechanical()
            if stage=='repair-three':raise RuntimeError('crash replacing mechanical correction')
            return {'passed':True}
        self.ed.receive.side_effect=receive
        with patch('smn_daily.editorial_review_problems',return_value=['unsupported cause']):
            with self.assertRaisesRegex(RuntimeError,'crash replacing'):self.day._article(self.ed,'AMD')
        self.assertEqual(self.day.state['articles']['AMD']['draft'],'repair-three')
        self.assertFalse(self.day.state['articles']['AMD']['mechanical_ok'])
        run=self.day.run_job
        self.day=smn_daily.Day(self.day.root,self.day.date)
        self.day.run_job=run
        self.ed.receive.side_effect=None
        with patch('smn_daily.editorial_review_problems',return_value=[]):self.day._article(self.ed,'AMD')
        self.assertEqual([call.args[2] for call in self.ed.repair.call_args_list],['repair-two','repair-three'])
        self.ed.finalize.assert_called_once_with('AMD','rereview')

    def test_continued_mechanical_failure_holds_without_review_or_fourth_repair(self):
        self.ed.receive.side_effect=lambda sym,stage:{'passed':True} if stage=='write' else self.failed_mechanical()
        with patch('smn_daily.editorial_review_problems',return_value=['unsupported cause']) as review:
            with self.assertRaisesRegex(smn_daily.Hold,'still fail after one correction'):self.day._article(self.ed,'AMD')
        self.assertEqual(review.call_count,1)
        self.assertEqual([call.args[2] for call in self.ed.repair.call_args_list],['repair-two','repair-three'])
        self.ed.finalize.assert_not_called()

    def test_extra_mechanical_repair_respects_job_budget(self):
        self.day.max_jobs=0;self.day.state['articles']['AMD']={'draft':'repair-two','review_stage':'rereview'}
        self.ed.receive.return_value=self.failed_mechanical()
        with self.assertRaisesRegex(smn_daily.Hold,'budget'):self.day._article(self.ed,'AMD')
        self.ed.repair.assert_not_called();self.ed.finalize.assert_not_called()

    def test_explicit_second_review_recovery_preserves_approved_article_and_requires_new_review(self):
        root=self.day.root;self.day.state['articles']['AMD']={
            'draft':'repair-two','review_stage':'rereview','mechanical_ok':True,'finalized':False,
            'held':{'reason':'AMD failed review twice: missing material forecast'}}
        other=root/'results'/ 'SPY';other.mkdir(parents=True)
        for name in ('article.html','review-binding.json','visual-checks.json','completion-check.json'):
            (other/name).write_text(name)
        self.day.state['articles']['SPY']={'finalized':True}
        job=root/'jobs'/'AMD-20260930-rereview';job.mkdir(parents=True)
        save_json(job/'output.json',{'passed':False,'checks':{'reader_value':{'passed':False,'reason':'missing forecast'}},'issues':[]})
        save_json(job/'receipt.json',{'output_sha256':sha256((job/'output.json').read_bytes())})
        with patch('editorial_gate.primary_sources'),patch('engine_edition_workflow.Edition') as edition:
            ledger=self.day.prepare_editorial_recovery('AMD')
        edition.return_value.repair.assert_called_once()
        self.assertTrue(ledger.exists())
        self.assertEqual(self.day.state['articles']['AMD']['review_stage'],'third-review')
        self.assertNotIn('held',self.day.state['articles']['AMD'])
        self.day.verify_editorial_recovery()
        (other/'article.html').write_text('changed')
        with self.assertRaisesRegex(smn_daily.Hold,'Approved article changed'):
            self.day.verify_editorial_recovery()
        with self.assertRaisesRegex(smn_daily.Hold,'already prepared'):
            self.day.prepare_editorial_recovery('AMD')

    def test_prepared_third_repair_gets_fresh_review_and_no_fourth_retry(self):
        self.day.state['articles']['AMD']={'draft':'repair-review-three','review_stage':'third-review',
                                            'mechanical_ok':False,'finalized':False}
        with patch('smn_daily.editorial_review_problems',return_value=['missing guidance']):
            with self.assertRaisesRegex(smn_daily.Hold,'editorial recovery review'):
                self.day._article(self.ed,'AMD')
        self.ed.receive.assert_called_once_with('AMD','repair-review-three')
        self.ed.repair.assert_not_called()
        self.ed.finalize.assert_not_called()


if __name__=='__main__':unittest.main()
