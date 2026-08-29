# Query Workflow

The flow the LLM follows when the user runs `/graph-works:query <question>`.

## Core principle

**Read `index.md` first, then drill in.** Do NOT grep the entire wiki or the codebase on every query — the index is there precisely so you don't have to. For code-level details neither the retrieval call nor the wiki covers, fall back to reading the code directly.

## Step-by-step

### 1. Read `index.md` and retrieve candidate pages

Read the index — the catalog — and, alongside it, run:

```bash
gw query --query "<question>" --json
```

This is the `claude_code`-backend default (the one `gw query` runs unless a workspace opts into `--backend bedrock`/`--backend vercel`). It is a retrieval call, not an answer-composing one: an internal LLM never sees the question. Treat its results as part of the starting candidate set, from the outset rather than only once the index comes up empty.

**Output shape.** The `--json` output is a `QueryBrief`:
```json
{
  "query": "<question>",
  "top_pages": [
    {"path": "concepts/foo", "excerpt": "...", "search_scores": {"...": 0.0}}
  ]
}
```
`path` is a bundle concept id relative to `<workspace>/okf/` — **no `.md` suffix**. To read the page, append `.md` (`<workspace>/okf/concepts/foo.md`), or resolve it via `gw graph`. Read each `top_pages` entry's `path` in full — the `excerpt` and `search_scores` are there to help you triage which pages to open first, not to quote as the answer.

Scan the index and pick the 3-10 pages most likely to contain the answer, from `top_pages` and the index together. A good monorepo query usually pulls across categories:

- `concepts/` — for cross-cutting patterns and high-level syntheses; filter by `kind: architecture` for big-picture questions, `kind: pattern` for reusable patterns
- `repositories/<repo>/packages/`, `repositories/<repo>/apps/` — for specific package/app surface area
- `dependencies/<ecosystem>/` for "how do we use X library" questions
- `work/` for "why does X fail / what's planned / what's in progress"
- `adrs/` for "why did we do it this way"
- `sources/` for evidence and original context

### 2. Read the picked pages

Read them in full. They're short, curated, and already cross-referenced.

### 3. Follow wikilinks opportunistically

If a read page points to another clearly relevant page, follow it. Stop when you have enough.

### 4. Read the code as a last resort

`gw query` is already step 1's retrieval call, not something reached only when index-reading fails — do not re-run it here. If neither `top_pages` nor the index surfaces the right page, the wiki doesn't cover the topic at all: read the **code directly** — the wiki is not authoritative for code-level specifics. In that case, flag the gap: "The wiki doesn't document X. I read `<file>` to answer; want me to file a concept/package page?"

### 5. Synthesize the answer

Format:
- **Direct answer** — 1-3 sentences
- **Supporting detail** — organized thematically
- **Inline citations** — mix of:
  - wiki page links: `[xxx](/repositories/<repo>/packages/xxx.md)`, `[yyy](/sources/yyy.md)`
  - code paths with line numbers: `` `packages/foo/src/bar.ts:42` ``
- **Related pages** — 3-5 links at the end

### 6. Offer to file the answer back

**Every good answer is a candidate wiki page.** At the end of the answer, ask:

> _Should I file this as a new page? Suggested location:
> `<workspace>/okf/concepts/<slug>.md` — pick the kind: `architecture` for system-level syntheses, `pattern` for reusable patterns, or omit for general concepts. Or I can append to [existing-page](/existing-page.md)._

If yes:
- Pick the right category and kind:
  - "how does X work" (big picture) → `concepts/<slug>.md` with `kind: architecture`
  - "how does X work" (pattern) → `concepts/<slug>.md` with `kind: pattern`
  - "how does X work" (general) → `concepts/<slug>.md` (no `kind`)
  - "A vs B" → `concepts/<a>-vs-<b>.md`
  - "why did we decide X" → `adrs/` (only if it's capturing a real past decision)
  - "what's planned for X / why does X fail / workaround for Y" → `work/` (`kind:` discriminates)
- Use the appropriate template (`concept-architecture.md`, `concept-pattern.md`, or `concept.md`)
- Add frontmatter with `category`, `summary`, `updated` (and `kind` if applicable)
- Update `<workspace>/okf/index.md`
- Append a `## [YYYY-MM-DD] create | <question>` entry to `log.md` with the filed response path.

## Output formats

Not every query wants a markdown answer. Offer the user:

- **Markdown page** (default) — filed back as a wiki page
- **Dependency list / usage table** — for "who uses X" questions, derived from package frontmatter + scan data
- **Comparison table** — for "A vs B"
- **Marp slide deck** — not currently implemented; if asked, say so and offer a plain markdown synthesis instead
- **Chart (matplotlib)** — for data-driven questions; save to `<workspace>/okf/assets/charts/`

## Anti-patterns

- Do not grep the entire repo on every query — use the index, then drill into the wiki, then to code only if needed
- Do not answer without citations — every claim must link to a wiki page or code path
- Do not create a new page for a trivial one-off question — only file back substantive answers worth keeping
- Do not invent content not in the wiki or code — if you don't know, say so and suggest a source to ingest or a concept page to create
- Do not skip the `log.md` entry when filing an answer back
