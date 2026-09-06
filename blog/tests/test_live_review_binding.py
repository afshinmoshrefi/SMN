"""Fresh transport bookkeeping cannot substitute for a supported review."""
from copy import deepcopy
import json
import unittest
from test_reader_promise import ARTICLE, case, plan_for, independent_review
import editorial_review
import reader_promise as rp


class LiveReviewBindingTests(unittest.TestCase):
    def setUp(self):
        self.card = case()
        self.brief = self.card['reader_brief']
        self.plan = plan_for(self.card)
        self.review = independent_review(ARTICLE, self.brief)

    def run_live(self, review):
        return editorial_review.review_article(ARTICLE,
            {'reader_brief': self.brief, 'editorial_plan': self.plan},
            send=lambda prompt: json.dumps({'decision': 'publish',
                'reader_promise_review': review}))

    def test_live_echo_typo_does_not_rewrite_correct_article(self):
        self.review['article_sha256'] = 'model-copy-typo'
        result = self.run_live(self.review)
        self.assertEqual(result['decision'], 'publish')
        bound = result['reader_promise_review']
        self.assertEqual(bound['article_sha256'], rp.fingerprint(ARTICLE))
        self.assertEqual(bound['model_reported_binding']['article_sha256'], 'model-copy-typo')
        self.assertEqual(self.review['article_sha256'], 'model-copy-typo')

    def test_imported_or_changed_article_review_remains_held(self):
        bound = self.run_live(self.review)['reader_promise_review']
        for html, brief in [(ARTICLE.replace('ACME', 'OTHER'), self.brief),
                            (ARTICLE, {**self.brief, 'selected_question_id': 'different'})]:
            checked = rp.review_reader_promise(html, brief, review=bound)
            self.assertFalse(checked['text_ready'])
            self.assertIn('PROMISE_REVIEW_PENDING', [i['code'] for i in checked['issues']])

    def test_binding_does_not_supply_missing_semantic_review(self):
        for review in [None, {}, {'passed': True}]:
            with self.subTest(review=review):
                result = self.run_live(review)
                self.assertEqual(result['decision'], 'repair')
                self.assertFalse(result['reader_promise_validation']['text_ready'])

    def test_binding_does_not_repair_wrong_passage_or_missing_reference(self):
        for mutation in ['quote', 'refs']:
            review = deepcopy(self.review)
            if mutation == 'quote':
                review['checks'][0]['quote'] = 'A different unseen headline'
            else:
                review['checks'][0]['evidence_refs'] = []
            result = self.run_live(review)
            self.assertFalse(result['reader_promise_validation']['text_ready'])

    def test_prompt_exposes_allowed_opening_passages(self):
        prompt = rp.build_promise_review_prompt(ARTICLE, self.brief, self.plan)
        self.assertIn('visible_preview_or_opening', prompt)
        self.assertIn('Older cycle observations change the picture', prompt)
        self.assertIn('not a detailed body paragraph', prompt)


if __name__ == '__main__':
    unittest.main()
