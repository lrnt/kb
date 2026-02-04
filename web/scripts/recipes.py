from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from pathlib import Path

from notes import split_frontmatter
from paths import RECIPES_DIR

INGREDIENT_RE = re.compile(
    r"@(?P<name>[^@#~{\n]+)(?:\{(?P<qty>[^}%]*)(?:%(?P<unit>[^}]*))?\})?"
)
COOKWARE_RE = re.compile(r"#(?P<name>[^@#~{\n]+)(?:\{[^}]*\})?")
TIMER_RE = re.compile(
    r"~(?:(?P<name>[^@#~{\n]+))?\{(?P<qty>[^}%]*)(?:%(?P<unit>[^}]*))?\}"
)
WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class Ingredient:
    name: str
    quantity: str
    unit: str


@dataclass(frozen=True)
class Cookware:
    name: str


@dataclass(frozen=True)
class Timer:
    name: str
    quantity: str
    unit: str


@dataclass(frozen=True)
class RecipeStep:
    kind: str
    text: str


@dataclass(frozen=True)
class RecipeInfo:
    path: Path
    rel: Path
    slug: Path
    title: str
    servings: str
    ingredients: list[Ingredient]
    cookware: list[Cookware]
    timers: list[Timer]
    steps: list[RecipeStep]
    metadata_hash: str
    public: bool


@dataclass
class RecipeIndex:
    recipes: list[RecipeInfo]
    by_path: dict[Path, RecipeInfo]


def clean_token_name(name: str) -> str:
    cleaned = name.strip()
    cleaned = cleaned.strip("(")
    cleaned = cleaned.rstrip(",.;:!?)")
    return cleaned.strip()


def make_metadata_hash(title: str) -> str:
    """Hash only title for index invalidation."""
    return hashlib.md5(title.encode()).hexdigest()


def parse_cooklang(
    content: str,
) -> tuple[list[Ingredient], list[Cookware], list[Timer], list[RecipeStep]]:
    ingredients: list[Ingredient] = []
    seen: set[tuple[str, str, str]] = set()
    cookware: list[Cookware] = []
    cookware_seen: set[str] = set()
    timers: list[Timer] = []
    steps: list[RecipeStep] = []
    current_lines: list[str] = []

    def replace_ingredient(match: re.Match) -> str:
        name = clean_token_name(match.group("name") or "")
        qty = (match.group("qty") or "").strip()
        unit = (match.group("unit") or "").strip()
        if name:
            key = (name.lower(), qty, unit)
            if key not in seen:
                ingredients.append(Ingredient(name=name, quantity=qty, unit=unit))
                seen.add(key)
        return name

    def replace_cookware(match: re.Match) -> str:
        name = clean_token_name(match.group("name") or "")
        if name:
            key = name.lower()
            if key not in cookware_seen:
                cookware.append(Cookware(name=name))
                cookware_seen.add(key)
        return name

    def replace_timer(match: re.Match) -> str:
        name = clean_token_name(match.group("name") or "")
        qty = (match.group("qty") or "").strip()
        unit = (match.group("unit") or "").strip()
        if name or qty or unit:
            timers.append(Timer(name=name, quantity=qty, unit=unit))
        duration = " ".join(part for part in (qty, unit) if part)
        if name and duration:
            return f"{name} ({duration})"
        return name or duration

    def flush_step():
        if not current_lines:
            return
        combined = " ".join(current_lines)
        combined = WHITESPACE_RE.sub(" ", combined).strip()
        if combined:
            steps.append(RecipeStep(kind="step", text=combined))
        current_lines.clear()

    for raw_line in content.splitlines():
        stripped_line = raw_line.strip()
        if not stripped_line:
            flush_step()
            continue
        if stripped_line.startswith(">"):
            flush_step()
            note_text = stripped_line[1:].strip()
            if note_text:
                steps.append(RecipeStep(kind="note", text=note_text))
            continue
        line = stripped_line
        line = INGREDIENT_RE.sub(replace_ingredient, line)
        line = COOKWARE_RE.sub(replace_cookware, line)
        line = TIMER_RE.sub(replace_timer, line)
        line = WHITESPACE_RE.sub(" ", line).strip()
        if line:
            current_lines.append(line)

    flush_step()

    return ingredients, cookware, timers, steps


def load_recipe(path: Path) -> RecipeInfo | None:
    raw = path.read_text()
    fm, body = split_frontmatter(raw)
    title = fm.get("title", "") or path.stem
    servings = fm.get("serves", "") or fm.get("servings", "")
    ingredients, cookware, timers, steps = parse_cooklang(body)
    rel = path.relative_to(RECIPES_DIR)
    slug = rel.with_suffix("")
    return RecipeInfo(
        path=path,
        rel=rel,
        slug=slug,
        title=title,
        servings=servings,
        ingredients=ingredients,
        cookware=cookware,
        timers=timers,
        steps=steps,
        metadata_hash=make_metadata_hash(title),
        public=True,
    )


def get_recipes() -> RecipeIndex:
    if not RECIPES_DIR.exists():
        return RecipeIndex(recipes=[], by_path={})

    recipes: list[RecipeInfo] = []
    by_path: dict[Path, RecipeInfo] = {}
    for path in RECIPES_DIR.rglob("*.cook"):
        info = load_recipe(path)
        if info is None:
            continue
        recipes.append(info)
        by_path[path] = info
    return RecipeIndex(recipes=recipes, by_path=by_path)
