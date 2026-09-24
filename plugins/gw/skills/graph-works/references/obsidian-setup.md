# Obsidian Setup

Recommended Obsidian configuration for a Code Wiki. None of this is strictly required — the wiki is just markdown files — but these settings remove friction.

## Open the vault

**Important:** point Obsidian at `<workspace>/okf/` — that is the bundle root and the Obsidian vault root; there is no separate `wiki/` subdir to distinguish it from.

1. Obsidian → "Open folder as vault" → pick `<workspace>/okf/`, e.g. `<repo>/.works/okf/`. The Obsidian sidebar will show `work/`, `code-graph/`, `sources/`, etc. Naming the repo's workspace dir something distinctive makes it obvious which repo the vault belongs to when several are open.
2. The repo root sits one level up — your repo-level `CLAUDE.md`, source code, and build artifacts stay outside Obsidian's view.

**Note on CLAUDE.md files:** there are typically three relevant files when working from a graph-works workspace: the repo's root `CLAUDE.md` (build/style conventions), `<workspace>/CLAUDE.md` (workspace-level schema, owned by `gw`), and `<workspace>/okf/CLAUDE.md` (wiki schema, owned by this plugin). Claude Code loads all three by walking the tree; they describe different layers and don't conflict.

## Settings → Files and Links

- **Default location for new notes:** none — new pages are typed (`docs/explanations/`, `adrs/`, `work/`, `sources/`, etc. under `<workspace>/okf/`); file each note in the lane matching its kind rather than relying on a single default folder
- **New link format:** `Absolute path in vault`
- **Use `[[Wikilinks]]`:** OFF
- **Attachment folder path:** `sources/references/` (attached images are copied here by the ingest flow, alongside the material that references them)
- **Automatically update internal links:** ON

## Settings → Hotkeys

- **"Download attachments for current file"** → `Ctrl/Cmd + Shift + D`
- **"Open graph view"** → `Ctrl/Cmd + G`

## Core plugins to enable

- **Graph view** — see the shape of your vault. Hubs, orphans, clusters. In a graph-works vault, you should see clusters per package/feature area.
- **Backlinks** — pane showing who links to the current page. Critical for "who depends on this package?"
- **Outgoing links** — complementary pane.
- **Templates** — enable and set the template folder to `.templates`
- **Tag pane** — tag-driven navigation
- **Search**
- **Page preview** — hover a link to preview
- **Canvas** — useful for architecture sketches

## Recommended community plugins

- **Obsidian Web Clipper** — clip articles to a local folder, then run `/gw:ingest <path>` to bring them into `sources/`
- **Dataview** — query over frontmatter. Dynamic tables like "all package pages where `language: typescript`".
- **Marp for Obsidian** — render any markdown with `marp: true` frontmatter as a slide deck.
- **Advanced Tables** — easier markdown table editing
- **Git** — commit on save, or hook into system git

## Dataview examples

All package pages, sorted by recency:
```dataview
table updated, package_type
from "packages"
sort updated desc
```

Open work items (bugs/security/perf) by severity:
```dataview
table kind, severity, affects, opened
from "work"
where status != "resolved" and (kind = "bug" or kind = "security" or kind = "perf")
sort severity desc
```

In-progress features and initiatives:
```dataview
list
from "work"
where status = "in-progress" and (kind = "feature" or kind = "epic")
sort target asc
```

Recent ADRs:
```dataview
list
from "adrs"
sort decision_date desc
limit 10
```

Dependencies grouped by ecosystem:
```dataview
table ecosystem, versions_in_use, used_by
from "code-graph"
where type = "Dependency"
group by ecosystem
```

## Git workflow

The wiki is usually inside the repo — commit it with the code:

```bash
cd <repo>
git add .works/okf/
git commit -m "wiki: scan — detected 3 new packages"
```

Or keep it as a separate repo if you want independent history:

```bash
cd <repo>/.works/okf
git init
git add .
git commit -m "init wiki"
```

## Tips

- **Graph view daily** — spot structural drift. Clusters per package/feature area; explanation pages tagged `architecture` as hubs.
- **Pin `index.md`, `log.md`, and the active architecture explanation or current `work/<epic>` item**
- **Split view** — code on the left, vault on the right. Browse the vault while the LLM edits.
- **Strict line breaks** — so your LLM's markdown renders as expected
- **Templater plugin (optional)** — auto-fill `package_path`, `updated:`, etc. when creating new pages manually
