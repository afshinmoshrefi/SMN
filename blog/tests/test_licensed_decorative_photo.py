from pathlib import Path
import json,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import decorative_hero_reuse as reuse
from tests.test_decorative_hero_reuse import PAGE,HERO

class LicensedPhotoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'C';self.root.mkdir()
        for name,data in [('article.json',b'unchanged facts'),('hero-asset.json',b'original unavailable receipt'),('article.html',PAGE.encode())]:(self.root/name).write_bytes(data)
        self.credit={'author':'Photographer','source_url':'https://commons.wikimedia.org/wiki/File:Existing_photo.jpg',
          'license_name':'CC BY-SA 4.0','license_url':reuse.CC_BY_SA,'modification_note':'Original photograph; no crop or retouch.'}
        self.hero={**HERO,'caption':'Archival photograph dated February 14, 2025.','photo_credit':self.credit}
        evidence={}
        for key in ['file_page','license_page']:
            name='licensed-'+key+'.html';path=self.root/name
            path.write_bytes(b'Photographer https://creativecommons.org/licenses/by-sa/4.0/')
            evidence[key]={'file':name,'sha256':reuse.sha(path.read_bytes())}
        self.receipt={'kind':'licensed_external_photograph_v1','symbol':'C','sha256':self.hero['sha256'],
          'photo_credit':self.credit,'visual_inspection_passed':True,'modification_status':'unmodified',
          'source_asset_url':'https://upload.wikimedia.org/wikipedia/commons/a/ab/Existing_photo.jpg',
          'publication_authorization':{'actor':'Afshin','reference':'test-explicit-C-approval','utc':'2026-10-19T16:55:52Z',
            'target_url':'https://seasonalmarketnews.com/editions/2026-10-16/C/article.html','asset_sha256':self.hero['sha256']},'license_evidence':evidence}
        self.record={'kind':reuse.LICENSED_PHOTO,'actor':'Codex','utc':'2026-10-19T17:00:00Z','visual_inspection_passed':True,
          'original_files':{n:reuse.sha((self.root/n).read_bytes()) for n in ['article.json','hero-asset.json','article.html']},'hero':self.hero}
        self.save()
    def save(self):
        source=self.root/'decorative-hero-source-receipt.json';source.write_text(json.dumps(self.receipt))
        self.record['source_receipt_sha256']=reuse.sha(source.read_bytes())
        (self.root/'decorative-hero-reuse.json').write_text(json.dumps(self.record))
    def test_honest_license_receipt_and_linked_credit_preserve_every_fact_byte(self):
        self.assertEqual(reuse.override(self.root),self.hero)
        rendered=reuse.replace(PAGE,self.hero)
        self.assertEqual(reuse.FIGURE.sub('',PAGE),reuse.FIGURE.sub('',rendered))
        self.assertIn('href="'+self.credit['source_url']+'"',rendered)
        self.assertIn('href="'+reuse.CC_BY_SA+'" rel="license"',rendered)
        self.assertIn(self.credit['author'],rendered);self.assertIn(self.credit['modification_note'],rendered)
        self.assertNotIn('AI-generated',rendered)
        from presentation_fallback import derive
        with_head=rendered.replace('<html>','<html><head><title>Fixture</title></head>')
        fallback=derive(with_head,keep_hero=True)
        self.assertIn('href="'+reuse.CC_BY_SA+'"',fallback)
        self.assertIn('Revenue was $12.4 billion.',fallback)
    def test_missing_or_wrong_license_and_unsafe_credit_links_refuse(self):
        for field,value in [('source_url','javascript:alert(1)'),('source_url','https://outside.example/File:photo'),('license_url','https://outside.example/license'),('author',''),('modification_note','')]:
            with self.subTest(field=field,value=value):
                hero={**self.hero,'photo_credit':{**self.credit,field:value}}
                with self.assertRaises(ValueError):reuse.replace(PAGE,hero)
    def test_changed_evidence_or_omitted_license_declaration_refuses(self):
        page=self.root/self.receipt['license_evidence']['file_page']['file'];page.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'evidence changed'):reuse.override(self.root)
        self.receipt['license_evidence']['file_page']['sha256']=reuse.sha(page.read_bytes());self.save()
        with self.assertRaisesRegex(ValueError,'license declaration'):reuse.override(self.root)
    def test_missing_authorization_or_inspection_cannot_create_a_pass(self):
        for field in ['publication_authorization','visual_inspection_passed']:
            old=self.receipt.pop(field);self.save()
            with self.assertRaisesRegex(ValueError,'authorized licensed'):reuse.override(self.root)
            self.receipt[field]=old
    def test_receipt_cannot_switch_author_license_asset_or_symbol(self):
        for field,value in [('photo_credit',{**self.credit,'author':'Other'}),('sha256','b'*64),('symbol','OTHER'),('modification_status','cropped')]:
            old=self.receipt[field];self.receipt[field]=value;self.save()
            with self.assertRaisesRegex(ValueError,'licensed photograph'):reuse.override(self.root)
            self.receipt[field]=old
    def test_original_article_and_reviews_are_not_rewritten(self):
        before={p.name:p.read_bytes() for p in self.root.iterdir()}
        reuse.override(self.root);reuse.replace(PAGE,self.hero)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir()})
        (self.root/'article.json').write_bytes(b'changed facts')
        with self.assertRaisesRegex(ValueError,'base changed'):reuse.override(self.root)
    def test_evidence_cannot_escape_result_directory(self):
        self.receipt['license_evidence']['file_page']['file']='../outside.html';self.save()
        with self.assertRaisesRegex(ValueError,'evidence path'):reuse.override(self.root)
    def test_permission_is_bound_to_article_symbol_and_image(self):
        for key,value in [('target_url','https://seasonalmarketnews.com/editions/2026-10-16/GS/article.html'),('asset_sha256','b'*64)]:
            original=self.receipt['publication_authorization'][key]
            self.receipt['publication_authorization'][key]=value;self.save()
            with self.assertRaisesRegex(ValueError,'authorized licensed'):reuse.override(self.root)
            self.receipt['publication_authorization'][key]=original

if __name__=='__main__':unittest.main()
