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

    def test_fresh_exact_speech_quote_resolver_is_server_bound_and_stale_is_held(self):
        from subscription_writer import utc_now
        env=patch.dict('os.environ',{'ELEVENLABS_API_KEY':'TEST_ONLY_KEY'});env.start();self.addCleanup(env.stop)
        config={'voice_id':'verified','model_id':'existing','voice_settings':{},'elevenlabs_account_sha256':'a'*64}
        script='Exact approved script.';identity=worker.eleven.request_identity(script,'verified','existing',{})
        quote={'verified':True,'verified_at':utc_now(),'request_sha256':identity,'credits':30,'account_sha256':'a'*64,'credential_sha256':sha256(b'TEST_ONLY_KEY')}
        seen=[]
        config['resolve_speech_quote']=lambda request:(seen.append(request) or quote)
        self.assertEqual(worker._speech_quote(config,script),quote)
        self.assertEqual(seen[0]['request_sha256'],identity)
        quote['request_sha256']='a'*64
        with self.assertRaises(ValueError):worker._speech_quote(config,script)
        quote.update(request_sha256=identity,verified_at='2020-01-01T00:00:00Z')
        with self.assertRaises(ValueError):worker._speech_quote(config,script)
        quote.update(verified_at=utc_now(),valid_until='2020-01-01T00:00:00Z')
        with self.assertRaises(ValueError):worker._speech_quote(config,script)

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

    def test_backfilled_script_keeps_original_copy_and_requires_separate_review(self):
        from subscription_writer import save_json
        chart=self.root/'chart.png';chart.write_bytes(b'TEST_ONLY_chart')
        prepared={'provenance':dict(article_id='canonical',revision='r1',article_sha256='a'*64),'input_hashes':{}}
        original={'video':None};script={'narration':{'text':'Verified script.'},'on_screen':{'text':'Historical qualification'},'native_chart_id':'native'}
        review={'status':'approved','reviewer':'Editor','reviewed_at':'2026-10-03T00:00:00Z','copy_sha256':digest(original),
                'prepared_sha256':digest(prepared),'provenance':prepared['provenance'],'checks':{'source':True}}
        separate=dict(review,generation_job='b'*24,script_sha256=digest(script),chart_sha256=sha256(chart.read_bytes()))
        inputs=dict(self.inputs,payload_sha256=digest(original),script='Verified script.',chart_id='native')
        job=jobs.create(self.root,'article_video',inputs,'test');full=jobs.load_json(jobs._path(self.root,job['id']))
        full['inputs']['script_job_id']='b'*24;save_json(jobs._path(self.root,job['id']),full)
        source={'prepared':prepared,'copy':original,'source_review':review,'video_script':script,'video_script_review':separate,
                'on_screen':'Historical qualification','chart_path':str(chart),'chart_sha256':sha256(chart.read_bytes())}
        config={'generation_enabled':True,'resolve_source':lambda inputs:source}
        with patch.object(worker,'validate_derivative'),patch('public_derivative.validate_video_script',create=True) as validate,patch.object(worker.eleven,'speech') as speech:
            valid=worker.run_one(self.root,job['id'],config)
            self.assertIn('voice',valid['holds'][0]);validate.assert_called_once_with(script,prepared,original)
            separate['script_sha256']='c'*64
            invalid=worker.run_one(self.root,job['id'],config)
            self.assertIn('approval binding',invalid['holds'][0]);speech.assert_not_called()
        self.assertIsNone(original['video'])

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
            credential_ready=True,flows_plan_verified=True,briefing_media_job_id='base',voice_id='verified',elevenlabs_account_sha256='a'*64,presenter_reference_sha256=sha256(b'image'),
            resolve_source=lambda i:{'briefing':briefing,'review':{},'active_revision':'r1'},
            avatar_request={'image_path':str(image),'audio_path':str(audio),'audio_receipt':receipt,
                'script':'Approved intro.','part':'intro','resolution':'720p','quote':{'verified':True,'verified_at':__import__('subscription_writer').utc_now(),'request_sha256':worker.eleven.avatar_identity(sha256(b'image'),sha256(b'audio'),'720p'),'credits':100,'account_sha256':'a'*64,'credential_sha256':sha256(b'TEST_ONLY_KEY')}})
        with patch.dict('os.environ',{'ELEVENLABS_API_KEY':'TEST_ONLY_KEY'}),\
                patch('daily_briefing.inspect',return_value={'issues':[],'review_status':'approved'}),\
                patch('briefing_media.base_media',return_value={'id':'base','voice_id':'verified','audio_sha256':'master'}),\
                patch('briefing_media.segment_audio',return_value={'audio_sha256':sha256(b'audio')}),\
                patch.object(worker.eleven,'avatar',return_value={'status':'pending'}) as avatar:
            first=worker.run_one(self.root,job['id'],config);second=worker.run_one(self.root,job['id'],config)
            self.assertEqual(first['stage'],'avatar_provider');self.assertEqual(second['attempts'],1)
            self.assertEqual(avatar.call_count,2);self.assertEqual(second['id'],job['id'])
            # A changed approved speech binding must stop before another provider call.
            image.write_bytes(b'changed')
            held=worker.run_one(self.root,job['id'],config)
            self.assertEqual(held['status'],'held');self.assertEqual(avatar.call_count,2)


if __name__=='__main__':unittest.main()
