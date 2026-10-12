# Source: plugins/graph-wiki/skills/graph-wiki/SKILL.md §Iron rules — item 6's
# link syntax rewritten, and the "touches >=3 files" rule dropped: the writer
# reconciles index.md and log.md itself, so it was an instruction no reader of
# this fragment — ingestor, librarian or linter — could act on.

IRON_RULES = """\
## Iron rules

1. **The code is the source of truth.** If the vault contradicts the code, the code wins — update the vault.
2. **The LLM never edits ingested source material.** It is read-only; only the wiki pages that describe it are written.
3. **All LLM writes for the wiki go under the vault path.** No exceptions.
4. **Every vault page has YAML frontmatter.**
5. **Every claim on a package page cites** either a source page (`[sources/xxx](/sources/xxx.md)`) or a code path (`packages/foo/src/bar.ts`).
6. **Good query answers get filed back** — explorations compound.\
"""


__all__ = ["IRON_RULES"]
