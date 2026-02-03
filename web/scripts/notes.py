from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import re
from pathlib import Path

from paths import NOTES_DIR

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


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
