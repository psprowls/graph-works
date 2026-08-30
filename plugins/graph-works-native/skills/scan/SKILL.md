---
name: scan
description: Use when the user says "scan the monorepo", "update entity pages", "catch the wiki up to the code", or invokes /gw:scan. Walks the repo, builds the code graph, and writes one graph-derived page per admitted entity (repository, package, app, agent_plugin, dependency, test_suite) nested under repositories/<repo>/ (and dependencies/ for deps). Reports added/updated/deleted entities by URI and surfaces deletions for confirmation.
---
# Scan the repository into the wiki

Build the code graph and write one page per admitted entity nested under `repositories/<repo>/` (and `dependencies/` for deps). This is the **entry point** for a fresh wiki — run it right after `/gw:onboard`.

## Usage

```
/gw:scan          # Claude Code
$scan                      # Codex
```

Workspace and repo are discovered automatically via `workspace_io`.

## Dispatch

Prefer running this skill's body in a forked sub-agent with the tool set
`Read, Write, Edit, Bash, Grep, Glob`. The scan walks the whole repo and its
output would flood a caller's context if run inline.

On a harness without sub-agent dispatch, run it inline in the current
context — the body below is written to work either way.

## Role

You keep `<workspace>/okf/repositories/<repo>/` (and `<workspace>/okf/dependencies/`) in sync with what the code graph says the repo contains. Scan runs as a three-phase pipeline: **emit** (build graph, write entity pages, inject deterministic file maps, compute commit-gate, serialize worklist) → **fan-out** (dispatch read-only subagents per entity whose prose needs a diff-driven refresh) → **apply** (inject structured results, stamp anchors, regenerate indexes/backlinks, log). The mechanical scripts own all page writes; fan-out subagents are strictly read-only (Read/Grep/Glob only, no Write) and return structured records that the apply phase persists.

Spawned per scan, not long-running.

## Inputs

- Repo root and wiki path (resolved automatically via the workspace resolver)
- Current state of `<workspace>/okf/repositories/<repo>/`

## When to run

- Right after `/gw:onboard`
- After pulling main (new packages may have landed)
- After a big refactor that added/removed/renamed packages
- Before `/gw:lint` (so drift reports are accurate)

## Workflow

Follow `../graph-works/references/scan-workflow.md`. Summary:

### 1. Emit the worklist

```bash
gw scan --emit-worklist "$GRAPH_WORKS_DIR/.gw/cache/worklist.json"
```

This builds the code graph, writes/updates/deletes entity pages under `repositories/<repo>/` (and `dependencies/`) deterministically, injects deterministic file maps, computes the commit-gate, and serializes the worklist (`prose_tasks`, `propagate_tasks`, `short_head`) to the given path. It also renders one **refresh brief** per stale entity into a sibling `briefs/` directory and resets an empty `results/` directory for the fan-out. It prints `worklist_path`, `briefs_dir`, `results_dir`, and a `ScanResult` with `entities_created`, `entities_updated`, `entities_deleted` (URIs), and `entity_errors`. Read those directory paths from the payload — never hardcode them. (`gw scan --no-narrate` is the structural-only fast path — no worklist, no briefs.) Scanner-owned keys (`uri`, `kind`, `depends_on`, `language`, …) are replaced from the graph each scan; human keys (`status`, `last_reviewed`, `owner`, `notes`) and a non-empty `summary` are preserved.

Surface deletions and red flags here exactly as described below.

### 2. Short-circuit on steady state

If `prose_tasks` and `propagate_tasks` are both empty lists, skip to reporting — a no-op scan dispatches zero subagents.

### 3. Fan out read-only subagents

Using the `dispatching-parallel-agents` batching discipline, dispatch a **PROSE-REFRESH subagent** per `prose_tasks` entry with a single instruction — *"Follow the brief at `<briefs_dir>/<page-stem>.md` exactly, and write your JSON object to the results path the brief names."* The brief is self-contained: it carries the deterministic/prose contract, the scoped source diff, the current prose sections, the File-map rows, the graph context, and the exact output schema. Do not paraphrase it, re-derive it, or add instructions of your own — the brief is the same contract the Bedrock path uses, and drift between them is the failure mode it exists to prevent.

Subagents run forked and are **strictly read-only** (Read/Grep/Glob only — NO Write). You assemble their structured output; the apply phase performs every page write.

### 4. Collect the results

Each prose-refresh subagent writes its own `<results_dir>/<page-stem>.json` — you do not assemble a combined results file and you never route replacement prose back through your own context. A failed or empty subagent simply leaves no file; its entity retries on the next scan. Emit cleared `results/` before the fan-out, so anything in there is from this run.

### 5. Apply

```bash
gw scan --apply --results-dir [[ORCA_RICH_MD:e803ddaf515070790706f91dffb597cf:inline-html:%3Cresults_dir-from-emit-payload%3E]] --short-head [[ORCA_RICH_MD:e803ddaf515070790706f91dffb597cf:inline-html:%3Cshort_head-from-worklist%3E]]
```

This injects all results — each one filtered against its task's declared prose surface before it touches a page — runs the refill-gated anchor stamp, regenerates indexes and backlinks, and appends to `log.md`. It prints an `ApplyResult` with `narrated`, `described`, `dir_filled`, `sections_filled`, and `stamped`. Report any `entity_errors` verbatim — a malformed per-entity result file shows up there rather than failing the apply.

### 6. Surface deletions (never silently)

The emit step has already applied deletions. Do not let them pass silently:

- Always list the deleted URIs.
- If `<workspace>/okf/` is under version control, run `git -C <workspace>/okf status --short repositories/` and offer to undo any deletion the user objects to with `git -C <workspace>/okf checkout -- repositories/<repo>/<kind-folder>/<file>`.
- Entity pages regenerate deterministically on the next scan, so undo/redo is always safe.

### 7. Report

Bulleted wikilinks to the changed entity pages. Suggest follow-ups (e.g. `/gw:lint` to catch drift, `/gw:ingest` on a README/spec to flesh out `## Narrative` and file-map descriptions).

> **Contract requirement.** The emit/apply pair must round-trip through `gw` as two subprocess
> calls with a filesystem handoff (`worklist.json`, `briefs/`, `results/`). `gw scan --emit-worklist`
> must print the `worklist_path`, `briefs_dir`, `results_dir`, and `ScanResult` payload on stdout as
> JSON, and must exit non-zero with a readable message when the scan aborts (the former
> `ScanAbortedError`). If `gw` cannot reproduce that handoff, the gap is a defect in `gw`, not a
> reason to reintroduce an in-process branch here.

## Rules

- **Don't silently delete entity pages** — always surface deletions; &gt;10 is a red flag.
- **Default fills prose** — the default scan fills `## Narrative` and file-map descriptions via the read-only fan-out + apply phases; `--no-narrate` skips the fan-out and leaves placeholders.
- **The graph is the source** — entity pages are rendered from the code graph, not hand-written.
- **If you hand-edit any entity page** (you normally won't — the script owns them), preserve human keys. Data frontmatter keys are replaced every scan; human keys (`status`, `last_reviewed`, `owner`, `notes`) and a non-empty `summary` are preserved.
- **No wiki writes from the fan-out.** A prose-refresh subagent's only write is its own `results/<stem>.json`; it never edits a wiki page or anything in the repo. The apply phase performs every page write.

## Red flags

Stop and ask before proceeding if:

- `entities_deleted` has **&gt;10** entries (likely a bad repo path or a failed graph build — inspect before committing).
- `entity_errors` is non-empty (partial write — report the errors verbatim).
- The script reports a hard abort (`scan aborted: cg update failed …`) — surface the diagnostic; do not retry blindly.

## Reference

→ `../graph-works/SKILL.md`
→ `../graph-works/references/scan-workflow.md`