"""Substrate-neutral file and format inspection.

Knows nothing of OKF, wikis or workspaces. Imports the standard library and
`doc_wiki_okf.reading.*`, and nothing else — enforced by
`tests/test_reading_boundaries.py`.
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
from doc_wiki_okf.reading.slug import SLUG_RE, slugify

__all__ = [
    "LANGUAGE_BY_EXT",
    "REPRESENTATIVE_INDEX_NAMES",
    "SLUG_RE",
    "extract",
    "language_for",
    "list_folder_files",
    "pick_representative",
    "slugify",
]
