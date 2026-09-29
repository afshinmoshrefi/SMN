import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import article_hero_image as hero


class HeroCheckTests(unittest.TestCase):
    """Sept 2026: COSTCO and WALMART heroes shipped with misspelled lettering."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name)/'hero_COST_ab12cd34.jpg')
        self.prompts, self.seeds = [], []

    def fake_generate(self, hero_prompt, concept_brief, hero_output_path, symbol, **kw):
        self.prompts.append(hero_prompt)
        self.seeds.append(kw['attempt'])
        Path(hero_output_path).write_bytes(b'jpeg')
        return hero_output_path

    def run_with(self, verdicts):
        answers = iter(verdicts)
        with patch.object(hero, 'generate_hero_image', side_effect=self.fake_generate), \
             patch.object(hero, 'check_hero_text', side_effect=lambda *a: next(answers)):
            return hero.generate_checked_hero('A warehouse at dusk.', {}, self.path, 'COST', 'Costco',
                                              width=10, height=10, date='2026-09-28')

    def test_clean_hero_is_checked_once(self):
        path, record = self.run_with([{'checked': True, 'passed': True}])
        self.assertEqual(path, self.path)
        self.assertEqual(len(record['attempts']), 1)
        self.assertTrue(json.loads(Path(self.path[:-4] + '.check.json').read_text())['passed'])

    def test_misspelled_hero_is_regenerated_without_text(self):
        path, record = self.run_with([{'checked': True, 'passed': False, 'misspelled_or_garbled': ['MARKELL']},
                                      {'checked': True, 'passed': True}])
        self.assertEqual(path, self.path)
        self.assertEqual(self.seeds, [1, 2])
        self.assertNotIn('unmarked', self.prompts[0])
        self.assertIn('unmarked', self.prompts[1])

    def test_retries_remove_what_invites_lettering(self):
        motif = ('Costco warehouse exterior with distinctive red and blue company signage, '
                 'membership retail architecture, commercial photography')
        second = hero._unbranded_prompt(motif, 'Costco Wholesale', 'COST')
        scene = second[:-len(hero.HERO_PLAIN)]
        self.assertNotIn('Costco', scene)
        self.assertNotIn('signage', scene)
        self.assertTrue(second.endswith(hero.HERO_PLAIN))
        self.assertIn('warehouse', second)
        third = hero._safe_scene_prompt({'sector': 'custom'})
        # Flux draws what a prompt names, even after "no": never name the things to avoid.
        for word in ('no ', 'sign', 'text', 'logo', 'box', 'letter'):
            self.assertNotIn(word, third.lower())

    def test_three_failures_publish_without_hero(self):
        bad = {'checked': True, 'passed': False, 'misspelled_or_garbled': ['WALMRT']}
        path, record = self.run_with([bad, bad, bad])
        self.assertIsNone(path)
        self.assertEqual(len(record['attempts']), 3)
        self.assertFalse(Path(self.path).exists())
        self.assertFalse(record['passed'])

    def write_jpeg(self):
        from PIL import Image
        Image.new('RGB', (300, 100), 'white').save(self.path, 'JPEG')

    def test_checker_outage_never_blocks(self):
        self.write_jpeg()
        with patch.object(hero.AI_tools.requests, 'post', side_effect=RuntimeError('HTTP 529')):
            verdict = hero.check_hero_text(self.path, 'Costco', 'COST')
        self.assertTrue(verdict['passed'])
        self.assertFalse(verdict['checked'])

    def test_garbled_word_fails_even_if_model_says_passed(self):
        self.write_jpeg()
        answer = {'visible_text': ['COSTCO WHOLESLAE'], 'misspelled_or_garbled': ['WHOLESLAE'], 'passed': True}
        reply = type('R', (), {'status_code': 200, 'json': lambda self: {'content': [
            {'type': 'tool_use', 'name': 'report_hero_text', 'input': answer}]}})()
        with patch.object(hero.AI_tools.requests, 'post', return_value=reply):
            self.assertFalse(hero.check_hero_text(self.path, 'Costco', 'COST')['passed'])


    def test_checker_sees_whole_hero_and_three_zoomed_parts(self):
        self.write_jpeg()
        self.assertEqual(len(hero._hero_views(self.path)), 4)


class HeroRenderRouteTests(unittest.TestCase):
    """Sept 29: gpt-image-2 is the first renderer; Flux is the backup."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name)/'hero_X_1.jpg')

    def test_openai_hero_is_used_when_it_works(self):
        import base64, io
        from PIL import Image
        buf = io.BytesIO(); Image.new('RGB', (32, 16)).save(buf, 'JPEG')
        reply = type('R', (), {'status_code': 200, 'json': lambda self: {
            'data': [{'b64_json': base64.b64encode(buf.getvalue()).decode()}], 'usage': {}}})()
        with patch.object(hero.AI_tools.requests, 'post', return_value=reply) as post, \
             patch.object(hero.AI_tools, 'generate_AI_Image_flux_schnell') as flux:
            self.assertEqual(hero.generate_hero_image('p', {}, self.path, 'X', width=2176, height=960), self.path)
        self.assertEqual(post.call_args.kwargs['json']['size'], '2176x960')
        flux.assert_not_called()

    def test_flux_backs_up_a_failed_openai_render(self):
        def flux(prompt, image_filepath, *a, **k):
            Path(image_filepath).write_bytes(b'jpeg')
            return 'https://flux/img'
        with patch.object(hero.AI_tools.requests, 'post', side_effect=RuntimeError('HTTP 500')), \
             patch.object(hero.AI_tools, 'generate_AI_Image_premium', side_effect=flux) as premium:
            self.assertEqual(hero.generate_hero_image('p', {}, self.path, 'X', width=2176, height=960), self.path)
        premium.assert_called_once()


if __name__ == '__main__':
    unittest.main()
