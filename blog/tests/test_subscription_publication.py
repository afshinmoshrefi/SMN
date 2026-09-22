import json,sys,tempfile,unittest
from pathlib import Path
from copy import deepcopy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from subscription_publication import CHECKS,digest_bytes,package,require_dev,reviewed,sha256_equal,write
from unittest.mock import patch
from visual_evidence import digest,format_value
from seasonal_edition import _measurement_text

class PublicationBoundaries(unittest.TestCase):
    def test_sha256_equality_accepts_case_only_variation_and_rejects_invalid_values(self):
        digest='a1'*32
        self.assertTrue(sha256_equal(digest,digest.upper()))
        self.assertTrue(sha256_equal(digest,digest[:16].upper()+digest[16:]))
        for value in (None,'a'*63,'g'*64,digest[:-1]+'2'):
            with self.subTest(value=value): self.assertFalse(sha256_equal(digest,value))
    def test_only_exact_dev_https_origin(self):
        require_dev('https://smn-dev.trxstat.com/editions/2026-09-08/')
        for url in ('https://seasonalmarketnews.com/','http://smn-dev.trxstat.com/',
                    'https://smn-dev.trxstat.com.evil.example/','https://user@smn-dev.trxstat.com/',
                    'https://smn-dev.trxstat.com/?redirect=https://seasonalmarketnews.com'):
            with self.subTest(url=url),self.assertRaises(ValueError):require_dev(url)

    def fixture(self,root):
        a={'title':'Reviewed article'};markup=b'<html>reviewed page</html>'
        r={'passed':True,'checks':{k:{'passed':True} for k in CHECKS},'issues':[]}
        write(root/'article.json',a);write(root/'mechanical-checks.json',{'passed':True,'article_sha256':digest(a)})
        write(root/'bundle.json',{'seasonal_contract':{}})
        write(root/'review.json',r);(root/'article.html').write_bytes(markup)
        write(root/'review-binding.json',{'article_sha256':digest(a),'review_sha256':digest_bytes((root/'review.json').read_bytes())})
        write(root/'visual-checks.json',{'passed':True,'article_html_sha256':digest_bytes(markup)})
        return a,r

    def test_review_cannot_transfer_to_changed_copy_or_changed_pixels(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);a,r=self.fixture(p);self.assertEqual(reviewed(p,p/'review.json'),a)
            write(p/'article.json',{'title':'Unreviewed replacement'})
            with self.assertRaisesRegex(ValueError,'Changed'):reviewed(p,p/'review.json')
            self.fixture(p);(p/'article.html').write_text('Changed rendered methodology')
            with self.assertRaisesRegex(ValueError,'Rendered-page'):reviewed(p,p/'review.json')

    def test_visual_proof_allows_uppercase_hash_but_rejects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);self.fixture(p)
            visual=json.loads((p/'visual-checks.json').read_text());visual['article_html_sha256']=visual['article_html_sha256'].upper();write(p/'visual-checks.json',visual)
            self.assertEqual(reviewed(p,p/'review.json')['title'],'Reviewed article')
            (p/'article.html').write_text('changed bytes')
            with self.assertRaisesRegex(ValueError,'Rendered-page'):reviewed(p,p/'review.json')

    def test_visual_screenshots_accept_uppercase_hash_but_reject_changed_bytes(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);self.fixture(p);(p/'shot.png').write_bytes(b'pixels')
            visual=json.loads((p/'visual-checks.json').read_text());visual['inspected_images']={'shot.png':digest_bytes(b'pixels').upper()};write(p/'visual-checks.json',visual)
            self.assertEqual(reviewed(p,p/'review.json')['title'],'Reviewed article')
            (p/'shot.png').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'screenshots'):reviewed(p,p/'review.json')

    def test_package_preflight_failure_creates_no_package(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            with self.assertRaisesRegex(ValueError,'Invalid symbol'):package(root,'2026-09-22','a'*40,{'bad-symbol':'review'})
            self.assertFalse((root/'publication-package').exists())

    def test_stage_record_prevents_package_rebuild_when_target_is_missing(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);write(root/'dev-stage.json',{})
            with self.assertRaisesRegex(ValueError,'Preserve prior'):package(root,'2026-09-22','a'*40,{})

    def test_empty_unrecorded_package_can_be_recovered_but_nonempty_is_preserved(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);target=root/'publication-package';target.mkdir()
            stages={}
            for n in range(6):
                sym='S%d'%n;stages[sym]='review';result=root/'results'/sym;result.mkdir(parents=True)
                write(result/'commission.json',{'history_status':'ok','production_article':{'symbol':sym,'tickers':[sym],'market_family':'US','pattern_start_date':'2026-01-01','pattern_days':5,'author_id':'author','direction':'long','lookback_years':'10','published_date':'2026-09-%02d'%(n+1),'url':'https://example.com'}})
                write(result/'bundle.json',{});write(result/'hero-asset.json',{'url':'hero.png','alt':'hero'})
                (result/'article.html').write_text('<meta name="robots" content="noindex,nofollow">')
                (result/'assets').mkdir();(result/'assets'/'hero.png').write_bytes(b'hero')
                (result/'evidence').mkdir();(result/'evidence'/'proof.json').write_text('{}')
            with patch('subscription_publication.reviewed',return_value={'title':'Reviewed','dek':'Dek'}):
                package(root,'2026-09-22','a'*40,stages)
            self.assertTrue(target.is_dir())
            self.assertEqual((target/'editions'/'2026-09-22'/'S0'/'assets'/'hero.png').read_bytes(),b'hero')
            self.assertTrue((target/'editions'/'2026-09-22'/'S0'/'evidence'/'proof.json').is_file())
            (target/'immutable').write_text('keep')
            with self.assertRaisesRegex(ValueError,'Preserve prior'):package(root,'2026-09-22','a'*40,{})

    def test_major_issue_overrides_top_level_pass(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);a,r=self.fixture(p)
            r['issues']=[{'severity':'major','problem':'Wrong period'}];write(p/'review.json',r)
            with self.assertRaisesRegex(ValueError,'Independent review'):reviewed(p,p/'review.json')

    def test_added_engine_path_rejects_legacy_unverified_source(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);self.fixture(p)
            write(p/'seasonal-manifest.json',{'card':{'story_cell':{}},'price_path':{}})
            write(p/'bundle.json',{'seasonal_contract':{'price_path_required':True}})
            with self.assertRaisesRegex(ValueError,'engine export required'):reviewed(p,p/'review.json')

    def test_known_measurements_never_mislabel_fund_weights_or_index_returns(self):
        self.assertEqual(format_value(14.92,'weight_percent'),'14.92%')
        self.assertEqual(format_value(14.92,'percent'),'+14.92%')
        def data(m):return {'card':{'instrument':{'semantics':{'measurement':m}}}}
        p=_measurement_text(data('price_index_change'))
        self.assertIn('excluding dividends',p[0]);self.assertNotIn('adjusted',p[1])
        q=_measurement_text(data('provider_reference_price_change'))
        self.assertIn('not been independently reconciled',q[2]);self.assertIn('not futures-account returns',q[2])
        with self.assertRaises(ValueError):_measurement_text(data('unknown'))

if __name__=='__main__':unittest.main()
