import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import promotion_jobs as jobs
import elevenlabs_client as eleven
from subscription_writer import load_json, save_json, sha256


class Jobs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inputs = {'article_id':'canonical-1', 'source_revision':'r1', 'source_hash':'a'*64, 'script':'Original'}

    def test_edit_has_new_identity_and_original_create_stays_original(self):
        old = jobs.create(self.root,'article_video', self.inputs,'editor')
        new = jobs.transition(self.root, old['id'],'edit',1,'editor',{'script':'Changed'})
        self.assertNotEqual(new['id'],old['id'])
        self.assertEqual(jobs.create(self.root,'article_video',self.inputs,'editor')['id'],old['id'])
        self.assertEqual(load_json(jobs._path(self.root,old['id']))['inputs']['script'],'Original')
        self.assertEqual(jobs.get_job(self.root,old['id'])['status'],'superseded')

    def test_pause_is_not_a_job(self):
        job = jobs.create(self.root,'article_video',self.inputs,'editor')
        jobs.pause(self.root,'all',True,'editor')
        self.assertEqual([j['id'] for j in jobs.list_jobs(self.root)],[job['id']])
        self.assertTrue(jobs.is_paused(self.root,'article_video'))

    def test_stale_review_and_unknown_retry_rejected(self):
        job = jobs.create(self.root,'article_video',self.inputs,'editor')
        job = jobs.update(self.root,job['id'],1,status='held',generation_status='unknown_outcome')
        with self.assertRaises(jobs.Conflict): jobs.transition(self.root,job['id'],'retry',2,'editor')
        with self.assertRaises(jobs.Conflict): jobs.transition(self.root,job['id'],'review',1,'editor',{})

    def test_artifact_containment_and_hash(self):
        job = jobs.create(self.root,'article_video',self.inputs,'editor')
        folder = jobs._folder(self.root)/'artifacts'/job['id']; folder.mkdir(parents=True)
        path=folder/'audio.mp3'; path.write_bytes(b'audio')
        artifact={'name':'audio','relative_path':'audio.mp3','media_type':'audio/mpeg','sha256':sha256(b'audio'),'review_status':'pending'}
        jobs.update(self.root,job['id'],1,artifacts=[artifact])
        self.assertEqual(jobs.get_artifact(self.root,job['id'],'audio')[0],path)
        path.write_bytes(b'changed')
        with self.assertRaises(ValueError): jobs.get_artifact(self.root,job['id'],'audio')
        jobs.update(self.root,job['id'],2,status='generated')
        with self.assertRaises(ValueError):jobs.transition(self.root,job['id'],'review',3,'editor',
            {'decision':'approved','payload_sha256':jobs.digest(self.inputs)})
        artifact['relative_path']='../../outside'; jobs.update(self.root,job['id'],3,artifacts=[artifact])
        with self.assertRaises(ValueError): jobs.get_artifact(self.root,job['id'],'audio')

    def test_missing_key_has_no_network_or_budget_effect(self):
        with patch.dict('os.environ',{},clear=True), patch.object(eleven,'_request') as request:
            with self.assertRaises(eleven.Held): eleven.speech(self.root,'script','voice','model',{}, {},self.root/'a.mp3')
            request.assert_not_called()
        self.assertFalse((self.root/'elevenlabs-budget.json').exists())

    def test_exact_quote_budget_and_unknown_outcome(self):
        quote={'verified':True,'verified_at':'now','request_sha256':'request','credits':3000}
        eleven._reserve(self.root,'request',quote)
        with self.assertRaises(eleven.Held): eleven._reserve(self.root,'request',quote)
        quote['request_sha256']='other'
        with self.assertRaises(eleven.Held): eleven._reserve(self.root,'other',quote)
        quote['credits']=10; quote['request_sha256']='wrong'
        with self.assertRaises(eleven.Held): eleven._reserve(self.root,'other',quote)


if __name__=='__main__': unittest.main()
