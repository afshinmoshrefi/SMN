"""Actual usage belongs to one draft even when a scan shares its transport."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from news_pipeline import run_news_article
from article_llm import ArticleModelError
from test_news_pipeline import NOW, packet, plan, article, passing_review


class NewsUsageIsolationTests(unittest.TestCase):
    def test_shared_transport_does_not_charge_prior_discovery_or_drafts_twice(self):
        class Transport:
            def __init__(self):
                self.calls = [{"stage": "previous-discovery", "usage": {"total_tokens": 999}}]
                self.responses = iter([json.dumps(x) for x in (plan(), article(), passing_review())])
            def __call__(self, prompt):
                self.calls.append({"stage": "current", "usage": {"total_tokens": 10}})
                return next(self.responses)
        result = run_news_article(**packet(), now=NOW, send=Transport())
        self.assertEqual(result["status"], "draft_ready")
        self.assertEqual(len(result["model_usage"]), 3)
        self.assertEqual(sum(c["usage"]["total_tokens"] for c in result["model_usage"]), 30)

    def test_configuration_failure_is_actionable_and_held(self):
        def unavailable(prompt):
            raise ArticleModelError("OpenAI credential is not configured")
        result = run_news_article(**packet(), now=NOW, send=unavailable)
        self.assertEqual(result["status"], "hold")
        self.assertIn("OpenAI credential is not configured", result["errors"])
        self.assertFalse(result["publishable"])


if __name__ == "__main__":
    unittest.main()
