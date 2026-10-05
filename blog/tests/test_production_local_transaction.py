"""Real installer file transactions in temp directories; no host/provider writes."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
if os.name=='nt' and 'fcntl' not in sys.modules:
    sys.modules['fcntl']=types.SimpleNamespace(LOCK_EX=1,LOCK_UN=2,flock=lambda *_:None)
import install_smn_primary_edition as installer
import production_continuity as rolling
from subscription_publication import write,read

DATE='2026-10-06'
COMMIT='a'*40
ORIGIN='https://seasonalmarketnews.com'

class LocalProductionTransactionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.web=self.root/'web';self.web.mkdir()
        self.state=self.root/'state';self.state.mkdir();self.blog=self.root/'blog';self.blog.mkdir()
        self.dash=self.root/'dash';self.dash.mkdir()
        self.generated=installer.GENERATED+('sitemap.xml','sitemap-news.xml','rss.xml','robots.txt','llms.txt')
        write(self.web/'posts.json',[{'symbol':'OLD','url':ORIGIN+'/old.html','published_date':'2026-10-02','edition_id':'old','title':'Retained'}])
        (self.web/'old.html').write_text('Retained exact article')
        for name in self.generated:
            if name!='posts.json':(self.web/name).write_text('<html><body>Existing '+name+'</body></html>')
        (self.blog/'rebuild_news_home.py').write_text('native renderer fixture')
        self.before={name:(self.web/name).read_bytes() for name in self.generated}
        config=types.SimpleNamespace(news_root_folder=str(self.web))
        feed=types.SimpleNamespace()
        for method,file in [('generate_sitemap','sitemap.xml'),('generate_news_sitemap','sitemap-news.xml'),('generate_rss_feed','rss.xml'),('generate_llms_txt','llms.txt')]:
            setattr(feed,method,lambda file=file:(Path(config.news_root_folder)/file).write_text('Generated '+file))
        def render(candidate):
            for name in ('index.html','suggest.json','search_index.json'):
                (candidate/name).write_text('<html><body>Fixture renderer</body></html>' if name=='index.html' else '[]')
        patches=[patch.object(installer,'WEB',self.web),patch.object(installer,'STATE',self.state),
            patch.object(installer,'DASH',self.dash),patch.object(installer,'BLOG',self.blog),
            patch.object(installer,'PRODUCTION',True),patch.object(installer,'ORIGIN',ORIGIN),
            patch.object(installer,'GENERATED',self.generated),patch.object(installer,'guard'),
            patch.object(installer,'render',side_effect=render),patch.object(rolling,'require_policy'),
            patch.dict(sys.modules,{'config':config,'publish_article':feed,'smn_runtime_assets':types.SimpleNamespace(preflight=lambda:None)})]
        for item in patches:item.start();self.addCleanup(item.stop)

    def package(self,published,revision=1):
        folder=self.root/('package-'+str(revision));folder.mkdir()
        landing=folder/'editions'/DATE/'index.html';landing.parent.mkdir(parents=True)
        landing.write_text('<html><body>Dated edition</body></html>')
        expected=['AAA','BBB'];entries=[]
        for symbol in published:
            rel='editions/'+DATE+'/'+symbol+'/article.html'
            file=folder/rel;file.parent.mkdir(parents=True);file.write_text('Reviewed '+symbol)
            entries.append({'symbol':symbol,'url':ORIGIN+'/'+rel,'source_commit':COMMIT,'edition_id':'subscription-'+DATE,'published_date':DATE,'title':symbol})
        write(folder/'entries.json',entries)
        manifest={'target_origin':ORIGIN,'production_allowed':True,'editorial_gate_version':1,'source_commit':COMMIT,
            'edition_date':DATE,'expected_symbols':expected,'published_symbols':published,'pending_symbols':[s for s in expected if s not in published],
            'complete':len(published)==2,'coverage_status':'complete' if len(published)==2 else 'partial' if published else 'notice',
            'continuity_policy':1,'publication_policy':'continuity-v1','revision':revision,'revision_id':str(revision)*64,
            'transaction_id':str(revision)*64,'selection_status':'frozen','selection_sha256':'b'*64,
            'files':{file.relative_to(folder).as_posix():installer.sha(file) for file in folder.rglob('*') if file.is_file()}}
        write(folder/'manifest.json',manifest)
        return folder

    def finish(self,record):
        receipt=read(record/'receipt.json')
        public=[{'rel':rel,'sha256':digest,'passed':True} for rel,digest in {**receipt['files'],**receipt['retained_articles'],**receipt['retained_heroes']}.items()]
        write(record/'live-verification.json',{'passed':True,'source_commit':COMMIT,'public_files':public})
        return installer.finish(record)

    def test_notice_partial_complete_use_same_urls_and_preserve_archive(self):
        for revision,published in enumerate(([],['AAA'],['AAA','BBB']),1):
            record=Path(installer.prepare(self.package(published,revision))['record'])
            installer.activate(record);receipt=self.finish(record)
            self.assertEqual(receipt['complete'],len(published)==2)
            self.assertEqual(len(read(self.web/'posts.json')),1+len(published))
            self.assertEqual((self.web/'old.html').read_text(),'Retained exact article')
            self.assertIn(DATE,(self.web/'index.html').read_text())
        self.assertEqual(len({p['url'] for p in read(self.web/'posts.json')}),3)

    def test_prepare_idempotency_and_interrupted_preparation_resume(self):
        package=self.package([])
        first=installer.prepare(package);second=installer.prepare(package)
        self.assertEqual(first['record'],second['record'])
        record=Path(first['record']);(record/'receipt.json').unlink()
        resumed=installer.prepare(package)
        self.assertEqual(resumed['record'],first['record'])
        self.assertEqual(len(list(record.glob('candidate.incomplete.*'))),1)

    def test_rollback_restores_catalog_and_overwritten_edition_bytes(self):
        first=Path(installer.prepare(self.package(['AAA'],1))['record']);installer.activate(first);self.finish(first)
        before={p.relative_to(self.web).as_posix():p.read_bytes() for p in self.web.rglob('*') if p.is_file()}
        second=Path(installer.prepare(self.package(['AAA','BBB'],2))['record']);installer.activate(second);installer.rollback(second)
        after={p.relative_to(self.web).as_posix():p.read_bytes() for p in self.web.rglob('*') if p.is_file()}
        self.assertEqual(before,after)

    def test_failed_browser_rolls_back_real_transaction(self):
        tx=self.root/'tx';tx.mkdir()
        prepared=installer.prepare(self.package([]));write(tx/'production-stage.json',prepared)
        with patch.object(rolling.subprocess,'run',side_effect=RuntimeError('browser failed')):
            with self.assertRaisesRegex(RuntimeError,'browser failed'):rolling._resume_or_publish(tx,self.root,'node',None)
        self.assertEqual(read(Path(prepared['record'])/'receipt.json')['status'],'rolled_back')
        self.assertEqual(self.before,{name:(self.web/name).read_bytes() for name in self.generated})

    def test_three_rolledback_attempts_stop_before_fourth_publication(self):
        edition=self.root/'edition'
        with patch.object(rolling.subprocess,'check_output',return_value=COMMIT),patch.object(rolling.subprocess,'run',side_effect=RuntimeError('browser failed')):
            for _ in range(3):
                with self.assertRaisesRegex(RuntimeError,'browser failed'):rolling.publish_available(edition,DATE,'production',repo=self.root)
            with self.assertRaisesRegex(ValueError,'Three publication attempts'):rolling.publish_available(edition,DATE,'production',repo=self.root)
        self.assertEqual(len(list(self.state.glob('smn-production-*'))),3)

if __name__=='__main__':unittest.main()
