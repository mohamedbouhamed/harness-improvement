"""HTML rendering. Inline markup: **bold**, `code`, [label](url)."""
import html
import re
from urllib.parse import quote, urlsplit

SAFE_SCHEMES = ("http", "https", "")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def text(value: str) -> str:
    return html.escape(value)


def attr(value: str) -> str:
    return html.escape(value, quote=False)


def _safe_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme.lower() not in SAFE_SCHEMES:
        return "#"
    return html.escape(url, quote=True)


def render_inline(source: str) -> str:
    out = text(source)
    out = _CODE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    out = _BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", out)

    def link(m):
        label, url = m.group(1), html.unescape(m.group(2))
        return f'<a href="{_safe_url(url)}">{label}</a>'

    return _LINK.sub(link, out)


def render_page(page: dict) -> str:
    title = page["title"]
    paragraphs = [p for p in page["body"].split("\n\n") if p.strip()]
    body = "\n".join(f"<p>{render_inline(p)}</p>" for p in paragraphs)
    tags = " ".join(f'<a class="tag" href="/tags/{quote(t)}">{text(t)}</a>' for t in page["tags"])
    return (f"<article>\n<h1>{text(title)}</h1>\n"
            f'<a class="edit" href="/edit/{quote(page["slug"])}" title="Edit {attr(title)}">edit</a>\n'
            f"{body}\n<footer>{tags}</footer>\n</article>")


def render_index(pages: list) -> str:
    items = "".join(f'<li><a href="/view/{quote(p["slug"])}">{text(p["title"])}</a></li>' for p in pages)
    return f"<ul>{items}</ul>"
