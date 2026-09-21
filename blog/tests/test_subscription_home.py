import ast
import unittest
from pathlib import Path

from blog.subscription_home import render_home, render_search


class SubscriptionHomeTests(unittest.TestCase):
    def post(self, day, number, **extra):
        return {"title": f"Title {number}", "dek": f"Dek {number}", "symbol": f"S{number}",
                "published_date": day, "url": f"/articles/{number}.html", **extra}

    def test_display_limit_age_and_archive_link(self):
        posts = [self.post("2026-09-21", n) for n in range(60)]
        posts += [self.post("2026-09-07", 60), self.post("2026-09-06", 61)]
        page = render_home(posts, "2026-09-21")
        self.assertEqual(page.count('class="wire-list-item"'), 39)  # 50 - lead - five - five
        self.assertIn("Search all older coverage", page)
        self.assertNotIn("Title 61", page)
        self.assertIn("50 of 62 articles", page)

    def test_escapes_fields_and_normalizes_links(self):
        page = render_home([self.post("2026-09-21", 1, title='<script>alert(1)</script>',
                                      dek='"quoted"', url='/a.html', hero_image='/hero.jpg')], "2026-09-21")
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn('href="https://smn-dev.trxstat.com/a.html"', page)
        self.assertNotIn("javascript:", page)

    def test_rejects_foreign_url_and_sorts_full_timestamp(self):
        with self.assertRaises(ValueError):
            render_home([self.post("2026-09-21", 1, url="https://example.com/a.html")], "2026-09-21")
        page = render_home([self.post("2026-09-21T10:00:00Z", 1),
                            self.post("2026-09-21T15:00:00Z", 2)], "2026-09-21")
        self.assertLess(page.index("Title 2"), page.index("Title 1"))

    def test_search_is_local_safe_dom_and_noindex(self):
        page = render_search()
        self.assertIn('fetch(\'posts.json\')', page)
        self.assertIn('document.createElement', page)
        self.assertIn('noindex, nofollow', page)
        self.assertNotIn('innerHTML', page)

    def test_module_does_not_import_production_integrations(self):
        tree = ast.parse(Path(__file__).parents[1].joinpath("subscription_home.py").read_text(encoding="utf-8"))
        imports = {name.name for node in ast.walk(tree) if isinstance(node, ast.Import) for name in node.names}
        self.assertFalse({"config", "redis", "requests"} & imports)


if __name__ == "__main__":
    unittest.main()
