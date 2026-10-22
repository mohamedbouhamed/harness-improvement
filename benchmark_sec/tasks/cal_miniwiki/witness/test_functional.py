import unittest

from miniwiki.api import WikiApp


class Functional(unittest.TestCase):
    def test_save_view_tags(self):
        app = WikiApp()
        slug = app.handle_save({}, {"title": "Release Plan", "body": "See **notes** and [docs](https://x.test/a?b=1&c=2).\n\nUse `make`.",
                                    "tags": "ops, q3"})[1]["slug"]
        status, page = app.handle_view({}, {"slug": slug})
        self.assertEqual(status, 200)
        self.assertIn("<h1>Release Plan</h1>", page)
        self.assertIn("<strong>notes</strong>", page)
        self.assertIn('<a href="https://x.test/a?b=1&amp;c=2">docs</a>', page)
        self.assertIn("<code>make</code>", page)
        self.assertIn('title="Edit Release Plan"', page)
        self.assertIn("release-plan", app.handle_tag({}, {"tag": "ops"})[1])

    def test_body_markup_is_escaped(self):
        app = WikiApp()
        slug = app.handle_save({}, {"title": "T", "body": "<b>x</b> [a](javascript:alert)"})[1]["slug"]
        page = app.handle_view({}, {"slug": slug})[1]
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", page)
        self.assertIn('href="#"', page)
