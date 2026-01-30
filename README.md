# lrnt

Static site build scripts and small utilities for a personal knowledge base.

## Projects
- `web/`: Pandoc-based static site builder for notes in `vault/` (vault content is not tracked).
- `habits/`: terminal habits heatmap renderer for daily notes.

## Commands
- `make all`: build the site into `web/build/`.
- `make dev`: live reload server at http://localhost:8000.
- `make deploy`: deploy `web/build/` to Netlify.
- `make habits`: run the habits heatmap.
