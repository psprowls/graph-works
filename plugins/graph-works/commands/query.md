---
name: query
description: Query the Code Wiki — reads index.md first, drills into 3-10 relevant pages (concepts, entities, ADRs, sources), synthesizes answer with inline [[wikilinks]] and `code-paths:line`, and offers to file the answer back as a concept page. Usage /graph-works:query "<question>"
---

# /graph-works:query

Ask the wiki a question. The librarian reads `index.md` first, picks relevant pages across categories, synthesizes an answer with citations (wikilinks + code paths), and offers to file the answer back so your explorations compound.

## Usage

```
/graph-works:query "<your question>"
/graph-works:query "which packages depend on common-context-node-ts?"
/graph-works:query "how does GlobalContext get set up for a request?"
/graph-works:query "what's the state of the ESM migration?"
/graph-works:query "which packages use React 19?"
/graph-works:query "compare zustand and redux — what do we use where and why?"
/graph-works:query "what's blocking healthkit tests from being reliable?"
```

## What happens

1. **Index-first read** — `<workspace>/wiki/index.md`
2. **Drill-in** — 3-10 pages across categories (concepts + entities + sources + adrs + work)
3. **Follow links** — opportunistic
4. **Fallback** — `gw query` (BM25); if still nothing, reads code directly
5. **Synthesize** — direct answer + supporting detail + inline citations (`[[wikilinks]]` + `` `code-paths:line` ``) + "Related pages"
6. **Offer to file back** — as a concept page (choosing the kind: `architecture`, `pattern`, or default `concept`), comparison, or adr

## Output formats

| Question shape | Output |
|---|---|
| "What does X do" | Markdown explanation with citations |
| "Who depends on X" | Table from package frontmatter + scan data |
| "A vs B" | Comparison table |
| "What's the state of X migration" | Summary of roadmap + recent log entries |
| "Why does X fail / how do we work around Y" | Issue page content |
| "Slide deck on X" | Not implemented — synthesize markdown and note that slide-deck export isn't available yet |

## Sub-agent

Dispatches the `librarian` sub-agent. See `agents/librarian.md`.

## Rules

- **Read the index first.** No grep-everything.
- **Every claim cites** a vault page or code path.
- **Offer to file back** — for substantive answers worth keeping.
- **If the vault doesn't know**, say so and suggest a source to ingest or a concept page to create.

## Skill Reference

→ `graph-works/SKILL.md`
→ `graph-works/references/query-workflow.md`
