from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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
