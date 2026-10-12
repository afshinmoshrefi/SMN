from pathlib import Path
import json,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import decorative_hero_reuse as reuse

PAGE='<html><body><article><h1>Same factual title</h1><figure class="hero"><img src="assets/old.jpg"><figcaption>Old illustration</figcaption></figure><p>Revenue was $12.4 billion.</p><figure class="data-figure">Protected chart</figure></article></body></html>'
HERO={'url':'assets/hero-reused-aaaaaaaaaaaa.jpg','alt':'Conceptual vehicle auction illustration','width':1200,'height':675,'sha256':'a'*64}

class DecorativeHeroReuseTests(unittest.TestCase):
    def test_only_decorative_figure_changes_and_all_fact_bytes_match(self):
        changed=reuse.replace(PAGE,HERO)
        self.assertEqual(reuse.FIGURE.sub('',PAGE),reuse.FIGURE.sub('',changed))
        self.assertIn('Revenue was $12.4 billion.',changed)
        self.assertIn('<figure class="data-figure">Protected chart</figure>',changed)
        self.assertIn(HERO['url'],changed)

    def test_financial_content_duplicate_figures_and_unsafe_url_refuse(self):
        for source in [PAGE.replace('Old illustration','<p>Financial information</p>'),PAGE+PAGE]:
            with self.assertRaises(ValueError):reuse.replace(source,HERO)
        with self.assertRaises(ValueError):reuse.replace(PAGE,{**HERO,'url':'https://unknown.example/image.jpg'})

    def test_base_and_attribution_are_verified_without_creating_a_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name,data in [('article.json',b'original facts'),('hero-asset.json',b'original image receipt'),('article.html',PAGE.encode())]:(root/name).write_bytes(data)
            receipt={'status':'live_verified','production_written':True,'retained_heroes':{'old.jpg':'a'*64},'retained_articles':{'old.html':'b'*64}}
            (root/'decorative-hero-source-receipt.json').write_text(json.dumps(receipt))
            record={'kind':'previously_published_decorative_illustration','actor':'Codex','utc':'2026-10-12T15:00:00Z','source_publication_url':'https://seasonalmarketnews.com/old.html','source_asset_url':'https://seasonalmarketnews.com/old.jpg','source_receipt_sha256':reuse.sha((root/'decorative-hero-source-receipt.json').read_bytes()),'original_files':{n:reuse.sha((root/n).read_bytes()) for n in ['article.json','hero-asset.json','article.html']},'hero':HERO}
            (root/'decorative-hero-reuse.json').write_text(json.dumps(record))
            self.assertEqual(reuse.override(root),HERO)
            (root/'article.json').write_bytes(b'changed facts')
            with self.assertRaisesRegex(ValueError,'base changed'):reuse.override(root)

if __name__=='__main__':unittest.main()
