# Brief riders

The stage skills this pipeline dispatches are **stock** — upstream's, unpatched,
and not patchable from here. A *rider* is the text
`skills/workflow/SKILL.md` step 3 inlines into the work-item brief to supply a
behavior the stock skill does not carry on its own.

Each section below carries a **Rider** — the verbatim text to inline. A rider is
not a summary and not optional: the stage skill cannot be patched, so the rider
text **is** the behavior.

**Keying.** Sections are keyed on the skill name's **last segment** — the part
after the colon in a qualified `superpowers:brainstorming`. A workspace override
pointing a stage at a user-level or repo-local `brainstorming` gets the same
rider, which is correct: the rider describes what the *stage* needs, not which
plugin supplies it.

**Substitutions.** `<workspace>` is replaced with the resolved absolute
workspace path before inlining. `<work-path>` is the item's canonical path.
`<worktree>` / `<branch>` come from the item's own frontmatter — see the
execute riders' conditional note.

**Every rider name here must appear in `SKILL.md`'s rider table, and every name
in that table must appear here.** `tests/test-doc-layout-claims.sh` asserts both
directions.

---

## Rider: brainstorming

**What it carries.** `--project-dir <workspace>` for the visual companion, plus
the spec STOP line.

Stock's `--project-dir <path>` stores companion session files under
`<path>/.superpowers/brainstorm/`. Passing the workspace keeps them there
instead of `/tmp`, where they are swept away between sessions.

**Do not carry a script path.** Inside a `gw:workflow` session
`${CLAUDE_PLUGIN_ROOT}` names the *graph-works* plugin root, not superpowers' —
an absolute path written here would resolve to the wrong plugin. The rider
carries the flag; the stock companion document resolves the path.

**Rider.**

> **Visual companion.** If you start the brainstorming visual companion server,
> pass `--project-dir <workspace>` on the invocation the companion document
> shows for your platform — resolve the script path exactly as that document
> says, and change nothing else about the command. This keeps companion session
> files in the graph-works workspace instead of `/tmp`, where they are swept
> away between sessions.
>
> STOP after writing the spec — do not invoke writing-plans. This is a single
> pipeline stage; the workflow skill advances the item.

**`STOP after writing the spec` is a byte-exact literal.** Two things in this
plugin match that string to detect that a work item already exists, and
suppress their auto-file path when it appears:
`hooks/skill-doc-routing` (its AUTO-FILE mode check) and
`skills/file/SKILL.md`'s "Auto-file mode" section. Altering the wording — the
em-dash included — breaks both.

---

## Rider: systematic-debugging

**What it carries.** A self-sufficient stop before stock Phase 4
(implementation).

**The stage skill self-chains.** Stock's **Phase 4** is implementation —
*"Create Failing Test Case"*, *"Implement Single Fix"*, *"Verify Fix"* — and it
names the execute-stage skills outright, telling the reader to use
`superpowers:test-driven-development` for the failing test and
`superpowers:verification-before-completion` before claiming success. That is a
harder self-chain than `brainstorming`, which merely *may* wander into
`writing-plans` and carries a STOP line anyway. The `diagnosis` variant is
design-only (`graph_works_core/workspace/pipeline.py:11`), so a Bug entering the
pipeline lands here at `design` and is invited to implement in the same session.

**The rider must be self-sufficient.** There is no patched copy to point at, so
the brief line is the whole instruction and has to survive being read after
upstream's own prose has spent a phase inviting the next step.

**Phase 3 is the subtle half.** Its *"Test Minimally — make the SMALLEST possible
change to test hypothesis"* is a legitimate part of root-cause work, but taken
literally it mutates the tree during a stage whose only output is a document. The
rider permits the investigation and bounds where it may leave marks.

**Rider.**

> You are running as the `design` stage of the graph-works work pipeline. Your
> output is the design document named in this brief — the root cause, the
> evidence for it, and the decisions that follow — not a fix.
>
> Work Phases 1-3 (Root Cause Investigation, Pattern Analysis, Hypothesis and
> Testing) and write them up. Do **not** enter Phase 4: do not create the failing
> test, do not implement the fix, and do not invoke `test-driven-development` or
> `verification-before-completion`. Where Phase 3 calls for testing a hypothesis,
> confine it to read-only probes and throwaway scripts, and leave the working
> tree as you found it — an uncommitted fix is still a fix.
>
> Record what Phase 4 *should* do as part of the design, so the execute stage can
> act on it. If the root cause turns out to be something other than the item
> describes, say so in the design; correcting the report is design work, and
> replacing it with a fix is not.
>
> STOP after writing the design — do not implement the fix. This is a single
> pipeline stage; the workflow skill advances the item.

**No byte-exact literal here.** Nothing matches this wording, so it is free to
be worded for the stage rather than for a guard.

---

## Rider: writing-plans

**What it carries.** A self-sufficient Execution-Handoff stop.

Stock `writing-plans` has no pipeline-stage guard of its own, so the brief line
is the whole instruction — and it has to survive being read *after* upstream's
own prose has spent a section actively inviting the next step. That compliance
risk is named, not mitigated: the rider makes the instruction complete enough to
be obeyed if it is obeyed at all.

The plan's save path needs no rider: step 3 already emits *"Write your output
document to `<artifact.path>` — this overrides the skill's default location."*

**Rider.**

> You are running as the `plan` stage of the graph-works work pipeline. When you
> reach the `## Execution Handoff` section, skip it entirely: do **not** call
> `AskUserQuestion`, do **not** invoke `subagent-driven-development` or
> `executing-plans`, do **not** begin implementing. Announce that the plan is
> saved and stop. Control returns to the `gw:workflow` skill, which advances the
> item.
>
> STOP after writing the plan — do not run the Execution Handoff. This is a
> single pipeline stage; the workflow skill advances the item.

**No `.tasks.json`, and no Task Persistence step.** Stock `writing-plans` has
neither — its skill directory holds only `SKILL.md` and
`plan-document-reviewer-prompt.md` — so a rider naming them would send the stage
looking for a section that does not exist. The pipeline's durable state is the
plan artifact at `artifact.path`; nothing reads a task file.

---

## Rider: planning-epics

**What it carries.** Its STOP line. `skills/planning-epics/SKILL.md` is ours and
carries the rest of its behavior in its own file.

**Rider.**

> STOP after writing the plan artifact and filing the children — do not advance
> the epic or start a child. This is a single pipeline stage; the workflow skill
> advances the item.

---

## Rider: subagent-driven-development

**What it carries.** Positive authorization plus mandatory isolation.

**Authorization is stated positively, not as a gate.** An item only reaches
`phase: execute` because design and plan completed and `gw work advance` moved
it, so a "confirm a direct implement directive, else stay read-only" check would
carry a condition that can never be false on this path.

**Isolation is what bites against stock.** Upstream's `using-git-worktrees` asks
**"Would you like me to set up an isolated worktree?"** and honors a decline by
working in place, and its directory selection falls back to `.worktrees/` at the
project root. Hence the positive form below.

**Rider.**

> **Authorization.** This dispatch is the implement directive for this work
> item. You are authorized to write code for the scope named in `affects`, and
> not to widen it.
>
> **Isolation.** Isolation is mandatory, not a consent question — do not ask
> whether to create a worktree. If `git rev-parse --git-dir` differs from
> `git rev-parse --git-common-dir` you are already in a linked worktree; proceed
> without creating another.

**Conditional clause.** When the item's frontmatter carries `worktree:` and
`branch:`, step 3 appends this sentence to the **Isolation** paragraph, with the
values substituted:

> The item's frontmatter names worktree `<worktree>` on branch `<branch>`; work
> there.

`gw work advance` stamps both fields when it moves an item into `phase:
execute`, so on the pipeline path they are normally present — but an item edited
by hand may lack them. When either is absent the sentence is omitted and the
stock skill's own directory selection applies. They are read from the item's
page the same way step 3 already reads `title`, `kind`, `summary`, `affects` and
`effort`; they are **not** in `gw work next`'s JSON, and nothing here adds them.

**This rider is the only source of isolation on the execute path.**
`skills/test-driven-development/SKILL.md` never mentions worktrees at all, so on
the `execute` / unplanned path nothing else supplies it.

**Worktree lifecycle is Orca's, not these skills'.** There is no
`$WORKSPACE/worktrees/` directory tier here and no cleanup widening in
`finishing-a-development-branch`; the rider names the worktree outright when the
item knows it. If that stops being true, the fix is a paragraph appended here —
not a code change.

---

## Rider: test-driven-development

**What it carries.** Positive authorization plus mandatory isolation — the same
Code-Change Gate as `subagent-driven-development`.

The Rider text below is **byte-identical** to the
`subagent-driven-development` rider, deliberately — both are `execute`-stage
skills reaching the same gate. It is repeated rather than cross-referenced so a
composer that reads only this section still emits the whole behavior.

**Rider.**

> **Authorization.** This dispatch is the implement directive for this work
> item. You are authorized to write code for the scope named in `affects`, and
> not to widen it.
>
> **Isolation.** Isolation is mandatory, not a consent question — do not ask
> whether to create a worktree. If `git rev-parse --git-dir` differs from
> `git rev-parse --git-common-dir` you are already in a linked worktree; proceed
> without creating another.

**Conditional clause.** Identical to the `subagent-driven-development` rider:
when the item's frontmatter carries `worktree:` and `branch:`, append

> The item's frontmatter names worktree `<worktree>` on branch `<branch>`; work
> there.

and omit it when either is absent.

---

## Rider: finishing-a-development-branch

**What it carries.** Merge-target resolution, an on-target confirmation menu,
and an explicit outcome line for workflow step 5. Only verified integration
resolves an item; the rider leaves the stock skill unmodified.

**Rider.**

> An empty finish target list is missing evidence, never proof of integration.
> Hold the finish stage and repair the target resolution before presenting choices.
>
> **Complete finish targets.** Consume the entire supplied `finish_targets`
> list in its given order. Each entry names the repository, worktree,
> source_branch and target_branch. Use that exact target as the stock skill's
> base branch; never reconstruct targets from scalar frontmatter or fall back
> to trunk for a missing enclosing anchor. Include tests and the integration
> choice for every target in one reviewable set. Execute the chosen outcome
> for each target, collecting pre/post integration commit evidence and merged
> test results for the workflow-owned receipt. The stock skill does not
> advance the item. Workflow advances exactly once after every target is
> verified integrated. Any failed check, conflict, missing evidence or
> unverified integration holds the entire finish stage; report already
> integrated targets explicitly because cross-repository atomicity is not
> promised. PR, keep/hold and discard never resolve the item.
>
> **Receipt procedure.** Before presenting the choice explain that verification
> requires ancestry-preserving fast-forward or merge commits. Squash/rebase does
> not prove integration. Keep source worktrees and branches until verification.
> Use the absolute installed plugin path and the core runtime (from an external
> checkout add `--project <graph-works source root>` to `uv run`, or use an
> installed interpreter with graph-works-core). Run before merging:
>
> `uv run --package graph-works-core python <plugin>/skills/finishing-relay/references/finish-receipt.py inspect <work-path> --workspace <workspace>`
>
> After each repository's successful merge and merged-result checks, run:
>
> `uv run --package graph-works-core python <plugin>/skills/finishing-relay/references/finish-receipt.py record <work-path> --workspace <workspace> --repo <name>`
>
> Commit the receipt and owner source link through the normal workspace commit
> procedure immediately. If receipt writing fails after a merge, retry `record`:
> it rediscovers source ancestry without another merge. Preserve partial receipts
> when a later target fails. Inspect again after every target is recorded; only
> `complete: true` allows workflow's one advance, using that inspection's
> `resolved_in` and `--no-infer-worktree`. Malformed receipts require repair;
> stale evidence blocks until refreshed. Never hand-author completion claims.
>
> **On-target check.** Before Step 4's menu, compare
> `git branch --show-current` with the confirmed merge target, in any checkout, including a linked worktree.
> When they match, present exactly these three options:
>
> 1. **Confirm integrated.** The stage's commits are already on `<merge target>`.
>    After the human confirms, report `confirm` with `resolved_in` equal to
>    `git rev-parse HEAD`. Step 1's tests must be green and the stage's work
>    committed; this option performs no git mutation or cleanup.
> 2. **Push as new branch and create a Pull Request.** Execute stock Step 5's
>    Option 2 using `git push origin HEAD:refs/heads/<new-branch>` and create
>    the PR against the merge target.
> 3. **Leave as-is.** Execute stock Step 5's Option 3 unchanged.
>
> Off target, keep the stock menu with the confirmed merge target as its base.
> Detached HEAD keeps the stock reduced menu; it cannot confirm integration.
>
> **Outcome report.** First list every repository target, its outcome, test
> results and integration evidence. End with this overall outcome line,
> including after a stop; merge/confirm means the entire set integrated:
> `Finish outcome: <merge|confirm|pr|keep|discard|none>; merge target: <branch>; resolved_in: <SHA|none>`
>
> Use `merge` only after a clean merge and green tests on the merged result,
> with the complete receipt inspection's `resolved_in`. Use `confirm` only
> for the confirmed on-target case, with receipt verification. Use `pr` for PR creation,
> `keep` for keep-as-is, and `discard` for a confirmed discard; their
> `resolved_in` is `none`. Failing tests, a stopped stage, or no choice made
> produces `none` with `resolved_in: none`. Creating a PR never supplies a
> resolution ref. Workflow step 5 owns the attended advance.

---

## Stages with no rider

`reconciling-spec`, `epic-design` and `finishing-relay` get nothing. All three
are ours and carry their behavior in their own files, so there is no stock
document to steer. A stage skill with no section here contributes no
`## Stage directives` block to the brief.

Whether a stage needs a rider is decided by one question, asked of the stock
skill: does it run past the stage boundary?
