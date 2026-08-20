---
name: planning-epics
description: Use when dispatched as the `plan` stage of an epic work item — decomposes the epic into concrete child work items, runs dependency/sequence analysis, files each child via `gw work file --parent/--depends-on`, and writes the plan_doc. Sibling of writing-plans (which plans a single feature).
---

# Planning Epics

Dispatched only at the **epic `plan` stage** by the `graph-works:workflow` skill.
Your job is to turn the epic's design spec into real, tracked child work items
plus a dependency graph — not to write code.

**Announce at start:** "I'm using the planning-epics skill to decompose this epic."

The workspace doc-routing hook injects the resolved absolute workspace path into
your context — use it if present when you see `<workspace>` below. If `gw` is not
on PATH, run it as `uv run --package graph-works-cli gw …`.

## The slug/stem contract (read first)

`<epic-slug>` is the epic's **file stem** — the same value `gw work next` takes,
e.g. `2026-06-26-epic-x`. The `--parent` value MUST be that stem. Every child you
file gets its own permanent slug, derived from its title; `gw work file --json`
returns it in the `slug` field. **Note each filed child's slug from the command
output** so later `--depends-on` values name real siblings, not guesses.

## Inputs

Your dispatch brief carries the epic's `title`, `kind` (`epic`), `summary`,
`affects`, `effort`, and links to its prior artifacts. Read the work item at
`<workspace>/wiki/work/<epic-slug>.md` for current frontmatter, including its
`spec_doc` pointer (by convention `<workspace>/wiki/work/<epic-slug>/01-design-spec.md`).
Read that `spec_doc` target first — it contains one section per anticipated
child: a full medium-detail section in the older single-pass design flow, or
a thin index entry (title / kind / slug words) if design ran the epic fan-out
(see step 4).

## Steps

1. **Read** the epic spec and the work item.

2. **Decompose** the epic into concrete child work items — one per child
   feature/bug/etc. Choose each child's `kind`
   (`feature`, `bug`, `tech-debt`, `test-gap`, `security`, `perf`, `spike`).
   Each child should be a clean, self-contained pipeline on its own.

3. **Sequence / dependency analysis** — determine which children must precede
   others. A child is runnable when every one of its `depends_on` edges is
   satisfied at the phase being dispatched; independent children should have
   **no** deps (they can run concurrently). Capture the rationale; you write
   it to the plan_doc in step 5.

   For each dependency, decide **what the dependent actually needs**:

   - It needs the dependency's code **merged** — the default. Write
     `--dep slug=<dep>` (or the equivalent `--depends-on`), which is
     `blocks: execute, needs: resolved`.
   - It needs only the dependency's **written plan or design** — it can start
     coding against a settled interface. Write `--dep slug=<dep>,needs=plan`
     (or `needs=design`). This is what lets two children overlap instead of
     serializing, and it is the reason this key exists.

   Three cautions. `needs` means *complete*: `needs: plan` opens once the
   dependency reaches `execute`, not while it sits at `plan`. And `blocks:
   design` / `blocks: plan` reintroduce, one edge at a time, exactly the
   child-serialization that phase-granular gating was built to remove — reach
   for them only when a child genuinely cannot be designed before its
   dependency lands.

   Third, and easiest to get wrong: an overlap edge does **not** order the
   merges. `--dep slug=<dep>,needs=plan` is `blocks: execute` — it opens the
   dependent's *execute* early and says nothing about `finish`, so the
   dependent can reach `finish` and merge while the dependency is still
   coding. Whenever the dependent's code would not build, or would land dead,
   on top of an unmerged dependency, it needs **two** edges naming that one
   slug: the overlap edge, plus `{slug: <dep>, blocks: finish, needs:
   execute}` to hold the merge. `gw work file` will not take both — it rejects
   any slug named twice across `--depends-on`/`--dep` — so file the child with
   the overlap edge and add the second entry to its `depends_on` frontmatter in
   the same second pass step 4 describes. Two entries for one slug are legal on
   the page; each `blocks` phase is evaluated on its own.

4. **File each child**, dependency-free / earlier children first so a later
   child can name an already-filed sibling in `--depends-on`. Pick 4
   intelligible slug words per child (the `epic-<kind>` prefix is derived
   automatically from `--parent` — never pass it yourself). If the epic's
   `01-design-spec.md` thin-index entry for this child includes a **`slug
   words:`** line (C3's format), use those four words **verbatim** for
   `--slug-words` — do not choose your own. This is what keeps the filed
   slug's suffix identical to the corresponding `child-specs/<slug-words>.md`
   draft, which is what makes the adoption step below a pure mechanical
   match. If no `slug words` line exists (an older-style spec with
   medium-detail sections only), pick words as today.

   ```bash
   gw work file --json --title "<child title>" --kind <kind> \
     --summary "<one line>" --parent <epic-slug> \
     --slug-words "<w1> <w2> <w3> <w4>" \
     [--depends-on <sibling-slug>,<sibling-slug>]
   ```

   For phase-granular edges, use the repeatable `--dep` instead (or alongside
   `--depends-on` — the two merge):

   ```bash
   gw work file --json --title "…" --kind feature \
     --parent <epic> --slug-words "a b c d" \
     --dep slug=<sibling-b>,needs=plan \
     --dep slug=<sibling-c>,blocks=finish,needs=execute
   ```

   Read the `slug` field from each JSON result and record it before filing the
   next child. `--parent` is validated against an existing `epic`- or `feature`-kind item (PARENT_KINDS), so
   `<epic-slug>` must be the epic/feature's file stem. If a child can only name a sibling
   filed after it (a genuine cycle of ordering, not of dependency — rare), file
   all children first, then a second pass edits the late child's `depends_on` in
   `<workspace>/wiki/work/<child-slug>.md` directly. Prefer ordering over the
   second pass.

4a. **Adopt pre-written specs.** After filing every child, run:

   ```bash
   gw work adopt-child-specs <epic-slug> --json
   ```

   This moves every `child-specs/<slug-words>.md` draft whose words match a
   filed child's slug into `<child-slug>/01-design-spec.md` and stamps that
   child's `spec_doc` frontmatter — mechanically, no judgment required from
   you. Record the result's four lists (`adopted`, `orphaned_drafts`,
   `unseeded_children`, `ambiguous`) for the plan_doc (step 5). This command is safe to
   run even when the epic used the older, non-fan-out design flow — with no
   `child-specs/` directory, it reports everything as `unseeded_children` and
   every child falls back to brainstorming at design, exactly as before.

   - **`orphaned_drafts`** — a draft exists but no filed child's slug matches
     it. Do not auto-file a child for it and do not delete the draft. This
     usually means pass-1's thin index was edited after fan-out (a child was
     dropped during the brainstorming skill's step-8 review) or a slug-words mismatch
     between the thin index and what you actually filed. **Warn in your
     announcement to the user** (name the stem and remind them the draft is
     still sitting in `child-specs/` for manual reconciliation) — do not
     stop the pipeline over it.
   - **`unseeded_children`** — a child was filed but has no matching draft.
     This is expected and not an error: that child's `design` stage falls
     back to `brainstorming`, same as any child from an epic that never ran
     the fan-out. Note it in the plan_doc so it's visible, not silently
     different from its fan-out-seeded siblings.
   - **`ambiguous`** — two or more filed children's slugs match the same
     draft stem. Neither is adopted. Warn and name both candidate slugs plus
     the stem; a human resolves it by hand — do not stop the pipeline over it.
     Record it in the plan_doc (step 5) and continue.

5. **Write the plan_doc** at `<workspace>/wiki/work/<epic-slug>/02-plan-plan.md`
   (or wherever the dispatch brief's `artifact.path` points, if set): the
   decomposition rationale plus the dependency graph — which child blocks which
   and why, and which children are independent and can run in parallel. List each
   child by its filed slug and `kind`. The children **are** the executable plan,
   so there is no `## Plan` table to keep in sync and no `.tasks.json` companion —
   this doc is the human-readable record of the decomposition.

   Include a **"### Spec adoption"** subsection listing, per child: adopted
   (with the source stem), unseeded (falls back to brainstorming), or — if
   any — orphaned drafts and ambiguous matches. This is the durable,
   reviewable record of which children got a pre-written spec and which will
   design from scratch; it is not just transient command output.

6. **STOP.** This is a single pipeline stage. Do not advance the epic, do not
   start working a child, do not invoke any execution skill. After writing the
   plan_doc, announce that the children are filed and the plan_doc is saved, and
   stop. Control returns to the `graph-works:workflow` skill, which advances the
   epic `plan → execute` and hands off for `/clear` + `/graph-works:next`.

## Notes

- **Auto-discovered.** This skill is loaded from `plugins/graph-works/skills/` —
  no marketplace / `plugin.json` edit is needed to register it. The
  `graph-works:workflow` router dispatches `skill="planning-epics"` for an epic at
  the `plan` phase.
- **Children whose spec was adopted reconcile instead of re-brainstorming.**
  When step 4a above found a matching `child-specs/<slug-words>.md` draft for a
  child, that child's frontmatter now carries `spec_doc`, exactly as if it had
  already completed a `design` stage. It still enters the pipeline at
  `phase: None`, but `work_io.workflow`'s routing (`_entry`/`_design`, built by
  [[work/2026-08-11-epic-feature-reconciling-spec-mode-routing]]) dispatches the
  **`reconciling-spec`** skill instead of `brainstorming` whenever `spec_doc` is
  set: it reads the adopted spec and the epic's decision ledger, folds in whatever
  landed since the spec was written, and advances — non-interactively, no human
  gate. A child with **no** matching draft (`unseeded_children` in step 4a's
  result) still brainstorms from scratch exactly as today; the fallback is the
  existing default behavior, not a regression, and step 5's plan_doc subsection is
  what makes it visible which children got which path.
