import io,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]/'blog'))
import subscription_dev_publish as publish

class PublishTests(unittest.TestCase):
 def root(self):
  d=Path(tempfile.mkdtemp());(d/'dev-stage.json').write_text(json.dumps({'source_commit':'a'*40,'remote':'/tmp/x','record':'/state/x'}));return d
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
 def test_wrapper_compiles(self):
  compile(publish.activation_wrapper(),'wrapper.py','exec')
 def test_missing_node_stops_before_remote_prepare(self):
  root=self.root()
  with patch.object(publish,'remote_prepare') as prepare:
   with self.assertRaisesRegex(ValueError,'--node'): publish.activate(root,root)
  prepare.assert_not_called()
 def test_lock_acknowledges_fresh_main_after_marker(self):
  class P:
   def __init__(s): s.stdout=io.StringIO('DEV_LOCK_ACQUIRED_RECHECK_MAIN\n');s.returncode=0;s.stdin=None;s.stderr=io.StringIO()
   def communicate(s,*a,**k): s.answer=a[0];return ('{}','')
   def poll(s): return 0
  root=self.root(); p=P()
  with patch.object(publish,'remote_prepare',return_value=json.dumps('/state/x')),patch('subscription_dev_publish.subprocess.Popen',return_value=p),patch.object(publish,'clean_main',return_value=(root,'a'*40)),patch.object(publish,'run',side_effect=RuntimeError('live failed')),patch.object(publish,'remote_call',return_value='{}'):
   with self.assertRaisesRegex(RuntimeError,'live failed'): publish.activate(root,root,node='node')
  self.assertEqual(p.answer,'a'*40+'\n')
 def test_failed_live_check_rolls_back(self):
  class P:
   def __init__(s): s.stdout=io.StringIO('DEV_LOCK_ACQUIRED_RECHECK_MAIN\n');s.returncode=0
   def communicate(s,*a,**k): return ('{}','')
   def poll(s): return 0
  root=self.root()
  with patch.object(publish,'remote_prepare',return_value=json.dumps('/state/x')),patch('subscription_dev_publish.subprocess.Popen',return_value=P()),patch.object(publish,'clean_main',return_value=(root,'a'*40)),patch.object(publish,'run',side_effect=RuntimeError('bad live')),patch.object(publish,'remote_call',return_value='{}') as remote:
   with self.assertRaisesRegex(RuntimeError,'bad live'): publish.activate(root,root,node='node')
  self.assertTrue(any(' rollback /state/x' in call.args[0] for call in remote.call_args_list))
 def test_remote_source_tamper_verification_rolls_back(self):
  root=self.root(); image=root/'landing.png';image.write_bytes(b'pixels')
  (root/'dev-activation.json').write_text(json.dumps({'record':'/state/x','source_commit':'a'*40}))
  (root/'live-verification.json').write_text(json.dumps({'passed':True,'source_commit':'a'*40,'public_hash_proof':True,'public_files':[{'passed':True}]}))
  (root/'live-landing-visual-checks.json').write_text(json.dumps({'passed':True,'inspected_images':{'landing.png':publish.sha(image)}}))
  with patch.object(publish,'clean_main',return_value=(root,'a'*40)),patch.object(publish,'run'),patch.object(publish,'remote_call',side_effect=[subprocess.CalledProcessError(1,'verify'),'{}']) as remote:
   with self.assertRaises(subprocess.CalledProcessError): publish.finish(root,root)
  self.assertIn(' rollback /state/x',remote.call_args_list[-1].args[0])
 def test_tampered_archive_stops_before_remote_inspection(self):
  root=self.root(); source=root/'committed-source.tar'; package=root/'publication-package.tar'
  source.write_bytes(b'changed');package.write_bytes(b'package')
  receipt=publish.read(root/'dev-stage.json');receipt.update(source_tar_sha256='0'*64,package_tar_sha256=publish.sha(package))
  with patch.object(publish,'remote_state') as state:
   with self.assertRaisesRegex(ValueError,'archive hash'): publish.remote_prepare(root,receipt)
  state.assert_not_called()
if __name__=='__main__': unittest.main()
