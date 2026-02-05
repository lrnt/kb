from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from pathlib import Path

from notes import split_frontmatter
from paths import BUILD_DIR, RECIPES_DIR
from static import cleanup_empty_dirs

INGREDIENT_RE = re.compile(
    r"@(?P<name>[^@#~{\n]+)(?:\{(?P<qty>[^}%]*)(?:%(?P<unit>[^}]*))?\})?"
)
COOKWARE_RE = re.compile(r"#(?P<name>[^@#~{\n]+)(?:\{[^}]*\})?")
TIMER_RE = re.compile(
    r"~(?:(?P<name>[^@#~{\n]+))?\{(?P<qty>[^}%]*)(?:%(?P<unit>[^}]*))?\}"
)
WHITESPACE_RE = re.compile(r"\s+")
DECIMAL_RE = re.compile(r"^\d+(?:\.\d+)?$")


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
    ingredients: list[Ingredient]


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
    step_ingredients: list[Ingredient] = []
    step_ingredients_seen: set[tuple[str, str, str]] = set()

    def replace_ingredient(match: re.Match) -> str:
        name = clean_token_name(match.group("name") or "")
        qty = (match.group("qty") or "").strip()
        unit = (match.group("unit") or "").strip()
        if name:
            key = (name.lower(), qty, unit)
            if key not in seen:
                ingredients.append(Ingredient(name=name, quantity=qty, unit=unit))
                seen.add(key)
            if key not in step_ingredients_seen:
                step_ingredients.append(Ingredient(name=name, quantity=qty, unit=unit))
                step_ingredients_seen.add(key)
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
            step_ingredients.clear()
            step_ingredients_seen.clear()
            return
        combined = " ".join(current_lines)
        combined = WHITESPACE_RE.sub(" ", combined).strip()
        if combined:
            steps.append(
                RecipeStep(
                    kind="step",
                    text=combined,
                    ingredients=list(step_ingredients),
                )
            )
        current_lines.clear()
        step_ingredients.clear()
        step_ingredients_seen.clear()

    for raw_line in content.splitlines():
        stripped_line = raw_line.strip()
        if not stripped_line:
            flush_step()
            continue
        if stripped_line.startswith(">"):
            flush_step()
            note_text = stripped_line[1:].strip()
            if note_text:
                steps.append(RecipeStep(kind="note", text=note_text, ingredients=[]))
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


def recipe_needs_rebuild(
    recipe: RecipeInfo,
    cache: dict,
    templates_changed: bool,
) -> bool:
    """Check if a recipe needs rebuilding."""
    key = str(recipe.rel)
    cached = cache.get("recipes", {}).get(key)

    if not cached:
        return True

    output = BUILD_DIR / cached["output"]
    if not output.exists():
        return True

    if templates_changed:
        return True

    if recipe.path.stat().st_mtime > cached["mtime"]:
        return True

    return False


def parse_decimal(value: str) -> float | None:
    stripped = value.strip()
    if not stripped:
        return None
    if not DECIMAL_RE.match(stripped):
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


def normalize_quantity(raw: str) -> tuple[str, float | None, bool]:
    if raw is None:
        return "", None, False
    stripped = raw.strip()
    if not stripped:
        return "", None, False
    fixed = stripped.startswith("=")
    if fixed:
        stripped = stripped[1:].strip()
    return stripped, parse_decimal(stripped) if stripped else None, fixed


def build_step_ingredient(ingredient: Ingredient) -> dict:
    qty_display, qty_value, fixed = normalize_quantity(ingredient.quantity)
    return {
        "name": ingredient.name,
        "qty_display": qty_display,
        "qty_value": qty_value,
        "unit": ingredient.unit,
        "fixed": fixed,
    }


def build_recipe(
    recipe: RecipeInfo,
    cache: dict,
    template,
    nav_html: str,
):
    """Build single recipe, return output path."""
    output = BUILD_DIR / "recipes" / recipe.slug / "index.html"
    output.parent.mkdir(parents=True, exist_ok=True)

    ingredient_rows = []
    for index, ingredient in enumerate(recipe.ingredients):
        qty_display, qty_value, fixed = normalize_quantity(ingredient.quantity)
        has_qty = bool(qty_display)
        ingredient_rows.append(
            (
                not has_qty,
                index,
                {
                    "name": ingredient.name,
                    "unit": ingredient.unit,
                    "qty_display": qty_display,
                    "qty_value": qty_value,
                    "fixed": fixed,
                },
            )
        )
    ingredients = [row[2] for row in sorted(ingredient_rows, key=lambda row: row[:2])]
    steps = []
    step_number = 0
    for item in recipe.steps:
        if item.kind == "step":
            step_number += 1
            steps.append(
                {
                    "kind": "step",
                    "text": item.text,
                    "number": step_number,
                    "ingredients": [
                        build_step_ingredient(ingredient)
                        for ingredient in item.ingredients
                    ],
                }
            )
        elif item.kind == "note":
            steps.append(
                {
                    "kind": "note",
                    "text": item.text,
                    "number": None,
                    "ingredients": [],
                }
            )
    servings_value = parse_decimal(recipe.servings) if recipe.servings else None
    servings_is_int = False
    servings_display = recipe.servings
    if servings_value is not None:
        if servings_value.is_integer() and servings_value > 0:
            servings_is_int = True
            servings_display = str(int(servings_value))

    page_html = template.render(
        page_title=recipe.title,
        title=recipe.title,
        nav_html=nav_html,
        recipe=recipe,
        ingredients=ingredients,
        steps=steps,
        servings_value=servings_value,
        servings_is_int=servings_is_int,
        servings_display=servings_display,
    )
    output.write_text(page_html)

    key = str(recipe.rel)
    cache.setdefault("recipes", {})[key] = {
        "mtime": recipe.path.stat().st_mtime,
        "metadata_hash": recipe.metadata_hash,
        "output": str(output.relative_to(BUILD_DIR)),
    }

    return output


def recipes_index_needs_rebuild(cache: dict, recipes: list[RecipeInfo]) -> bool:
    """Check if any recipe metadata changed (requires index rebuild)."""
    output = BUILD_DIR / "recipes" / "index.html"
    if not output.exists():
        return True

    cached = cache.get("recipes", {})
    recipe_keys = {str(recipe.rel) for recipe in recipes}
    if set(cached.keys()) != recipe_keys:
        return True

    for recipe in recipes:
        key = str(recipe.rel)
        cached_entry = cached.get(key, {})
        if cached_entry.get("metadata_hash") != recipe.metadata_hash:
            return True

    return False


def build_recipes_index(
    recipes: list[RecipeInfo],
    template,
    nav_html: str,
):
    """Build recipes index page."""
    output = BUILD_DIR / "recipes" / "index.html"
    output.parent.mkdir(parents=True, exist_ok=True)

    if recipes:
        sorted_recipes = sorted(
            recipes,
            key=lambda recipe: (recipe.title.lower(), recipe.slug.as_posix()),
        )
        recipe_items = [
            {
                "title": recipe.title,
                "url": f"/recipes/{recipe.slug.as_posix()}",
            }
            for recipe in sorted_recipes
        ]
    else:
        recipe_items = []

    page_html = template.render(
        page_title="Recipes",
        title="Recipes",
        nav_html=nav_html,
        recipes=recipe_items,
    )
    output.write_text(page_html)

    return output


def prune_removed_recipes(cache: dict, recipes: list[RecipeInfo]) -> bool:
    """Remove cached/build outputs for recipes no longer present."""
    recipe_keys = {str(recipe.rel) for recipe in recipes}
    removed = False

    for key in list(cache.get("recipes", {}).keys()):
        if key in recipe_keys:
            continue
        cached = cache["recipes"][key]
        output = BUILD_DIR / cached.get("output", "")
        if output.exists():
            output.unlink()
            cleanup_empty_dirs(output.parent, BUILD_DIR)
            removed = True
        del cache["recipes"][key]
        removed = True

    return removed
