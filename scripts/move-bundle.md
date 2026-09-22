# move-bundle — move bundle directories per a rules file, repairing every link

One reusable, dry-run-first entry point for bundle restructures. A committable
YAML rules file expands into one `{old: new}` mapping, which goes to a single
`okf_ext.moves.plan_move_many` call — so a reference *between* two members that
both move is computed against both new bases. Dry run is the default; any
refusal exits 1 having written nothing.

Not named `migrate_*` deliberately: `okf_io/migrate.py` is the unrelated OKF
v0.1 → v0.2 format migration.

## Use

```bash
WS=/path/to/workspace

uv run python scripts/move_bundle.py "$WS/okf" --rules moves.yaml            # dry run
uv run python scripts/move_bundle.py "$WS/okf" --rules moves.yaml --write    # apply
uv run python scripts/move_bundle.py "$WS/okf" --rules moves.yaml            # again: "0 moves"
```

## The rules file

Directory rules only. Both ends are bundle-relative, anchored at the bundle
root, with no `..` segment. An unknown key is refused rather than ignored.

```yaml
moves:
  - dir: explanations
    to: docs/explanations
  - dir: references
    to: docs/reference
  - dir: tutorials
    to: docs/tutorials
  - dir: how-tos
    to: docs/how-tos
```

Because rules are root-anchored, a `references` rule reaches `references/`
alone — never `sources/references/` and never `work/<item>/references/`.

## Refusals

| Kind | Meaning |
| --- | --- |
| `bad-rules` | The file is not valid YAML, or has no top-level `moves:` list. |
| `bad-rule` | An entry is not a mapping, has an unknown key, or an end that is missing, non-string, absolute, or contains `..`. |
| `rule-overlap` | Two rules claim one source with different destinations. A plain `dict` would let the later rule silently win. |
| `empty-rule` | A rule matched nothing **and** its destination holds nothing either — the `references` versus `reference` typo. Once a move has landed, the destination check makes a re-run a clean no-op instead. |
| anything else | Forwarded from `okf_ext.moves` — `dest-exists`, `not-a-member`, `escapes-root` and the rest of `RefusalKind`. |

## What it does not do

Index regeneration, the `log.md` entry and `gw config sync` are **not** this
script's concern; it prints them as follow-ups. Frontmatter rewrites (`type:`,
tags) are out of scope for v1.

**Generated code-wiki pages are not guarded.** A rule naming `repositories/` or
`dependencies/` will move them, and the next `gw scan` writes them back at their
schema-declared paths. Move generated lanes by changing the scan config instead.

**Wikilinks are never repaired** — `okf_ext.moves` cannot see one. They are
counted and reported through `MovePlan.stranded`, never silently dropped.

## Reserved members, and the recovery path

`index.md` and `log.md` are refused by the engine as `reserved-source`, and any
refusal invalidates the whole plan — so a naive directory expansion moves
*nothing* the moment a lane has an `index.md`. This script carries them itself:
a lane index's entries are relative and its siblings move with it, so it needs
no content edit, only a rename. It is excluded from the mapping, hidden from the
planning load via `ignore=`, renamed directly, and its inbound references are
repaired by a `plan_repair` computed against a **reloaded** bundle — the reload
is required, because the move plan and the repair plan both edit the root index.

If that recomputed repair plan is refused, the script rolls the reserved
renames back to their source paths, reports the refusals, and exits 1. The
ordinary move is left in place -- it succeeded and is internally consistent
on its own; only the reserved half, whose repair failed, is undone. With the
index back at its source, the same rule reclassifies it as reserved on a
re-run, `plan_repair` recomputes the same mapping against the current
bundle, and the move completes once the offending reference has been fixed.
