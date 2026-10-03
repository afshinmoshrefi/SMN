import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import article_index
import membership_publication as publication
import membership_pipeline as pipeline
import install_smn_primary_edition as installer
from article_content_store import ContentError
from visual_evidence import digest


def derivative_fixture():
    from test_public_derivative import DerivativeTests
    fixture = DerivativeTests(); fixture.setUp()
    return fixture


class PrivatePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.web = self.root/'web'; self.web.mkdir()
        self.private = self.root/'private'
        for name, value in [('NEWS_ROOT',self.web),('POSTS_JSON',self.web/'posts.json'),
                            ('LOCK_FILE',self.root/'posts.lock'),('BACKUP_DIR',self.root/'backups')]:
            p = patch.object(article_index,name,value); p.start(); self.addCleanup(p.stop)
        env = patch.dict(os.environ,SMN_READER_PRIVATE_ROOT=str(self.private));env.start();self.addCleanup(env.stop)
        article_index.save_posts([],backup=False)
        publication.store().set_enabled(True)
        self.post = {'url':'https://smn-dev.trxstat.com/editions/2026-10-03/TEST/article.html',
                     'slug':'test-subscription-2026-10-03','title':'Company faces pressure','symbol':'TEST'}
        self.raw = '<html><head><title>Company faces pressure</title></head><body><main><h1>Company faces pressure</h1><section data-role="opening"><p>The exact public opening describes a historical limitation.</p></section><p>PROTECTED_BODY</p><img src="/editions/2026-10-03/TEST/assets/native.png"></main></body></html>'
        self.package = self.root/'package'; self.package.mkdir()
        relative = self.post['url'].split('trxstat.com/')[1]
        f = self.package/relative; f.parent.mkdir(parents=True);f.write_text(self.raw)
        native = f.parent/'assets/native.png';native.parent.mkdir();native.write_bytes(b'NATIVE_PRIVATE')
        self.manifest = {'edition_date':'2026-10-03','files':{relative:publication.sha(f.read_bytes()),
                        native.relative_to(self.package).as_posix():publication.sha(native.read_bytes())}}
        self.record = self.root/'edition-test'; self.record.mkdir()

    def batch(self):
        with patch.object(pipeline,'retain_capsule',return_value={self.post['slug']:{'article':'unused','review':'unused'}}):
            return pipeline.prepare_batch([self.post],self.package,self.record,self.manifest)

    def test_private_first_activation_and_rollback_never_install_public_body(self):
        batch = self.batch();new = pipeline.read(batch['journal'])['new_posts'][0]
        self.assertFalse((self.web/'editions').exists())
        with self.assertRaises(ContentError):publication.store().resolve(batch['new'][0]['canonical'])
        pipeline.activate_batch(batch);pipeline.activate_batch(batch)
        article_index.save_posts([new]);pipeline.verify_private(batch)
        full = publication.store().read_revision(batch['new'][0]['canonical'],'full')
        self.assertIn('PROTECTED_BODY',full);self.assertIn('/member/assets?',full)
        self.assertNotIn('PROTECTED_BODY',str(publication.store().read_revision(batch['new'][0]['canonical'])))
        pipeline.rollback_batch(batch);pipeline.rollback_batch(batch)
        self.assertTrue(Path(new['path']).is_file());self.assertFalse((self.web/'editions').exists())
        with self.assertRaises(ContentError):publication.store().resolve(batch['new'][0]['canonical'])

    def test_peer_private_revision_blocks_rollback(self):
        batch=self.batch();pipeline.activate_batch(batch)
        new= pipeline.read(batch['journal'])['new_posts'][0]
        previous=publication.store().resolve(publication.path_for(new))
        newer, manifest=publication.prepare(new,self.raw.replace('PROTECTED_BODY','PEER_BODY'),previous=previous,source_root=self.package)
        publication.store().activate_revision(publication.path_for(new),manifest['revision'],'peer',previous['revision'])
        with self.assertRaisesRegex(ContentError,'Peer changed'):pipeline.rollback_batch(batch)
        self.assertIn('PEER_BODY',publication.store().read_revision(publication.path_for(new),'full'))

    def test_interrupted_batch_is_durable_and_can_finish_or_rollback(self):
        batch=self.batch();original=publication.store().activate_revision
        def crash(*args,**kwargs):
            result=original(*args,**kwargs);raise RuntimeError('crash after private activation')
        with patch('article_content_store.ContentStore.activate_revision',side_effect=crash):
            with self.assertRaises(RuntimeError):pipeline.activate_batch(batch)
        self.assertEqual(pipeline.read(batch['journal'])['state'],'activating')
        pipeline.activate_batch(batch);self.assertEqual(pipeline.read(batch['journal'])['state'],'active')
        pipeline.rollback_batch(batch)

    def test_finish_requires_public_and_member_and_raw_denial_proofs(self):
        batch=self.batch();row=batch['new'][0]
        proof={'membership_articles':[dict(row,public_preview_passed=True,member_full_passed=True,anonymous_full_absent=True,
            assets=[dict(a,member_passed=True,anonymous_denied=not a['public'],public_passed=a['public']) for a in row['assets']],
            raw_assets=[{'url':u,'denied':True} for u in row['raw_asset_urls']])]}
        installer.verify_membership_proof(batch,proof)
        proof['membership_articles'][0]['raw_assets']=[]
        with self.assertRaisesRegex(ValueError,'raw engine'):installer.verify_membership_proof(batch,proof)
        proof['membership_articles'][0]['member_full_passed']=False
        with self.assertRaisesRegex(ValueError,'verification missing'):installer.verify_membership_proof(batch,proof)

    def test_capsule_is_private_and_rejects_tampered_review(self):
        fixture=derivative_fixture();self.addCleanup(fixture.doCleanups)
        root=self.root/'writer';article=root/'results/TEST';article.parent.mkdir(parents=True)
        import shutil
        shutil.copytree(fixture.article_dir,article)
        job=root/'jobs/TEST-20261003-review';job.parent.mkdir();shutil.copytree(fixture.job,job)
        (article/'article.html').write_text(self.raw)
        pipeline.write(article/'visual-checks.json',{'passed':True,'article_html_sha256':publication.sha(self.raw.encode())})
        pipeline.write(root/'daily-state.json',{'date':'2026-10-03'})
        sources, files=pipeline.capture_sources(root,self.package,'2026-10-03',{'TEST':'review'})
        self.manifest.update(membership_sources=sources,private_files=files)
        with patch('subscription_publication.reviewed',return_value={'title':self.post['title']}):
            retained=pipeline.retain_capsule(self.package,self.record,self.manifest,[self.post])
        self.assertIn(str(self.private),retained[self.post['slug']]['article'])
        output=next(self.package/f for f in files if f.endswith('/output.json'))
        output.write_text('{}')
        with self.assertRaisesRegex(ContentError,'capsule changed'):pipeline.retain_capsule(self.package,self.record,self.manifest,[self.post])

    def test_qualified_registration_is_bound_and_queue_idempotent(self):
        fixture=derivative_fixture();self.addCleanup(fixture.doCleanups)
        pipeline.write(fixture.article_dir/'seasonal-manifest.json',{'images':[{'variant':'bars_mae_mfe','url':'chart.png','sha256':publication.sha(b'CHART')}]})
        (fixture.article_dir/'chart.png').write_bytes(b'CHART')
        batch=self.batch();batch['new'][0]['source']={'article':str(fixture.article_dir),'review':str(fixture.job)}
        pipeline.activate_batch(batch)
        with patch('engine_seasonal.verify_assets',return_value=True):
            first=pipeline.register_sources(batch);second=pipeline.register_sources(batch)
        self.assertEqual(first['jobs'][0]['id'],second['jobs'][0]['id']);self.assertEqual(first['holds'],[])
        source=pipeline.read(self.private/'qualified-sources'/(publication.sha(self.post['slug'].encode())+'.json'))
        self.assertEqual(source['prepared']['provenance']['article_id'],self.post['url'])
        self.assertEqual(source['native_charts']['bars_mae_mfe']['sha256'],publication.sha(b'CHART'))
        self.assertNotIn('copy',source)
        self.assertEqual(pipeline.read(fixture.article_dir/'source.json')['card']['production_original'],fixture.url)

    def test_postprocessing_dataset_is_private_with_same_url(self):
        from types import SimpleNamespace
        config=SimpleNamespace(news_root_folder=str(self.web))
        with pipeline.private_processing(config) as stage:
            self.assertTrue(self.private in stage.parents)
            target=Path(config.news_root_folder)/'datasets/test.json';target.parent.mkdir();target.write_text('ENGINE')
            self.assertFalse((self.web/'datasets').exists())
        self.assertEqual(config.news_root_folder,str(self.web));self.assertFalse(target.exists())



class LegacyPublisherTests(unittest.TestCase):
    setUp = PrivatePipelineTests.setUp
    def publisher(self, name):
        # Isolate engine/network imports, but execute the actual publisher and
        # transaction adapter against real temporary catalog/SQLite storage.
        import ast
        from types import SimpleNamespace
        tree=ast.parse((Path(__file__).parents[1]/'publish_article.py').read_text())
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
        config=SimpleNamespace(news_root_folder=str(self.web))
        def process(**kwargs):
            stage=Path(config.news_root_folder)
            self.assertTrue(self.private in stage.parents)
            native=stage/'editions/2026-10-03/TEST/assets/native.png'
            native.parent.mkdir(parents=True);native.write_bytes(b'NATIVE_PRIVATE')
            data=stage/'datasets/engine.json';data.parent.mkdir();data.write_text('ENGINE_PRIVATE')
            return self.raw.replace('</main>', '<script>fetch("/datasets/engine.json")</script></main>')
        def home():
            for post in pipeline.read(self.web/'posts.json'):
                self.assertTrue(Path(post['path']).is_file())
                self.assertEqual(publication.store().resolve(publication.path_for(post))['revision'],post['membership_revision'])
            self.assertFalse((self.web/'editions').exists())
        namespace={'membership':publication,'membership_pipeline':pipeline,'Path':Path,'json':json,'config':config,
            'article_post_process':process,'_now_iso_utc':lambda:'2026-10-03T00:00:00Z',
            '_extract_dek_from_html':lambda raw:'The exact public opening describes a historical limitation.',
            'DEFAULT_PUBLISH_STATUS':'true','DEFAULT_TONE':'neutral','DEFAULT_WEBSITE_ID':0,
            'make_redis_key':lambda **kw:'key','save_article_to_redis':lambda *a,**kw:None,
            'delete_article_from_redis':lambda *a:None,'build_home':home,
            'upsert_search_index_entry':lambda root,entry:root/'search_index.json',
            'delete_search_index_entry':lambda *a:None,'_delete_article_images':lambda *a:None,
            '_safe_unlink':lambda path:self.fail('Private immutable revision must not be deleted')}
        for n in ('generate_sitemap','generate_news_sitemap','generate_rss_feed','generate_robots_txt','generate_llms_txt','notify_indexnow'):
            namespace[n]=lambda *a:None
        exec(compile(ast.Module(body=[function],type_ignores=[]),'actual-publisher','exec'),namespace)
        return namespace[name]

    def publish(self):
        info={'news_root':str(self.web),'out_path':str(self.web/'editions/2026-10-03/TEST/article.html'),
              'article_html':self.raw,'full_url':self.post['url'],'slug':self.post['slug'],'market_family':'US','title':self.post['title']}
        return self.publisher('write_article_and_register')(info,'1','TEST','2026-10-03',5,'10','long','author')

    def test_legacy_publisher_private_body_dataset_and_deletion(self):
        result=self.publish();posts=pipeline.read(self.web/'posts.json');post=posts[0]
        self.assertTrue(Path(result['file_path']).is_file());self.assertFalse((self.web/'datasets').exists())
        full=publication.store().read_revision(publication.path_for(post),'full')
        self.assertIn('PROTECTED_BODY',full);self.assertNotIn('/datasets/engine.json',full)
        self.publish();self.assertEqual(len(pipeline.read(self.web/'posts.json')),1)
        result=self.publisher('delete_article_web')('1','TEST','2026-10-03',5,'10','author',slug=self.post['slug'])
        self.assertTrue(result['removed']);self.assertEqual(pipeline.read(self.web/'posts.json'),[])
        self.assertTrue(Path(post['path']).is_file())
        with self.assertRaises(ContentError):publication.store().resolve(publication.path_for(post))

    def test_legacy_crash_recovery_finishes_exact_catalog_without_public_body(self):
        save=article_index.save_posts
        with patch.object(article_index,'save_posts',side_effect=RuntimeError('crash catalog')):
            with self.assertRaisesRegex(RuntimeError,'crash catalog'):self.publish()
        self.assertTrue(list((self.private/'publishing').glob('*.json')))
        self.assertFalse((self.web/'editions').exists())
        with article_index.posts_lock():publication.recover()
        post=pipeline.read(self.web/'posts.json')[0]
        self.assertEqual(publication.store().resolve(publication.path_for(post))['revision'],post['membership_revision'])
        self.assertFalse(list((self.private/'publishing').glob('*.json')))

    def test_corrupt_catalog_never_becomes_empty_archive(self):
        (self.web/'posts.json').write_text('CORRUPT')
        with self.assertRaises(json.JSONDecodeError):self.publish()
        self.assertEqual((self.web/'posts.json').read_text(),'CORRUPT')


class QueueIngressTests(unittest.TestCase):
    setUp = PrivatePipelineTests.setUp
    def test_generated_assets_stay_private_and_publisher_uses_same_attempt(self):
        import sys
        from types import SimpleNamespace
        config=SimpleNamespace(news_root_folder=str(self.web))
        with patch.dict(sys.modules,config=config), patch.dict(os.environ,SMN_NEWS_ROOT=str(self.web)):
            @pipeline.asset_generation
            def charts():
                target=Path(config.news_root_folder)/'editions/2026-10-03/TEST/assets/native.png'
                target.parent.mkdir(parents=True);target.write_bytes(b'EXACT_GENERATED_NATIVE')
                return target
            @pipeline.generation_scope
            def attempt():
                native=charts();self.assertTrue(self.private in native.parents)
                self.assertEqual(config.news_root_folder,str(self.web))
                with pipeline.private_processing(config) as stage:
                    pipeline.materialize_existing_assets(stage,self.raw,'',self.web)
                    self.assertEqual((stage/'editions/2026-10-03/TEST/assets/native.png').read_bytes(),native.read_bytes())
                    new,manifest=publication.prepare(self.post,self.raw,source_root=stage)
                with article_index.posts_lock():publication.commit_post([],None,new,manifest)
                return {'status':'success'}
            self.assertEqual(attempt()['status'],'success')
            self.assertFalse((self.web/'editions').exists())
            self.assertEqual(config.news_root_folder,str(self.web))
            records=list((self.private/'legacy-ingress').glob('*/generation.json'))
            self.assertEqual(len(records),1);self.assertEqual(pipeline.read(records[0])['status'],'success')
            full=publication.store().resolve(publication.path_for(self.post))
            asset=next(iter(full['assets']))
            self.assertEqual(publication.store().private_asset_path(publication.path_for(self.post),full['revision'],asset).read_bytes(),b'EXACT_GENERATED_NATIVE')

    def test_failed_generation_keeps_private_evidence_and_restores_config(self):
        import sys
        from types import SimpleNamespace
        config=SimpleNamespace(news_root_folder=str(self.web))
        with patch.dict(sys.modules,config=config), patch.dict(os.environ,SMN_NEWS_ROOT=str(self.web)):
            @pipeline.asset_generation
            def broken():
                path=Path(config.news_root_folder)/'engine.txt';path.write_text('EXACT_ENGINE')
                raise RuntimeError('provider failure')
            @pipeline.generation_scope
            def attempt():return broken()
            with self.assertRaises(RuntimeError):attempt()
            self.assertEqual(config.news_root_folder,str(self.web))
            self.assertEqual(pipeline.read(next((self.private/'legacy-ingress').glob('*/generation.json')))['status'],'failed')
            self.assertFalse((self.web/'engine.txt').exists())
            self.assertTrue(list((self.private/'legacy-ingress').glob('*/engine.txt')))



class GatedInstallerTests(unittest.TestCase):
    setUp = PrivatePipelineTests.setUp
    def installation(self):
        state,dash,blog=[self.root/n for n in ('state','dashboard','blog')]
        for path in (state,dash,blog):path.mkdir()
        for name in ('rebuild_news_home.py','pin_store.py','article_index.py','membership_publication.py',
                     'article_content_store.py','reader_app.py','membership_pipeline.py'):
            (blog/name).write_text('installed helper')
        (self.web/'index.html').write_text('<html><head></head><body>original home</body></html>')
        (self.web/'search.html').write_text('<html><head></head><body>native search</body></html>')
        self.post.update(source_commit='a'*40,edition_id='2026-10-03',published_date='2026-10-03')
        pipeline.write(self.package/'entries.json',[self.post])
        self.manifest.update(target_origin=installer.ORIGIN,production_allowed=False,editorial_gate_version=1,source_commit='a'*40)
        self.manifest['files']['entries.json']=publication.sha((self.package/'entries.json').read_bytes())
        pipeline.write(self.package/'manifest.json',self.manifest)
        def render(candidate):
            (candidate/'index.html').write_text('<html><head></head><body>new home</body></html>')
            pipeline.write(candidate/'suggest.json',[]);pipeline.write(candidate/'search_index.json',[])
        patches=[patch.object(installer,n,v) for n,v in [('WEB',self.web),('STATE',state),('DASH',dash),('BLOG',blog)]]
        patches += [patch.object(installer,'guard',lambda:None),patch.object(installer,'render',render),
                    patch.object(article_index,'LOCK_FILE',dash/'posts.lock'),
                    patch.object(pipeline,'retain_capsule',return_value={self.post['slug']:{'article':'unused','review':'unused'}}),
                    patch.object(pipeline,'register_sources',return_value={'jobs':[],'holds':[]})]
        for item in patches:item.start();self.addCleanup(item.stop)
        return Path(installer.prepare(self.package)['record'])

    def test_installer_activates_private_registry_before_catalog_and_only_public_metadata(self):
        record=self.installation();receipt=pipeline.read(record/'receipt.json')
        self.assertEqual(set(receipt['files']),set(installer.GENERATED))
        self.assertFalse((record/'candidate/editions').exists())
        atomic=installer.atomic
        def ordered(path,data):
            if path.name=='posts.json':
                manifest=publication.store().resolve(publication.path_for(self.post))
                self.assertEqual(manifest['revision'],receipt['membership_publication']['new'][0]['revision'])
            atomic(path,data)
        with patch.object(installer,'atomic',side_effect=ordered):installer.activate(record)
        post=pipeline.read(self.web/'posts.json')[0];self.assertTrue(Path(post['path']).is_file())
        self.assertFalse((self.web/'editions').exists())
        installer.rollback(record)
        self.assertEqual(pipeline.read(self.web/'posts.json'),[])
        self.assertTrue(Path(post['path']).is_file());self.assertFalse((self.web/'editions').exists())

    def test_failed_home_activation_rolls_back_private_pointers_and_catalog(self):
        record=self.installation();atomic=installer.atomic;failed=[False]
        def crash(path,data):
            if path.name=='index.html' and not failed[0]:failed[0]=True;raise RuntimeError('home activation crash')
            atomic(path,data)
        with patch.object(installer,'atomic',side_effect=crash):
            with self.assertRaisesRegex(RuntimeError,'home activation crash'):installer.activate(record)
        self.assertEqual(pipeline.read(record/'receipt.json')['status'],'rolled_back')
        self.assertEqual(pipeline.read(self.web/'posts.json'),[])
        with self.assertRaises(ContentError):publication.store().resolve(publication.path_for(self.post))
        self.assertFalse((self.web/'editions').exists())

    def test_membership_configuration_is_allowlisted_and_not_shell_evaluated(self):
        path=self.root/'membership.env'
        path.write_text('SMN_READER_PRIVATE_ROOT='+str(self.private)+'\nSMN_NEWS_ROOT='+str(self.web)+
                        '\nSMN_READER_ENV=dev\nSMN_DASHBOARD_STATE='+str(self.root/'state')+'\nSTRIPE_SECRET_KEY=SHOULD_NOT_LOAD\n')
        with patch.dict(os.environ,{},clear=False):
            before=os.environ.get('STRIPE_SECRET_KEY')
            pipeline.load_configuration(path)
            self.assertEqual(os.environ.get('STRIPE_SECRET_KEY'),before)
        path.write_text('SMN_READER_ENV=prod')
        with self.assertRaisesRegex(ContentError,'complete Dev'):pipeline.load_configuration(path)


if __name__=='__main__':unittest.main()
