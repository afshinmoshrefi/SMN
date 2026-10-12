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
import home_quote_contract as quotes
from subscription_publication import write,read

DATE='2026-10-06'
COMMIT='a'*40
ORIGIN='https://seasonalmarketnews.com'

class LocalProductionTransactionTests(unittest.TestCase):
    def setUp(self):
        modules=patch.dict(sys.modules);modules.start();self.addCleanup(modules.stop)
        for name in ('rebuild_news_home','pin_store','article_index','seo_helpers'):
            sys.modules.pop(name,None)
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
        if receipt.get('home_quote_contract'):
            row=next(p for p in public if p['rel']=='index.html')
            row.update(sha256=installer.sha(self.web/'index.html'),public_sha256=installer.sha(self.web/'index.html'),home_quote_contract=quotes.contract((self.web/'index.html').read_bytes()))
        write(record/'live-verification.json',{'passed':True,'source_commit':COMMIT,'public_files':public})
        return installer.finish(record)

    def with_market_quotes(self):
        from tests.test_home_quote_contract import BAR
        (self.web/'index.html').write_text('<html><body>'+BAR+'Old archive</body></html>')
        (self.blog/'update_news_quotes.py').write_text('Known fixture quote writer')
        renderer=installer.render.side_effect
        def render(candidate):
            renderer(candidate)
            page=(candidate/'index.html').read_text().replace('<body>','<body>'+BAR)
            (candidate/'index.html').write_text(page)
        return patch.object(installer,'render',side_effect=render)

    def test_quote_refresh_before_activation_and_before_finish(self):
        with self.with_market_quotes():
            prepared=installer.prepare(self.package(['AAA']))
            (self.web/'index.html').write_bytes((self.web/'index.html').read_bytes().replace(b'100.25',b'102.50'))
            record=Path(prepared['record']);installer.activate(record)
            (self.web/'index.html').write_bytes((self.web/'index.html').read_bytes().replace(b'100.25',b'103.50'))
            finished=self.finish(record)
        self.assertTrue(finished['production_written'])
        self.assertEqual(finished['status'],'live_verified')
        self.assertNotEqual(finished['files']['index.html'],finished['verified_home_quotes']['origin']['observed_sha256'])

    def test_quote_refresh_rollback_keeps_quote_and_archive(self):
        with self.with_market_quotes():
            backup=(self.web/'index.html').read_bytes()
            record=Path(installer.prepare(self.package(['AAA']))['record']);installer.activate(record)
            (self.web/'index.html').write_bytes((self.web/'index.html').read_bytes().replace(b'100.25',b'105.00'))
            installer.rollback(record)
        self.assertEqual((self.web/'index.html').read_bytes(),backup.replace(b'100.25',b'105.00'))
        self.assertEqual((self.web/'old.html').read_text(),'Retained exact article')

    def test_peer_homepage_edit_blocks_finish_and_rollback(self):
        with self.with_market_quotes():
            record=Path(installer.prepare(self.package(['AAA']))['record']);installer.activate(record)
            (self.web/'index.html').write_bytes((self.web/'index.html').read_bytes().replace(b'Fixture renderer',b'Peer changed content'))
            with self.assertRaisesRegex(ValueError,'quote contract'):self.finish(record)
            with self.assertRaisesRegex(ValueError,'content or quote markup'):installer.rollback(record)

    def test_quote_writer_drift_blocks_relaxed_home_validation(self):
        with self.with_market_quotes():
            record=Path(installer.prepare(self.package(['AAA']))['record'])
            (self.blog/'update_news_quotes.py').write_text('Unrecognized writer')
            with self.assertRaisesRegex(ValueError,'quote writer changed'):installer.activate(record)

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

    def test_verified_native_receipt_resumes_without_canonical_receipt(self):
        """A crash between native finish and canonical receipt must not republish."""
        tx=self.root/'verified-tx';tx.mkdir()
        prepared=installer.prepare(self.package(['AAA']))
        write(tx/'production-stage.json',prepared)
        record=Path(prepared['record'])
        installer.activate(record)
        finished=self.finish(record)
        self.assertTrue(finished['production_written'])
        self.assertTrue(read(record/'receipt.json')['production_written'])
        with patch.object(rolling.subprocess,'run') as browser,patch.object(installer,'activate') as activate:
            resumed=rolling._resume_or_publish(tx,self.root,'node',None)
        browser.assert_not_called();activate.assert_not_called()
        self.assertEqual(resumed['status'],'live_verified')
        self.assertEqual(resumed['transaction_id'],finished['transaction_id'])
        self.assertFalse(resumed['complete'])

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

    def test_conflicting_preloaded_renderer_fails_before_transaction(self):
        foreign=types.SimpleNamespace(__file__=str(self.root/'foreign/rebuild_news_home.py'))
        with patch.dict(sys.modules,{'rebuild_news_home':foreign}):
            with self.assertRaisesRegex(ValueError,'Conflicting production native renderer'):
                installer.prepare(self.package([]))
        self.assertEqual(list(self.state.iterdir()),[])

    def test_conflicting_preloaded_renderer_helper_fails_before_transaction(self):
        for name in ('pin_store','article_index','seo_helpers'):
            foreign=types.SimpleNamespace(__file__=str(self.root/'foreign'/(name+'.py')))
            with patch.dict(sys.modules,{name:foreign}):
                with self.assertRaisesRegex(ValueError,'Conflicting production native renderer'):
                    installer.prepare(self.package([],revision=len(list(self.root.glob('package-*')))+1))
        self.assertEqual(list(self.state.iterdir()),[])

    def test_immutable_renderer_hash_drift_blocks_activation(self):
        for name in ('pin_store.py','article_index.py','seo_helpers.py'):(self.blog/name).write_text('Fixture helper')
        with patch.object(installer,'__file__',str(self.blog/'install_smn_primary_edition.py')):
            prepared=installer.prepare(self.package([]))
            self.assertEqual(prepared['runtime_blog'],str(self.blog))
            (self.blog/'rebuild_news_home.py').write_text('Peer changed renderer')
            with self.assertRaisesRegex(ValueError,'renderer changed'):
                installer.activate(Path(prepared['record']))

    def test_cold_render_uses_explicit_runtime_file_without_publisher_preload(self):
        renderer=self.blog/'rebuild_news_home.py'
        renderer.write_text('''def build_home():
 INDEX_HTML.write_text('<html><head></head><body>Bound fixture renderer</body></html>')
 SUGGEST_JSON.write_text('[]')
''')
        candidate=self.root/'cold';candidate.mkdir();write(candidate/'posts.json',[])
        write(self.web/'search_index.json',[])
        # Call the actual render implementation, with no cached home or publisher dependency.
        source=Path(installer.__file__).read_text()
        import ast
        tree=ast.parse(source);node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='render')
        scope=dict(installer.__dict__);scope['RUNTIME_BLOG']=self.blog
        exec(compile(ast.Module(body=[node],type_ignores=[]),installer.__file__,'exec'),scope)
        with patch.dict(sys.modules):
            sys.modules.pop('rebuild_news_home',None)
            sys.modules.pop('publish_article',None)
            scope['render'](candidate)
        self.assertIn('Bound fixture renderer',(candidate/'index.html').read_text())
        self.assertEqual(read(candidate/'suggest.json'),[])

if __name__=='__main__':unittest.main()
