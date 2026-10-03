import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import elevenlabs_client as eleven
from subscription_writer import load_json,save_json,sha256


class Avatar(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.image=self.root/'person.jpg';self.image.write_bytes(b'image')
        self.audio=self.root/'intro.mp3';self.audio.write_bytes(b'audio');self.output=self.root/'avatar.mp4'
        self.identity=eleven.avatar_identity(sha256(b'image'),sha256(b'audio'),'720p')
        self.quote={'request_sha256':self.identity,'verified':True,'verified_at':'2026-10-03T00:00:00Z','credits':3392}
        self.env=patch.dict(os.environ,{'ELEVENLABS_API_KEY':'test-only'});self.env.start();self.addCleanup(self.env.stop)

    def call(self):return eleven.avatar(self.root,self.image,self.audio,'720p',self.quote,self.output)

    def test_submit_inline_once_and_obey_poll_interval(self):
        with patch.object(eleven,'_request',return_value={'id':'generation1','status':'pending'}) as request:
            result=self.call();self.assertEqual(result['status'],'pending');self.call()
            self.assertEqual(request.call_count,1)
            payload=request.call_args.args[1]
            self.assertEqual(payload['model_id'],'creatify-aurora')
            self.assertEqual(payload['image']['type'],'inline_base64')
            self.assertNotIn('test-only',str(load_json(self.output.with_suffix('.provider.json'))))

    def test_unknown_submission_never_resubmits(self):
        with patch.object(eleven,'_request',side_effect=eleven.Held('unknown')) as request:
            with self.assertRaises(eleven.Held):self.call()
            with self.assertRaises(eleven.Held):self.call()
            self.assertEqual(request.call_count,1)
        self.assertEqual(load_json(self.root/'elevenlabs-budget.json')['reservations'][0]['status'],'unknown_outcome')

    def test_changed_input_rejected_before_second_request(self):
        with patch.object(eleven,'_request',return_value={'id':'generation1','status':'pending'}) as request:
            self.call();self.image.write_bytes(b'different')
            with self.assertRaises(eleven.Held):self.call()
            self.assertEqual(request.call_count,1)

    def test_budget_blocks_submission(self):
        save_json(self.root/'elevenlabs-budget.json',{'ceiling':5000,'reservations':[
            {'request_sha256':'previous','quoted_credits':2000,'status':'received'}]})
        with patch.object(eleven,'_request') as request:
            with self.assertRaises(eleven.Held):self.call()
            request.assert_not_called()

    def test_signed_download_never_uses_api_key_and_is_bound(self):
        with patch.object(eleven,'_request',return_value={'id':'generation1','status':'pending'}):self.call()
        state=load_json(self.output.with_suffix('.provider.json'));state['last_checked_at']='2026-01-01T00:00:00Z'
        save_json(self.output.with_suffix('.provider.json'),state)
        with patch.object(eleven,'_request',return_value={'status':'completed','content_mime_type':'video/mp4',
                'content_url':'https://storage.googleapis.com/generations/result?signature=private'}),patch.object(eleven,'build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value=b'mp4'
            receipt=self.call()
            download=opener.return_value.open.call_args.args[0]
            self.assertNotIn('Xi-api-key',download.headers)
            self.assertNotIn('signature',str(receipt));self.assertEqual(receipt['video_sha256'],sha256(b'mp4'))
        self.assertEqual(load_json(self.root/'elevenlabs-budget.json')['reservations'][0]['status'],'received')

    def test_unqualified_download_host_never_fetched(self):
        with patch.object(eleven,'_request',return_value={'id':'generation1','status':'pending'}):self.call()
        state=load_json(self.output.with_suffix('.provider.json'));state['last_checked_at']='2026-01-01T00:00:00Z'
        save_json(self.output.with_suffix('.provider.json'),state)
        with patch.object(eleven,'_request',return_value={'status':'completed','content_mime_type':'video/mp4',
                'content_url':'https://localhost/private'}),patch.object(eleven,'build_opener') as opener:
            with self.assertRaises(eleven.Held):self.call()
            opener.assert_not_called()


if __name__=='__main__':unittest.main()
