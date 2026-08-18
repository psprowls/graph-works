# Source: plugins/graph-wiki/agents/ingestor.md §4. Write the source summary —
# reconciled against what `commands/ingest.py` actually reads.

FRONTMATTER_RULES = """\
## Frontmatter rules

Every page you write opens with YAML frontmatter between `---` delimiters.

Required:
- `title`: a descriptive title for the page. This one is load-bearing — the
  page's path and the name of its reference copy are both derived from it.
- `description`: one line on the source's main contribution.

Optional, emitted when the material supports them:
- `source_kind`: the classification, from the closed enum listed under
  **Source landing** below. Omit it when the material has no clear genre; the
  caller's own hint decides instead. It does NOT control routing — every
  ingested document lands under `sources/`.
- `authors`: who wrote the material.
- `source_date`: the date the material itself carries, not today's date.
- `tags`: short topic labels.
- `tokens`: the material's approximate length in tokens, if you can tell.

Emit no other keys. Anything else is dropped on read, so a field invented here
is silently lost rather than stored.\
"""


__all__ = ["FRONTMATTER_RULES"]
