import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import install_smn_recovery_edition as deploy


class RecoveryGuards(unittest.TestCase):
    def package(self,root):
        date='2026-09-10';commit='a'*40;entries=[];files={}
        for sym in ['HRL','TRV','IBM','KDP','KMB','JNJ']:
            rel=f'editions/{date}/{sym}/article.html';path=root/rel;path.parent.mkdir(parents=True);path.write_text(sym)
            files[rel]=hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append({'symbol':sym,'url':deploy.ORIGIN+'/'+rel,'source_commit':commit,
                            'title':'Study '+sym,'dek':'Existing seasonal article','hero_image':'',
                            'published_date':date+'T07:00:00Z','market_family':'US','pattern_days':30})
        (root/'entries.json').write_text(json.dumps(entries))
        files['entries.json']=deploy.sha(root/'entries.json')
        m={'edition_date':date,'source_commit':commit,'target_origin':deploy.ORIGIN,'production_allowed':False,'files':files}
        (root/'manifest.json').write_text(json.dumps(m));return m

    def test_exact_dev_package_and_changed_asset(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.package(root)
            self.assertEqual(len(deploy.validate_package(root)[1]),6)
            (root/'editions/2026-09-10/HRL/article.html').write_text('substitution')
            with self.assertRaisesRegex(ValueError,'Changed'):deploy.validate_package(root)

    def test_production_origin_never_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);m=self.package(root);m['target_origin']='https://seasonalmarketnews.com'
            (root/'manifest.json').write_text(json.dumps(m))
            with self.assertRaisesRegex(ValueError,'Dev-only'):deploy.validate_package(root)

    def test_wrong_host_rejected_before_mutations(self):
        with patch.object(deploy.subprocess,'check_output',return_value='10.0.0.98'):
            with self.assertRaisesRegex(ValueError,'Exact temporary'):deploy.guard()

    def test_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);m=self.package(root);m['files']['../outside']='0'*64
            (root/'manifest.json').write_text(json.dumps(m))
            with self.assertRaisesRegex(ValueError,'unsafe'):deploy.validate_package(root)

    @unittest.skipIf(sys.platform=='win32','Recovery activation uses Linux symlinks')
    def test_mixed_nginx_line_endings_activate_and_rollback_unchanged(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as d, ExitStack() as stack:
            root=Path(d);base=root/'web';old=base/'previous';old.mkdir(parents=True)
            current=base/'current';current.symlink_to(old)
            nginx=root/'nginx';original=(
                b'server {\r\nserver_name smn-dev.trxstat.com;\n'
                b'root /var/www/smn-dev-recovery/current;\r\n'
                b'location = / { return 302 /editions/2026-09-08/; }\n}\r\n')
            nginx.write_bytes(original);state=root/'state';code=root/'code'
            for name,value in {'BASE':base,'CURRENT':current,'NGINX':nginx,'STATE':state,
                               'LOCK':state/'dev-activation.lock','CODE':code}.items():
                stack.enter_context(patch.object(deploy,name,value))
            stack.enter_context(patch.object(deploy.subprocess,'check_output',return_value='192.168.1.176'))
            commands=stack.enter_context(patch.object(deploy.subprocess,'run'))
            package=root/'package';package.mkdir();self.package(package)
            source=root/'source';source.mkdir()
            (source/'source-provenance.json').write_text(json.dumps({'source_commit':'a'*40,'files':{}}))
            record=Path(deploy.prepare(package,source))
            self.assertEqual((record/'nginx-before').read_bytes(),original)
            active=deploy.activate(record)
            self.assertEqual(str(current.resolve()),active['candidate_web'])
            self.assertIn(b'try_files /index.html =404;',nginx.read_bytes())
            self.assertEqual(len(deploy.read(current/'posts.json')),6)
            deploy.rollback(record)
            self.assertEqual(current.resolve(),old)
            self.assertEqual(nginx.read_bytes(),original)
            self.assertFalse(deploy.LOCK.exists())
            self.assertEqual(commands.call_count,4)

    def test_cumulative_catalog_keeps_old_articles_and_repeat_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            web=Path(d)
            def entry(day,symbol):
                p=web/'editions'/day/symbol/'article.html';p.parent.mkdir(parents=True);p.write_text('Approved '+symbol)
                return {'url':deploy.ORIGIN+'/'+p.relative_to(web).as_posix(),'symbol':symbol,
                        'title':'Study '+symbol,'dek':'Reader context','hero_image':'','published_date':day+'T07:00:00Z',
                        'market_family':'US','pattern_days':30,'direction':'long'}
            first=entry('2026-09-17','OLD');old=web/'editions/2026-09-17/OLD/article.html';expected=deploy.sha(old)
            deploy.build_home(web,[first],'2026-09-17','a'*40)
            second=entry('2026-09-21','NEW')
            proof=deploy.build_home(web,[second],'2026-09-21','b'*40)
            self.assertEqual([p['symbol'] for p in deploy.read(web/'posts.json')],['NEW','OLD'])
            self.assertEqual(deploy.sha(old),expected)
            self.assertIn(first['url'],proof['previous_article_urls'])
            self.assertIn('wire-lead',(web/'index.html').read_text())
            self.assertIn('search.html',(web/'index.html').read_text())
            deploy.build_home(web,[second],'2026-09-21','b'*40)
            self.assertEqual(len(deploy.read(web/'posts.json')),2)

    def test_missing_catalog_cannot_silently_discard_retained_articles(self):
        with tempfile.TemporaryDirectory() as d:
            web=Path(d);p=web/'editions/2026-09-17/OLD/article.html';p.parent.mkdir(parents=True);p.write_text('Approved')
            old={'url':deploy.ORIGIN+'/'+p.relative_to(web).as_posix(),'symbol':'OLD','title':'Earlier article',
                 'published_date':'2026-09-17T07:00:00Z','hero_image':''}
            with self.assertRaisesRegex(ValueError,'explicit archive bootstrap'):
                deploy.build_home(web,[],'2026-09-21','b'*40)
            seed={'entries':[old],'article_hashes':{old['url']:deploy.sha(p)}}
            (web/'editions/2026-09-21').mkdir(parents=True)
            deploy.build_home(web,[],'2026-09-21','b'*40,seed)
            self.assertEqual(len(deploy.read(web/'posts.json')),1)

    def test_home_route_is_repeatable_and_rejects_unknown_configuration(self):
        before='location = / { return 302 /editions/2026-09-17/; }'
        after=deploy.home_config(before)
        self.assertEqual(deploy.home_config(after),after)
        with self.assertRaises(ValueError):deploy.home_config('location = / { proxy_pass http://other; }')


if __name__=='__main__':unittest.main()
