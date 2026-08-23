---
name: planning-epics
description: Use when dispatched as the plan stage of an Epic or Release work item.
---

# Planning Epics

Turn the owning item’s approved design into path-native child work items and a
reviewable dependency graph. Do not write implementation code.

**Announce at start:** “I’m using the planning-epics skill to decompose this work item.”

The dispatch brief supplies `<work-path>` and, when this stage owns an artifact,
an absolute `artifact.path`. Canonical work identity is always the extensionless
bundle-relative path returned by the CLI. Never derive identity from a page stem.

## Inputs

1. Run `gw work next <work-path> --json` if the dispatch brief lacks current
   routing data.
2. Read the item page at `<workspace>/okf/<work-path>.md`.
3. Read the design artifact at
   `<workspace>/okf/<work-path>/references/01-design.md`, or the exact
   source path supplied by the dispatch brief.

If `gw` is not on PATH, use `uv run --package graph-works-cli gw …`.

## Decompose and order

Choose concrete child types from `Epic`, `Feature`, `Bug`, `TechDebt`,
`TestGap`, or `Spike`. `Release` is root-only and cannot be filed beneath a
parent. Keep independent children dependency-free so they can run concurrently.

Every dependency is a complete edge:

```text
path=<canonical-path>,blocks=<design|plan|execute|finish>,needs=<design|plan|execute|resolved>
```

There are no defaults or shorthand forms. Use `blocks=execute,needs=resolved`
when code needs the dependency merged. Use an earlier `needs` phase only when a
settled design or plan is genuinely sufficient.

## File children

File dependency-free and earlier children first so later children can use the
real `path` returned by `--json`:

```bash
gw work file --json \
  --title "<child title>" \
  --kind Feature \
  --summary "<one line>" \
  --name "<stable basename words>" \
  --parent-path "<work-path>" \
  --affects "<repo paths or packages>" \
  --dep "path=<earlier-child-path>,blocks=execute,needs=resolved"
```

Record the result’s `path` before filing the next child. Each child page lives
at `<workspace>/okf/<child-path>.md`; its owned directory is
`<workspace>/okf/<child-path>/`, and its later design artifact belongs at
`<workspace>/okf/<child-path>/references/01-design.md`.

Do not pre-write or transplant child designs during decomposition. Each child’s
design stage owns its canonical design artifact.

## Write the plan

Write the decomposition record to the dispatch brief’s `artifact.path`, or by
default to:

```text
<workspace>/okf/<work-path>/references/02-plan.md
```

Include:

- each child’s canonical `path`, type, summary, and affects;
- every complete dependency edge and its rationale;
- which children are independent and may run concurrently;
- any child that could not be filed and the exact CLI error.

The filed children are the executable plan, so the canonical plan artifact
needs no parallel task inventory.

## Stop

Stop after writing the plan and filing the children. Do not advance the parent,
start a child, or invoke an execution skill. The workflow skill owns advancement.
