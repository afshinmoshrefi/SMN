from pathlib import Path
import hashlib,json,sys,tempfile,unittest
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hero_asset_quality as quality
import presentation_fallback as fallback
from unittest.mock import patch

class HeroAssetQualityTests(unittest.TestCase):
    def test_placeholder_provenance_or_known_bytes_never_mean_real_art(self):
        self.assertTrue(quality.placeholder({'asset_kind':'neutral_placeholder'}))
        self.assertTrue(quality.placeholder({'provider':'SMN deterministic neutral placeholder'}))
        self.assertTrue(quality.placeholder({'sha256':quality.KNOWN_BLANK_JPEG,'provenance':{'kind':'illustration'}}))

    def test_blank_file_and_real_image_are_distinguished_and_hash_bound(self):
        with tempfile.TemporaryDirectory() as folder:
            result=Path(folder);(result/'assets').mkdir();file=result/'assets/hero.png'
            image=Image.new('RGB',(600,300),'white');image.save(file)
            record={'url':'assets/hero.png','sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'alt':'Editorial art','provenance':{'kind':'illustration'}}
            (result/'hero-asset.json').write_text(json.dumps(record))
            self.assertIsNone(quality.available_hero(result))
            image.putdata([(x%256,y%256,(x+y)%256) for y in range(300) for x in range(600)]);image.save(file)
            record['sha256']=hashlib.sha256(file.read_bytes()).hexdigest();(result/'hero-asset.json').write_text(json.dumps(record))
            self.assertEqual(quality.available_hero(result),record)
            file.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'changed'):quality.available_hero(result)

    def test_valid_hero_survives_layout_fallback_without_changing_prose(self):
        from tests.test_presentation_fallback import PAGE
        page=fallback.derive(PAGE,keep_hero=True)
        self.assertIn('assets/hero.png',page)
        self.assertIn('Revenue was $12.4 billion. [1]',page)
        self.assertNotIn('overflow:hidden',page)
        with self.assertRaisesRegex(ValueError,'active content'):
            fallback.derive(PAGE.replace('src="assets/hero.png"','src="assets/hero.png" onerror="alert(1)"'),keep_hero=True)

if __name__=='__main__':unittest.main()
