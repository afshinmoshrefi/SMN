"""Responsive private hero identity tests; fixture reviews are not approvals."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from angle_chrome import build_chrome, assemble_article
from cohort_policy import build_selection_evidence, baseline_cell
import private_charts
import reader_promise as rp
from test_reader_promise import png


def card_fixture():
    instrument = {'resource_id': '2', 'provider': 'fixture', 'exchange': 'US', 'symbol': 'ACME',
                  'series_id': 'adjusted-fixture', 'asset_class': 'equity',
                  'semantics': {'measurement': 'adjusted_price_return'}}
    rows = [{'year': year, 'net': -2 if year >= 2021 else 3, 'mfe': 5, 'mae': -4}
            for year in range(1986, 2026)]
    evidence = build_selection_evidence(observations=rows, instrument=instrument,
        anchor_date='2026-08-21', days=30, as_of='2026-09-06T16:00:00Z',
        coverage={'start_year': 1986, 'end_year': 2025, 'source_ref': 'synthetic-fixed-panel'})
    return {'symbol': 'ACME', 'resource_id': '2', 'instrument': instrument,
            'selection_evidence': evidence, 'story_cell': baseline_cell(evidence)}


class ResponsiveHeroTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        desktop, mobile = self.root/'desktop-asset.png', self.root/'mobile-asset.png'
        desktop_hash, mobile_hash = png(desktop, 1800, 945), png(mobile, 768, 1152)
        self.asset = {'path': str(desktop), 'url': desktop.as_uri(), 'sha256': desktop_hash,
            'mobile': {'path': str(mobile), 'url': mobile.as_uri(), 'sha256': mobile_hash,
                       'evidence_sha256': 'same-data'}, 'evidence_sha256': 'same-data',
            'alt': 'Synthetic comparison samples', 'caption': 'Data illustration: synthetic samples.',
            'provenance': {'kind': 'illustration'}}
        card = card_fixture()
        card['hero_asset'] = self.asset
        self.chrome = build_chrome(card, hero_url=self.asset['url'])
        self.html = '<article><h1>ACME comparison</h1>' + self.chrome['HERO'] + '</article>'
        self.review = {'asset_sha256': desktop_hash, 'mobile_asset_sha256': mobile_hash,
            'article_sha256': rp.fingerprint(self.html), 'reviewer_id': 'test-editor',
            'method': 'human_visual', 'reviewed_at': '2026-09-06T16:00:00Z',
            'checks': [{'check': key, 'verdict': 'pass', 'variants': ['desktop', 'mobile'],
                        'observation': 'Fixture-only structural review of both desktop and mobile image variants.'}
                       for key in ('lettering', 'identity', 'provenance', 'crop', 'factual_implication')],
            'views': []}
        for kind, width in [('desktop', 1360), ('mobile', 390)]:
            path = self.root/(kind+'-screenshot.png')
            source = self.asset['mobile'] if kind == 'mobile' else self.asset
            self.review['views'].append({'kind': kind, 'path': str(path), 'sha256': png(path, width, 900),
                                        'asset_url': source['url'], 'asset_sha256': source['sha256']})

    def check(self):
        return rp.review_hero(self.html, self.asset, self.review, trusted_reviewers={'test-editor'})

    def test_picture_renders_mobile_source_and_desktop_fallback(self):
        self.assertIn('<picture><source media="(max-width: 600px)"', self.html)
        self.assertIn('srcset="'+self.asset['mobile']['url']+'"', self.html)
        self.assertIn('<img src="'+self.asset['url']+'"', self.html)
        self.assertTrue(self.check()['ready'], self.check())

    def test_assembly_keeps_desktop_metadata_image_and_prose(self):
        prose = '<h1>Unchanged title</h1><p class="dek">Unchanged dek.</p>{{HERO}}<p>Unchanged body.</p>'
        result = assemble_article(prose, self.chrome)
        self.assertIn('<p>Unchanged body.</p>', result['html'])
        self.assertIn('"image": "'+self.asset['url']+'"', result['html'])
        self.assertLess(result['html'].index('Unchanged dek.'), result['html'].index('<picture>'))

    def test_changed_mobile_bytes_do_not_reuse_a_desktop_review(self):
        png(Path(self.asset['mobile']['path']), 768, 1153)
        result = self.check()
        self.assertFalse(result['ready'])
        self.assertIn('mobile_hero_manifest_hash_missing_or_stale', result['pending'])
        self.assertIn('mobile_hero_review_hash_missing_or_stale', result['pending'])

    def test_changed_desktop_manifest_does_not_pass(self):
        self.asset['sha256'] = 'wrong'
        self.assertIn('desktop_hero_manifest_hash_missing_or_stale', self.check()['pending'])

    def test_mobile_url_or_breakpoint_or_unreviewed_source_holds(self):
        for old, new in [('srcset="'+self.asset['mobile']['url'], 'srcset="https://wrong.test/image'),
                         ('max-width: 600px', 'max-width: 900px'),
                         ('<source ', '<source srcset="https://wrong.test/first.png"><source ')]:
            with self.subTest(change=new):
                original = self.html
                self.html = original.replace(old, new)
                self.review['article_sha256'] = rp.fingerprint(self.html)
                self.assertIn('reviewed_mobile_hero_not_in_rendered_article', self.check()['issues'])
                self.html = original

    def test_img_srcset_cannot_override_the_reviewed_fallback(self):
        self.html = self.html.replace('<img ', '<img srcset="https://wrong.test/desktop.png 2x" ')
        self.review['article_sha256'] = rp.fingerprint(self.html)
        self.assertIn('unreviewed_hero_img_srcset', self.check()['issues'])

    def test_removed_mobile_manifest_cannot_hide_a_responsive_source(self):
        del self.asset['mobile']
        self.assertIn('unreviewed_responsive_hero_source', self.check()['issues'])

    def test_different_evidence_for_mobile_holds(self):
        self.asset['mobile']['evidence_sha256'] = 'different-data'
        self.assertIn('responsive_hero_evidence_mismatch', self.check()['issues'])

    def test_each_check_must_cover_both_variants(self):
        for value in (None, ['desktop'], ['desktop', 5], ['mobile', 'mobile']):
            with self.subTest(value=value):
                self.review['checks'][0]['variants'] = value
                self.assertFalse(self.check()['ready'])

    def test_screenshot_must_bind_to_the_asset_that_was_displayed(self):
        self.review['views'][1]['asset_url'] = self.asset['url']
        self.assertIn('actual_mobile_and_desktop_crop_evidence_required', self.check()['pending'])

    def test_missing_mobile_file_or_review_holds(self):
        del self.review['mobile_asset_sha256']
        self.assertIn('mobile_hero_review_hash_missing_or_stale', self.check()['pending'])
        Path(self.asset['mobile']['path']).unlink()
        self.assertIn('local_mobile_hero_bytes_unavailable', self.check()['pending'])

    def test_incomplete_mobile_manifest_rejected_by_private_chrome(self):
        card = card_fixture()
        card['hero_asset'] = copy.deepcopy(self.asset)
        del card['hero_asset']['mobile']['sha256']
        with self.assertRaises(ValueError):
            build_chrome(card, hero_url=self.asset['url'])


@unittest.skipUnless(importlib.util.find_spec('matplotlib'), 'matplotlib renderer dependency unavailable')
class MobileChartRenderTests(unittest.TestCase):
    def test_baseline_axis_preserves_fractional_percentage_ticks(self):
        import matplotlib.pyplot as plt
        try:
            with tempfile.TemporaryDirectory() as directory, patch.object(plt, 'close'):
                private_charts.render_baseline_bars(card_fixture(), directory)
                formatter = plt.gcf().axes[0].yaxis.get_major_formatter()
                for value, expected in ((2.5, '2.5%'), (7.5, '7.5%'), (12.5, '12.5%'),
                                        (-2.5, '-2.5%'), (.005, '0.005%')):
                    with self.subTest(value=value):
                        self.assertEqual(formatter(value), expected)
        finally:
            plt.close('all')

    def test_mobile_and_desktop_share_exact_evidence_and_real_png_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(private_charts, '_render_mobile_comparison', wraps=private_charts._render_mobile_comparison) as mobile:
                result = private_charts.render_comparison_hero(card_fixture(), directory)
            evidence = json.loads((Path(directory)/'comparison-hero-evidence.json').read_text())
            self.assertEqual(json.loads(json.dumps(mobile.call_args.args[1])), evidence['views'])
            self.assertEqual(result['mobile']['evidence_sha256'], result['evidence_sha256'])
            for asset, size in ((result, (1800, 945)), (result['mobile'], (768, 1152))):
                raw = Path(asset['path']).read_bytes()
                self.assertEqual(raw[:8], b'\x89PNG\r\n\x1a\n')
                self.assertEqual(struct.unpack('>II', raw[16:24]), size)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), asset['sha256'])


if __name__ == '__main__':
    unittest.main()
