"""Write a brand-new File page. The one writer in this lane that creates a
file rather than editing one.

Neither `okf_ext.sections.scaffold.render_skeleton` (returns a body string --
"nothing here creates a file... where that string goes is tier 3's
decision"), nor `okf_ext.generators.plan_regenerate` (skips any concept not
already a bundle member), nor `okf_ext.writing.write_all` (its unwritable
probe opens the target `"r+b"`, which requires the file to already exist)
can create a file. This module fills that gap.

`Document.set` inserts each key at the position `PREFERRED_KEY_ORDER`
implies (or the end, for a key the core schema does not know -- every File
owned/provenance key beyond `generated`), so this never hand-builds YAML
text; it builds an in-memory `Document` the same way any other okf-io writer
does and asks it to serialize itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from okf_ext.sections.scaffold import render_skeleton
from okf_ext.shape import SectionSet
from okf_io import parse

from code_wiki_okf.config import RepoConfig
from code_wiki_okf.mirror.paths import mirror_page_path

#: The order keys are set in. `Document.set` places each one per
#: `PREFERRED_KEY_ORDER` when the core schema knows it, and appends at the
#: end otherwise -- so this order only matters for keys the core schema does
#: *not* know (every File owned/provenance key beyond `generated`), where it
#: is also the insertion order.
_KEY_ORDER: tuple[str, ...] = (
    "type",
    "resource",
    "title",
    "description",
    "tags",
    "language",
    "package",
    "role_flags",
    "generated",
    "last_updated_commit",
)


def write_new_page(
    bundle_root: Path,
    repo: RepoConfig,
    rel_path: str,
    frontmatter: dict[str, Any],
    *,
    section_set: SectionSet,
) -> Path:
    """Write a fresh File page for *rel_path* at its mirrored location.

    Raises `FileExistsError` if the target already exists -- this path never
    overwrites; an existing page is always an update through
    `okf_ext.generators` instead. Raises `KeyError` if `section_set` carries
    no `"File"` declaration (a caller/config error, not content).
    """
    target = mirror_page_path(bundle_root, repo.name, rel_path)
    if target.exists():
        raise FileExistsError(f"{target}: a page already exists here; write_new_page never overwrites")

    declaration = section_set.types["File"]
    document = parse("")
    for key in _KEY_ORDER:
        if key in frontmatter:
            document.set(key, frontmatter[key])
    document.set_body(render_skeleton(declaration))

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document.serialize(), encoding="utf-8")
    return target


__all__ = ["write_new_page"]
