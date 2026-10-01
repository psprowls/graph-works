"""A new OKF page's text, written through okf-io's own frontmatter writer so quoting is never hand-rolled."""

from __future__ import annotations

from collections.abc import Mapping

from okf_io import parse

_SEED = "---\ntype: seed\n---\n"


def new_page_text(frontmatter: Mapping[str, object], body: str) -> str:
    """Frontmatter in *frontmatter*'s order (after okf-io's preferred keys), a blank line, then *body*."""
    if "type" not in frontmatter:
        raise ValueError("a page's frontmatter must carry `type`")
    document = parse(_SEED)
    for key, value in frontmatter.items():
        document.set(key, value)
    document.set_body("\n" + body)
    return document.serialize()


__all__ = ["new_page_text"]
