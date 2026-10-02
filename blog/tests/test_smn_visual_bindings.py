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

    def _run(self,job,answer=None):
        manifest=load_json(job/'job.json')
        save_json(job/'output.json',answer or {'passed':True,'defects':[]})
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

    def test_minor_only_failed_verdict_gets_one_fresh_tile_aware_inspection(self):
        original=self.root/'jobs/SPY-20260930-visual';calls=[];original_bytes={}
        def run(job):
            calls.append(job.name)
            if job==original:
                result=self._run(job,{'passed':False,'defects':[{'image':'qa-desktop','severity':'minor','problem':'Paragraph continues at crop edge'}]})
                original_bytes.update({p.name:p.read_bytes() for p in job.iterdir() if p.is_file()})
                return result
            prompt=(job/'prompt.txt').read_text()
            self.assertIn('contiguous crops',prompt);self.assertIn('crop boundary alone',prompt)
            return self._run(job)
        record=self._article(run)
        self.assertTrue(record['passed']);self.assertEqual(len(calls),2)
        self.assertTrue(record['inspector']['job_id'].endswith('visual-reinspect'))
        self.assertEqual(original_bytes,{p.name:p.read_bytes() for p in original.iterdir() if p.is_file()})
        self.assertTrue(visual.verify_article_visual(self.root,'SPY')['passed'])
        self.assertTrue(self._article(lambda _:self.fail('completed inspection rerun'))['passed'])

    def test_major_defects_never_trigger_consistency_retry(self):
        calls=[]
        def run(job):
            calls.append(job.name)
            return self._run(job,{'passed':True,'defects':[{'image':'desktop','severity':'major','problem':'Real overlap'}]})
        self.assertFalse(self._article(run)['passed']);self.assertEqual(len(calls),1)
        with self.assertRaises(ValueError):visual.verify_inconsistent_visual_hold(self.root,'SPY')

    def test_landing_prompt_recognizes_native_compact_links_and_contiguous_tiles(self):
        self.assertIn('compact headline links',visual.LANDING_RULES)
        self.assertIn('crop boundary alone',visual.LANDING_RULES)

    def test_reinspection_is_bounded_and_budget_is_not_increased(self):
        failed={'passed':False,'defects':[]};calls=[]
        def run(job):calls.append(job.name);return self._run(job,failed)
        with self.assertRaisesRegex(ValueError,'budget'):
            visual.article(self.root,'2026-09-30','SPY',{}, {},run,max_jobs=1)
        self.assertEqual(len(calls),1)
        self.assertFalse((self.root/'jobs/SPY-20260930-visual-reinspect').exists())
        self.assertFalse(visual.verify_inconsistent_visual_hold(self.root,'SPY')['passed'])
        self.assertFalse(self._article(run)['passed']);self.assertEqual(len(calls),2)
        self.assertFalse(self._article(lambda _:self.fail('third model invocation'))['passed'])
        with self.assertRaisesRegex(ValueError,'another inspection'):visual.verify_inconsistent_visual_hold(self.root,'SPY')

    def test_legacy_approval_prompt_remains_verifiable(self):
        images=sorted(self.out.glob('qa-*.png'));names=', '.join(p.name for p in images)
        answer,receipt,_=visual._job(self.root,'2026-09-30','SPY','visual',
            visual.PAGE_RULES.format(names=names),visual.PAGE_SCHEMA,images,{}, {},'visual',self._run)
        save_json(self.out/'visual-checks.json',visual._article_record(answer,receipt,
            visual._sha(self.out/'article.html'),images,images,self.out))
        self.assertTrue(visual.verify_article_visual(self.root,'SPY')['passed'])
        self.assertTrue(self._article(lambda _:self.fail('legacy approval rerun'))['passed'])


if __name__=='__main__':
    unittest.main()
