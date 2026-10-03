"""Synthetic media fixtures only: no likeness, speech provider or real publication."""
from pathlib import Path
import shutil
import tempfile
import unittest

import briefing_media as media
import promotion_jobs as jobs
from daily_briefing import digest
from speech_timing import write_captions
from subscription_writer import save_json,sha256
from video_render import _run,probe


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg fixture runtime required')
class Media(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.briefing={'script':[{'text':'Test intro. Full fixture report.'}]};self.script=self.briefing['script'][0]['text']
        job=jobs.create(self.root,'daily_briefing',{'briefing_id':'fixture','source_revision':digest(self.briefing),'source_hash':digest(self.briefing)},'test')
        self.identifier=job['id'];self.folder=jobs._folder(self.root)/'artifacts'/self.identifier;self.folder.mkdir(parents=True)
        audio=self.folder/'narration.mp3'
        _run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','sine=frequency=440:duration=4','-c:a','libmp3lame',str(audio)])
        video=self.folder/'briefing.mp4'
        _run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=c=blue:s=160x240:d=4','-i',str(audio),'-map','0:v','-map','1:a','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-t','4',str(video)])
        duration=float(probe(audio)['format']['duration'])
        timing={'audio_sha256':sha256(audio.read_bytes()),'script_sha256':sha256(self.script.encode()),'method':'synthetic_fixture',
                'segments':[{'text':'Test intro.','offset':0,'start':0,'end':.8},
                            {'text':'Full fixture report.','offset':12,'start':1,'end':3.8}]}
        save_json(self.folder/'narration.alignment.json',timing);write_captions(self.folder/'captions.vtt',timing)
        (self.folder/'transcript.txt').write_text(self.script+'\n')
        save_json(self.folder/'narration.receipt.json',{'voice_id':'test-only','audio_sha256':sha256(audio.read_bytes())})
        save_json(self.folder/'briefing.receipt.json',{'briefing_sha256':digest(self.briefing),'audio_sha256':sha256(audio.read_bytes()),'video_sha256':sha256(video.read_bytes())})
        artifacts=[{'name':p.name,'relative_path':p.name,'media_type':'application/octet-stream','sha256':sha256(p.read_bytes())} for p in self.folder.iterdir()]
        jobs.update(self.root,self.identifier,job['version'],status='reviewed',review_status='approved',artifacts=artifacts,review={'payload_sha256':digest(jobs.load_json(jobs._path(self.root,self.identifier))['inputs']),'artifact_hashes':{a['name']:a['sha256'] for a in artifacts}})
        self.base=media.base_media(self.root,self.identifier,self.briefing,'test-only')

    def test_real_composition_keeps_full_master_audio_captions_and_duration(self):
        output=self.root/'composed/briefing.mp4';segment=media.segment_audio(self.base,'intro','Test intro.',output.parent/'segment.wav')
        avatar=self.root/'TEST_ONLY_presenter_fixture.mp4'
        _run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=c=red:s=160x240:d=1','-c:v','libx264','-pix_fmt','yuv420p',str(avatar)])
        receipt=media.compose_avatar(output,self.base,avatar,segment)
        self.assertGreater(receipt['duration_seconds'],3.9)
        self.assertTrue(receipt['complete_narration']);self.assertEqual(receipt['avatar_interval'],[0,1])
        for name in ('narration.mp3','captions.vtt','transcript.txt'):
            self.assertEqual((output.parent/name).read_bytes(),self.base['files'][name].read_bytes())
        with self.assertRaises(ValueError):media.compose_avatar(output,self.base,avatar,segment)
        changed=dict(segment,master_audio_sha256='b'*64)
        with self.assertRaises(ValueError):media.compose_avatar(self.root/'bad.mp4',self.base,avatar,changed)

    def test_short_or_mismatched_presenter_duration_is_rejected(self):
        segment=media.segment_audio(self.base,'intro','Test intro.',self.root/'segment.wav')
        avatar=self.root/'TEST_ONLY_short_presenter.mp4'
        _run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=c=red:s=160x240:d=0.4',
              '-c:v','libx264','-pix_fmt','yuv420p',str(avatar)])
        with self.assertRaisesRegex(ValueError,'measured master audio interval'):
            media.compose_avatar(self.root/'rejected.mp4',self.base,avatar,segment)
        self.assertFalse((self.root/'rejected.mp4').exists())

    def test_changed_source_missing_audio_or_incomplete_captions_is_closed(self):
        with self.assertRaises(ValueError):media.base_media(self.root,self.identifier,{'script':[{'text':'Changed source.'}]},'test-only')
        with self.assertRaises(ValueError):media.segment_audio(self.base,'intro','Test intr',self.root/'partial.wav')
        captions=self.folder/'captions.vtt';captions.write_text('WEBVTT\n\n00:00:00.000 --> 00:00:00.800\nTest intro.\n')
        job=jobs.load_json(jobs._path(self.root,self.identifier))
        for artifact in job['artifacts']:
            if artifact['name']=='captions.vtt':artifact['sha256']=sha256(captions.read_bytes())
        job['review']['artifact_hashes']['captions.vtt']=sha256(captions.read_bytes());save_json(jobs._path(self.root,self.identifier),job)
        with self.assertRaisesRegex(ValueError,'Master captions'):media.base_media(self.root,self.identifier,self.briefing,'test-only')
        (self.folder/'narration.mp3').unlink()
        with self.assertRaises((ValueError,FileNotFoundError)):media.base_media(self.root,self.identifier,self.briefing,'test-only')

if __name__=='__main__':unittest.main()
