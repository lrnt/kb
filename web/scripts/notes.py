from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
from pathlib import Path
from typing import TYPE_CHECKING

from frontmatter import split_frontmatter
from paths import ABOUT_MD, BUILD_DIR, NOTES_DIR
from render import render_markdown, render_page
from static import cleanup_empty_dirs

if TYPE_CHECKING:
    from markdown import Markdown


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
    entries.append(("books", "Books", "/books", ""))
    entries.append(("recipes", "Recipes", "/recipes", ""))
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


def build_note(
    note: NoteInfo,
    cache: dict,
    renderer: "Markdown",
    template,
    nav_html: str,
):
    """Build single note, return output path."""
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

    index_output = BUILD_DIR / "index.html"
    if not index_output.exists():
        return True
    about_mtime = ABOUT_MD.stat().st_mtime if ABOUT_MD.exists() else 0
    if about_mtime != cache.get("about_md_mtime", 0):
        return True

    return False


def build_index(
    cache: dict,
    renderer: "Markdown",
    template,
    nav_html: str,
):
    """Build index.html from about.md."""
    output = BUILD_DIR / "index.html"
    output.parent.mkdir(parents=True, exist_ok=True)

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

    cache["about_md_mtime"] = ABOUT_MD.stat().st_mtime if ABOUT_MD.exists() else 0

    return output


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
