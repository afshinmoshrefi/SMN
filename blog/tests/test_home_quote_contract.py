from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import home_quote_contract as quotes

BAR='''<div class="market-bar"><div class="market-track"><span class="market-item up">SPX <span class="market-price">100.25</span><span class='market-change up'>+0.25%</span></span></div></div>'''
PAGE=('<html><body>'+BAR+'<p>Bound article and manual pin</p></body></html>').encode()

class HomeQuoteContractTests(unittest.TestCase):
    def test_numeric_refresh_is_allowed_without_rebasing_content(self):
        changed=PAGE.replace(b'100.25',b'99.00').replace(b'+0.25%',b'-1.25%').replace(b'item up',b'item down').replace(b'change up',b'change down')
        self.assertNotEqual(quotes.contract(PAGE)['observed_sha256'],quotes.contract(changed)['observed_sha256'])
        self.assertTrue(quotes.matches(quotes.contract(PAGE),quotes.contract(changed)))

    def test_article_pin_symbol_link_and_active_markup_changes_fail(self):
        for old,new in [(b'Bound article',b'Changed article'),(b'manual pin',b'removed pin'),(b'SPX',b'FAKE'),(b'100.25',b'<script>alert(1)</script>'),(b'class="market-price"',b'class="market-price" onclick="alert(1)"')]:
            with self.subTest(new=new):
                self.assertFalse(quotes.matches(quotes.contract(PAGE),quotes.contract(PAGE.replace(old,new))))

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
