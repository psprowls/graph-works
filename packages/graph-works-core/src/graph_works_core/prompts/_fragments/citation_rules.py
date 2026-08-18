# Adopted from plugins/graph-wiki/agents/librarian.md §Rules — link syntax rewritten.

CITATION_RULES = """\
## Citation rules

- **Every claim cites** — a vault page (`[sources/foo](/sources/foo.md)`) or a code path (`` `path/to/file.py:line` ``).
- **If the vault doesn't know, say so.** Suggest a source to ingest or a concept page to create; don't invent content.
- **Use root-absolute markdown links** for all cross-references between vault pages — `[title](/lane/slug.md)`. Wiki-style double-bracket links are wrong here.
- **Links must point at existing vault pages.** Never fabricate a link target that doesn't exist in the vault.
- **Cite aggressively.** Every claim on a package page links to a source page or a code path.\
"""


__all__ = ["CITATION_RULES"]
