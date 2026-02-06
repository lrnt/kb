from __future__ import annotations

import re
from typing import TYPE_CHECKING
import xml.etree.ElementTree as etree

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown import Markdown
from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor
from markdown.util import AtomicString

from paths import TEMPLATES_DIR

WIKILINK_RE = r"\[\[([^\]]+)\]\]"
WHITESPACE_RE = re.compile(r"\s+")

if TYPE_CHECKING:
    from notes import NoteInfo


def normalize_wikilink_key(value: str) -> str:
    """Normalize wikilink keys for map lookups."""
    target = value.strip()
    if target.startswith("[[") and target.endswith("]]"):
        target = target[2:-2]
    if "|" in target:
        target = target.split("|", 1)[0]
    if "#" in target:
        target = target.split("#", 1)[0]
    if target.endswith(".md"):
        target = target[:-3]
    target = WHITESPACE_RE.sub(" ", target.strip())
    return target.lower()


def build_wikilink_map(public_notes: list["NoteInfo"]) -> dict[str, str]:
    """Build wikilink lookup map for public notes."""
    stem_counts: dict[str, int] = {}
    title_counts: dict[str, int] = {}

    for note in public_notes:
        stem_key = normalize_wikilink_key(note.path.stem)
        stem_counts[stem_key] = stem_counts.get(stem_key, 0) + 1

        title = note.title
        if title:
            title_key = normalize_wikilink_key(title)
            title_counts[title_key] = title_counts.get(title_key, 0) + 1

    link_map: dict[str, str] = {}
    for note in public_notes:
        rel = note.rel
        url = "/" + str(rel.with_suffix(""))

        path_key = normalize_wikilink_key(rel.with_suffix("").as_posix())
        link_map[path_key] = url

        stem_key = normalize_wikilink_key(note.path.stem)
        if stem_counts.get(stem_key, 0) == 1:
            link_map[stem_key] = url

        title = note.title
        if title:
            title_key = normalize_wikilink_key(title)
            if title_counts.get(title_key, 0) == 1:
                link_map[title_key] = url

    return link_map


def split_wikilink(raw: str) -> tuple[str, str]:
    """Split a wikilink target and label."""
    if "|" in raw:
        target, label = raw.split("|", 1)
        return target.strip(), label.strip()
    return raw.strip(), ""


def wikilink_label_from_target(target: str) -> str:
    """Derive a readable label from a wikilink target."""
    label = target.strip()
    if "#" in label:
        label = label.split("#", 1)[0]
    if label.endswith(".md"):
        label = label[:-3]
    return label.strip()


def resolve_wikilink(target: str, link_map: dict[str, str]) -> str | None:
    """Resolve a wikilink target using the map."""
    key = normalize_wikilink_key(target)
    return link_map.get(key)


class WikiLinkInlineProcessor(InlineProcessor):
    """Inline processor for Obsidian-style wikilinks."""

    def __init__(self, pattern: str, link_map: dict[str, str]):
        super().__init__(pattern)
        self.link_map = link_map

    def handleMatch(self, m, data):
        raw = m.group(1)
        target, label = split_wikilink(raw)
        label_text = label or wikilink_label_from_target(target)
        resolved = resolve_wikilink(target, self.link_map)
        if not resolved:
            return AtomicString(label_text), m.start(0), m.end(0)
        el = etree.Element("a")
        el.set("href", resolved)
        el.text = label_text
        return el, m.start(0), m.end(0)


class WikiLinkExtension(Extension):
    """Markdown extension to handle Obsidian wikilinks."""

    def __init__(self, **kwargs):
        self.link_map = kwargs.pop("link_map", {})
        super().__init__(**kwargs)

    def extendMarkdown(self, md):
        md.inlinePatterns.register(
            WikiLinkInlineProcessor(WIKILINK_RE, self.link_map),
            "wikilink",
            175,
        )


def build_markdown_renderer(link_map: dict[str, str]) -> Markdown:
    """Create a Markdown renderer with site extensions."""
    return Markdown(
        extensions=[
            WikiLinkExtension(link_map=link_map),
        ],
        output_format="xhtml",
    )


def render_markdown(renderer: Markdown, content: str) -> str:
    """Render Markdown content into HTML."""
    renderer.reset()
    return renderer.convert(content)


def get_template_env() -> Environment:
    """Create a Jinja environment for HTML templates."""
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html"]),
    )


def get_templates_mtime() -> float:
    """Get the latest mtime across HTML templates."""
    mtimes = []
    if TEMPLATES_DIR.exists():
        for path in TEMPLATES_DIR.rglob("*.html"):
            mtimes.append(path.stat().st_mtime)
    return max(mtimes, default=0)


def templates_changed(
    cache: dict,
    templates_mtime: float,
    nav_hash: str,
    wikilinks_hash: str,
) -> bool:
    """Check if shared templates require rebuilds."""
    return (
        templates_mtime > cache.get("templates_mtime", 0)
        or nav_hash != cache.get("nav_hash", "")
        or wikilinks_hash != cache.get("wikilinks_hash", "")
    )


def update_template_cache(
    cache: dict,
    templates_mtime: float,
    nav_hash: str,
    wikilinks_hash: str,
):
    """Persist template-related values into cache."""
    cache["templates_mtime"] = templates_mtime
    cache["nav_hash"] = nav_hash
    cache["wikilinks_hash"] = wikilinks_hash


def render_page(
    template,
    *,
    page_title: str,
    title: str,
    nav_items: list,
    content_html: str,
) -> str:
    """Render a full HTML page using Jinja templates."""
    return template.render(
        page_title=page_title,
        title=title,
        nav_items=nav_items,
        content_html=content_html,
    )
