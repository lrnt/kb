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

from books import (
    build_books_index,
    books_index_needs_rebuild,
    get_books,
    sync_book_covers,
)
from notes import (
    build_index,
    build_nav,
    build_note,
    get_public_notes,
    index_needs_rebuild,
    load_note_info,
    needs_rebuild,
    parse_frontmatter,
    prune_private_notes,
)
from paths import BUILD_DIR, CACHE_FILE, ROOT
from recipes import (
    build_recipe,
    build_recipes_index,
    get_recipes,
    prune_removed_recipes,
    recipe_needs_rebuild,
    recipes_index_needs_rebuild,
)
from render import (
    build_markdown_renderer,
    build_wikilink_map,
    get_template_env,
    get_templates_mtime,
    templates_changed,
    update_template_cache,
)
from static import sync_static_items

DEFAULT_CACHE = {
    "templates_mtime": 0,
    "nav_hash": "",
    "wikilinks_hash": "",
    "notes": {},
    "recipes": {},
    "books": {},
    "about_md_mtime": 0,
}


def new_cache() -> dict:
    """Create a fresh build cache with defaults."""
    cache = DEFAULT_CACHE.copy()
    cache["notes"] = {}
    cache["recipes"] = {}
    cache["books"] = {}
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
    if not isinstance(cache.get("recipes"), dict):
        cache["recipes"] = {}
    if not isinstance(cache.get("books"), dict):
        cache["books"] = {}
    return cache


def save_cache(cache: dict):
    """Persist cache to disk."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def cleanup_build_artifacts():
    """Remove build-only artifacts from output."""
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()


def sync_build_assets(books: list) -> list[Path]:
    changed = []
    changed.extend(sync_static_items())
    changed.extend(sync_book_covers(books))
    return changed


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
    recipe_index = get_recipes()
    recipes = recipe_index.recipes
    recipes_pruned = prune_removed_recipes(cache, recipes)
    book_index = get_books()
    books = book_index.books

    nav_html, nav_hash = build_nav(public_notes)
    wikilink_map = build_wikilink_map(public_notes)
    wikilinks_payload = json.dumps(wikilink_map, sort_keys=True, indent=2)
    wikilinks_hash = hashlib.md5(wikilinks_payload.encode()).hexdigest()

    templates_mtime = get_templates_mtime()
    templates_changed_flag = templates_changed(
        cache, templates_mtime, nav_hash, wikilinks_hash
    )

    template_env = get_template_env()
    base_template = template_env.get_template("base.html")
    recipe_template = template_env.get_template("recipe.html")
    recipes_template = template_env.get_template("recipes.html")
    books_template = template_env.get_template("books.html")
    renderer = build_markdown_renderer(wikilink_map)

    if args.all or templates_changed_flag:
        for note in public_notes:
            output = build_note(note, cache, renderer, base_template, nav_html)
            changed_files.append(output)

        output = build_index(cache, renderer, base_template, nav_html)
        changed_files.append(output)

        for recipe in recipes:
            output = build_recipe(
                recipe,
                recipe_index.by_path,
                cache,
                recipe_template,
                nav_html,
            )
            changed_files.append(output)

        output = build_recipes_index(recipes, recipes_template, nav_html)
        changed_files.append(output)

        output = build_books_index(books, books_template, nav_html, cache)
        changed_files.append(output)

        changed_files.extend(sync_build_assets(books))

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
                    base_template,
                    nav_html,
                )
                changed_files.append(output)
            update_template_cache(cache, templates_mtime, nav_hash, wikilinks_hash)
            changed_files.extend(sync_build_assets(books))
            if args.keep_artifacts:
                save_cache(cache)
            else:
                cleanup_build_artifacts()
            print(f"Skipped private note: {note_path}")
            return

        output = build_note(note_info, cache, renderer, base_template, nav_html)
        changed_files.append(output)

        if notes_pruned or index_needs_rebuild(cache, public_notes):
            output = build_index(
                cache,
                renderer,
                base_template,
                nav_html,
            )
            changed_files.append(output)

        changed_files.extend(sync_build_assets(books))

    elif args.index:
        output = build_index(cache, renderer, base_template, nav_html)
        changed_files.append(output)
        output = build_recipes_index(recipes, recipes_template, nav_html)
        changed_files.append(output)
        output = build_books_index(books, books_template, nav_html, cache)
        changed_files.append(output)
        changed_files.extend(sync_build_assets(books))

    elif args.static:
        changed_files.extend(sync_build_assets(books))

    else:
        for note in public_notes:
            if needs_rebuild(note, cache, templates_changed=templates_changed_flag):
                output = build_note(
                    note,
                    cache,
                    renderer,
                    base_template,
                    nav_html,
                )
                changed_files.append(output)

        if notes_pruned or index_needs_rebuild(cache, public_notes):
            output = build_index(
                cache,
                renderer,
                base_template,
                nav_html,
            )
            changed_files.append(output)

        for recipe in recipes:
            if recipe_needs_rebuild(
                recipe,
                recipe_index.by_path,
                cache,
                templates_changed=templates_changed_flag,
            ):
                output = build_recipe(
                    recipe,
                    recipe_index.by_path,
                    cache,
                    recipe_template,
                    nav_html,
                )
                changed_files.append(output)

        if recipes_pruned or recipes_index_needs_rebuild(cache, recipes):
            output = build_recipes_index(recipes, recipes_template, nav_html)
            changed_files.append(output)

        if books_index_needs_rebuild(cache, books):
            output = build_books_index(books, books_template, nav_html, cache)
            changed_files.append(output)

        changed_files.extend(sync_build_assets(books))

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
