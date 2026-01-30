# Habit Heatmap

Render a terminal heatmap from your daily habit checkboxes.

## Usage

```bash
uv run main.py
```

## Examples

```bash
uv run main.py --weeks 12
uv run main.py --from 2026-01-01 --to 2026-01-31
uv run main.py --vault ./vault/dailies
uv run main.py --no-show-legend
```

## Notes

- Daily files are expected to be named `YYYY-MM-DD.md`; non-date files are ignored.
- The script uses checkbox lines with a `#habits/<id>` tag.
- Only tagged habits are tracked; untagged checkboxes are ignored.
- If a day has no tagged habits, it is treated as "no data".
