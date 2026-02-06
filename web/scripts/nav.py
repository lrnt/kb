from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from notes import NoteInfo


@dataclass(frozen=True)
class NavEntry:
    sort_key: str
    title: str
    url: str
    parent: str


@dataclass(frozen=True)
class NavItem:
    title: str
    url: str
    parent_label: str


STATIC_NAV = [
    NavEntry(sort_key="books", title="Books", url="/books", parent=""),
    NavEntry(sort_key="recipes", title="Recipes", url="/recipes", parent=""),
]


def build_nav(public_notes: list["NoteInfo"]) -> tuple[list[NavItem], str]:
    """Build sidebar nav entries and return data + hash."""
    entries = list(STATIC_NAV)
    for note in public_notes:
        rel = note.rel
        url = "/" + str(rel.with_suffix(""))
        title = note.title or note.path.stem
        parent = rel.parent.as_posix()
        entries.append(NavEntry(title.lower(), title, url, parent))

    entries.sort(key=lambda entry: (entry.sort_key, entry.url))

    items: list[NavItem] = []

    for entry in entries:
        parent_label = (
            "" if entry.parent in (".", "") else entry.parent.replace("/", " / ")
        )
        items.append(
            NavItem(
                title=entry.title,
                url=entry.url,
                parent_label=parent_label,
            )
        )

    payload = json.dumps(
        [asdict(item) for item in items],
        sort_keys=True,
        separators=(",", ":"),
    )
    nav_hash = hashlib.md5(payload.encode()).hexdigest()
    return items, nav_hash
