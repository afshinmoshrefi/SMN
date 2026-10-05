import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import presentation_fallback as fallback


PAGE = '''<!doctype html><html><head><title>Study</title><style>p{height:1px}</style></head>
<body><article><h1>Study</h1><figure class="hero"><img src="assets/hero.png"><figcaption>Illustration</figcaption></figure>
<p style="overflow:hidden">Revenue was $12.4 billion. [1]</p><figure class="data-figure"><img src="assets/chart.png"><figcaption>Observed history, not a forecast.</figcaption></figure>
<a href="https://tradewave.ai/study/123">TradeWave study</a></article></body></html>'''


class PresentationFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.result = Path(self.temp.name)
        (self.result/'assets').mkdir()
        (self.result/'assets/chart.png').write_bytes(b'bound-chart')
        (self.result/'chart-manifest.json').write_text(json.dumps({
            'context':{'file_sha256':{'chart.png':fallback.sha(b'bound-chart')}}}))
        (self.result/'article.html').write_text(PAGE)

    def test_derivative_keeps_financial_text_charts_and_study_link(self):
        rendered = fallback.derive(PAGE)
        self.assertIn('Revenue was $12.4 billion. [1]', rendered)
        self.assertIn('Observed history, not a forecast.', rendered)
        self.assertIn('https://tradewave.ai/study/123', rendered)
        self.assertIn('assets/chart.png', rendered)
        self.assertNotIn('assets/hero.png', rendered)
        self.assertNotIn('overflow:hidden', rendered)
        self.assertIn('deterministic-readable-v1', rendered)

    def test_failed_substantive_review_cannot_use_fallback(self):
        with patch('editorial_gate.verify_content', side_effect=ValueError('Unsupported forecast')):
            with self.assertRaisesRegex(ValueError, 'Unsupported forecast'):
                fallback.prepare_fallback(self.result)

    def test_derivative_is_bound_and_original_failure_is_preserved(self):
        failure = self.result/'visual-checks-failed.json'
        failure.write_text('{"passed":false}')
        before = {p.name:p.read_bytes() for p in self.result.iterdir() if p.is_file()}
        with patch('editorial_gate.verify_content', return_value={'passed':True}) as content:
            rendered, proof = fallback.prepare_fallback(self.result)
            self.assertEqual(fallback.verify_fallback(self.result, rendered, proof), proof)
            self.assertTrue(proof['browser_verification_required'])
            content.assert_called_with(self.result, allow_held_binding=True)
            with self.assertRaisesRegex(ValueError, 'differs from bound content'):
                fallback.verify_fallback(self.result, rendered.replace('$12.4', '$14.2'), proof)
        self.assertEqual(before, {p.name:p.read_bytes() for p in self.result.iterdir() if p.is_file()})

    def test_changed_chart_still_blocks_when_decorative_hero_missing(self):
        (self.result/'assets/chart.png').write_bytes(b'wrong-chart')
        with patch('editorial_gate.verify_content', return_value={'passed':True}):
            with self.assertRaisesRegex(ValueError, 'Reviewed chart asset changed'):
                fallback.prepare_fallback(self.result)

    def test_unrecognized_or_active_markup_is_not_silently_removed(self):
        for extra in ('<script>alert(1)</script>', '<iframe src="https://example.com"></iframe>'):
            with self.assertRaisesRegex(ValueError, 'active content'):
                fallback.derive(PAGE.replace('</article>', extra+'</article>'))
        with self.assertRaisesRegex(ValueError, 'Hero contains unexpected content'):
            fallback.derive(PAGE.replace('<figcaption>Illustration', '<p>Revenue data</p><figcaption>Illustration'))


if __name__ == '__main__':
    unittest.main()
