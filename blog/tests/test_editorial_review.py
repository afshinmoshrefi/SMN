import json, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from editorial_review import repair_article, run_review_cycle

HTML = '<html><body><article>QQQ 30 calendar days</article></body></html>'
FACTS = {'symbol':'QQQ','days':30}

class EditorialReviewTests(unittest.TestCase):
    def test_clean_article_publishes_without_repair(self):
        calls=[]
        def send(prompt):
            calls.append(prompt)
            return json.dumps({'decision':'publish','hard_issues':[],'soft_issues':[]})
        html, report=run_review_cycle(HTML, FACTS, send=send)
        self.assertEqual(html, HTML)
        self.assertEqual(report['decision'],'publish')
        self.assertEqual(len(calls),1)

    def test_repair_requires_clean_second_review(self):
        responses=iter([
            json.dumps({'decision':'repair','hard_issues':[{'code':'days','detail':'wrong'}],'soft_issues':[]}),
            '<html><body><article>QQQ 30 calendar days repaired</article></body></html>',
            json.dumps({'decision':'publish','hard_issues':[],'soft_issues':[]}),
        ])
        html, report=run_review_cycle(HTML, FACTS, send=lambda _: next(responses))
        self.assertIn('repaired', html)
        self.assertEqual(report['decision'],'publish')

    def test_unresolved_issue_holds(self):
        responses=iter([
            json.dumps({'decision':'repair','hard_issues':[{'code':'citation','detail':'bad'}],'soft_issues':[]}),
            '<html><body><article>still bad</article></body></html>',
            json.dumps({'decision':'hold','hard_issues':[{'code':'citation','detail':'bad'}],'soft_issues':[]}),
        ])
        _, report=run_review_cycle(HTML, FACTS, send=lambda _: next(responses))
        self.assertEqual(report['decision'],'hold')

    # 2026-07-16 incident: a JSON-conditioned repairer wrapped the article as
    # {"html": "..."} and the raw JSON text shipped downstream as the article.
    def test_repair_unwraps_json_wrapped_html(self):
        wrapped = json.dumps({'html': '<!doctype html>\n<html><body>fixed</body></html>'})
        review = {'decision': 'repair', 'hard_issues': [], 'soft_issues': []}
        repaired = repair_article(HTML, review, FACTS, send=lambda _: wrapped)
        self.assertTrue(repaired.startswith('<!doctype html>'))
        self.assertNotIn('{"html"', repaired)

    def test_repair_rejects_json_that_merely_mentions_html(self):
        # A bare substring check let {"html": "<html ..."} through; the document
        # must BE HTML, not contain it.
        blob = json.dumps({'notes': 'see <html> and </html> tags', 'html': 42})
        review = {'decision': 'repair', 'hard_issues': [], 'soft_issues': []}
        with self.assertRaises(ValueError):
            repair_article(HTML, review, FACTS, send=lambda _: blob)

    def test_repair_accepts_plain_html_document(self):
        doc = '<html><body><article>plain repaired</article></body></html>'
        review = {'decision': 'repair', 'hard_issues': [], 'soft_issues': []}
        self.assertEqual(repair_article(HTML, review, FACTS, send=lambda _: doc), doc)

if __name__ == '__main__': unittest.main()
