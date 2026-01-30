# Web

Static site build scripts (Pandoc + Lua filter) for notes in `../vault`.

## Commands
- `./scripts/build.py` clean build output for deploy
- `./scripts/build.py --keep-artifacts` incremental build with cache/includes
- `./scripts/build.py --all` full rebuild (use `--keep-artifacts` to keep cache)
- `./scripts/dev.py` live reload at http://localhost:8000

Notes publish when frontmatter includes `public: true`.
