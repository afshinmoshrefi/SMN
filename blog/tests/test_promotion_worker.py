from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import promotion_jobs as jobs
import promotion_worker as worker
from daily_briefing import digest
from subscription_writer import sha256


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

    def test_avatar_submission_then_same_job_poll_without_extra_attempt(self):
        briefing={'script':[{'text':'Approved intro. Full story.'}]}
        image=self.root/'person.jpg';image.write_bytes(b'image')
        audio=self.root/'intro.mp3';audio.write_bytes(b'audio')
        receipt={'voice_id':'verified','model_id':'existing','settings':{},'audio_sha256':sha256(b'audio')}
        receipt['request_sha256']=worker.eleven.request_identity('Approved intro.','verified','existing',{})
        job=jobs.create(self.root,'daily_avatar',{'briefing_id':'edition','source_revision':'r1','source_hash':digest(briefing)},'editor')
        config=dict(generation_enabled=True,avatar_ready=True,likeness_verified=True,voice_verified=True,
            credential_ready=True,flows_plan_verified=True,voice_id='verified',presenter_reference_sha256=sha256(b'image'),
            resolve_source=lambda i:{'briefing':briefing,'review':{},'active_revision':'r1'},
            avatar_request={'image_path':str(image),'audio_path':str(audio),'audio_receipt':receipt,
                'script':'Approved intro.','part':'intro','resolution':'720p','quote':{}})
        with patch('daily_briefing.inspect',return_value={'issues':[],'review_status':'approved'}),\
                patch('video_render.probe',return_value={'format':{'duration':'3'}}),\
                patch.object(worker.eleven,'avatar',return_value={'status':'pending'}) as avatar:
            first=worker.run_one(self.root,job['id'],config);second=worker.run_one(self.root,job['id'],config)
            self.assertEqual(first['stage'],'avatar_provider');self.assertEqual(second['attempts'],1)
            self.assertEqual(avatar.call_count,2);self.assertEqual(second['id'],job['id'])
            # A changed approved speech binding must stop before another provider call.
            receipt['voice_id']='different'
            held=worker.run_one(self.root,job['id'],config)
            self.assertEqual(held['status'],'held');self.assertEqual(avatar.call_count,2)


if __name__=='__main__':unittest.main()
