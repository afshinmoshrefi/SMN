import json,sys,tempfile,unittest
from pathlib import Path
from copy import deepcopy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from subscription_publication import CHECKS,digest_bytes,require_dev,reviewed,write
from visual_evidence import digest,format_value
from seasonal_edition import _measurement_text

class PublicationBoundaries(unittest.TestCase):
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
