---
name: workflow
description: Use when driving a work item through its development pipeline — runs `gw work next` to compute the stage, dispatches the stage skill (brainstorming, reconciling-spec, systematic-debugging, writing-plans, subagent-driven-development, test-driven-development, finishing-a-development-branch), verifies the artifact, and advances the item with `gw work advance`. One stage per invocation; clear context between stages. Pass `--descend` to auto-continue into the next actionable child leaf when an epic is waiting on children.
---

# Work Item Workflow

Dispatch one pipeline stage for a work item, then advance it. The CLI owns every
decision (routing, transitions, validation); this skill only relays.

**One stage per invocation, by design.** Never chain stages in a session — each
stage gets a fresh context window. The work item plus its owned `references/` artifacts are the
durable state between sessions; nothing depends on conversation memory.

**Invariant:** a work item's canonical path — and its
`<workspace>/okf/<work-path>/` owned directory — always exists before any spec
or plan is written. `gw work file` creates the owned directory at filing time. Brainstorming's auto-file mode enforces this
for standalone invocations; every stage dispatched from this pipeline assumes
it already holds.

The workspace doc-routing hook injects the resolved absolute workspace path into your context — use it if present when you see `<workspace>` mentioned in a command or instruction.

`gw` being on PATH doesn't prove it's this repo's build — a stale entry point can
own the name. Verify identity, not presence: `gw util describe-surface --json
>/dev/null 2>&1 || echo "gw is not graph-works-cli — use: uv run --package
graph-works-cli gw …"`.

## Steps

### 1. Resolve & report

Run `gw next <work-path> --json`.

`gw next` wraps the read-only `gw work next` and adds two keys to the JSON:
`guidance` (ranked phase-relevant pages) and `guidance_warnings`. `--file`
defaults to `"auto"`, which resolves beneath the item’s owned directory
and writes the assembled guidance bodies there when any matched; pass an
explicit `--file <work-path>` to override, or `--file ""` to skip writing entirely.
The parent dir is created on demand. The resolved (or skipped) target comes
back in the JSON's `guidance_file` key. `gw work next` itself may also return
`normalized` — a list of persisted managed-source repairs, each carrying the
canonical item `path`, canonical `source_id` (`design`), and root-absolute
`resource`; `null` means no source was repaired. A `--descend` repair may list
both the requested ancestor and selected leaf. Relay each entry to the user
like any other CLI finding: it
is the one write `gw work next` performs, and it is reported precisely so it
is never silent. All the blocker / terminal / dispatch fields the steps below
read are unchanged from `gw work next`.

- If `blockers` is non-empty:
  - If a blocker reports a **terminal status** (`resolved`, `wontfix`, or
    `superseded`) or **`phase=done`**, run **Terminal handling** (below): the
    pipeline is finished and the remaining work is ingest + archive.
  - Otherwise report each blocker and **stop** — *except* the **effort-required**
    blocker and the **"waiting on children"** blocker, which are handled by the
    dedicated bullets below (a non-null `on_complete` with empty `blockers` is the
    separate **satisfied gate**, also below). Do not improvise around `mitigated`
    items, invalid enums, or unknown paths — these are human decisions.
- If the only blocker says **effort required**: ask the user to size the item
  (xtra-small / small / medium / large / xtra-large — xtra-small/small means a bug-like item skips the planning stage),
  then run `gw work advance <work-path> --effort <value>` and re-run `gw work next`.
- If `action.skill` is **null**, `blockers` is empty, and `on_complete` is
  **non-null** — this is a **satisfied gate** (an epic whose children are all
  terminal, or an epic whose finish stage is satisfied). Do not dispatch a
  skill: run `gw work advance <work-path>` directly (step 5) — its own terminal
  check governs what happens next (Terminal handling if the advance lands on
  `phase: done` / `work_status: resolved`, otherwise the step 6 hand-off).
- If `action.skill` is **null** and a blocker says **"waiting on children"** —
  the epic's execute gate is unsatisfied. If the invocation carried `--descend`
  (or the user asks to auto-continue), re-run `gw next <work-path> --json --descend`:
  the JSON now describes the next actionable *leaf* in `selected_path`, with
  the chain in `descent.path`. Announce the descent path, then drive the leaf
  through the normal steps — dispatch transition, stage skill, advance — all
  against the leaf path. If the descend itself reports a `--descend:` blocker,
  or no `--descend` was requested, report the blocker, list the open children
  from `child_rollup.open_paths` suggesting `/graph-works:workflow <child-path>` for
  each, and **stop** (nothing to advance).
- Otherwise announce the dispatch: item title, kind, phase, and the stage skill
  from `action.skill`.

### 2. Apply the dispatch transition (when present)

If the JSON carries a non-null `on_dispatch`, apply it mechanically **before**
dispatching: run `gw work advance <work-path>`, supplying any flag named in
`on_dispatch.requires` (e.g. `--owner <handle>` when dispatching execution —
ask the user if no owner is known). Do not special-case stages; the CLI encodes
which transitions happen at dispatch time.

### 3. Dispatch the stage skill

Invoke the stage skill named by `action.skill` via the Skill tool, using the
value **verbatim**, prepending a work-item brief.

`action.skill` is the complete name. A qualified `<plugin>:<skill>` is passed
as-is; a bare name is passed as-is and resolves to a user-level or repo-local
skill. **Never prepend a namespace** — there is no implicit plugin, and
prefixing a qualified name produces a name that resolves to nothing.

**Preflight — confirm the skill resolves.** Before invoking, check that the name
you are about to invoke — `action.skill`, used verbatim — appears
in this harness's available-skills listing. If it does not, **stop** and report
that name, and, when it is qualified (`<plugin>:<skill>`), the plugin that is
missing. Do not substitute a similar skill, and do not perform the stage
yourself. This applies to bare and qualified names alike: a bare name is a valid
configured value, so checking only qualified ones would leave exactly that case
unguarded.

**Auto-drive relay override.** If `action.skill`'s **last segment** — the part
after the colon in a qualified `superpowers:finishing-a-development-branch` — is
`finishing-a-development-branch`, **and** the dispatch prompt that launched
this session contains an `Auto-drive context:` line, dispatch
`graph-works:finishing-relay` instead of the configured skill —
the merge target is already embedded in that same line, so no extra
forwarding is needed beyond the standard work-item brief below. This
override needs no STOP line: `finishing-relay` doesn't self-chain into
another stage, same as the stock skill it replaces.

- title, kind, summary, `affects`, and effort from the item's frontmatter
- links to the canonical `design` and `plan` entries in `sources[]` so the
  stage starts from durable artifacts, not from memory
- when `artifact.path` is set: "Write your output document to
  `<artifact.path>` — this overrides the skill's default location."
- when the `gw next` output's `guidance` list is non-empty, add a
  `## Relevant guidance` block to the brief pointing the stage skill at the
  assembled bundle:

  ```
  ## Relevant guidance
  Phase-relevant guidance assembled at: <guidance_file value from gw next's JSON output>
  Read it before starting this stage.
  ```

  Omit this block entirely when `guidance` is empty (guidance skipped or no
  matches). Surface any `guidance_warnings` to the user as plain notes.
- **Stage directives (riders).** Look `action.skill` up in the rider table
  below, keyed on its **last segment** — the part after the colon in a qualified
  `superpowers:brainstorming`. If a rider exists, open
  `references/brief-riders.md`, find the matching `## Rider: <name>` section, and
  inline its **Rider** block into the brief **verbatim**, under a
  `## Stage directives` heading. A rider is not a summary and not optional: the
  stage skill is stock and cannot be patched, so the rider text *is* the
  behavior. If `action.skill` has no rider, add nothing.

  Substitute `<workspace>` with the resolved absolute workspace path and
  `<work-path>` with the item's canonical path. Where a section documents a
  **Conditional clause**, apply its condition against the item's frontmatter and
  omit the clause when the condition does not hold.

  Today's STOP lines live in this table too. They were always riders; there is
  no reason for two mechanisms. Omitting one lets the skill self-chain into the
  next stage, collapsing two stages into one session.

<!-- rider-table:start -->

| Skill | Stage | Carries |
|---|---|---|
| `brainstorming` | design | `--project-dir <workspace>` for the visual companion, plus the spec STOP line |
| `writing-plans` | plan | A self-sufficient Execution-Handoff stop |
| `planning-epics` | plan (epic) | Its STOP line |
| `subagent-driven-development` | execute | Positive authorization + mandatory isolation |
| `test-driven-development` | execute | Positive authorization + mandatory isolation |
| `finishing-a-development-branch` | finish | The on-trunk two-option menu |

<!-- rider-table:end -->

  `reconciling-spec`, `finishing-relay` and `systematic-debugging` have no
  rider — they are ours, or they carry no `MOVE-TO-BRIEF` behavior.

The stock skills honor user-preference path overrides; they stay unmodified.

### 4. Verify the artifact

When `artifact.path` is set, check the file exists after the stage completes.
The brainstorming and writing-plans skills' own stock locations are already
the pipeline path — `<work-path>/references/01-design.md` and `02-plan.md`
respectively — the same path `artifact.path` points to, so the file should
already be there. If the skill instead wrote elsewhere, move the file (and
any `.tasks.json` companion) to `artifact.path` and say so.

### 5. Advance

Run `gw work advance <work-path>` with whatever flags the stage produced
(`--effort` if the command demands it, `--resolved-in <ref>` when completing
the finish stage). Report the lint findings it returns — they are the item's
health check, not noise. If the command errors with *effort required*, ask the
user to size the item as in step 1 — never pick an effort yourself — then retry.

**Relay no-advance outcomes.** If the just-completed stage was
`graph-works:finishing-relay`, skip this step's own `gw work advance` call
entirely for **every** relay outcome, including `merge` — the relay skill
already settled the item's state: for `merge`, its own R5 step already ran
`gw work advance <work-path> --resolved-in <ref>`; for `pr`/`hold`/`discard`, R5
deliberately chose not to advance. Calling `gw work advance` again here for
the `merge` outcome would double-advance an already-advanced item and error.
Because step 5 isn't calling advance itself in the `merge` case, it won't
naturally observe a `phase: done` / `work_status: resolved` landing either — to
decide whether **Terminal handling** applies, check the item's resulting
state directly (re-run `gw work next <work-path> --json`, or trust relay's own
`worker_done` report) instead of relying on this step's advance call to
surface it. For the `pr`/`hold`/`discard` outcomes the item deliberately
stays at `phase: finish`, so skip Terminal handling and go straight to step
6 — but see step 6's carve-out below before using its stock hand-off text.

If the advance lands the item at `phase: done` and `work_status: resolved`, run
**Terminal handling** (below) instead of the step 6 hand-off.

### 6. Hand off

End with: "Phase advanced to `<phase>`. Clear context (`/clear`) and run
`/graph-works:workflow <work-path>` to continue."

**Relay no-advance hand-off.** For a `graph-works:finishing-relay` stage that
reported `pr`, `hold`, or `discard`, the stock hand-off text above is wrong —
nothing advanced. Say instead: "`<work-path>` stays at `phase: finish` pending an
attended pass (relay outcome: `<pr|hold|discard>`). Clear context (`/clear`)
and run `/graph-works:workflow <work-path>` when ready to continue attended."

(Items that have reached a terminal state are handled by **Terminal handling**
below, not this hand-off.)

### Terminal handling

Run this when an item has reached a terminal state — either `gw work advance`
just landed it at `phase: done` / `work_status: resolved` (step 5), or `gw work next`
reported a terminal-status / `phase=done` blocker (step 1). This is post-pipeline
cleanup, not a pipeline stage — run it inline (it does not get its own fresh
context window).

1. **Ingest the design (resolved only).** If `work_status` is `resolved`, read
   `<workspace>/okf/<work-path>.md` and find the `sources[]` entry with
   `id: design`. If its root-absolute `resource` resolves beneath the OKF
   bundle, dispatch the ingest skill (`graph-works:ingest`) on that artifact inline; the ingestor runs its own
   confirmation dialog and, on success, archives the source and repoints the
   pointer. Skip the ingest (announce "no design to ingest") when `work_status` is
   `wontfix`/`superseded`, when no `design` source exists, or when the resource is already gone
   (already ingested). Plan-artifact ingest is a deferred future extension —
   ingest does not yet accept plan-type sources.

2. **Offer to archive (any terminal status).** Ask the user "Archive `<work-path>`
   now?" If yes, run `/graph-works:archive <work-path>`. If no, report that the item
   stays in `work/` and can be archived later with `/graph-works:archive`.

### Detaching a child

When `gw work advance <work-path>` refuses with *"waiting on children"*, either
finish the children (`gw next <work-path> --descend`) or explicitly move a
complete child subtree with `gw work reparent <child-path> --parent
<new-parent-path>`. Physical placement is ownership; do not add a hierarchy
field to frontmatter.
