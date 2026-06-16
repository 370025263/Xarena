"""
skill/frontmatter.py -- YAML frontmatter parser/serializer for SKILL.md v2
====================================================================
Small helper module to round-trip `---\\n<yaml>\\n---\\n<body>` markdown files.

Follows the Anthropic Agent Skill open standard:
- Opening fence is `---` on the first line
- Closing fence is `---` on its own line
- YAML between fences becomes a dict
- Everything after the closing fence (with one leading newline eaten) is body

The round-trip property: `parse(serialize(fm, body)) == (fm, body)` for any
dict/str pair where `body` starts with content (not a raw `---`).
"""

from __future__ import annotations

import yaml


def parse(text: str) -> tuple[dict, str]:
    """Parse a SKILL.md-style file into (frontmatter_dict, body_str).

    If no valid frontmatter is present, returns ({}, text) and leaves the
    input verbatim. Handles:
      - Missing opening `---`                -> ({}, text)
      - Missing closing `---`                -> ({}, text)  (treat as no frontmatter)
      - Empty frontmatter block              -> ({}, body)
      - Non-dict YAML (e.g. list, scalar)    -> ({}, text)  (invalid for our use)
    """
    if not text:
        return {}, ""

    # Normalize line endings a little; keep body bytes intact otherwise.
    # We only split on \n so Windows \r\n will be preserved inside the body.
    if not text.startswith("---"):
        return {}, text

    lines = text.split("\n")
    # First line must be exactly '---' (optionally with trailing CR).
    if lines[0].rstrip("\r").strip() != "---":
        return {}, text

    # Find the closing '---'
    closing_idx = -1
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r").strip() == "---":
            closing_idx = i
            break

    if closing_idx == -1:
        return {}, text

    yaml_block = "\n".join(lines[1:closing_idx])
    body_lines = lines[closing_idx + 1:]
    # Eat a single leading blank line after the closing fence, if present,
    # so that on the next serialize we can insert one cleanly without stacking.
    if body_lines and body_lines[0].strip() == "":
        body_lines = body_lines[1:]
    body = "\n".join(body_lines)

    try:
        fm = yaml.safe_load(yaml_block) if yaml_block.strip() else {}
    except yaml.YAMLError:
        return {}, text

    if not isinstance(fm, dict):
        return {}, text

    return fm, body


def serialize(fm: dict, body: str) -> str:
    """Serialize (frontmatter_dict, body_str) back into a SKILL.md string.

    Layout:
        ---
        <yaml>
        ---
        <body>

    If `fm` is empty/None, returns `body` unchanged (no fences).
    """
    if not fm:
        return body or ""

    yaml_text = yaml.safe_dump(
        fm,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    )
    # yaml.safe_dump always ends with a single \n; strip to avoid double blanks.
    yaml_text = yaml_text.rstrip("\n")

    body = body or ""
    if not body.startswith("\n"):
        body_sep = "\n"
    else:
        body_sep = ""

    return f"---\n{yaml_text}\n---\n{body_sep}{body}"


def update_frontmatter(text: str, patch: dict) -> str:
    """Convenience: parse, deep-merge patch into frontmatter, re-serialize,
    body preserved byte-for-byte.
    """
    fm, body = parse(text)
    _deep_merge(fm, patch)
    return serialize(fm, body)


def _deep_merge(dst: dict, src: dict) -> None:
    """In-place recursive merge. src wins on scalar conflicts."""
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
