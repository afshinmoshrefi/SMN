from pathlib import Path
from io import BytesIO
import base64,hashlib,json,tempfile,unittest
from unittest.mock import patch,Mock
from PIL import Image
import inspected_archive_hero as archive
import subscription_inputs as inputs
from tests.test_subscription_inputs import package

class InspectedArchiveHeroTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.web=Path(self.temp.name)/'web';self.web.mkdir()
        image=Image.new('RGB',(600,300),'#126868');image.paste('#f8df8a',(300,0,600,300));output=BytesIO();image.save(output,format='JPEG');self.raw=output.getvalue()
        asset=self.web/'image.jpg';asset.write_bytes(self.raw);article=self.web/'article.html';article.write_bytes(b'previously published')
        self.item={'symbol':'VIX','asset_path':'image.jpg','article_path':'article.html',
            'asset_sha256':hashlib.sha256(self.raw).hexdigest(),'article_sha256':hashlib.sha256(article.read_bytes()).hexdigest(),
            'width':600,'height':300,'alt':'Inspected conceptual illustration','actor':'Codex',
            'approved_utc':'2026-10-12T16:00:00+00:00','publication_receipt_sha256':'a'*64}
        self.library=self.web/'library.json';self.write_library()
        (self.web/'posts.json').write_text(json.dumps([{'symbol':'VIX','url':'https://seasonalmarketnews.com/article.html'}]))
    def write_library(self):self.library.write_text(json.dumps({'schema_version':1,'assets':[self.item]}))
    def read(self,symbol='VIX'):return archive.available({'symbol':symbol},web=self.web,library=self.library)
    def test_unchanged_inspected_same_symbol_asset_is_copied_without_provider(self):
        item=self.read();self.assertEqual(base64.b64decode(item['image_base64']),self.raw)
        self.assertFalse(item['api_cost_stage']);self.assertEqual(item['asset_extension'],'.jpg')
        self.assertIsNone(self.read('OTHER'))
    def test_changed_art_article_or_symbol_cannot_reuse_old_inspection(self):
        (self.web/'image.jpg').write_bytes(b'changed');self.assertIsNone(self.read())
        (self.web/'image.jpg').write_bytes(self.raw);(self.web/'article.html').write_bytes(b'changed article');self.assertIsNone(self.read())
        (self.web/'article.html').write_bytes(b'previously published');(self.web/'posts.json').write_text('[]');self.assertIsNone(self.read())
    def test_input_preparation_generates_fresh_hero_instead_of_reusing_archive(self):
        root=Path(self.temp.name)/'input';inputs.capture(root,'2026-09-30',lambda _:package())
        blob=b'fresh generated hero'
        generated={'image_base64':base64.b64encode(blob).decode(),'sha256':hashlib.sha256(blob).hexdigest(),
                   'provider':'fresh fixture','api_cost_stage':True}
        callback=Mock(return_value=generated)
        with patch.object(archive,'available',side_effect=AssertionError('archive must not be primary')):
            first=inputs.prepare_heroes(root,'2026-09-30',callback,nonessential_fallback=True)
            second=inputs.prepare_heroes(root,'2026-09-30',callback,nonessential_fallback=True)
        callback.assert_called_once_with(unittest.mock.ANY,'2026-09-30')
        self.assertTrue(first['hero_ready']);self.assertTrue(second['idempotent'])
        receipt=json.loads((root/'input-heroes.json').read_text());self.assertTrue(receipt['api_cost_stage'])
        self.assertEqual(receipt['heroes']['VIX']['sha256'],generated['sha256'])

if __name__=='__main__':unittest.main()
