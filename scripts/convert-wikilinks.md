# convert-wikilinks — Obsidian wikilinks to OKF markdown links

Rewrites `[[entities/pkg_okf-io]]` into `[okf-io](/entities/pkg_okf-io.md)` across
a graph-wiki vault, so okf-io's `LinkGraph` can see links that were previously
invisible to it. Implements the link-form half of the live-vault migration; it
does **not** move pages.

Dry-run by default. Nothing is written without `--write`.

## Use

```bash
# Read what would change. Writes nothing.
uv run python scripts/convert_wikilinks.py "$GRAPH_WIKI_WORKSPACE/wiki"

# Apply it.
uv run python scripts/convert_wikilinks.py "$GRAPH_WIKI_WORKSPACE/wiki" --write
```

The vault is a git repo, so review with `git diff` before committing. Always read
a dry run first — especially the unresolved list, which is where a vault tells you
about its dead links.

Run it through `uv run` from the repo root. Unlike `gw_dispatch.py` this script is
not stdlib-only — it imports `okf_io` for the bundle load, the markdown parse and
the byte-preserving write — so invoking it directly gets a
`ModuleNotFoundError: No module named 'okf_io'`.

## Options

| Flag | Default | Effect |
|---|---|---|
| `VAULT` | *required* | The bundle root — the directory containing `index.md`, e.g. `<workspace>/wiki`. Not the workspace itself. |
| `--write` | off | Apply changes. Without it the script only reports. |
| `--relative` | off | Emit `../entities/pkg_okf-io.md` instead of `/entities/pkg_okf-io.md`. See "Which link form" below. |
| `--no-title-backfill` | off | Skip adding `title:` to link targets that lack one. |
| `-h`, `--help` | | Usage. |

Exit code is `0` on success, `2` if `VAULT` is not a directory.

## Which link form

Root-absolute (the default) is resolved from the **bundle root**, which is the
vault directory you pass in — so the destination carries no `/wiki/` prefix even
though the file lives at `<workspace>/wiki/entities/…`. Writing `/wiki/entities/…`
would point at a member `wiki/entities/…` that does not exist under that root and
would break every link.

| | okf-io | GitHub / VS Code preview / Obsidian |
|---|---|---|
| default, root-absolute | resolves | 404 — looks for `<repo>/entities/…` |
| `--relative` | resolves | resolves |

Root-absolute is the settled decision (it survives page moves, since a relayout
does not invalidate links merely pointing past the moved page). Use `--relative`
for a vault that is read primarily through a file browser.

## What it will not touch

- **Anything the markdown parser calls code.** Fenced blocks and inline-code
  spans are excluded, which is what keeps `[[tool.importlinter.contracts]]` in a
  TOML sample and `` `Callable[[EntryTarget], str | None]` `` intact.
- **Targets that name no page.** Left byte-identical and listed under
  "unresolved targets" in the report — this covers template placeholders like
  `[[entities/pkg_<pkg>]]`, prose about `[[wikilink]]`, and genuinely dead links.
- **Embeds** (`![[…]]`), which mean something different from a link.
- **Reserved files' frontmatter.** `index.md` and `log.md` are valid link
  *targets* but never gain a `title:` — §12 permits only `okf_version` on them.
- **Dot-directories**, so `.templates/` is never walked.

## Link text

First match wins:

1. an explicit alias — `[[page|Some text]]` → `Some text`
2. the target's `title:` frontmatter
3. the target's H1
4. the filename stem with its entity prefix stripped (`pkg_`, `dep_`, `repo_`,
   `app_`, `domain_`, `agent_plugin_`, `unit_tests_`)

The stem is last because it collides: `unit_tests_okf-io` strips to `okf-io`,
which is already `pkg_okf-io`'s name. H1 gives `okf-io-unit-tests`.

By default, any page reached by a converted link that has no `title:` gets one
written from its H1 — that is what makes bare links resolve to good text rather
than to filenames. `--no-title-backfill` turns that off, leaving those pages
untouched.

## Reading the report

```
converted        873      links rewritten
files changed    73       files that will be written
titles added     13       pages gaining a title:
left alone       6        (unresolved) — listed individually below
```

The per-target unresolved list gives `file:line` for each occurrence. Treat it as
a to-do list for the vault, not as a failure of the script.

## Re-running

The conversion is idempotent — a second run reports `converted 0` and leaves
every file byte-identical. That is what makes it safe to run again after pages
move: `git mv` the pages, re-run, and the links converge.

## Testing

```bash
uv run pytest scripts/tests
```

These live outside the repo's `testpaths` and coverage `source` list, so they do
not affect the 95% gate and `just check` does not run them.

Before pointing this at an unfamiliar vault, dry-run it and diff the whole tree —
both defects found during development survived a green unit suite and were caught
only by comparing the vault before and after.
