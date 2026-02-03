#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = ["jinja2", "markdown"]
# ///
"""Incremental build system for static site."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from notes import (
    build_nav,
    get_public_notes,
    load_note_info,
    parse_frontmatter,
    split_frontmatter,
)
from paths import ABOUT_MD, BUILD_DIR, CACHE_FILE, ROOT
from render import (
    build_markdown_renderer,
    build_wikilink_map,
    get_template_env,
    get_templates_mtime,
    render_markdown,
    render_page,
    templates_changed,
    update_template_cache,
)
from static import cleanup_empty_dirs, sync_static_items

if TYPE_CHECKING:
    from markdown import Markdown
    from notes import NoteInfo

DEFAULT_CACHE = {
    "templates_mtime": 0,
    "nav_hash": "",
    "wikilinks_hash": "",
    "notes": {},
    "about_md_mtime": 0,
}


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


def needs_rebuild(note: "NoteInfo", cache: dict, templates_changed: bool) -> bool:
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
    note: "NoteInfo",
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


def index_needs_rebuild(cache: dict, public_notes: list["NoteInfo"]) -> bool:
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


def prune_private_notes(cache: dict, public_notes: list["NoteInfo"]) -> bool:
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

    templates_mtime = get_templates_mtime()
    templates_changed_flag = templates_changed(
        cache, templates_mtime, nav_hash, wikilinks_hash
    )

    template_env = get_template_env()
    template = template_env.get_template("base.html")
    renderer = build_markdown_renderer(wikilink_map)

    if args.all or templates_changed_flag:
        for note in public_notes:
            output = build_note(note, cache, renderer, template, nav_html)
            changed_files.append(output)

        output = build_index(cache, renderer, template, nav_html)
        changed_files.append(output)

        changed_files.extend(sync_static_items())

    elif args.note:
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

    if args.json:
        print(json.dumps([str(p.relative_to(BUILD_DIR)) for p in changed_files]))
    else:
        for f in changed_files:
            print(f"Built: {f.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
