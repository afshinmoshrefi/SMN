import ast
import re
import sys
import unittest
from pathlib import Path


BLOG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BLOG))

from article_sources import (
    canonicalize_sources_section,
    drop_orphan_citation_markers,
    normalize_research_source_ids,
)
from citation_gate import validate_citations


SOURCE_SECTION_RE = re.compile(
    r'<section\b[^>]*class=["\'][^"\']*\bsources\b[^"\']*["\'][^>]*>.*?</section\s*>',
    re.IGNORECASE | re.DOTALL,
)


class ResearchSourceNormalizationTests(unittest.TestCase):
    def test_sparse_ids_and_nested_references_become_contiguous(self):
        research = {
            "sources": [
                {"id": 1, "url": "https://one.test/a", "title": "One"},
                {"id": 6, "url": "https://two.test/b", "title": "Two"},
                {"id": "9", "url": "https://three.test/c", "title": "Three"},
            ],
            "catalysts": [{"source_id": 6}, {"source_id": "9"}],
            "earnings": {"sources": [1, 9, 8]},
            "temporal": {"fresh_source_ids": [6, "9", 8]},
            "missing_reference": {"source_id": 8},
            "unrelated_record": {"id": 99},
        }

        returned = normalize_research_source_ids(research)

        self.assertIs(returned, research)
        self.assertEqual([source["id"] for source in research["sources"]], [1, 2, 3])
        self.assertEqual(
            [item["source_id"] for item in research["catalysts"]], [2, 3]
        )
        self.assertEqual(research["earnings"]["sources"], [1, 3])
        self.assertEqual(research["temporal"]["fresh_source_ids"], [2, 3])
        self.assertIsNone(research["missing_reference"]["source_id"])
        self.assertEqual(research["unrelated_record"]["id"], 99)

    def test_prompt_normalizes_after_filtering_and_before_temporal_annotation(self):
        tree = ast.parse((BLOG / "article_prompt.py").read_text(encoding="utf-8"))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "create_article_prompt"
        )
        calls = {
            node.func.id: node.lineno
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in {
                "_filter_research_sources",
                "normalize_research_source_ids",
                "_annotate_research_temporal",
            }
        }
        self.assertLess(calls["_filter_research_sources"], calls["normalize_research_source_ids"])
        self.assertLess(calls["normalize_research_source_ids"], calls["_annotate_research_temporal"])


class CanonicalSourcesRenderingTests(unittest.TestCase):
    def setUp(self):
        self.research = {
            "sources": [
                {
                    "id": 1,
                    "publisher": "Reuters",
                    "title": "First & Best",
                    "url": "https://reuters.com/a?x=1&y=2",
                },
                {
                    "id": 2,
                    "publisher": "SEC",
                    "title": "Company filing",
                    "url": "https://sec.gov/b",
                },
            ]
        }

    def test_replaces_only_sources_content_and_passes_citation_gate(self):
        original = (
            '<article><p data-x="1">Keep this exactly.<sup>[2]</sup></p>\n'
            '<section class="sources" data-layout="wide"><h3>Old</h3><ol>'
            '<li><a href="https://wrong.test/old">Wrong</a></li></ol></section>\n'
            '<footer>Keep footer.</footer></article>'
        )

        rendered = canonicalize_sources_section(original, self.research)

        self.assertEqual(
            SOURCE_SECTION_RE.sub("<SOURCES>", rendered),
            SOURCE_SECTION_RE.sub("<SOURCES>", original),
        )
        self.assertIn('<section class="sources" data-layout="wide">', rendered)
        self.assertIn('href="https://reuters.com/a?x=1&amp;y=2"', rendered)
        self.assertIn("Reuters - First &amp; Best", rendered)
        self.assertNotIn("wrong.test", rendered)
        gate = validate_citations(rendered, research=self.research, whitelist=set())
        self.assertTrue(gate["ok"], gate)
        self.assertEqual(gate["stats"]["sources"], 2)

    def test_omitted_sources_are_inserted_without_rewriting_body(self):
        original = '<html><body><article><p>Unchanged body.</p></article></body></html>'

        rendered = canonicalize_sources_section(original, self.research)

        self.assertIn("<p>Unchanged body.</p>", rendered)
        self.assertEqual(rendered.count('<section class="sources">'), 1)
        self.assertLess(rendered.index('<section class="sources">'), rendered.index("</article>"))

    def test_duplicate_sources_sections_are_collapsed_to_one(self):
        original = (
            '<article><p>Body</p><section class="sources"><ol></ol></section>'
            '<aside>Unchanged</aside><section class="sources"><ol></ol></section></article>'
        )

        rendered = canonicalize_sources_section(original, self.research)

        self.assertEqual(rendered.count('<section class="sources">'), 1)
        self.assertIn("<aside>Unchanged</aside>", rendered)


class DropOrphanCitationMarkersTests(unittest.TestCase):
    SOURCES = (
        '<section class="sources"><h3>Sources</h3><ol>'
        '<li><a href="https://a.test/1">One</a></li>'
        '<li><a href="https://b.test/2">Two</a></li>'
        "</ol></section>"
    )

    def _doc(self, body):
        return f"<article><p>{body}</p>{self.SOURCES}</article>"

    def test_marker_past_the_end_is_dropped_and_valid_ones_survive(self):
        html, dropped = drop_orphan_citation_markers(
            self._doc('Revenue rose<sup>[2]</sup> on volume<sup>[11]</sup>.')
        )

        self.assertEqual(dropped, [11])
        self.assertIn("<sup>[2]</sup>", html)
        self.assertNotIn("[11]", html)
        self.assertIn("Revenue rose", html)
        self.assertIn("on volume.", html)

    def test_no_sources_section_drops_every_marker(self):
        html, dropped = drop_orphan_citation_markers(
            "<article><p>A claim<sup>[1]</sup> and another<sup>[3]</sup>.</p></article>"
        )

        self.assertEqual(dropped, [1, 3])
        self.assertNotIn("<sup>", html)
        self.assertIn("A claim and another.", html)

    def test_clean_article_is_returned_byte_for_byte(self):
        original = self._doc('Nothing wrong here<sup>[1]</sup>.')

        html, dropped = drop_orphan_citation_markers(original)

        self.assertEqual(dropped, [])
        self.assertIs(html, original)

    def test_markers_inside_the_sources_list_are_never_touched(self):
        original = (
            '<article><p>Body.</p><section class="sources"><h3>Sources</h3><ol>'
            "<li>Note<sup>[9]</sup></li></ol></section></article>"
        )

        html, dropped = drop_orphan_citation_markers(original)

        self.assertEqual(dropped, [])
        self.assertIn("<sup>[9]</sup>", html)

    def test_indentation_outside_the_seam_is_preserved(self):
        original = (
            '<article>\n    <p>Deeply    indented<sup>[7]</sup> text.</p>\n' + self.SOURCES + "</article>"
        )

        html, dropped = drop_orphan_citation_markers(original)

        self.assertEqual(dropped, [7])
        self.assertIn("\n    <p>", html)
        self.assertIn("Deeply    indented text.", html)


if __name__ == "__main__":
    unittest.main()
