#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = ["jinja2", "markdown"]
# ///
"""Incremental build system for static site."""

import argparse
from dataclasses import dataclass
import hashlib
import html
import json
import re
import shutil
import sys
from pathlib import Path
import xml.etree.ElementTree as etree

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown import Markdown
from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor
from markdown.util import AtomicString

# Paths (relative to web/)
ROOT = Path(__file__).parent.parent
NOTES_DIR = ROOT.parent.parent / "vault"
BUILD_DIR = ROOT / "build"
STATIC_DIR = ROOT / "static"
CACHE_FILE = BUILD_DIR / ".build_cache.json"
TEMPLATES_DIR = ROOT / "templates"
ABOUT_MD = NOTES_DIR / "about.md"
STATIC_ITEMS = [
    STATIC_DIR,
    ROOT / "_redirects",
]

DEFAULT_CACHE = {
    "templates_mtime": 0,
    "nav_hash": "",
    "wikilinks_hash": "",
    "notes": {},
    "about_md_mtime": 0,
}

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
WHITESPACE_RE = re.compile(r"\s+")
WIKILINK_RE = r"\[\[([^\]]+)\]\]"


@dataclass(frozen=True)
class NoteInfo:
    path: Path
    rel: Path
    title: str
    public: bool
    content: str
    metadata_hash: str


@dataclass
class NoteIndex:
    notes: list[NoteInfo]
    by_path: dict[Path, NoteInfo]


def new_cache() -> dict:
    """Create a fresh build cache with defaults."""
    cache = DEFAULT_CACHE.copy()
    cache["notes"] = {}
    return cache


def load_cache() -> dict:
    """Load build cache, return defaults if missing/corrupt."""
    cache = new_cache()
    if CACHE_FILE.exists():
        try:
            loaded = json.loads(CACHE_FILE.read_text())
        except (json.JSONDecodeError, TypeError):
            loaded = None
        if isinstance(loaded, dict):
            for key in cache:
                if key in loaded:
                    cache[key] = loaded[key]

    if not isinstance(cache.get("notes"), dict):
        cache["notes"] = {}
    return cache


def save_cache(cache: dict):
    """Persist cache to disk."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def cleanup_build_artifacts():
    """Remove build-only artifacts from output."""
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()


def parse_frontmatter(content: str) -> dict:
    """Parse YAML frontmatter into a flat dict."""
    match = FRONTMATTER_RE.match(content)
    if not match:
        return {}

    fm = {}
    for line in match.group(1).split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip().strip("\"'")
    return fm


def split_frontmatter(content: str) -> tuple[dict, str]:
    """Split YAML frontmatter from body content."""
    match = FRONTMATTER_RE.match(content)
    if not match:
        return {}, content
    fm = parse_frontmatter(content)
    body = content[match.end() :].lstrip("\n")
    return fm, body


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


def make_metadata_hash(title: str) -> str:
    """Hash only title for index invalidation."""
    return hashlib.md5(title.encode()).hexdigest()


def load_note_info(path: Path) -> NoteInfo:
    """Load note content and metadata from disk."""
    raw = path.read_text()
    fm, body = split_frontmatter(raw)
    title = fm.get("title", "")
    public = fm.get("public", "").lower() == "true"
    rel = path.relative_to(NOTES_DIR)
    return NoteInfo(
        path=path,
        rel=rel,
        title=title,
        public=public,
        content=body,
        metadata_hash=make_metadata_hash(title),
    )


def get_public_notes() -> NoteIndex:
    """Get all public notes with metadata parsed once."""
    if not NOTES_DIR.exists():
        return NoteIndex(notes=[], by_path={})

    notes: list[NoteInfo] = []
    by_path: dict[Path, NoteInfo] = {}
    for path in NOTES_DIR.rglob("*.md"):
        info = load_note_info(path)
        if info.public:
            notes.append(info)
            by_path[path] = info
    return NoteIndex(notes=notes, by_path=by_path)


def build_nav(public_notes: list[NoteInfo]) -> tuple[str, str]:
    """Build sidebar nav HTML and return HTML + hash."""
    entries: list[tuple[str, str, str, str]] = []
    for note in public_notes:
        rel = note.rel
        url = "/" + str(rel.with_suffix(""))
        title = note.title or note.path.stem
        parent = rel.parent.as_posix()
        entries.append((title.lower(), title, url, parent))

    entries.sort(key=lambda x: (x[0], x[2]))

    items: list[str] = []

    for _, title, url, parent in entries:
        safe_title = html.escape(title)
        parent_label = "" if parent in (".", "") else parent.replace("/", " / ")
        if parent_label:
            label = (
                f'<span class="nav-path">{html.escape(parent_label)}</span>'
                f'<span class="nav-title">{safe_title}</span>'
            )
        else:
            label = f'<span class="nav-title">{safe_title}</span>'
        items.append(
            f'<li class="nav-item"><a class="nav-link" href="{url}">{label}</a></li>'
        )

    nav_html = "\n".join(items)
    nav_hash = hashlib.md5(nav_html.encode()).hexdigest()
    return nav_html, nav_hash


def build_wikilink_map(public_notes: list[NoteInfo]) -> dict[str, str]:
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

    def extendMarkdown(self, md: Markdown):
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


def needs_rebuild(note: NoteInfo, cache: dict, templates_changed: bool) -> bool:
    """Check if a note needs rebuilding."""
    key = str(note.rel)
    cached = cache.get("notes", {}).get(key)

    if not cached:
        return True

    output = BUILD_DIR / cached["output"]
    if not output.exists():
        return True

    if templates_changed:
        return True

    if note.path.stat().st_mtime > cached["mtime"]:
        return True

    return False


def render_page(
    template,
    *,
    page_title: str,
    title: str,
    nav_html: str,
    content_html: str,
) -> str:
    """Render a full HTML page using Jinja templates."""
    return template.render(
        page_title=page_title,
        title=title,
        nav_html=nav_html,
        content_html=content_html,
    )


def build_note(
    note: NoteInfo,
    cache: dict,
    renderer: Markdown,
    template,
    nav_html: str,
) -> Path:
    """Build single note, return output path."""
    # foo.md → build/foo/index.html
    rel = note.rel
    output = BUILD_DIR / rel.with_suffix("") / "index.html"
    output.parent.mkdir(parents=True, exist_ok=True)

    content_html = render_markdown(renderer, note.content)
    page_title = note.title or note.path.stem
    page_html = render_page(
        template,
        page_title=page_title,
        title=note.title,
        nav_html=nav_html,
        content_html=content_html,
    )
    output.write_text(page_html)

    # Update cache
    key = str(rel)
    cache.setdefault("notes", {})[key] = {
        "mtime": note.path.stat().st_mtime,
        "metadata_hash": note.metadata_hash,
        "output": str(output.relative_to(BUILD_DIR)),
    }

    return output


def index_needs_rebuild(cache: dict, public_notes: list[NoteInfo]) -> bool:
    """Check if any note's metadata changed (requires index rebuild)."""
    for note in public_notes:
        key = str(note.rel)
        cached = cache.get("notes", {}).get(key, {})
        if cached.get("metadata_hash") != note.metadata_hash:
            return True

    # Also rebuild if about.md changed
    index_output = BUILD_DIR / "index.html"
    if not index_output.exists():
        return True
    about_mtime = ABOUT_MD.stat().st_mtime if ABOUT_MD.exists() else 0
    if about_mtime != cache.get("about_md_mtime", 0):
        return True

    return False


def build_index(
    cache: dict,
    renderer: Markdown,
    template,
    nav_html: str,
) -> Path:
    """Build index.html from about.md."""
    output = BUILD_DIR / "index.html"
    output.parent.mkdir(parents=True, exist_ok=True)

    # Build index content from about.md
    about_content = ABOUT_MD.read_text() if ABOUT_MD.exists() else ""
    fm, body = split_frontmatter(about_content)

    content_html = render_markdown(renderer, body)
    title = fm.get("title", "")
    page_title = title
    page_html = render_page(
        template,
        page_title=page_title,
        title=title,
        nav_html=nav_html,
        content_html=content_html,
    )
    output.write_text(page_html)

    # Update cache
    cache["about_md_mtime"] = ABOUT_MD.stat().st_mtime if ABOUT_MD.exists() else 0

    return output


def copy_if_newer(src: Path, dst: Path) -> bool:
    """Copy src to dst if src is newer. Return True if copied."""
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def sync_static_dir(src_dir: Path, dest_dir: Path) -> list[Path]:
    """Copy directory contents if changed. Return list of changed files."""
    changed = []
    if not src_dir.exists():
        return changed

    for src_file in src_dir.rglob("*"):
        if src_file.is_dir():
            continue

        rel = src_file.relative_to(src_dir)
        dst_file = dest_dir / rel

        if copy_if_newer(src_file, dst_file):
            changed.append(dst_file)

    return changed


def sync_static_file(src: Path, dest: Path) -> list[Path]:
    """Copy single file if changed. Return list of changed files."""
    changed = []
    if src.exists():
        if copy_if_newer(src, dest):
            changed.append(dest)
    else:
        if dest.exists():
            dest.unlink()
            changed.append(dest)
    return changed


def sync_static_items() -> list[Path]:
    """Sync static items into build root."""
    changed = []
    for src in STATIC_ITEMS:
        dest = BUILD_DIR / src.name
        if src.is_dir():
            changed.extend(sync_static_dir(src, dest))
        else:
            changed.extend(sync_static_file(src, dest))
    return changed


def cleanup_empty_dirs(start: Path, stop: Path):
    """Remove empty directories up to stop (exclusive)."""
    current = start
    while current != stop and current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def prune_private_notes(cache: dict, public_notes: list[NoteInfo]) -> bool:
    """Remove cached/build outputs for notes no longer public."""
    public_keys = {str(note.rel) for note in public_notes}
    removed = False

    for key in list(cache.get("notes", {}).keys()):
        if key in public_keys:
            continue
        cached = cache["notes"][key]
        output = BUILD_DIR / cached.get("output", "")
        if output.exists():
            output.unlink()
            cleanup_empty_dirs(output.parent, BUILD_DIR)
            removed = True
        del cache["notes"][key]
        removed = True

    return removed


def main():
    parser = argparse.ArgumentParser(description="Build static site")
    parser.add_argument("--all", action="store_true", help="Full rebuild")
    parser.add_argument("--note", type=Path, help="Build single note")
    parser.add_argument("--index", action="store_true", help="Rebuild index only")
    parser.add_argument("--static", action="store_true", help="Sync static files only")
    parser.add_argument("--clean", action="store_true", help="Remove build directory")
    parser.add_argument(
        "--json", action="store_true", help="Output changed files as JSON"
    )
    parser.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Keep build cache for incremental builds",
    )
    args = parser.parse_args()

    if args.clean:
        if BUILD_DIR.exists():
            shutil.rmtree(BUILD_DIR)
        print("Cleaned build/")
        return

    cache = load_cache()
    changed_files: list[Path] = []
    note_index = get_public_notes()
    public_notes = note_index.notes
    notes_pruned = prune_private_notes(cache, public_notes)

    nav_html, nav_hash = build_nav(public_notes)
    wikilink_map = build_wikilink_map(public_notes)
    wikilinks_payload = json.dumps(wikilink_map, sort_keys=True, indent=2)
    wikilinks_hash = hashlib.md5(wikilinks_payload.encode()).hexdigest()

    # Check if templates changed (affects all notes)
    templates_mtime = get_templates_mtime()
    templates_changed_flag = templates_changed(
        cache, templates_mtime, nav_hash, wikilinks_hash
    )

    template_env = get_template_env()
    template = template_env.get_template("base.html")
    renderer = build_markdown_renderer(wikilink_map)

    if args.all or templates_changed_flag:
        # Full rebuild
        for note in public_notes:
            output = build_note(note, cache, renderer, template, nav_html)
            changed_files.append(output)

        output = build_index(cache, renderer, template, nav_html)
        changed_files.append(output)

        changed_files.extend(sync_static_items())

    elif args.note:
        # Single note rebuild
        note_path = args.note if args.note.is_absolute() else ROOT / args.note
        if not note_path.exists():
            print(f"Error: {note_path} not found", file=sys.stderr)
            sys.exit(1)

        note_info = note_index.by_path.get(note_path)
        if note_info is None:
            try:
                note_info = load_note_info(note_path)
            except ValueError:
                content = note_path.read_text()
                fm = parse_frontmatter(content)
                if fm.get("public", "").lower() == "true":
                    raise
                note_info = None

        if not note_info or not note_info.public:
            if notes_pruned or index_needs_rebuild(cache, public_notes):
                output = build_index(
                    cache,
                    renderer,
                    template,
                    nav_html,
                )
                changed_files.append(output)
            update_template_cache(cache, templates_mtime, nav_hash, wikilinks_hash)
            changed_files.extend(sync_static_items())
            if args.keep_artifacts:
                save_cache(cache)
            else:
                cleanup_build_artifacts()
            print(f"Skipped private note: {note_path}")
            return

        output = build_note(note_info, cache, renderer, template, nav_html)
        changed_files.append(output)

        # Check if metadata changed → need index rebuild
        if notes_pruned or index_needs_rebuild(cache, public_notes):
            output = build_index(
                cache,
                renderer,
                template,
                nav_html,
            )
            changed_files.append(output)

        changed_files.extend(sync_static_items())

    elif args.index:
        output = build_index(cache, renderer, template, nav_html)
        changed_files.append(output)
        changed_files.extend(sync_static_items())

    elif args.static:
        changed_files.extend(sync_static_items())

    else:
        # Incremental: check what needs rebuilding
        for note in public_notes:
            if needs_rebuild(note, cache, templates_changed=templates_changed_flag):
                output = build_note(
                    note,
                    cache,
                    renderer,
                    template,
                    nav_html,
                )
                changed_files.append(output)

        if notes_pruned or index_needs_rebuild(cache, public_notes):
            output = build_index(
                cache,
                renderer,
                template,
                nav_html,
            )
            changed_files.append(output)

        changed_files.extend(sync_static_items())

    update_template_cache(cache, templates_mtime, nav_hash, wikilinks_hash)
    if args.keep_artifacts:
        save_cache(cache)
    else:
        cleanup_build_artifacts()

    # Output
    if args.json:
        print(json.dumps([str(p.relative_to(BUILD_DIR)) for p in changed_files]))
    else:
        for f in changed_files:
            print(f"Built: {f.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
