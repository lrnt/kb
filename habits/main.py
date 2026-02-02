#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = ["rich"]
# ///

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from rich.console import Console
from rich.table import Table
from rich.text import Text

ROOT = Path(__file__).resolve().parents[1]
VAULT_DIR = ROOT.parent / "vault" / "dailies"

DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")
CHECKBOX_RE = re.compile(r"^\s*-\s*\[(?P<state>[ xX])\]")
HABIT_TAG_RE = re.compile(r"#habits/[A-Za-z0-9_\-/]+")
BLOCK = ""


@dataclass(frozen=True)
class HabitDay:
    total: int
    checked: int

    @property
    def ratio(self) -> float:
        if self.total == 0:
            return 0.0
        return self.checked / self.total


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a habits heatmap.")
    parser.add_argument("--vault", default=None, help="Path to daily notes.")
    parser.add_argument("--from", dest="from_date", help="Start date (YYYY-MM-DD).")
    parser.add_argument("--to", dest="to_date", help="End date (YYYY-MM-DD).")
    parser.add_argument("--weeks", type=int, default=52, help="Weeks to show.")
    return parser.parse_args()


def parse_date_literal(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_date_from_filename(name: str) -> Optional[date]:
    match = DATE_RE.match(name)
    if not match:
        return None
    year, month, day = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def iter_days(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def parse_habits_from_file(path: Path) -> dict[str, bool]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    habits: dict[str, bool] = {}
    for line in content.splitlines():
        match = CHECKBOX_RE.match(line)
        if not match:
            continue
        tags = HABIT_TAG_RE.findall(line)
        if not tags:
            continue
        is_checked = match.group("state").lower() == "x"
        for tag in tags:
            current = habits.get(tag)
            habits[tag] = is_checked if current is None else (current or is_checked)
    return habits


def ratio_to_level(ratio: float) -> int:
    if ratio <= 0:
        return 0
    if ratio < 0.25:
        return 1
    if ratio < 0.5:
        return 2
    if ratio < 0.75:
        return 3
    return 4


def chunk_weeks(start: date, end: date) -> list[list[date]]:
    days = list(iter_days(start, end))
    return [days[i : i + 7] for i in range(0, len(days), 7)]


def render_heatmap(
    console: Console,
    weeks: list[list[date]],
    data_by_date: dict[date, Optional[HabitDay]],
    from_date: date,
    to_date: date,
    today: date,
    colors: list[str],
) -> None:
    no_activity_style = "#5a5a5a"
    blank = " " * len(BLOCK)

    for weekday in range(7):
        line = Text()
        for week in weeks:
            day = week[weekday]
            if day > today:
                line.append(blank)
                continue
            glyph = BLOCK
            if day < from_date or day > to_date:
                style = no_activity_style
            else:
                data = data_by_date.get(day)
                if data is None or data.checked == 0:
                    style = no_activity_style
                else:
                    level = ratio_to_level(data.ratio)
                    style = colors[level]
            line.append(glyph, style=style)
        console.print(line)


def build_stats_line(parts: list[str], width: int) -> Text:
    base_len = sum(len(part) for part in parts)
    joined = " | ".join(parts)
    if width <= 0:
        return Text(joined)
    total_spaces = width - base_len
    if total_spaces < 2:
        return Text(joined)
    gap1 = total_spaces // 2 + total_spaces % 2
    gap2 = total_spaces // 2
    return Text(parts[0] + (" " * gap1) + parts[1] + (" " * gap2) + parts[2])


def heatmap_width(weeks: list[list[date]], block: str) -> int:
    columns = len(weeks)
    if columns == 0:
        return 0
    return columns * len(block)


def resolve_date_range(args: argparse.Namespace, today: date) -> tuple[date, date]:
    from_date = parse_date_literal(args.from_date) if args.from_date else None
    to_date = parse_date_literal(args.to_date) if args.to_date else None
    days_span = args.weeks * 7 - 1

    if from_date is None and to_date is None:
        to_date = today
        from_date = today - timedelta(days=days_span)
    elif from_date is None:
        to_date = to_date or today
        from_date = to_date - timedelta(days=days_span)
    elif to_date is None:
        to_date = today

    return from_date, to_date


def main() -> None:
    args = parse_args()
    if args.vault is None:
        vault_path = VAULT_DIR
    else:
        vault_path = Path(args.vault)
    today = date.today()

    from_date, to_date = resolve_date_range(args, today)

    if from_date > to_date:
        raise SystemExit("--from must be on or before --to")

    date_to_path: dict[date, Path] = {}
    for path in vault_path.iterdir():
        if not path.is_file():
            continue
        file_date = parse_date_from_filename(path.name)
        if file_date is None:
            continue
        date_to_path[file_date] = path

    data_by_date: dict[date, Optional[HabitDay]] = {}
    habit_stats: dict[str, dict[str, int]] = {}
    for day in iter_days(from_date, to_date):
        path = date_to_path.get(day)
        if path is None:
            data_by_date[day] = None
            continue
        habits_for_day = parse_habits_from_file(path)
        if not habits_for_day:
            data_by_date[day] = None
            continue
        total = len(habits_for_day)
        checked = sum(1 for status in habits_for_day.values() if status)
        data_by_date[day] = HabitDay(total=total, checked=checked)
        for tag, status in habits_for_day.items():
            stats = habit_stats.setdefault(tag, {"days": 0, "checked": 0})
            stats["days"] += 1
            if status:
                stats["checked"] += 1

    start_grid = from_date - timedelta(days=from_date.weekday())
    end_grid = to_date + timedelta(days=(6 - to_date.weekday()))
    weeks = chunk_weeks(start_grid, end_grid)

    colors = ["#0b1f0b", "#0b2d0b", "#186318", "#2bd42b", "#9dff9d"]
    console = Console()
    render_heatmap(
        console,
        weeks,
        data_by_date,
        from_date,
        to_date,
        today,
        colors,
    )

    data_points = [data for data in data_by_date.values() if data is not None]
    no_activity_days = sum(
        1 for data in data_by_date.values() if data is None or data.checked == 0
    )
    avg_ratio = 0.0
    if data_points:
        avg_ratio = sum(data.ratio for data in data_points) / len(data_points)
    stats_parts = [
        f"Tracked: {len(data_points)} days",
        f"Avg: {avg_ratio * 100:.0f}%",
        f"No activity: {no_activity_days} days",
    ]
    stats_width = heatmap_width(weeks, BLOCK)
    console.print(build_stats_line(stats_parts, stats_width))

    if habit_stats:
        console.print()
        console.print()
        table = Table(show_header=True, header_style="bold")
        table.add_column("Habit", justify="left")
        table.add_column("Checked", justify="right")
        table.add_column("%", justify="right")
        rows = []
        for tag, stats in habit_stats.items():
            days = stats["days"]
            checked = stats["checked"]
            percent = (checked / days * 100.0) if days else 0.0
            rows.append((tag, days, checked, percent))
        rows.sort(key=lambda row: row[3], reverse=True)
        for tag, days, checked, percent in rows:
            table.add_row(tag, f"{checked}/{days}", f"{percent:.0f}")
        console.print(table)


if __name__ == "__main__":
    main()
