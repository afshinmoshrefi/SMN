from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import home_quote_contract as quotes

BAR='''<div class="market-bar"><div class="market-track"><a href="/markets/sp500.html" class="market-item up">SPX <span class="market-price">100.25</span><span class='market-change up'>+0.25%</span></a></div></div>'''
PAGE=('<html><body>'+BAR+'<p>Bound article and manual pin</p></body></html>').encode()

class HomeQuoteContractTests(unittest.TestCase):
    def test_actual_quote_producer_empty_futures_payload_does_not_hold_content(self):
        fixtures=Path(__file__).parent/'fixtures/quote-unavailable'
        normal=(fixtures/'normal-futures.html').read_bytes();empty=(fixtures/'empty-futures.html').read_bytes()
        self.assertTrue(quotes.matches(quotes.contract(normal),quotes.contract(empty)))
        backup=normal.replace(b'Bound edition',b'Before publication')
        restored=quotes.restore_with_current_quotes(backup,empty,quotes.contract(normal))
        self.assertEqual(quotes.split(restored)[1],quotes.split(empty)[1])
        self.assertIn(b'Before publication',restored)
        self.assertTrue(self.rejected(normal,empty.replace(b'Bound edition',b'Peer edit')))

    def test_unavailable_price_is_only_allowed_for_a_flat_item_without_change(self):
        price=b'<span class="market-price">100.25</span>';change=b"<span class='market-change up'>+0.25%</span>"
        unavailable=PAGE.replace(price,b'').replace(change,b'').replace(b'market-item up',b'market-item flat')
        self.assertTrue(quotes.matches(quotes.contract(PAGE),quotes.contract(unavailable)))
        for bad in [unavailable.replace(b'flat',b'up'),unavailable.replace(b'SPX ',b'SPX '+change),unavailable.replace(b'<div class="market-bar">',b'<div class="market-bar">'+price),unavailable.replace(b'</a>',b'</a>'+price)]:
            self.assertTrue(self.rejected(PAGE,bad))

    def test_quote_elision_cannot_hide_price_relocation_or_nonquote_markup(self):
        price=b'<span class="market-price">100.25</span>';change=b"<span class='market-change up'>+0.25%</span>"
        for bad in [PAGE.replace(price,b'').replace(b'SPX ',price+b'SPX '),PAGE.replace(price,price+b'<em>Injected</em>'),PAGE.replace(change,change+b'<em>Injected</em>')]:
            self.assertTrue(self.rejected(PAGE,bad))

    def test_previous_v3_contract_is_not_silently_reinterpreted(self):
        current=quotes.contract(PAGE);old={**current,'version':3,'kind':'market-bar-renderer-quotes-v3'}
        self.assertFalse(quotes.matches(old,current))
    def test_unowned_or_relocated_change_and_unsupported_futures_fail(self):
        change=b"<span class='market-change up'>+0.25%</span>"
        price=b'<span class="market-price">100.25</span>'
        for bad in [PAGE.replace(b'<div class="market-bar">',b'<div class="market-bar">'+change),PAGE.replace(price,change+price).replace(change+b'</a>',b'</a>'),PAGE.replace(change,b'').replace(b'</a>',b'</a>'+change)]:
            self.assertTrue(self.rejected(PAGE,bad))
        future=PAGE.replace(price,b'<span class="market-price" style="color:#818cf8">104.00<sup style=\'font-size:9px;color:#6366f1;margin-left:1px\'>F</sup></span>')
        for slug in [b'vix',b'gold',b'crude-oil']:
            self.assertTrue(self.rejected(PAGE.replace(b'sp500',slug),future.replace(b'sp500',slug)))
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
