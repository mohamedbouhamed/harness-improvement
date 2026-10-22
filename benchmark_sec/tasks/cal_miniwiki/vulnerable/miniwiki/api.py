"""Handlers called by the web layer. Each returns (status, html or body)."""
from .render import render_index, render_page
from .store import PageStore

MAX_TAGS = 8


class WikiApp:
    def __init__(self):
        self.store = PageStore()

    def handle_save(self, session, params):
        title = (params.get("title") or "").strip()
        if not title:
            return 400, {"error": "title required"}
        tags = [t.strip() for t in params.get("tags", "").split(",") if t.strip()][:MAX_TAGS]
        return 201, {"slug": self.store.save(title, params.get("body", ""), tags)}

    def handle_view(self, session, params):
        page = self.store.get(params.get("slug", ""))
        if page is None:
            return 404, "<p>not found</p>"
        return 200, render_page(page)

    def handle_tag(self, session, params):
        pages = [self.store.get(s) for s in self.store.by_tag(params.get("tag", ""))]
        return 200, render_index(pages)
