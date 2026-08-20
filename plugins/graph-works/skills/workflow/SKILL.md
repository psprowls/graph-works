---
name: workflow
description: Use when driving a work item through its development pipeline — runs `gw work next` to compute the stage, dispatches the stage skill (brainstorming, reconciling-spec, systematic-debugging, writing-plans, subagent-driven-development, test-driven-development, finishing-a-development-branch), verifies the artifact, and advances the item with `gw work advance`. One stage per invocation; clear context between stages.
---

# Work Item Workflow

Dispatch one pipeline stage for a work item, then advance it. The CLI owns every
decision (routing, transitions, validation); this skill only relays.

**One stage per invocation, by design.** Never chain stages in a session — each
stage gets a fresh context window. The work item plus `raw/` artifacts are the
durable state between sessions; nothing depends on conversation memory.

**Invariant:** a work item's slug — and its `wiki/work/<slug>/` working directory
— always exists before any spec or plan is written. `gw work file` creates the
working directory at filing time. Brainstorming's auto-file mode enforces this
for standalone invocations; every stage dispatched from this pipeline assumes
it already holds.

The workspace doc-routing hook injects the resolved absolute workspace path into your context — use it if present when you see `<workspace>` mentioned in a command or instruction.

If `gw` is not on PATH, run it as
`uv run --package graph-works-cli gw …`.

## Steps

### 1. Resolve & report

Run `gw next <slug> --json`.

`gw next` wraps the read-only `gw work next` and adds two keys to the JSON:
`guidance` (ranked phase-relevant pages) and `guidance_warnings`. `--file`
defaults to `"auto"`, which resolves to `wiki/work/<slug>/NN-<phase>-guidance.md`
and writes the assembled guidance bodies there when any matched; pass an
explicit `--file <path>` to override, or `--file ""` to skip writing entirely.
The parent dir is created on demand. The resolved (or skipped) target comes
back in the JSON's `guidance_file` key. `gw work next` itself may also return
`normalized` — a dict with a `"spec_doc": "<rel>"` key when it repaired a
missing `spec_doc` pointer (plus `"ancestor_spec_doc": "<rel>"` when
`--descend` also healed the ancestor's pointer), `null` otherwise. Relay it to
the user like any other CLI finding: it
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
    items, invalid enums, or unknown slugs — these are human decisions.
- If the only blocker says **effort required**: ask the user to size the item
  (xtra-small / small / medium / large / xtra-large — xtra-small/small means a bug-like item skips the planning stage),
  then run `gw work advance <slug> --effort <value>` and re-run `gw work next`.
- If `action.skill` is **null**, `blockers` is empty, and `on_complete` is
  **non-null** — this is a **satisfied gate** (an epic whose children are all
  terminal, or an epic whose finish stage is satisfied). Do not dispatch a
  skill: run `gw work advance <slug>` directly (step 5) — its own terminal
  check governs what happens next (Terminal handling if the advance lands on
  `phase: done` / `status: resolved`, otherwise the step 6 hand-off).
- If `action.skill` is **null** and a blocker says **"waiting on children"** —
  the epic's execute gate is unsatisfied. If the invocation carried `--descend`
  (or the user asks to auto-continue), re-run `gw next <slug> --json --descend`:
  the JSON now describes the next actionable *leaf* (its top-level `slug`), with
  the chain in `descent.path`. Announce the descent path, then drive the leaf
  through the normal steps — dispatch transition, stage skill, advance — all
  against the leaf slug. If the descend itself reports a `--descend:` blocker,
  or no `--descend` was requested, report the blocker, list the open children
  from `child_rollup.open_slugs` suggesting `/graph-works:next <child>` for
  each, and **stop** (nothing to advance).
- Otherwise announce the dispatch: item title, kind, phase, and the stage skill
  from `action.skill`.

### 2. Apply the dispatch transition (when present)

If the JSON carries a non-null `on_dispatch`, apply it mechanically **before**
dispatching: run `gw work advance <slug>`, supplying any flag named in
`on_dispatch.requires` (e.g. `--owner <handle>` when dispatching execution —
ask the user if no owner is known). Do not special-case stages; the CLI encodes
which transitions happen at dispatch time.

### 3. Dispatch the stage skill

Invoke the stage skill named by `action.skill` via the Skill tool (namespaced
`graph-works:<skill>`), prepending a work-item brief:

**Auto-drive relay override.** If `action.skill` is
`finishing-a-development-branch` **and** the dispatch prompt that launched
this session contains an `Auto-drive context:` line, dispatch
`graph-works:finishing-relay` instead of `finishing-a-development-branch` —
the merge target is already embedded in that same line, so no extra
forwarding is needed beyond the standard work-item brief below. This
override needs no STOP line: `finishing-relay` doesn't self-chain into
another stage, same as the stock skill it replaces.

- title, kind, summary, `affects`, and effort from the item's frontmatter
- links to prior artifacts (`spec_doc`, `plan_doc`) so the stage starts from
  the durable state, not from memory
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
- when the dispatched `action.skill` is a chained-handoff skill, add the
  matching STOP line so its pipeline-stage guard fires (without it the skill
  self-chains into the next stage, collapsing two stages into one session):
  - dispatching `brainstorming` → add: *"STOP after writing the spec — do not
    invoke writing-plans. This is a single pipeline stage; the workflow skill
    advances the item."*
    This prepended work-item brief and STOP line are the canonical "a work item
    already exists" signal that `brainstorming` keys off of to suppress its
    standalone auto-file path — no dispatch-logic change is needed here.
  - dispatching `writing-plans` → add: *"STOP after writing the plan — do not
    run the Execution Handoff. This is a single pipeline stage; the workflow
    skill advances the item."*
  - dispatching `planning-epics` → add: *"STOP after writing the plan_doc and
    filing the children — do not advance the epic or start a child. This is a
    single pipeline stage; the workflow skill advances the item."*
  - `systematic-debugging`, `test-driven-development`,
    `subagent-driven-development`, and `finishing-a-development-branch` need no
    STOP line — they do not self-chain into the next stage.

The stock skills honor user-preference path overrides; they stay unmodified.

### 4. Verify the artifact

When `artifact.path` is set, check the file exists after the stage completes.
If the skill wrote to its stock location (`<workspace>/raw/specs/` or
`<workspace>/raw/plans/` in the workspace), move the file (and any `.tasks.json`
companion) to `artifact.path` and say so.

### 5. Advance

Run `gw work advance <slug>` with whatever flags the stage produced
(`--effort` if the command demands it, `--resolved-in <ref>` when completing
the finish stage). Report the lint findings it returns — they are the item's
health check, not noise. If the command errors with *effort required*, ask the
user to size the item as in step 1 — never pick an effort yourself — then retry.

**Relay no-advance outcomes.** If the just-completed stage was
`graph-works:finishing-relay`, skip this step's own `gw work advance` call
entirely for **every** relay outcome, including `merge` — the relay skill
already settled the item's state: for `merge`, its own R5 step already ran
`gw work advance <slug> --resolved-in <ref>`; for `pr`/`hold`/`discard`, R5
deliberately chose not to advance. Calling `gw work advance` again here for
the `merge` outcome would double-advance an already-advanced item and error.
Because step 5 isn't calling advance itself in the `merge` case, it won't
naturally observe a `phase: done` / `status: resolved` landing either — to
decide whether **Terminal handling** applies, check the item's resulting
state directly (re-run `gw work next <slug> --json`, or trust relay's own
`worker_done` report) instead of relying on this step's advance call to
surface it. For the `pr`/`hold`/`discard` outcomes the item deliberately
stays at `phase: finish`, so skip Terminal handling and go straight to step
6 — but see step 6's carve-out below before using its stock hand-off text.

If the advance lands the item at `phase: done` and `status: resolved`, run
**Terminal handling** (below) instead of the step 6 hand-off.

### 6. Hand off

End with: "Phase advanced to `<phase>`. Clear context (`/clear`) and run
`/graph-works:next <slug>` to continue."

**Relay no-advance hand-off.** For a `graph-works:finishing-relay` stage that
reported `pr`, `hold`, or `discard`, the stock hand-off text above is wrong —
nothing advanced. Say instead: "`<slug>` stays at `phase: finish` pending an
attended pass (relay outcome: `<pr|hold|discard>`). Clear context (`/clear`)
and run `/graph-works:next <slug>` when ready to continue attended."

(Items that have reached a terminal state are handled by **Terminal handling**
below, not this hand-off.)

### Terminal handling

Run this when an item has reached a terminal state — either `gw work advance`
just landed it at `phase: done` / `status: resolved` (step 5), or `gw work next`
reported a terminal-status / `phase=done` blocker (step 1). This is post-pipeline
cleanup, not a pipeline stage — run it inline (it does not get its own fresh
context window).

1. **Ingest the spec (resolved only).** If `status` is `resolved` (from the
   `gw work next` / `gw work advance` JSON result), read the item's `spec_doc`
   from its frontmatter (`<workspace>/wiki/work/<slug>.md` — the finish→done
   transition does not re-stamp it). If `spec_doc` is set and the
   file exists at `<workspace>/<spec_doc>`, dispatch the ingest skill
   (`graph-works:ingest`) on that path inline; the ingestor runs its own
   confirmation dialog and, on success, archives the source and repoints the
   pointer. Skip the ingest (announce "no spec to ingest") when the status is
   `wontfix`/`superseded`, when `spec_doc` is unset (e.g. a small `test-gap`
   item that skipped design), or when the `spec_doc` file is already gone
   (already ingested). `plan_doc` ingest is a deferred future extension —
   ingest does not yet accept plan-type sources.

2. **Offer to archive (any terminal status).** Ask the user "Archive `<slug>`
   now?" If yes, run `/graph-works:archive <slug>`. If no, report that the item
   stays in `work/` and can be archived later with `/graph-works:archive`.

### Detaching a child

When `gw work advance <slug>` refuses with *"waiting on children"* (a feature
or epic gated on open children), the options are finishing the children
(`gw next <slug> --descend`) or detaching the child:

1. Edit the child's page (`wiki/work/<child>.md`) with the Edit tool and
   delete its `parent:` line — the child's `parent` key is the single source
   of truth; the parent's `children` list is derived.
2. Run `gw work regen-index` — the parent's `children` list refreshes.
3. If siblings `depends_on` the detached child, rule `depends-on-not-sibling`
   flags them on the next lint; clean those references up as needed.

Offer this whenever the children gate blocks an advance.
