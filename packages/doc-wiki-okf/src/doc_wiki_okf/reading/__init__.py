"""Substrate-neutral file and format inspection.

Knows nothing of OKF, wikis or workspaces. Imports the standard library and
`doc_wiki_okf.reading.*`, and nothing else — enforced by
`tests/test_reading_boundaries.py`.

`links.iter_link_targets` and `links.resolve_companion` are deliberately absent
from this surface: `reading/` is an internal subpackage, and its `__init__`
exports what the rest of the lane consumes. Link-target parsing is not on that
list — import it from `doc_wiki_okf.reading.links` if a caller ever needs it.
"""

from __future__ import annotations

from doc_wiki_okf.reading.extract import extract
from doc_wiki_okf.reading.files import (
    LANGUAGE_BY_EXT,
    REPRESENTATIVE_INDEX_NAMES,
    language_for,
    list_folder_files,
    pick_representative,
)
from doc_wiki_okf.reading.skills import SkillBundle, gather_skill_sources, resolve_skill_anchor
from doc_wiki_okf.reading.slug import SLUG_RE, slugify

__all__ = [
    "LANGUAGE_BY_EXT",
    "REPRESENTATIVE_INDEX_NAMES",
    "SLUG_RE",
    "SkillBundle",
    "extract",
    "gather_skill_sources",
    "language_for",
    "list_folder_files",
    "pick_representative",
    "resolve_skill_anchor",
    "slugify",
]
