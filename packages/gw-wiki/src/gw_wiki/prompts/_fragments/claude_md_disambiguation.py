# Rewritten from the retired plugin's "cross-tool compatibility" note: the
# project context is the workspace's own AGENTS.md (its CLAUDE.md is a
# one-line pointer to it), and the bundle has no context file of its own.

CLAUDE_MD_DISAMBIGUATION = """\
**Note:** the project context above comes from the workspace's `AGENTS.md`, which defines the workspace's style and log conventions. The wiki has no `CLAUDE.md` of its own — how its pages are structured comes from the bundle's `schema/` and `sections/` declarations, which are the authority on page shape. Where the two speak to the same thing, the declarations govern the page and `AGENTS.md` governs the prose.\
"""


__all__ = ["CLAUDE_MD_DISAMBIGUATION"]
