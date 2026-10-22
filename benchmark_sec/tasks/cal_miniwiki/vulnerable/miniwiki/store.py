"""In-memory page store keyed by slug."""
import re
import time

_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    slug = _SLUG.sub("-", title.lower()).strip("-")
    return slug[:60] or "page"


class PageStore:
    def __init__(self):
        self.pages = {}

    def save(self, title: str, body: str, tags: list) -> str:
        slug = slugify(title)
        self.pages[slug] = {"slug": slug, "title": title, "body": body, "tags": list(tags),
                            "updated": time.time()}
        return slug

    def get(self, slug: str):
        return self.pages.get(slug)

    def by_tag(self, tag: str) -> list:
        return sorted(p["slug"] for p in self.pages.values() if tag in p["tags"])
