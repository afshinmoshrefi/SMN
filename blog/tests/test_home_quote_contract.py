from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import home_quote_contract as quotes

BAR='''<div class="market-bar"><div class="market-track"><span class="market-item up">SPX <span class="market-price">100.25</span><span class='market-change up'>+0.25%</span></span></div></div>'''
PAGE=('<html><body>'+BAR+'<p>Bound article and manual pin</p></body></html>').encode()

class HomeQuoteContractTests(unittest.TestCase):
    def test_actual_oct14_cash_to_futures_refresh_and_rollback(self):
        fixtures=Path(__file__).parent/'fixtures/oct14-home-quotes'
        candidate=(fixtures/'candidate-home.html').read_bytes()
        current=(fixtures/'live-home.html').read_bytes()
        backup=(fixtures/'backup-home.html').read_bytes()
        self.assertTrue(quotes.matches(quotes.contract(candidate),quotes.contract(current)))
        restored=quotes.restore_with_current_quotes(backup,current,quotes.contract(candidate))
        self.assertEqual(quotes.split(restored)[1],quotes.split(current)[1])
        self.assertEqual(quotes.split(restored)[0]+quotes.split(restored)[2],quotes.split(backup)[0]+quotes.split(backup)[2])
    def test_legitimate_futures_marker_and_unavailable_flat_change(self):
        future=PAGE.replace(b'<span class="market-price">100.25</span>',b'<span class="market-price" style="color:#818cf8">104.00<sup style=\'font-size:9px;color:#6366f1;margin-left:1px\'>F</sup></span>')
        self.assertTrue(quotes.matches(quotes.contract(PAGE),quotes.contract(future)))
        flat=future.replace(b'market-item up',b'market-item flat').replace(b"<span class='market-change up'>+0.25%</span>",b'')
        self.assertTrue(quotes.matches(quotes.contract(PAGE),quotes.contract(flat)))
    def test_missing_nonflat_change_duplicate_change_and_marker_injection_fail(self):
        change=b"<span class='market-change up'>+0.25%</span>"
        for bad in [PAGE.replace(change,b''),PAGE.replace(change,change+change),PAGE.replace(b'market-change up',b'market-change down'),PAGE.replace(b'class="market-price"',b'class="market-price" style="color:#818cf8"'),PAGE.replace(b'100.25',b"100.25<sup style='font-size:9px;color:#6366f1;margin-left:1px'>X</sup>")]:
            self.assertTrue(self.rejected(PAGE,bad))
    def test_display_mode_does_not_authorize_other_markup(self):
        fixtures=Path(__file__).parent/'fixtures/oct14-home-quotes'
        candidate=(fixtures/'candidate-home.html').read_bytes();current=(fixtures/'live-home.html').read_bytes()
        for old,new in [(b'/markets/dow.html',b'/markets/fake.html'),(b'>DOW<',b'>FAKE<'),(b'class="market-price"',b'class="market-price" onclick="bad()"')]:
            self.assertTrue(self.rejected(candidate,current.replace(old,new)))
        prefix,bar,suffix=quotes.split(current)
        self.assertTrue(self.rejected(candidate,prefix+bar+suffix+b'<script>peer edit</script>'))
    def test_old_contract_version_is_not_silently_reinterpreted(self):
        current=quotes.contract(PAGE);old={**current,'version':2,'kind':'market-bar-numeric-quotes-v2'}
        self.assertFalse(quotes.matches(old,current))
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
