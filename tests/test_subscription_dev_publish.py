import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]/'blog'))
import subscription_dev_publish as publish

class PublishTests(unittest.TestCase):
 def test_review_stages_requires_six_bindings(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'results').mkdir()
   for n in range(6):
    p=root/'results'/('S%d'%n);p.mkdir();(p/'review-binding.json').write_text(json.dumps({'review_stage':'review'}))
   self.assertEqual(len(publish.review_stages(root)),6)
 def test_clean_main_rejects_dirty_source(self):
  with tempfile.TemporaryDirectory() as d, patch.object(publish,'run'),patch('subscription_dev_publish.subprocess.check_output',side_effect=['dirty\n']):
   with self.assertRaisesRegex(ValueError,'clean'): publish.clean_main(d)
 def test_finish_requires_hash_proof(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'dev-activation.json').write_text(json.dumps({'source_commit':'a'*40}))
   (root/'live-verification.json').write_text(json.dumps({'passed':True,'source_commit':'a'*40}))
   (root/'live-landing-visual-checks.json').write_text(json.dumps({'passed':True,'inspected_images':{}}))
   with patch.object(publish,'clean_main',return_value=(root,'a'*40)):
    with self.assertRaisesRegex(ValueError,'hash proof'): publish.finish(root,root)
if __name__=='__main__': unittest.main()
