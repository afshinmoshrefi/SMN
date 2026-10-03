from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import promotion_jobs as jobs
import promotion_worker as worker


class Worker(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.inputs={'article_id':'canonical','source_revision':'r1','source_hash':'a'*64}

    def test_missing_source_review_prevents_provider_call(self):
        job=jobs.create(self.root,'article_video',self.inputs,'editor')
        with patch('promotion_worker.eleven.speech') as speech:
            result=worker.run_one(self.root,job['id'],{'generation_enabled':True,'resolve_source':lambda i:{'prepared':{}}})
            speech.assert_not_called()
        self.assertEqual(result['status'],'held');self.assertEqual(result['attempts'],0)

    def test_pause_prevents_source_access_and_provider_call(self):
        job=jobs.create(self.root,'article_video',self.inputs,'editor');jobs.pause(self.root,'all',True,'editor')
        with patch('promotion_worker.eleven.speech') as speech:
            result=worker.run_one(self.root,job['id'],{'generation_enabled':True,'resolve_source':lambda i:self.fail('resolver called')})
            speech.assert_not_called()
        self.assertEqual(result['status'],'draft')

    def test_first_derivative_needs_no_copy_and_keeps_job_id(self):
        job=jobs.create(self.root,'derivative',self.inputs,'editor')
        with patch('public_derivative_jobs.generate',return_value=job) as generate:
            result=worker.run_one(self.root,job['id'],{'generation_enabled':True,
                'resolve_source':lambda i:{'prepared':{'original':'source'}},'writer_settings':{'model':'existing','effort':'low'},'codex':'codex'})
            self.assertEqual(result['id'],job['id']);self.assertEqual(generate.call_args.kwargs['job_id'],job['id'])

    def test_unknown_result_cannot_retry_or_reenter_worker(self):
        job=jobs.create(self.root,'daily_briefing',dict(briefing_id='edition',source_revision='r1',source_hash='a'*64),'editor')
        job=jobs.update(self.root,job['id'],1,status='held',generation_status='unknown_outcome')
        with self.assertRaises(jobs.Conflict):worker.run_one(self.root,job['id'],{})


if __name__=='__main__':unittest.main()
