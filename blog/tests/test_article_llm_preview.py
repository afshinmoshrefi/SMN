"""Offline transport and audience-preview checks; never calls a paid API."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

BLOG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BLOG))

from article_llm import ArticleLLM, ArticleModelError, chat_payload, collect_model_usage
from article_preview import audience_has_full_access, safe_fragment, write_article_preview


FAKE_KEY = "fictional-test-credential-must-not-appear-in-records"


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def model_reply():
    return {"model": "gpt-6-astra-returned-snapshot", "service_tier": "default",
            "choices": [{"finish_reason": "stop", "message": {"content": '{"draft":"A grounded article"}'}}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 430, "total_tokens": 1630,
                      "prompt_tokens_details": {"cached_tokens": 100, "cache_write_tokens": 1097},
                      "completion_tokens_details": {"reasoning_tokens": 35}}}


class ArticleTransportTests(unittest.TestCase):
    def test_astra_defaults_low_and_removes_unsupported_sampling_options(self):
        payload = chat_payload("Write a draft", temperature=0.2, top_p=0.9, top_logprobs=2,
                               logprobs=True, frequency_penalty=None, system="Return JSON")
        self.assertEqual(payload["model"], "gpt-6-astra")
        self.assertEqual(payload["reasoning_effort"], "low")
        for option in ("temperature", "top_p", "top_logprobs", "logprobs", "frequency_penalty"):
            self.assertNotIn(option, payload)
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "Return JSON"})
        self.assertFalse(payload["stream"])

    def test_unsupported_none_effort_is_normalized_and_invalid_effort_rejected(self):
        for effort in ("none", "minimal"):
            self.assertEqual(chat_payload("draft", reasoning_effort=effort)["reasoning_effort"], "low")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            chat_payload("draft", reasoning_effort="not-an-effort")

    def test_legacy_model_sampling_parameters_remain_available(self):
        payload = chat_payload("draft", model="gpt-5.1", temperature=0.3, top_p=0.8)
        self.assertEqual(payload["temperature"], 0.3)
        self.assertEqual(payload["top_p"], 0.8)

    def test_actual_model_usage_and_cache_write_provenance_are_preserved(self):
        captured = []

        def opener(request, timeout):
            captured.append(json.loads(request.data))
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.get_header("Authorization"), "Bearer " + FAKE_KEY)
            return FakeResponse(model_reply())

        with patch.dict(os.environ, {}, clear=True):
            provider = ArticleLLM(api_key=FAKE_KEY, opener=opener, stage="editorial")
            text = provider("Draft from these inspected facts")
        self.assertEqual(text, model_reply()["choices"][0]["message"]["content"])
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["reasoning_effort"], "low")
        self.assertFalse(captured[0]["store"])
        self.assertEqual(captured[0]["service_tier"], "default")
        record = provider.calls[0]
        self.assertEqual(record["requested_model"], "gpt-6-astra")
        self.assertEqual(record["model"], "gpt-6-astra-returned-snapshot")
        self.assertEqual(record["usage"], model_reply()["usage"])
        self.assertEqual(record["stage"], "editorial")
        self.assertEqual(record["status"], "ok")
        self.assertEqual(len(record["prompt_sha256"]), 64)
        self.assertGreaterEqual(record["duration_seconds"], 0)
        self.assertNotIn(FAKE_KEY, json.dumps(record))
        self.assertNotIn("Draft from these inspected facts", json.dumps(record))

    def test_refused_truncated_and_empty_responses_fail_once_without_leaking_payload(self):
        cases = []
        refused = model_reply()
        refused["choices"][0]["message"]["refusal"] = "Refusal payload includes " + FAKE_KEY
        cases.append(("refused", refused))
        length = model_reply()
        length["choices"][0].update(finish_reason="length", message={"content": FAKE_KEY})
        cases.append(("incomplete", length))
        empty = model_reply()
        empty["choices"][0]["message"]["content"] = "   "
        cases.append(("empty", empty))
        for expected, reply in cases:
            with self.subTest(expected=expected), patch.dict(os.environ, {}, clear=True):
                requests = []

                def opener(request, timeout):
                    requests.append(request)
                    return FakeResponse(reply)

                provider = ArticleLLM(api_key=FAKE_KEY, opener=opener)
                with self.assertRaisesRegex(ArticleModelError, expected) as raised:
                    provider("test prompt")
                self.assertEqual(len(requests), 1)
                self.assertEqual(len(provider.calls), 1)
                self.assertEqual(provider.calls[0]["status"], "error")
                self.assertNotIn(FAKE_KEY, str(raised.exception) + json.dumps(provider.calls))

    def test_http_and_network_error_messages_are_sanitized_without_retries(self):
        errors = [urllib.error.HTTPError("https://api.openai.com", 429, FAKE_KEY, {}, io.BytesIO(FAKE_KEY.encode())),
                  urllib.error.URLError(FAKE_KEY), TimeoutError(FAKE_KEY)]
        for error in errors:
            with self.subTest(error=type(error).__name__), patch.dict(os.environ, {}, clear=True):
                requests = []

                def opener(request, timeout):
                    requests.append(request)
                    raise error

                provider = ArticleLLM(api_key=FAKE_KEY, opener=opener)
                with self.assertRaises(ArticleModelError) as raised:
                    provider("draft")
                self.assertNotIn(FAKE_KEY, str(raised.exception) + json.dumps(provider.calls))
                self.assertEqual(len(requests), 1)
                self.assertEqual(provider.calls[0]["status"], "error")

    def test_missing_credential_does_not_attempt_a_request(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = ArticleLLM(opener=lambda *args, **kwargs: self.fail("No request without credential"))
            with self.assertRaisesRegex(ArticleModelError, "credential"):
                provider("draft")
        self.assertFalse(provider.calls)

    def test_custom_injected_providers_are_not_labeled_as_astra(self):
        class OtherProvider:
            calls = [{"model": "different-returned-model", "usage": {"completion_tokens": 10}}]

        records = collect_model_usage(write=OtherProvider(), plan=lambda prompt: "fixture")
        self.assertEqual(records["write"][0]["model"], "different-returned-model")
        self.assertEqual(records["plan"], [])


ARTICLE = '''<h1>A historical pattern worth examining</h1>
<p class="dek">A short description of the reader's question.</p>
<p class="byline">Seasonal Market News</p>
<figure class="hero"><img src="https://example.test/hero.png" alt="Seasonal chart"></figure>
<p class="direct-answer">A complete opening answer with a supporting source.<sup><a href="#source-1">[1]</a></sup></p>
<section><h2>Evidence and limitations</h2><p>FULL_ANALYSIS_ONLY: a distinct detailed paragraph that belongs in the registered article.</p>
<p>SECOND_FULL_PARAGRAPH: a useful comparison with further supporting evidence.</p></section>
<section class="sources"><h2>Sources</h2><ol><li id="source-1"><a href="https://example.test/report">Source report</a></li></ol></section>'''


class ArticlePreviewTests(unittest.TestCase):
    def test_access_policy_defaults_disabled_for_all_audiences(self):
        self.assertTrue(audience_has_full_access(article_type="seasonal"))
        self.assertTrue(audience_has_full_access(article_type="seasonal", mode="paid"))
        self.assertTrue(audience_has_full_access(article_type="news"))

    def test_registered_free_user_unlocks_initial_proposed_access(self):
        self.assertFalse(audience_has_full_access(article_type="seasonal", enabled=True))
        self.assertTrue(audience_has_full_access(article_type="seasonal", enabled=True, registered=True, paid=False))
        self.assertTrue(audience_has_full_access(article_type="seasonal", enabled=True, paid=True))
        self.assertFalse(audience_has_full_access(article_type="seasonal", enabled=True, registered=True, mode="paid"))

    def test_news_is_public_even_if_future_registration_or_paid_rules_are_enabled(self):
        for mode in ("registration", "paid"):
            self.assertTrue(audience_has_full_access(article_type="news", enabled=True, mode=mode))

    def test_visitor_file_excludes_full_paragraphs_instead_of_css_hiding_them(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = write_article_preview({"status": "ready", "html": ARTICLE}, directory)
            visitor = Path(paths["visitor.html"]).read_text(encoding="utf-8")
            registered = Path(paths["registered.html"]).read_text(encoding="utf-8")
            full = Path(paths["article.html"]).read_text(encoding="utf-8")
            self.assertIn("A complete opening answer", visitor)
            self.assertIn("Continue with a free TradeWave account", visitor)
            self.assertNotIn("FULL_ANALYSIS_ONLY", visitor)
            self.assertNotIn("SECOND_FULL_PARAGRAPH", visitor)
            self.assertIn('id="source-1"', visitor)
            self.assertIn("FULL_ANALYSIS_ONLY", registered)
            self.assertEqual(registered, full)
            metadata = json.loads(Path(paths["preview.json"]).read_text(encoding="utf-8"))
            self.assertFalse(metadata["access_enabled"])
            self.assertFalse(metadata["news_publish_enabled"])
            self.assertEqual(metadata["paid_access"], "deferred")

    def test_news_visitor_receives_same_complete_article_as_registered_user(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = write_article_preview({"status": "draft_ready", "article_html": ARTICLE}, directory, article_type="news")
            visitor = Path(paths["visitor.html"]).read_text(encoding="utf-8")
            full = Path(paths["registered.html"]).read_text(encoding="utf-8")
            self.assertEqual(visitor, full)
            self.assertIn("FULL_ANALYSIS_ONLY", visitor)
            self.assertNotIn("Continue with a free TradeWave account", visitor)

    def test_sanitization_removes_executable_markup_credentials_and_event_handlers(self):
        unsafe = '''<script>secret_script_payload()</script><style>secret_style_payload</style>
<iframe src="https://example.test/embed">secret_iframe_payload</iframe>
<p onclick="secret_handler()" style="position:fixed">Readable text &amp; meaning</p>
<a href="javascript&#58;secret_scheme()">Unsafe link</a>
<a href="https://user:password@example.test/report">Credential URL</a>
<img src="data:text/html,secret_data_payload" onerror="secret_image_handler()">
<a href="https://example.test/report">Safe report</a><a href="#source-1">Citation</a>'''
        cleaned = safe_fragment(unsafe)
        for prohibited in ("<script", "<style", "<iframe", "secret_", "onclick", "onerror", "javascript:", "user:password", "data:text"):
            self.assertNotIn(prohibited, cleaned)
        self.assertIn("Readable text &amp; meaning", cleaned)
        self.assertIn('href="https://example.test/report"', cleaned)
        self.assertIn('href="#source-1"', cleaned)

    def test_held_status_and_escaped_editorial_reason_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            result = {"status": "hold", "html": ARTICLE,
                      "validation": {"editorial": {"issues": [{"problem": "Unsupported claim <script>alert(1)</script>"}]}}}
            paths = write_article_preview(result, directory)
            full = Path(paths["article.html"]).read_text(encoding="utf-8")
            self.assertIn("Held for review", full)
            self.assertIn("Unsupported claim &lt;script&gt;", full)
            self.assertNotIn("<script>alert", full)
            self.assertIn("Access rules and new publishing controls are inactive", full)

    def test_preview_refuses_publication_directory_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "public_html" / "news"
            with self.assertRaisesRegex(ValueError, "outside publication"):
                write_article_preview({"html": ARTICLE}, target)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
