#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = ["livereload"]
# ///
"""Live reload dev server with smart incremental builds."""

import json
import subprocess
import sys
from pathlib import Path

from livereload import Server

ROOT = Path(__file__).parent.parent
BUILD_DIR = ROOT / "build"
BUILD_SCRIPT = ROOT / "scripts" / "build.py"
VAULT_DIR = ROOT.parent.parent / "vault"
STATIC_ITEMS = [
    ROOT / "static",
    ROOT / "_redirects",
]


def run_build(*args) -> list[str]:
    """Run build.py with args, return list of changed files."""
    cmd = ["uv", "run", str(BUILD_SCRIPT), "--json", "--keep-artifacts", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)

    if result.returncode != 0:
        print(f"Build error: {result.stderr}", file=sys.stderr)
        return []

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return []


def build_incremental():
    """Incremental build (default)."""
    changed = run_build()
    print(f"Incremental build: {len(changed)} files")
    return changed


def build_static():
    """Sync static items."""
    changed = run_build("--static")
    print(f"Static sync: {changed}")
    return changed


if __name__ == "__main__":
    # Initial build
    print("Initial build...")
    run_build("--all")

    server = Server()

    # Watch notes -> incremental rebuild
    server.watch(str(VAULT_DIR / "**/*.md"), build_incremental)

    # Watch templates -> incremental rebuild (auto-detects full rebuild)
    server.watch(str(ROOT / "templates" / "**/*.html"), build_incremental)

    # Watch static items -> sync only
    for item in STATIC_ITEMS:
        server.watch(str(item), build_static)

    print("Starting dev server at http://localhost:8000")
    server.serve(root=str(BUILD_DIR), port=8000, open_url_delay=0.5)
