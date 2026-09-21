---
name: query
description: Use when the user asks a substantive question about the repo that the wiki might answer, or invokes /gw:query "<question>". Reads index.md first, drills into 3-10 relevant pages across categories (concepts, entities, ADRs, sources, work), synthesizes an answer with inline root-absolute markdown links and `code-path:line` citations, and offers to file the answer back as a new concept page (choosing the kind).
---

# Query the wiki

Ask the wiki a question. The librarian reads `index.md` first, picks relevant pages across categories, synthesizes an answer with citations (wikilinks + code paths), and offers to file the answer back so your explorations compound.

## Usage

```
/gw:query "<your question>"          # Claude Code
$query "<question>"                            # Codex
/gw:query "which packages depend on common-context-node-ts?"
/gw:query "how does GlobalContext get set up for a request?"
/gw:query "what's the state of the ESM migration?"
/gw:query "which packages use React 19?"
/gw:query "compare zustand and redux — what do we use where and why?"
/gw:query "what's blocking healthkit tests from being reliable?"
```

## Dispatch

Prefer running this skill's body in a forked sub-agent with the tool set
`Read, Write, Edit, Bash, Grep, Glob`. A query reads many pages to answer one
question; its intermediate reading would flood a caller's context if run
inline.

On a harness without sub-agent dispatch, run it inline in the current
context — the body below is written to work either way.

## Role

You answer questions against a Code Wiki. You prioritize reading the vault over re-deriving from code — the vault already contains pre-synthesized knowledge with cross-references. If the vault doesn't cover the question, you fall back to reading the code (the source of truth), and flag the gap so the user can ingest/create appropriate pages.

You **file good answers back** so explorations compound.

Spawned per-query.

## Inputs

- The user's question
- The current state of `<workspace>/okf/` (especially `index.md`)
- The repo's code (fallback when vault is insufficient)

## Output formats

| Question shape | Output |
|---|---|
| "What does X do" | Markdown explanation with citations |
| "Who depends on X" | Table from package frontmatter + scan data |
| "A vs B" | Comparison table |
| "What's the state of X migration" | Summary of roadmap + recent log entries |
| "Why does X fail / how do we work around Y" | Issue page content |
| "Slide deck on X" | Not implemented — synthesize markdown and note that slide-deck export isn't available yet |

## Workflow

Follow `../graph-works/references/query-workflow.md`. Summary:

### 1. Read `index.md` first
Also run the retrieval call:
```bash
gw query --query "<question>" --json
```
This is the `claude_code`-backend default — it returns a `top_pages` list, each with a `path`, an `excerpt`, and `search_scores`. Treat those paths as part of the starting candidate set alongside `index.md`, from the outset rather than only once the index comes up empty. `--backend bedrock` / `--backend vercel` still run the full internal pipeline (an internal LLM call composes the answer) for workspaces that opt into it.

Pick 3-10 pages across categories most likely to contain the answer:
- `explanations/` — cross-cutting patterns and high-level syntheses (filter by the `architecture` tag for big-picture questions, `pattern` for reusable patterns)
- `repositories/<repo>/packages/`, `repositories/<repo>/apps/` — package/app surface area
- `dependencies/<ecosystem>/` — external-library questions
- `work/` — bug / tech-debt / planned / in-progress questions
- `adrs/` — "why did we do it this way"
- `sources/` — evidence and original context

**In-repo doc sources:** Search results may include `type: Source` pages with `source_kind: doc` — these summarize in-repo `.md` design docs. When citing a claim that originates in such a doc, prefer the vault source page (`[<slug>](/sources/<YYYY-MM>-<slug>.md)`); the source page itself cites the canonical repo-relative `source_path`.

### 2. Read the picked pages in full

### 3. Follow wikilinks opportunistically
Stop when you have enough.

### 4. Read the code as a last resort
`gw query` is already step 1's retrieval call, not something reached only when index-reading fails. If neither `top_pages` nor the index covers the question, read the code directly.

### 5. Synthesize the answer
Format:
- **Direct answer** — 1-3 sentences
- **Supporting detail** — organized thematically
- **Inline citations** — root-absolute markdown links for vault pages, `` `code-paths:line` `` for code
- **Related pages** — 3-5 links at the end

### 6. Offer to file back
```
_Should I file this as a new page? Suggested location:
 `<workspace>/okf/explanations/<slug>.md` — tag it `architecture` for system-level syntheses
 or `pattern` for reusable patterns. Or I can append to [existing-page](/existing-page.md)._
```

If yes, pick the right tag (see above), follow `../graph-works/references/page-formats.md` for the `Explanation` frontmatter (`type`, `title`, `description`, `tags`, `updated`) and headings, update `index.md`, append to `log.md` with `op: create`.

## Rules

- **Read the index first.** No grep-everything.
- **Every claim cites** a vault page or code path.
- **Offer to file back** — for substantive answers worth keeping.
- **If the vault doesn't know**, say so and suggest a source to ingest or an explanation page to create.
- **Output format follows the question** — "A vs B" → table; "who depends on X" → list; "how does X work" → prose with citations.

## Red flags

- Answering without reading the index → go back
- Citing only one page for a multi-package question → broaden
- Inventing a concept not in the vault or code → stop, suggest creation
- Filing a new page for a trivial question → don't pollute the vault

## Reference

→ `../graph-works/SKILL.md`
→ `../graph-works/references/query-workflow.md`
