"""Exercise catalog preservation and rollback entirely in a temporary fixture."""
from pathlib import Path
import contextlib,io,json,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import install_subscription_dev as deploy

class DevInstallTests(unittest.TestCase):
    def fixture(self,t):
        root=Path(t)/'site';root.mkdir();package=Path(t)/'package';package.mkdir()
        date='2026-09-08';entries=[]
        prior={'symbol':'OLD','pattern_start_date':'2026-08-01','pattern_days':50,'published_date':'2026-08-01T00:00:00Z','url':'/old.html','title':'Preserved'}
        for name,value in [('posts.json',[prior]),('search_index.json',[prior]),('suggest.json',[prior])]:
            (root/name).write_text(json.dumps(value),encoding='utf-8')
        (root/'index.html').write_text('<html>Existing header\n    <!-- Hero Section -->\nExisting content</html>',encoding='utf-8')
        for i in range(6):
            sym='SYM'+str(i);rel=f'editions/{date}/{sym}/article.html';p=package/rel;p.parent.mkdir(parents=True);p.write_text('New '+sym)
            entries.append({'symbol':sym,'pattern_start_date':date,'pattern_days':40,'published_date':date+'T00:00:00Z',
                'url':'https://smn-dev.trxstat.com/'+rel,'title':'New '+sym,'dek':'A new article','market_family':'US','tags':[],'edition_id':'subscription-'+date})
        (package/'entries.json').write_text(json.dumps(entries),encoding='utf-8')
        (package/'home-section.html').write_text('<section>New edition</section>')
        manifest={'schema_version':1,'target_origin':'https://smn-dev.trxstat.com','target_root':str(root),
                  'production_allowed':False,'edition_date':date,'edition_id':'subscription-'+date,'source_commit':'a'*40,
                  'files':{p.relative_to(package).as_posix():deploy.hashfile(p) for p in package.rglob('*') if p.is_file()}}
        (package/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        return root,package,Path(t)/'state/dev-activation.lock'

    def execute(self,root,package,lock,atomic):
        with patch.object(deploy,'ROOT',root),patch.object(deploy,'LOCK',lock),\
             patch.object(deploy.socket,'gethostname',return_value='SMN'),\
             patch.object(deploy.subprocess,'check_output',return_value='192.168.1.180'),\
             patch.object(deploy,'atomic',side_effect=atomic),contextlib.redirect_stdout(io.StringIO()):
            deploy.install(package)

    def test_existing_content_preserved_and_repeat_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as t:
            root,package,lock=self.fixture(t)
            self.execute(root,package,lock,lambda p,d:p.write_bytes(d))
            first=deploy.read(root/'posts.json')
            self.assertEqual(len(first),7);self.assertTrue(any(p['symbol']=='OLD' for p in first))
            self.assertIn('Existing content',(root/'index.html').read_text())
            self.assertEqual(len(deploy.read(root/'search_index.json')),7)
            self.assertFalse(lock.exists())
            self.execute(root,package,lock,lambda p,d:p.write_bytes(d))
            self.assertEqual(deploy.read(root/'posts.json'),first)
            self.assertEqual((root/'index.html').read_text().count('SMN SUBSCRIPTION EDITION START'),1)

    def test_failure_after_catalog_write_restores_original_files(self):
        with tempfile.TemporaryDirectory() as t:
            root,package,lock=self.fixture(t)
            original={p.name:p.read_bytes() for p in root.iterdir()};failed=False
            def flaky(p,data):
                nonlocal failed
                if p.name=='search_index.json' and not failed:
                    failed=True;raise OSError('Simulated disk failure')
                p.write_bytes(data)
            with self.assertRaises(OSError):self.execute(root,package,lock,flaky)
            for name,data in original.items():self.assertEqual((root/name).read_bytes(),data)
            self.assertFalse(list((root/'editions').rglob('article.html')))
            self.assertFalse(lock.exists())

    def test_production_host_is_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as t:
            root,package,lock=self.fixture(t)
            with patch.object(deploy,'ROOT',root),patch.object(deploy,'LOCK',lock),\
                 patch.object(deploy.socket,'gethostname',return_value='seasonalmarketnews'),\
                 patch.object(deploy.subprocess,'check_output',return_value='209.182.216.112'):
                with self.assertRaisesRegex(ValueError,'Exact SMN Dev'):deploy.install(package)
            self.assertFalse(lock.parent.exists())

if __name__=='__main__':unittest.main()
