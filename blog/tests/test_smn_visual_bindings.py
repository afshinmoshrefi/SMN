"""Offline checks that old vision answers cannot approve new surfaces."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import smn_visual as visual
from subscription_writer import prepare_job, save_json, load_json


class VisualBindingsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.out=self.root/'results/SPY';self.out.mkdir(parents=True)
        (self.out/'article.html').write_text('<html>current article</html>')
        for name in ('qa-desktop.png','qa-mobile.png'):
            Image.new('RGB',(20,20),'blue').save(self.out/name)
        save_json(self.out/'layout-checks.json',{'passed':True,'article_html_sha256':visual._sha(self.out/'article.html')})
        self.prepare=patch.object(visual.smn_models,'prepare',side_effect=self._prepare)
        self.prepare.start()

    def tearDown(self):
        self.prepare.stop();self.temp.cleanup()

    def _prepare(self,roles,role,*args,**kwargs):
        return prepare_job(*args,**kwargs,model='gpt-6-luna',effort='low')

    def _run(self,job):
        manifest=load_json(job/'job.json')
        save_json(job/'output.json',{'passed':True,'defects':[]})
        receipt={'job_id':manifest['job_id'],'stage':manifest['stage'],'input_hashes':manifest['input_hashes'],
                 'evidence_sha256':manifest['evidence_sha256'],'output_sha256':visual._sha(job/'output.json'),
                 'api_fallback':False,'billing_source':'subscription','model_requested':'gpt-6-luna','effort_requested':'low'}
        save_json(job/'receipt.json',receipt)
        return receipt

    def _article(self,run=None):
        return visual.article(self.root,'2026-09-30','SPY',{}, {},run or self._run)

    def test_current_completed_visual_is_verified_and_reused_without_running(self):
        self.assertTrue(self._article()['passed'])
        self.assertTrue(visual.verify_article_visual(self.root,'SPY')['passed'])
        self.assertTrue(self._article(lambda _:self.fail('completed job rerun'))['passed'])

    def test_tiled_and_legacy_visual_records_bind_to_actual_job_images(self):
        Image.new('RGB',(20,2001),'blue').save(self.out/'qa-desktop.png')
        self._article();self.assertTrue(visual.verify_article_visual(self.root,'SPY')['passed'])
        record=load_json(self.out/'visual-checks.json');record.pop('inspected_job_images')
        save_json(self.out/'visual-checks.json',record)
        self.assertTrue(visual.verify_article_visual(self.root,'SPY')['passed'])
        tile=next((self.out/'vision-tiles').glob('*.png'));tile.write_bytes(b'changed tile')
        with self.assertRaisesRegex(ValueError,'inspected images changed'):
            visual.verify_article_visual(self.root,'SPY')

    def test_changed_html_cannot_use_old_layout(self):
        self._article();(self.out/'article.html').write_text('new article')
        with self.assertRaisesRegex(ValueError,'Layout capture'):
            self._article()
        with self.assertRaisesRegex(ValueError,'Layout capture'):
            visual.verify_article_visual(self.root,'SPY')

    def test_changed_screenshot_cannot_be_restamped_from_old_job(self):
        self._article();before=(self.out/'visual-checks.json').read_bytes()
        Image.new('RGB',(20,20),'red').save(self.out/'qa-desktop.png')
        with self.assertRaisesRegex(ValueError,'inspected images changed'):
            self._article()
        self.assertEqual((self.out/'visual-checks.json').read_bytes(),before)
        with self.assertRaisesRegex(ValueError,'stale'):
            visual.verify_article_visual(self.root,'SPY')

    def test_altered_output_or_receipt_is_rejected(self):
        self._article();job=self.root/'jobs/SPY-20260930-visual'
        save_json(job/'output.json',{'passed':True,'defects':[{'image':'desktop','severity':'minor','problem':'changed'}]})
        with self.assertRaisesRegex(ValueError,'receipt'):
            visual.verify_article_visual(self.root,'SPY')

    def test_empty_screenshot_binding_or_major_defect_cannot_pass(self):
        self._article();record=load_json(self.out/'visual-checks.json')
        record['inspected_images']={};save_json(self.out/'visual-checks.json',record)
        with self.assertRaisesRegex(ValueError,'stale'):
            visual.verify_article_visual(self.root,'SPY')
        record['inspected_images']={p.name:visual._sha(p) for p in self.out.glob('qa-*.png')}
        record['defects']=[{'image':'desktop','severity':'major','problem':'overlap'}]
        save_json(self.out/'visual-checks.json',record)
        with self.assertRaisesRegex(ValueError,'failed'):
            visual.verify_article_visual(self.root,'SPY')

    def test_changed_schema_or_prepared_image_is_rejected_on_reuse(self):
        self._article();job=self.root/'jobs/SPY-20260930-visual'
        save_json(job/'schema.json',{'type':'object'})
        with self.assertRaisesRegex(ValueError,'assignment changed'):
            self._article()


if __name__=='__main__':
    unittest.main()
