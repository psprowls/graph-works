"""The wiki-page vertical: read one page with its link neighbourhood.

Layer 2 -- independent of every other vertical; imports `okf_io` and
`workspace` only.
"""

from __future__ import annotations

from graph_works_core.wiki_page.commands import PageLink, PageRead, run_page_read

__all__ = ["PageLink", "PageRead", "run_page_read"]
