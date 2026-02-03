from __future__ import annotations

import shutil
from pathlib import Path

from paths import BUILD_DIR, STATIC_ITEMS


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
