#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Incremental build system for static site."""

import argparse
from dataclasses import dataclass
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Paths (relative to web/)
ROOT = Path(__file__).parent.parent
NOTES_DIR = ROOT.parent / "vault"
BUILD_DIR = ROOT / "build"
STATIC_DIR = ROOT / "static"
CACHE_FILE = BUILD_DIR / ".build_cache.json"
HEADER_TEMPLATE = ROOT / "header.html"
FOOTER_TEMPLATE = ROOT / "footer.html"
INCLUDES_DIR = BUILD_DIR / "_includes"
HEADER_INCLUDE = INCLUDES_DIR / "header.html"
FILTERS_DIR = ROOT / "scripts" / "filters"
WIKILINK_FILTER = FILTERS_DIR / "wikilinks.lua"
WIKILINKS_META = INCLUDES_DIR / "wikilinks.json"
ABOUT_MD = NOTES_DIR / "about.md"
STATIC_ITEMS = [
    STATIC_DIR,
    ROOT / "_redirects",
]

PANDOC_FLAGS = [
    "-f",
    "markdown+wikilinks_title_after_pipe",
    "-t",
    "html5",
    "-s",
    "-c",
    "/static/style.css",
    "--section-divs",
]

NAV_PLACEHOLDER = "__NAV__"

DEFAULT_CACHE = {
    "header_mtime": 0,
    "footer_mtime": 0,
    "nav_hash": "",
    "wikilinks_hash": "",
    "notes": {},
    "static_mtime": 0,
    "about_md_mtime": 0,
    "assets": [],
}

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
WHITESPACE_RE = re.compile(r"\s+")
ASSET_PATTERNS = [
    re.compile(r"!\[[^\]]*\]\(([^)]+)\)"),
    re.compile(r"\[[^\]]*\]\(([^)]+)\)"),
    re.compile(r"!\[\[([^\]]+)\]\]"),
    re.compile(r"\[\[([^\]]+)\]\]"),
]


@dataclass(frozen=True)
class NoteInfo:
    path: Path
    rel: Path
    title: str
    date: str
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
    cache["assets"] = []
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
    if not isinstance(cache.get("assets"), list):
        cache["assets"] = []
    return cache


def save_cache(cache: dict):
    """Persist cache to disk."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def cleanup_build_artifacts():
    """Remove build-only artifacts from output."""
    if INCLUDES_DIR.exists():
        shutil.rmtree(INCLUDES_DIR)
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


def make_metadata_hash(title: str, date: str) -> str:
    """Hash only title+date for index invalidation."""
    return hashlib.md5(f"{title}|{date}".encode()).hexdigest()


def load_note_info(path: Path) -> NoteInfo:
    """Load note content and metadata from disk."""
    content = path.read_text()
    fm = parse_frontmatter(content)
    title = fm.get("title", "")
    date = fm.get("date", "")
    public = fm.get("public", "").lower() == "true"
    rel = path.relative_to(NOTES_DIR)
    return NoteInfo(
        path=path,
        rel=rel,
        title=title,
        date=date,
        public=public,
        content=content,
        metadata_hash=make_metadata_hash(title, date),
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


def write_wikilinks_meta(payload: str):
    """Write wikilinks metadata file into build/_includes."""
    INCLUDES_DIR.mkdir(parents=True, exist_ok=True)
    WIKILINKS_META.write_text(payload)


def render_header(nav_html: str) -> str:
    """Render header include with sidebar nav."""
    if not HEADER_TEMPLATE.exists():
        return nav_html
    template = HEADER_TEMPLATE.read_text()
    if NAV_PLACEHOLDER in template:
        return template.replace(NAV_PLACEHOLDER, nav_html)
    return f"{template}\n{nav_html}"


def write_header_include(nav_html: str):
    """Write rendered header include into build/_includes."""
    INCLUDES_DIR.mkdir(parents=True, exist_ok=True)
    HEADER_INCLUDE.write_text(render_header(nav_html))


def includes_changed(
    cache: dict,
    header_mtime: float,
    footer_mtime: float,
    nav_hash: str,
    wikilinks_hash: str,
) -> bool:
    """Check if shared includes require rebuilds."""
    return (
        header_mtime > cache.get("header_mtime", 0)
        or footer_mtime > cache.get("footer_mtime", 0)
        or nav_hash != cache.get("nav_hash", "")
        or wikilinks_hash != cache.get("wikilinks_hash", "")
        or not HEADER_INCLUDE.exists()
        or not WIKILINKS_META.exists()
    )


def update_includes_cache(
    cache: dict,
    header_mtime: float,
    footer_mtime: float,
    nav_hash: str,
    wikilinks_hash: str,
):
    """Persist include-related values into cache."""
    cache["header_mtime"] = header_mtime
    cache["footer_mtime"] = footer_mtime
    cache["nav_hash"] = nav_hash
    cache["wikilinks_hash"] = wikilinks_hash


def needs_rebuild(note: NoteInfo, cache: dict, header_changed: bool) -> bool:
    """Check if a note needs rebuilding."""
    key = str(note.rel)
    cached = cache.get("notes", {}).get(key)

    if not cached:
        return True

    output = BUILD_DIR / cached["output"]
    if not output.exists():
        return True

    if header_changed:
        return True

    if note.path.stat().st_mtime > cached["mtime"]:
        return True

    return False


def build_pandoc_command(
    header_include: Path,
    footer_include: Path,
    wikilinks_meta: Path,
) -> list[str]:
    """Build the base pandoc command with shared flags."""
    cmd = ["pandoc", *PANDOC_FLAGS]
    if WIKILINK_FILTER.exists():
        cmd.extend(["--lua-filter", str(WIKILINK_FILTER)])
    if wikilinks_meta and wikilinks_meta.exists():
        cmd.extend(["--metadata-file", str(wikilinks_meta)])
    if header_include and header_include.exists():
        cmd.extend(["-B", str(header_include)])
    if footer_include and footer_include.exists():
        cmd.extend(["-A", str(footer_include)])
    return cmd


def run_pandoc_file(
    input_path: Path,
    output: Path,
    header_include: Path,
    footer_include: Path,
    wikilinks_meta: Path,
):
    """Run pandoc for a file input."""
    cmd = build_pandoc_command(header_include, footer_include, wikilinks_meta)
    cmd.extend([str(input_path), "-o", str(output)])
    subprocess.run(cmd, check=True, cwd=ROOT)


def run_pandoc_text(
    content: str,
    output: Path,
    header_include: Path,
    footer_include: Path,
    wikilinks_meta: Path,
):
    """Run pandoc for a text input."""
    cmd = build_pandoc_command(header_include, footer_include, wikilinks_meta)
    cmd.extend(["-o", str(output)])
    subprocess.run(cmd, input=content.encode(), check=True, cwd=ROOT)


def build_note(
    note: NoteInfo,
    cache: dict,
    header_include: Path,
    footer_include: Path,
    wikilinks_meta: Path,
) -> Path:
    """Build single note, return output path."""
    # foo.md → build/foo/index.html
    rel = note.rel
    output = BUILD_DIR / rel.with_suffix("") / "index.html"
    output.parent.mkdir(parents=True, exist_ok=True)

    run_pandoc_file(note.path, output, header_include, footer_include, wikilinks_meta)

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
    header_include: Path,
    footer_include: Path,
    wikilinks_meta: Path,
) -> Path:
    """Build index.html from about.md."""
    output = BUILD_DIR / "index.html"
    output.parent.mkdir(parents=True, exist_ok=True)

    # Build index content from about.md
    about_content = ABOUT_MD.read_text() if ABOUT_MD.exists() else ""

    run_pandoc_text(
        about_content, output, header_include, footer_include, wikilinks_meta
    )

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


def normalize_link_target(raw: str) -> str:
    """Normalize link target to a filesystem-like path."""
    target = raw.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    if " " in target:
        target = target.split(" ", 1)[0]
    if "|" in target:
        target = target.split("|", 1)[0]
    if "#" in target:
        target = target.split("#", 1)[0]
    return target.strip()


def extract_asset_paths(note: NoteInfo) -> set[Path]:
    """Extract local asset paths referenced by a note."""
    content = note.content
    assets: set[Path] = set()

    for pattern in ASSET_PATTERNS:
        for raw in pattern.findall(content):
            target = normalize_link_target(raw)
            if not target:
                continue
            if "://" in target or target.startswith("mailto:"):
                continue
            suffix = Path(target).suffix.lower()
            if not suffix or suffix == ".md":
                continue

            if target.startswith("/"):
                asset_path = NOTES_DIR / target.lstrip("/")
            else:
                asset_path = note.path.parent / target
            try:
                asset_path = asset_path.resolve()
            except FileNotFoundError:
                continue
            if not asset_path.is_file():
                continue
            if not asset_path.is_relative_to(NOTES_DIR):
                continue
            assets.add(asset_path)

    return assets


def cleanup_empty_dirs(start: Path, stop: Path):
    """Remove empty directories up to stop (exclusive)."""
    current = start
    while current != stop and current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def sync_note_assets(cache: dict, public_notes: list[NoteInfo]) -> list[Path]:
    """Copy assets referenced by public notes into build/.

    Returns list of changed files.
    """
    changed = []
    desired_assets: set[Path] = set()

    for note in public_notes:
        desired_assets.update(extract_asset_paths(note))

    desired_rel = {asset.relative_to(NOTES_DIR) for asset in desired_assets}
    cached_assets = set(cache.get("assets", []))

    for asset in desired_assets:
        rel = asset.relative_to(NOTES_DIR)
        dst_file = BUILD_DIR / rel

        if copy_if_newer(asset, dst_file):
            changed.append(dst_file)

    for rel_str in cached_assets - {str(r) for r in desired_rel}:
        stale = BUILD_DIR / rel_str
        if stale.exists():
            stale.unlink()
            cleanup_empty_dirs(stale.parent, BUILD_DIR)
            changed.append(stale)

    cache["assets"] = sorted(str(r) for r in desired_rel)
    return changed


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
        help="Keep build cache and includes for incremental builds",
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
    wikilinks_payload = json.dumps(
        {"wikilinks": wikilink_map}, sort_keys=True, indent=2
    )
    wikilinks_hash = hashlib.md5(wikilinks_payload.encode()).hexdigest()

    # Check if includes changed (affects all notes)
    header_mtime = HEADER_TEMPLATE.stat().st_mtime if HEADER_TEMPLATE.exists() else 0
    footer_mtime = FOOTER_TEMPLATE.stat().st_mtime if FOOTER_TEMPLATE.exists() else 0
    header_changed = includes_changed(
        cache, header_mtime, footer_mtime, nav_hash, wikilinks_hash
    )

    if header_changed:
        write_header_include(nav_html)
        write_wikilinks_meta(wikilinks_payload)

    header_include = HEADER_INCLUDE if HEADER_INCLUDE.exists() else HEADER_TEMPLATE
    footer_include = FOOTER_TEMPLATE
    wikilinks_meta = WIKILINKS_META

    if args.all or header_changed:
        # Full rebuild
        for note in public_notes:
            output = build_note(
                note, cache, header_include, footer_include, wikilinks_meta
            )
            changed_files.append(output)

        output = build_index(cache, header_include, footer_include, wikilinks_meta)
        changed_files.append(output)

        changed_files.extend(sync_static_items())
        changed_files.extend(sync_note_assets(cache, public_notes))

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
                    header_include,
                    footer_include,
                    wikilinks_meta,
                )
                changed_files.append(output)
            changed_files.extend(sync_note_assets(cache, public_notes))
            update_includes_cache(
                cache, header_mtime, footer_mtime, nav_hash, wikilinks_hash
            )
            changed_files.extend(sync_static_items())
            if args.keep_artifacts:
                save_cache(cache)
            else:
                cleanup_build_artifacts()
            print(f"Skipped private note: {note_path}")
            return

        output = build_note(
            note_info, cache, header_include, footer_include, wikilinks_meta
        )
        changed_files.append(output)

        # Check if metadata changed → need index rebuild
        if notes_pruned or index_needs_rebuild(cache, public_notes):
            output = build_index(
                cache,
                header_include,
                footer_include,
                wikilinks_meta,
            )
            changed_files.append(output)

        changed_files.extend(sync_note_assets(cache, public_notes))
        changed_files.extend(sync_static_items())

    elif args.index:
        output = build_index(cache, header_include, footer_include, wikilinks_meta)
        changed_files.append(output)
        changed_files.extend(sync_static_items())

    elif args.static:
        changed_files.extend(sync_static_items())
        changed_files.extend(sync_note_assets(cache, public_notes))

    else:
        # Incremental: check what needs rebuilding
        for note in public_notes:
            if needs_rebuild(note, cache, header_changed=header_changed):
                output = build_note(
                    note,
                    cache,
                    header_include,
                    footer_include,
                    wikilinks_meta,
                )
                changed_files.append(output)

        if notes_pruned or index_needs_rebuild(cache, public_notes):
            output = build_index(
                cache,
                header_include,
                footer_include,
                wikilinks_meta,
            )
            changed_files.append(output)

        changed_files.extend(sync_static_items())
        changed_files.extend(sync_note_assets(cache, public_notes))

    update_includes_cache(cache, header_mtime, footer_mtime, nav_hash, wikilinks_hash)
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
