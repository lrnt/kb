# Agents Guide

Personal knowledge base utilities with two main tools: a static site builder for
notes and a terminal habits heatmap.

## Project Layout

- `vault/`: source notes (Obsidian-style). Notes publish to the site when
  frontmatter includes `public: true`.
- `web/`: Pandoc-based static site builder and assets. Treat `web/build/` as
  generated output.
- `habits/`: Python CLI that renders a terminal heatmap from daily notes.

## Common Commands

- `make all`: build the site into `web/build/`.
- `make dev`: live reload server at http://localhost:8000.
- `make deploy`: deploy `web/build/` to Netlify.
- `make habits`: run the habits heatmap.
- `./web/scripts/build.py --all`: full rebuild (supports `--keep-artifacts`).
- `./web/scripts/dev.py`: live reload server.
- `uv run main.py`: run the habits CLI (see `habits/README.md` for flags).

## Habits Data Rules

- Daily files live in `vault/dailies/` and must be named `YYYY-MM-DD.md`.
- Only checkbox lines with a `#habits/<id>` tag are counted; untagged checkboxes
  are ignored.

## Code Style

- Python scripts should declare dependencies in the script itself (PEP 723
  metadata).
- Python scripts should use the `#!/usr/bin/env -S uv run` shebang.

## Notes For Changes

- Do not edit files under `vault/` or `web/build/`.
- There are no tests in this repo; use the build or scripts above to validate
  changes.
