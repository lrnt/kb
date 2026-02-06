from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from pathlib import Path

from paths import BOOKS_DIR, BUILD_DIR
from static import cleanup_empty_dirs, copy_if_newer

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
ISBN_RE = re.compile(r"\D+")
WORD_RE = re.compile(r"[A-Za-z0-9]+")

COVER_URL_ROOT = "/static/books"

STATUS_ORDER = ("reading", "finished", "to-read")
STATUS_LABELS = {
    "reading": "Currently reading",
    "finished": "Finished",
    "to-read": "To read",
}


@dataclass(frozen=True)
class BookInfo:
    path: Path
    rel: Path
    title: str
    author: str
    year: str
    isbn13: str
    status: str
    created: str
    started: str
    finished: str
    rating: str
    cover_url: str
    initials: str
    metadata_hash: str


@dataclass
class BookIndex:
    books: list[BookInfo]
    by_path: dict[Path, BookInfo]


def strip_quotes(value: str) -> str:
    trimmed = value.strip()
    if len(trimmed) < 2:
        return trimmed
    if trimmed[0] == trimmed[-1] and trimmed[0] in ('"', "'"):
        return trimmed[1:-1].strip()
    return trimmed


def parse_frontmatter_block(raw: str) -> dict:
    lines = raw.splitlines()
    fm: dict[str, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if ":" not in line:
            index += 1
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        value = rest.strip()
        if value == "":
            index += 1
            block_lines: list[str] = []
            while index < len(lines):
                next_line = lines[index]
                if not next_line.strip():
                    index += 1
                    continue
                if re.match(r"^\s", next_line):
                    block_lines.append(next_line.strip())
                    index += 1
                    continue
                break
            value = strip_quotes(" ".join(block_lines).strip())
            fm[key] = value
            continue
        fm[key] = strip_quotes(value)
        index += 1
    return fm


def parse_frontmatter(content: str) -> dict:
    match = FRONTMATTER_RE.match(content)
    if not match:
        return {}
    return parse_frontmatter_block(match.group(1))


def normalize_isbn(value: str) -> str:
    digits = ISBN_RE.sub("", value or "")
    if len(digits) in (10, 13):
        return digits
    return ""


def build_initials(title: str) -> str:
    words = WORD_RE.findall(title)
    if not words:
        return ""
    if len(words) == 1:
        return words[0][:3].upper()
    return (words[0][0] + words[1][0]).upper()


def normalize_status(raw: str) -> str:
    status = (raw or "").strip().lower().replace("_", "-")
    if status in STATUS_ORDER:
        return status
    return "to-read"


def make_metadata_hash(*fields: str) -> str:
    payload = "|".join(field.strip() for field in fields)
    return hashlib.md5(payload.encode()).hexdigest()


def load_book(path: Path) -> BookInfo | None:
    raw = path.read_text()
    fm = parse_frontmatter(raw)
    title = fm.get("title", "").strip() or path.stem
    author = fm.get("author", "").strip()
    year = fm.get("year", "").strip()
    isbn13 = normalize_isbn(fm.get("isbn13", ""))
    status = normalize_status(fm.get("status", ""))
    created = fm.get("created", "").strip()
    started = fm.get("started", "").strip()
    finished = fm.get("finished", "").strip()
    rating = fm.get("rating", "").strip()
    rel = path.relative_to(BOOKS_DIR)
    cover_rel = rel.with_suffix(".jpg")
    cover_path = path.with_suffix(".jpg")
    cover_url = (
        f"{COVER_URL_ROOT}/{cover_rel.as_posix()}" if cover_path.exists() else ""
    )
    initials = build_initials(title)
    metadata_hash = make_metadata_hash(
        title,
        author,
        year,
        isbn13,
        status,
        created,
        started,
        finished,
        rating,
        cover_url,
    )
    return BookInfo(
        path=path,
        rel=rel,
        title=title,
        author=author,
        year=year,
        isbn13=isbn13,
        status=status,
        created=created,
        started=started,
        finished=finished,
        rating=rating,
        cover_url=cover_url,
        initials=initials,
        metadata_hash=metadata_hash,
    )


def get_books() -> BookIndex:
    if not BOOKS_DIR.exists():
        return BookIndex(books=[], by_path={})
    books: list[BookInfo] = []
    by_path: dict[Path, BookInfo] = {}
    for path in BOOKS_DIR.rglob("*.md"):
        info = load_book(path)
        if info is None:
            continue
        books.append(info)
        by_path[info.rel] = info
    return BookIndex(books=books, by_path=by_path)


def date_sort_key(value: str) -> int:
    if not value:
        return 0
    parts = value.split("-")
    if len(parts) != 3:
        return 0
    if not all(part.isdigit() for part in parts):
        return 0
    return int("".join(parts))


def build_book_card(book: BookInfo) -> dict:
    return {
        "title": book.title,
        "author": book.author,
        "year": book.year,
        "cover_url": book.cover_url,
        "cover_alt": f"Cover of {book.title}",
        "initials": book.initials,
        "rating": book.rating,
    }


def build_sections(books: list[BookInfo]) -> list[dict]:
    grouped = {status: [] for status in STATUS_ORDER}
    for book in books:
        grouped.setdefault(book.status, []).append(book)

    sections: list[dict] = []
    for status in STATUS_ORDER:
        items = grouped.get(status, [])
        if status == "reading":
            items = sorted(
                items,
                key=lambda book: (-date_sort_key(book.started), book.title.lower()),
            )
        elif status == "finished":
            items = sorted(
                items,
                key=lambda book: (-date_sort_key(book.finished), book.title.lower()),
            )
        else:
            items = sorted(
                items,
                key=lambda book: (-date_sort_key(book.created), book.title.lower()),
            )
        if not items:
            continue
        sections.append(
            {
                "key": status,
                "label": STATUS_LABELS.get(status, status.title()),
                "books": [build_book_card(book) for book in items],
            }
        )
    return sections


def books_index_needs_rebuild(cache: dict, books: list[BookInfo]) -> bool:
    output = BUILD_DIR / "books" / "index.html"
    if not output.exists():
        return True
    cached = cache.get("books", {})
    keys = {str(book.rel) for book in books}
    if set(cached.keys()) != keys:
        return True
    for book in books:
        key = str(book.rel)
        if cached.get(key, {}).get("metadata_hash") != book.metadata_hash:
            return True
    return False


def build_books_index(
    books: list[BookInfo],
    template,
    nav_html: str,
    cache: dict,
):
    output = BUILD_DIR / "books" / "index.html"
    output.parent.mkdir(parents=True, exist_ok=True)

    sections = build_sections(books)
    page_html = template.render(
        page_title="Books",
        title="Books",
        nav_html=nav_html,
        sections=sections,
    )
    output.write_text(page_html)

    cache["books"] = {
        str(book.rel): {"metadata_hash": book.metadata_hash} for book in books
    }
    return output


def sync_book_covers(books: list[BookInfo]) -> list[Path]:
    changed: list[Path] = []
    cover_root = BUILD_DIR / "static" / "books"
    keep_rel: set[str] = set()

    for book in books:
        rel_cover = book.rel.with_suffix(".jpg")
        keep_rel.add(rel_cover.as_posix())
        src = book.path.with_suffix(".jpg")
        dest = cover_root / rel_cover
        if src.exists():
            if copy_if_newer(src, dest):
                changed.append(dest)
        else:
            if dest.exists():
                dest.unlink()
                changed.append(dest)
                cleanup_empty_dirs(dest.parent, cover_root)

    if cover_root.exists():
        for dest in cover_root.rglob("*.jpg"):
            rel = dest.relative_to(cover_root).as_posix()
            if rel in keep_rel:
                continue
            dest.unlink()
            changed.append(dest)
            cleanup_empty_dirs(dest.parent, cover_root)

    return changed
