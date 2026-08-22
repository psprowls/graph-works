# Adopted from plugins/graph-wiki/skills/graph-wiki/SKILL.md §Cross-tool compatibility
# — rewritten: graph-works seeds no wiki-level `CLAUDE.md` and reads none, so the
# source's two-file framing describes a layout this package does not have.

CLAUDE_MD_DISAMBIGUATION = """\
**Note:** the project context above comes from your repo's root `CLAUDE.md` (or `AGENTS.md`), which defines the repo's build and style conventions. The wiki has no `CLAUDE.md` of its own — how its pages are structured comes from the bundle's `schema/` and `sections/` declarations, which are the authority on page shape. Where the two speak to the same thing, the declarations govern the page and the root file governs the prose.\
"""


__all__ = ["CLAUDE_MD_DISAMBIGUATION"]
