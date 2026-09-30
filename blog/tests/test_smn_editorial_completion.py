"""Controller routes require current approvals and bound the correction loop."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import smn_daily
from subscription_writer import save_json


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


if __name__=='__main__':unittest.main()
