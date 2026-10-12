from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import home_quote_contract as quotes

BAR='''<div class="market-bar"><div class="market-track"><span class="market-item up">SPX <span class="market-price">100.25</span><span class='market-change up'>+0.25%</span></span></div></div>'''
PAGE=('<html><body>'+BAR+'<p>Bound article and manual pin</p></body></html>').encode()

class HomeQuoteContractTests(unittest.TestCase):
    def rejected(self,original,changed):
        try:return not quotes.matches(quotes.contract(original),quotes.contract(changed))
        except ValueError as error:
            if not str(error).startswith(('Invalid quote','Expected one complete')):raise
            return True

    def test_normalization_sentinels_cannot_be_supplied_as_input(self):
        for changed in [PAGE.replace(b'100.25',b'PRICE'),PAGE.replace(b'market-item up',b'market-item DIRECTION'),PAGE.replace(b'market-change up',b'market-change DIRECTION'),PAGE.replace(b'+0.25%',b'CHANGE')]:
            self.assertTrue(self.rejected(PAGE,changed))
    def test_element_count_order_and_significant_link_whitespace_are_bound(self):
        price=b'<span class="market-price">100.25</span>'
        original=PAGE.replace(b'SPX ',b'<a href="/markets/sp500.html">SPX</a> ')
        for changed in [original.replace(price,b''),original.replace(price,price+price),original.replace(b'/markets/sp500.html',b'/markets/sp 500.html')]:
            self.assertTrue(self.rejected(original,changed))
        for bad in [b'--.',b'',b'1,00.25']:
            self.assertTrue(self.rejected(original,original.replace(b'100.25',bad)))
    def test_numeric_refresh_is_allowed_without_rebasing_content(self):
        changed=PAGE.replace(b'100.25',b'99.00').replace(b'+0.25%',b'-1.25%').replace(b'item up',b'item down').replace(b'change up',b'change down')
        self.assertNotEqual(quotes.contract(PAGE)['observed_sha256'],quotes.contract(changed)['observed_sha256'])
        self.assertTrue(quotes.matches(quotes.contract(PAGE),quotes.contract(changed)))

    def test_article_pin_symbol_link_and_active_markup_changes_fail(self):
        for old,new in [(b'Bound article',b'Changed article'),(b'manual pin',b'removed pin'),(b'SPX',b'FAKE'),(b'100.25',b'<script>alert(1)</script>'),(b'class="market-price"',b'class="market-price" onclick="alert(1)"')]:
            with self.subTest(new=new):
                self.assertTrue(self.rejected(PAGE,PAGE.replace(old,new)))

    def test_duplicate_or_missing_span_is_rejected(self):
        for page in [b'<html>missing</html>',PAGE+BAR.encode()]:
            with self.assertRaises(ValueError):quotes.contract(page)

    def test_rollback_keeps_current_quotes_and_restores_old_content(self):
        backup=PAGE.replace(b'Bound article',b'Before publication')
        current=PAGE.replace(b'100.25',b'104.00')
        restored=quotes.restore_with_current_quotes(backup,current,quotes.contract(PAGE))
        self.assertEqual(restored,backup.replace(b'100.25',b'104.00'))
        with self.assertRaises(ValueError):quotes.restore_with_current_quotes(backup,current.replace(b'Bound article',b'Peer edit'),quotes.contract(PAGE))

if __name__=='__main__':unittest.main()
