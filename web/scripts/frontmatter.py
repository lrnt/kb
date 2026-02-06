from __future__ import annotations

import re

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def strip_quotes(value: str) -> str:
    trimmed = value.strip()
    if len(trimmed) < 2:
        return trimmed
    if trimmed[0] == trimmed[-1] and trimmed[0] in ('"', "'"):
        return trimmed[1:-1].strip()
    return trimmed


def parse_frontmatter_block(raw: str) -> dict[str, str]:
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


def parse_frontmatter(content: str) -> dict[str, str]:
    match = FRONTMATTER_RE.match(content)
    if not match:
        return {}
    return parse_frontmatter_block(match.group(1))


def split_frontmatter(content: str) -> tuple[dict[str, str], str]:
    match = FRONTMATTER_RE.match(content)
    if not match:
        return {}, content
    fm = parse_frontmatter_block(match.group(1))
    body = content[match.end() :].lstrip("\n")
    return fm, body
