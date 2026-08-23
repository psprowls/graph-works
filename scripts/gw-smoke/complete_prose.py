#!/usr/bin/env python3
"""Complete human-owned prose so the deterministic smoke can validate strictly.

The structural scan deliberately runs without a model. Its generated concepts therefore retain
the human-owned description and required prose placeholders. This smoke-only fixture step fills
those fields through the public document model, then the second scan proves the writers preserve
them byte for byte.
"""

from __future__ import annotations

import sys
from pathlib import Path

from code_wiki_okf.config import load_config
from graph_works_core import resolve
from graph_works_core.scan.commands import heading_key, is_unfilled, splice_sections
from okf_ext.body import find_section
from okf_ext.shape import load_sections
from okf_io import load_bundle

_DESCRIPTION = "Deterministic human-owned prose supplied by the strict smoke fixture."


def complete(workspace: Path) -> tuple[int, int]:
    """Fill missing descriptions and required prose placeholders in *workspace*."""
    layout = resolve(workspace=workspace.resolve())
    config = load_config(
        layout.bundle_dir,
        config_path=layout.manifest_path,
        graph_dir=layout.cache_dir,
        declarations_dir=layout.config_dir,
    )
    section_set = load_sections(config.declarations_dir / "sections")
    bundle = load_bundle(layout.bundle_dir)

    descriptions = 0
    sections = 0
    for concept_id in sorted(bundle.concepts):
        document = bundle.concepts[concept_id]
        if document.parse_error is not None:
            continue

        changed = False
        if not (document.fm.description or "").strip():
            document.set("description", _DESCRIPTION)
            descriptions += 1
            changed = True

        declaration = section_set.types.get((document.fm.type or "").strip())
        replacements: dict[str, str] = {}
        if declaration is not None:
            for spec in declaration.sections:
                if not spec.required or spec.ownership != "prose" or spec.seeded_is_complete:
                    continue
                section = find_section(document.body, spec.heading, level=spec.level)
                if section is not None and is_unfilled(section.slice(document.body), spec.placeholder):
                    replacements[heading_key(spec)] = (
                        f"Deterministic human-owned `{spec.heading}` prose supplied by the strict smoke fixture."
                    )

        if replacements:
            body, filled = splice_sections(document.body, replacements)
            document.set_body(body)
            sections += filled
            changed = True

        if changed:
            document.save()

    return descriptions, sections


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: complete_prose.py <workspace>", file=sys.stderr)
        return 2

    descriptions, sections = complete(Path(sys.argv[1]))
    print(f"strict smoke prose completed: {descriptions} description(s), {sections} section(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
