"""Exercise the real recap template and exact pre-POST media/body evidence."""
from datetime import date
import hashlib,json
from html.parser import HTMLParser
import unittest
from unittest.mock import patch
from tests.test_newsletter_safe_retry import NewsletterSafeRetryTests


class Images(HTMLParser):
    def __init__(self):super().__init__();self.images=[]
    def handle_starttag(self,tag,attrs):
        if tag=='img':self.images.append(dict(attrs))


class NewsletterRenderedMediaTests(NewsletterSafeRetryTests):
    def setUp(self):
        super().setUp()
        self.sender.config.smn_favicon='https://seasonalmarketnews.com/favicon.png'
        self.recap=[]
        for index,symbol in enumerate(['GS','PGR','C','XLK','MRK','SPX','OZK','ASML',
                                       'MSFT','NVDA','AMZN','QQQ','SPY','GC','WFC','CSX']):
            self.recap.append({'slug':'recap-'+symbol,'symbol':symbol,'title':symbol+' market analysis',
                'url':'https://seasonalmarketnews.com/editions/2026-10-16/'+symbol+'/article.html',
                'published_date':'2026-10-16','hero_status':'unavailable' if index<5 else 'available',
                'hero_image':None if index<5 else 'https://seasonalmarketnews.com/assets/hero-'+symbol+'.jpg'})
        self.sender.get_email_groups.side_effect=None
        self.sender.get_email_groups.return_value={'SMN-WEEKLY':'weekly-group'}
        self.sender.create_campaign.side_effect=None
        self.sender.create_campaign.return_value=('123','created')
        self.stack.enter_context(patch.object(self.sender,'_schedule_campaign_explicit',
            return_value={'data':{'id':'123','status':'ready'}}))

    def weekly(self):
        with patch.object(self.sender,'_today',return_value=date(2026,10,18)), \
                patch.object(self.sender,'_filter_weekly',return_value=self.recap), \
                patch.object(self.sender,'_generate_weekly_narrative',return_value=(
                    'Weekly market analysis','A selection of recently published market analysis.')):
            self.sender.weekly_send()

    def test_actual_recap_render_omits_five_unavailable_heroes_and_keeps_eleven_https_images(self):
        self.weekly()
        submitted=self.sender.create_campaign.call_args.args[-1]
        parser=Images();parser.feed(submitted)
        heroes=[row for row in parser.images if row.get('alt','').endswith(' seasonal pattern')]
        self.assertEqual(len(heroes),11)
        self.assertEqual([row['alt'].split()[0] for row in heroes],[row['symbol'] for row in self.recap[5:]])
        self.assertTrue(all(row['src'].startswith('https://seasonalmarketnews.com/') for row in heroes))
        for row in self.recap[:5]:
            self.assertIn('>'+row['symbol']+'</span></div>',submitted)
        record=json.loads(self.sender.STATE_FILE.read_text())['campaigns']['weekly:2026-10-18']
        self.assertEqual(record['submitted_html'],submitted)
        self.assertEqual(record['submitted_html_sha256'],hashlib.sha256(submitted.encode()).hexdigest())
        self.assertEqual([row['symbol'] for row in record['media_warnings']],['GS','PGR','C','XLK','MRK'])
        self.assertEqual(len(record['submitted_media']),16)
        saved=self.sender.STATE_FILE.read_bytes()
        self.weekly()
        self.assertEqual(self.sender.STATE_FILE.read_bytes(),saved)
        self.sender.create_campaign.assert_called_once()

    def test_exact_rendered_body_is_journaled_before_an_uncertain_creation_and_not_resent(self):
        def uncertain(*args):
            record=json.loads(self.sender.STATE_FILE.read_text())['campaigns']['weekly:2026-10-18']
            self.assertEqual(record['phase'],'create_unknown')
            self.assertEqual(record['submitted_html'],args[-1])
            raise RuntimeError('Provider outcome unknown')
        self.sender.create_campaign.side_effect=uncertain
        with self.assertRaisesRegex(RuntimeError,'outcome unknown'):self.weekly()
        saved=self.sender.STATE_FILE.read_bytes()
        self.weekly()
        self.assertEqual(self.sender.STATE_FILE.read_bytes(),saved)
        self.sender.create_campaign.assert_called_once()

    def test_all_available_real_render_has_no_media_warning_and_does_not_claim_delivery(self):
        for row in self.recap:
            row.update(hero_status='available',hero_image='https://seasonalmarketnews.com/assets/'+row['symbol']+'.jpg')
        self.weekly()
        record=json.loads(self.sender.STATE_FILE.read_text())['campaigns']['weekly:2026-10-18']
        parser=Images();parser.feed(record['submitted_html'])
        self.assertEqual(len([row for row in parser.images if row.get('alt','').endswith(' seasonal pattern')]),16)
        self.assertEqual(record['media_warnings'],[])
        self.assertEqual(record['phase'],'scheduled')
        self.assertNotIn('provider_counts',record)


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(NewsletterRenderedMediaTests(name) for name in
        NewsletterRenderedMediaTests.__dict__ if name.startswith('test_'))


if __name__=='__main__':unittest.main()
