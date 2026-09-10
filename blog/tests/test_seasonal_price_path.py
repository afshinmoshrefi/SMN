"""Regression: never restore the independent median/date-snapping implementation."""
import unittest
from seasonal_price_path import derive, attach, render, validate
from test_engine_seasonal import FIXTURES, card
from engine_seasonal import AUTHORITY
from visual_evidence import digest

class PriceCalculationAuthority(unittest.TestCase):
    def test_legacy_raw_price_paths_fail_closed(self):
        for function in (derive, attach, render):
            with self.subTest(function=function.__name__), self.assertRaises(ValueError):
                function({}, b'raw prices', {})
    def test_only_existing_engine_evidence_is_accepted(self):
        c=card(FIXTURES[0]);p={'authority':AUTHORITY,'evidence_sha256':digest(c['price_path'])}
        self.assertTrue(validate(p,c))
        p['evidence_sha256']='changed'
        with self.assertRaisesRegex(ValueError,'changed'):validate(p,c)

if __name__=='__main__':unittest.main()
