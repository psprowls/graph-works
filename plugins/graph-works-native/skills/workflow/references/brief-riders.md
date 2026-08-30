# Brief riders

The stage skills this pipeline dispatches are **stock** — upstream's, unpatched,
and unpatchable once the fork is gone. A *rider* is the text
`skills/workflow/SKILL.md` step 3 inlines into the work-item brief so a behavior
the fork used to carry as a patch survives without one.

Each section below has two parts:

- **Provenance** — which `plugins/PATCHES.md` entry the behavior descends from,
  and the exact `git diff` that re-derives it. That command is what keeps a
  rider auditable after the subtree it came from is deleted; it is the reason
  the ledger's narrative is never the migration inventory.
- **Rider** — the verbatim text to inline. A rider is not a summary and not
  optional: the stage skill cannot be patched, so the rider text **is** the
  behavior.

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

**Provenance.** `plugins/PATCHES.md` entries #11 (`skills/brainstorming/scripts/start-server.sh`)
and #16 (`skills/brainstorming/visual-companion.md`), plus the STOP line that
already lived in `SKILL.md` step 3.

```bash
git diff b36e0829c6d0:skills/brainstorming/scripts/start-server.sh \
        HEAD:plugins/graph-works/skills/brainstorming/scripts/start-server.sh
git diff b36e0829c6d0:skills/brainstorming/visual-companion.md \
        HEAD:plugins/graph-works/skills/brainstorming/visual-companion.md
```

**This is a reduction, not a transplant.** The patch *deletes* `--project-dir`
from every documented invocation and moves the decision inside `start-server.sh`
as a three-tier resolver (graph-works workspace → `<workspace>/brainstorm/`;
else git repo root → `.brainstorming/`; else `/tmp/brainstorm-<session>`), plus
a `.superpowers/brainstorm/` → `.brainstorming/` rename. With the script stock
and unpatchable, **only the flag survives.** Stock's `--project-dir <path>`
stores under `<path>/.superpowers/brainstorm/`, so what is preserved is the
behavior that mattered — companion session files persisting in the workspace
instead of being swept out of `/tmp`. What dies with the fork is the
`.brainstorming/` directory name and the git-repo-root tier. **Recorded as a
loss, not as parity.**

**Do not carry the script path.** The patched copy rewrote the bare relative
`scripts/start-server.sh` to a `${CLAUDE_PLUGIN_ROOT}`-absolute path. That
literal must not appear in this rider: inside a `graph-works:workflow` session
`${CLAUDE_PLUGIN_ROOT}` names the *graph-works* plugin root, not superpowers'.
The rider carries the flag; the stock document resolves the path.

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

**The last sentence pair is a byte-exact literal.**
`skills/brainstorming/SKILL.md` keys its "a work item already exists"
suppression off that exact string. While the fork's patched brainstorming copy
still exists, altering it — em-dash included — breaks the suppression.

---

## Rider: writing-plans

**Provenance.** `plugins/PATCHES.md` entry #18 (`skills/writing-plans/SKILL.md`).

```bash
git diff b36e0829c6d0:skills/writing-plans/SKILL.md \
        HEAD:plugins/graph-works/skills/writing-plans/SKILL.md
```

**The change is the signal, not the guard.** Today's brief emits
*"STOP after writing the plan — do not run the Execution Handoff"* as a **token**.
It can be short because it is not self-sufficient: the patched
`writing-plans/SKILL.md` carries a "Pipeline-stage guard — check FIRST"
paragraph that names that exact line and spells out the consequences. The brief
points at the guard; the guard does the work.

Stock `writing-plans` has no such paragraph. So the brief line must stop being a
signal and become the whole instruction — and it has to survive being read
*after* upstream's own prose has spent a section actively inviting the next
step. That is the unmeasured compliance risk epic decision 3 keeps named rather
than mitigated; this rider does not reduce it, it only makes the instruction
complete enough to be obeyed if it is obeyed at all.

The patch's other half — the save-path change from
`docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md` to the workspace plan lane
— needs no rider: step 3 already emits *"Write your output document to
`<artifact.path>` — this overrides the skill's default location."*

**Rider.**

> You are running as the `plan` stage of the graph-works work pipeline. When you
> reach the `## Execution Handoff` section, skip it entirely: do **not** call
> `AskUserQuestion`, do **not** invoke `subagent-driven-development` or
> `executing-plans`, do **not** begin implementing. After Task Persistence,
> announce that the plan and `.tasks.json` are saved, and stop. Control returns
> to the `graph-works:workflow` skill, which advances the item.
>
> STOP after writing the plan — do not run the Execution Handoff. This is a
> single pipeline stage; the workflow skill advances the item.

**The last sentence pair is a byte-exact literal.** The patched
`skills/writing-plans/SKILL.md` guard paragraph quotes
*"STOP after writing the plan — do not run the Execution Handoff"* verbatim.
While that patched copy still exists, keeping the literal means both the stock
path (the paragraph above it) and the forked path (the guard) fire.

---

## Rider: planning-epics

**Provenance.** No ledger entry. `skills/planning-epics/SKILL.md` is ours
(`plugins/PATCHES.md` entry #19, ours-side additions) and carries its own
behavior in its own file. Only its STOP line was ever brief-side, and it folds
into this table rather than living beside it — they were always riders, and
there is no reason for two mechanisms.

**Rider.**

> STOP after writing the plan artifact and filing the children — do not advance
> the epic or start a child. This is a single pipeline stage; the workflow skill
> advances the item.

---

## Rider: subagent-driven-development

**Provenance.** `plugins/PATCHES.md` entry #12 (`skills/using-git-worktrees/SKILL.md`)
— the Code-Change Gate, reached by every code-writing path.

```bash
git diff b36e0829c6d0:skills/using-git-worktrees/SKILL.md \
        HEAD:plugins/graph-works/skills/using-git-worktrees/SKILL.md
```

**Inverted into a positive rider.** The patched gate is *"confirm a direct
implement directive, else STOP and stay read-only."* On the pipeline path that
branch is dead: the item only reaches `phase: execute` because design and plan
completed and `gw work advance` moved it. Transplanting the check verbatim would
carry lines of policy whose condition can never be false.

What *does* bite against stock is the other half. Upstream's
`using-git-worktrees` asks **"Would you like me to set up an isolated
worktree?"** and honors a decline by working in place, and its directory
selection falls back to `.worktrees/` at the project root. Hence the positive
form below.

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
execute`, so on the pipeline path they are normally present — but items that
reached `execute` before that stamping existed, and any item edited by hand,
may lack them. When either is absent the sentence is omitted and the stock
skill's own directory selection applies. They are read from the item's page the same way
step 3 already reads `title`, `kind`, `summary`, `affects` and `effort`; they
are **not** in `gw work next`'s JSON, and nothing here adds them.

**This rider matters more than the ledger suggests** —
`skills/test-driven-development/SKILL.md` never mentions worktrees at all. On
the `execute` / unplanned path nothing else supplies isolation, so the rider is
the only source of it.

**Not carried (D-003).** Entry #12's `$WORKSPACE/worktrees/` directory-selection
tier is dropped along with entry #4's matching cleanup widening. Worktree
lifecycle here is managed by Orca, not by these skills, and the rider names the
worktree outright when the item knows it. If that stops being true, the fix is a
paragraph appended here — not a code change.

---

## Rider: test-driven-development

**Provenance.** Same as `subagent-driven-development` above:
`plugins/PATCHES.md` entry #12.

```bash
git diff b36e0829c6d0:skills/using-git-worktrees/SKILL.md \
        HEAD:plugins/graph-works/skills/using-git-worktrees/SKILL.md
```

The Rider text below is **byte-identical** to the
`subagent-driven-development` rider, deliberately — both are `execute`-stage
skills reaching the same Code-Change Gate. It is repeated rather than
cross-referenced so a composer that reads only this section still emits the
whole behavior.

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

**Provenance.** `plugins/PATCHES.md` entries #4 and #22
(`skills/finishing-a-development-branch/SKILL.md`).

```bash
git diff b36e0829c6d0:skills/finishing-a-development-branch/SKILL.md \
        HEAD:plugins/graph-works/skills/finishing-a-development-branch/SKILL.md
```

**The on-trunk menu only (D-003).** That diff also carries a
`WORKSPACE=$(bash …/resolve-workspace.sh)` capture in Step 2 and a widening of
Step 6's cleanup-ownership test, with a matching Common-Rationalizations row.
Those are **dropped, with no separate item filed**: worktree lifecycle here is
managed by Orca, so there is no workspace-level worktrees tree for stock's
narrower ownership test to fail to clean.

**Rider.**

> Before presenting Step 4's menu, run the on-trunk check. If
> `git rev-parse --git-dir` equals `git rev-parse --git-common-dir` (a normal
> repo, not a linked worktree) **and** `git branch --show-current` equals the
> base branch, this session's commits already sit on the base — there is nothing
> to merge. Present exactly two options instead of three:
>
> 1. Push as new branch and create a Pull Request — this is Step 5's Option 2 in
>    its detached-HEAD form (`git push origin HEAD:refs/heads/<new-branch>`),
>    because there is no existing feature branch to push.
> 2. Leave it as-is — Step 5's Option 3, unchanged.
>
> No new execution prose: both options are Step 5's existing ones.

**Why the check is scoped to the normal-repo case,** and it is not the obvious
reason: it is *not* that a worktree can never be on the base branch — a shared
epic worktree is, which is exactly why `finishing-relay` needs its own trunk
case. It is that the configuration only arises under auto-drive, and step 3's
relay override already routes auto-drive's finish stage to
`graph-works:finishing-relay` instead of here.

---

## Stages with no rider

`reconciling-spec`, `finishing-relay` and `systematic-debugging` get nothing.
The first two are ours and carry their behavior in their own files;
`systematic-debugging` does not self-chain and has no `MOVE-TO-BRIEF` behavior
attached to it. A stage skill with no section here contributes no
`## Stage directives` block to the brief.
