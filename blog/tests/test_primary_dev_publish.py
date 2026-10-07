"""Publication transaction tests with temporary roots; never touch a server site."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import install_smn_primary_edition as p

class PrimaryPublicationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.web,self.state,self.dash,self.blog = [root/n for n in ('web','state','dash','blog')]
        for x in (self.web,self.state,self.dash,self.blog): x.mkdir()
        self.patches = [patch.object(p,k,v) for k,v in [('WEB',self.web),('STATE',self.state),('DASH',self.dash),('BLOG',self.blog)]]
        self.patches += [patch.object(p,'guard',lambda:None),patch.object(p,'render',self.render)]
        for x in self.patches:x.start()
        for name in ('rebuild_news_home.py','pin_store.py','article_index.py'):(self.blog/name).write_text('unchanged')
        (self.web/'old.html').write_text('approved original')
        (self.web/'hero.jpg').write_bytes(b'old hero')
        p.write(self.web/'posts.json',[{'url':p.ORIGIN+'/old.html','title':'old','published_date':'2026-09-23','custom_dashboard':'retain','hero_image':p.ORIGIN+'/hero.jpg'}])
        (self.web/'index.html').write_text('original home')
        (self.web/'search.html').write_text('<html><head><meta content="index, follow" name="robots"></head><body>native search</body></html>')
        p.write(self.dash/'pins.json',{'pins':[{'slug':'old','position':1}]})
        self.package=root/'package';self.package.mkdir()
        entries=[];files={}
        for symbol in 'ABCDEF':
            rel='editions/2026-09-24/'+symbol+'/article.html'
            file=self.package/rel;file.parent.mkdir(parents=True);file.write_text(symbol)
            files[rel]=p.sha(file)
            entries.append({'url':p.ORIGIN+'/'+rel,'source_commit':'a'*40,'symbol':symbol,'edition_id':'2026-09-24','published_date':'2026-09-24','title':symbol})
        p.write(self.package/'entries.json',entries)
        files['entries.json']=p.sha(self.package/'entries.json')
        p.write(self.package/'manifest.json',{'target_origin':p.ORIGIN,'production_allowed':False,'editorial_gate_version':1,'source_commit':'a'*40,'edition_date':'2026-09-24','files':files})
    def tearDown(self):
        for x in reversed(self.patches):x.stop()
        self.tmp.cleanup()
    def render(self,candidate):
        (candidate/'index.html').write_text('<html><head><meta name="robots" content="index, follow"></head><body>new home</body></html>')
        p.write(candidate/'suggest.json',[])
        p.write(candidate/'search_index.json',[])
    def prepared(self):
        return Path(p.prepare(self.package)['record'])
    def test_native_partial_notice_names_pending_without_relabeling_archive(self):
        entries = p.read(self.package/'entries.json')[:5]
        p.write(self.package/'entries.json', entries)
        manifest = p.read(self.package/'manifest.json')
        manifest['files'].pop('editions/2026-09-24/F/article.html')
        manifest['files']['entries.json'] = p.sha(self.package/'entries.json')
        manifest.update(continuity_policy=1, publication_policy='continuity-v1',
                        expected_symbols=list('ABCDEF'), published_symbols=list('ABCDE'),
                        pending_symbols=['F'], complete=False, coverage_status='partial',
                        revision=1, revision_id='b'*64, transaction_id='c'*64,
                        selection_status='frozen', selection_sha256='d'*64)
        p.write(self.package/'manifest.json', manifest)
        record = self.prepared()
        notice = (record/'candidate/index.html').read_text()
        self.assertIn('5 of 6 articles available', notice)
        self.assertIn('Pending: F.', notice)
        self.assertEqual(p.read(record/'candidate/coverage-status.json')['pending_symbols'], ['F'])
        self.assertFalse((record/'candidate/editions/2026-09-24/F/article.html').exists())
        archived = next(row for row in p.read(record/'candidate/posts.json') if row['title']=='old')
        self.assertEqual(archived['published_date'], '2026-09-23')
        self.assertEqual((self.web/'old.html').read_text(), 'approved original')
    def test_index_and_search_noindex_preserve_native_content(self):
        record=self.prepared()
        for name in ('index.html','search.html'):
            text=(record/'candidate'/name).read_text()
            self.assertIn('content="noindex,nofollow"',text)
            self.assertNotIn('index, follow',text)
        self.assertIn('native search',(record/'candidate/search.html').read_text())
        p.activate(record)
        if os.name != 'nt':  # Windows reports ACL-backed writable files as 0666.
            self.assertEqual((self.web/'posts.json').stat().st_mode & 0o777,0o644)
        p.rollback(record)
        self.assertIn('index, follow',(self.web/'search.html').read_text())
    def test_merge_preserves_arbitrary_dashboard_metadata(self):
        record=self.prepared();posts=p.read(record/'candidate/posts.json')
        self.assertEqual(len(posts),7)
        self.assertEqual(next(x for x in posts if x['title']=='old')['custom_dashboard'],'retain')
        self.assertEqual((self.web/'index.html').read_text(),'original home')
    def test_activation_and_rollback_preserve_archive_and_pins(self):
        record=self.prepared();pins=p.sha(self.dash/'pins.json')
        p.activate(record)
        self.assertEqual(len(p.read(self.web/'posts.json')),7)
        p.rollback(record)
        self.assertEqual(len(p.read(self.web/'posts.json')),1)
        self.assertEqual(p.sha(self.dash/'pins.json'),pins)
        self.assertEqual((self.web/'old.html').read_text(),'approved original')
        self.assertFalse((self.state/'dev-activation.lock').exists())
    def test_pin_drift_blocks_without_writes(self):
        record=self.prepared();p.write(self.dash/'pins.json',{'pins':[]})
        with self.assertRaisesRegex(ValueError,'pins changed'):p.activate(record)
        self.assertEqual(len(p.read(self.web/'posts.json')),1)
    def test_finish_requires_every_retained_public_hash(self):
        record=self.prepared();r=p.activate(record)
        p.write(record/'live-verification.json',{'passed':True,'source_commit':'a'*40,'public_files':[]})
        with self.assertRaisesRegex(ValueError,'verification missing'):p.finish(record)
        # New files verified but no retained sample: still refused.
        p.write(record/'live-verification.json',{'passed':True,'source_commit':'a'*40,'public_files':[{'rel':k,'sha256':v,'passed':True} for k,v in r['files'].items()]})
        if r['retained_articles'] or r['retained_heroes']:
            with self.assertRaisesRegex(ValueError,'verification missing'):p.finish(record)
        p.write(record/'live-verification.json',{'passed':True,'source_commit':'a'*40,'public_files':[{'rel':k,'sha256':v,'passed':True} for k,v in {**r['files'],**r['retained_articles'],**r['retained_heroes']}.items()]})
        self.assertEqual(p.finish(record)['status'],'live_verified')
    def test_rollback_refuses_to_overwrite_peer_edit(self):
        record=self.prepared();p.activate(record)
        (self.web/'index.html').write_text('peer update')
        with self.assertRaisesRegex(ValueError,'Peer changed'):p.rollback(record)
        self.assertEqual((self.web/'index.html').read_text(),'peer update')

if __name__=='__main__':unittest.main()
