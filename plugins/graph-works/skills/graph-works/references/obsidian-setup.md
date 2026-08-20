# Obsidian Setup

> **Substrate ownership.** This document describes behavior that the graph-works rebuild is
> re-implementing. Identifiers and paths here are retargeted for the `graph-works` namespace, but
> the behavioral truth is owned by [`2026-08-13-epic-feature-workspace-manifest-layout-resolution`](/work/2026-08-13-epic-feature-workspace-manifest-layout-resolution.md) and is re-authored there, not here.
> Treat a disagreement between this page and that item as this page being stale.

Recommended Obsidian configuration for a Code Wiki. None of this is strictly required — the wiki is just markdown files — but these settings remove friction.

## Open the vault

**Important:** point Obsidian at `<workspace>/` (the graph-works workspace root), not at the wiki subdir. Obsidian indexes whatever folder you open as a vault, and the workspace root is the level where `wiki/`, `raw/`, `work/`, and `knowledge/` are all visible as sibling folders — they're all part of the same compounding knowledge base.

1. Obsidian → "Open folder as vault" → pick `<workspace>/`, e.g. `<repo>/graph-works/`. The Obsidian sidebar will show `wiki/`, `raw/`, `work/`, etc. as siblings. Naming the workspace dir something distinctive (the default `graph-works/` is fine) makes it obvious which repo the vault belongs to when several are open.
2. The repo root sits one level up — your repo-level `CLAUDE.md`, source code, and build artifacts stay outside Obsidian's view.

**Note on CLAUDE.md files:** there are typically three relevant files when working from a graph-works workspace: the repo's root `CLAUDE.md` (build/style conventions), `<workspace>/CLAUDE.md` (workspace-level schema, owned by `gw`), and `<workspace>/wiki/CLAUDE.md` (wiki schema, owned by this plugin). Claude Code loads all three by walking the tree; they describe different layers and don't conflict.

## Settings → Files and Links

- **Default location for new notes:** `wiki/` (curated pages go in the wiki, not at the workspace root)
- **New link format:** `Shortest path when possible`
- **Use `[[Wikilinks]]`:** ON
- **Attachment folder path:** `raw/assets/` (clipped images go to the workspace's `raw/` layer alongside the wiki, owned by `gw`)
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
- **Page preview** — hover a wikilink to preview
- **Canvas** — useful for architecture sketches

## Recommended community plugins

- **Obsidian Web Clipper** — clip articles to `../raw/articles/` as markdown
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
from "dependencies"
group by ecosystem
```

## Git workflow

The wiki is usually inside the repo — commit it with the code:

```bash
cd <repo>
git add graph-works/wiki/
git commit -m "wiki: scan — detected 3 new packages"
```

Or keep it as a separate repo if you want independent history:

```bash
cd <repo>/graph-works/wiki
git init
git add .
git commit -m "init wiki"
```

## Tips

- **Graph view daily** — spot structural drift. Clusters per package/feature area; concept pages with `kind: architecture` as hubs.
- **Pin `index.md`, `log.md`, and the active architecture concept page or current `work/<epic>` item**
- **Split view** — code on the left, vault on the right. Browse the vault while the LLM edits.
- **Strict line breaks** — so your LLM's markdown renders as expected
- **Templater plugin (optional)** — auto-fill `package_path`, `updated:`, etc. when creating new pages manually
