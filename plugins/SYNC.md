# Syncing `plugins/graph-works` with upstream

`plugins/graph-works/` is a `git subtree` of [obra/superpowers](https://github.com/obra/superpowers),
vendored verbatim. Every file under that prefix comes from upstream unchanged, except the ones
recorded in [`PATCHES.md`](./PATCHES.md).

The tracked remote is `obra/superpowers`, per ADR-0019 (track obra/superpowers as the plugin
upstream). The vendored release is `v6.3.0` (`b36e0829`), the last row of the merge ledger below.

This document is the whole ritual for pulling a new upstream release. Follow it exactly. The two
hard rules below are not style preferences — breaking either one destroys the merge base *silently*,
and the damage surfaces one release later as a whole-tree conflict rather than as an error.

## Preconditions

- The working tree is clean (`git status --porcelain` prints nothing). `git subtree` refuses to run
  otherwise.
- You are on a branch, not a detached HEAD.
- The `upstream` remote exists:

  ```bash
  git remote -v | grep upstream
  # upstream  https://github.com/obra/superpowers.git (fetch)
  # upstream  https://github.com/obra/superpowers.git (push)
  ```

  If it is missing (remotes are repo-global, so this should not happen, but clones start without it):

  ```bash
  git remote add upstream https://github.com/obra/superpowers.git
  ```

- You have picked a **tag**, not a branch. Pulling `main` vendors an arbitrary mid-flight commit and
  makes the ledger row meaningless.
- **Resolve that tag to a SHA and keep it.** Tags are mutable, and a tracked upstream has moved one
  in practice: pcvelz's `v6.5.0` was published and retracted within an hour on 2026-08-14, its
  content re-released as `v6.4.1`. Merging a tag that is later deleted leaves a ledger row pointing
  at a commit nobody can fetch. (The `v6.4.x` and `v6.5.x` numbers throughout this document are that
  fork's; obra's line reaches `v6.3.0`.)

  ```bash
  git fetch upstream --tags
  git ls-remote upstream 'refs/tags/<tag>^{}'   # record this; it is what the ledger row must name
  ```

  Re-resolve if any significant time passes between checking and merging.

- **Resolve the tag through the remote, not through a local tag name.** This repo's tag namespace
  is not obra's alone: 27 of its 75 local `v*` tags were fetched from `pcvelz/superpowers`, the
  fork the 2026-06-09 graft came from, and collide by number with an obra release. `v6.3.0` is one
  of them — `git rev-parse v6.3.0^{commit}` answers `43765d64` (pcvelz), while the release actually
  vendored here is `b36e0829` (obra). `git fetch --tags` will not correct an existing tag, so a
  bare `rev-parse` silently hands you the wrong tree, and `git subtree pull upstream <tag>` merges
  a fork this repo stopped tracking. Two tags in `PATCHES.md` — `v5.5.0` and `v6.4.0`, the graft's
  base and the first vendored release — are pcvelz-only and exist here as local objects the
  `upstream` remote cannot restore; `git fetch https://github.com/pcvelz/superpowers.git --tags`
  brings them back if a clone needs them for `just audit-delta`.

## The invocation

```bash
git subtree pull --prefix=plugins/graph-works upstream <tag> --squash \
  -m "chore(plugin): merge superpowers <tag>"
```

Substitute the real tag in both places. (This block previously used `v6.5.0` as its example. That
was an invented placeholder when written, and it later became a real-then-retracted tag — an
operator pasting it verbatim today would pull a tag that no longer exists. Placeholders here are
now angle-bracketed for that reason.)

## Hard rule 1 — always `--squash`

**Never run a bare `git subtree pull` or `git subtree merge` against this prefix.**

`--squash` is all-or-nothing across the whole history of the subtree. A single non-squashed pull
splices upstream's full commit graph into this repo and corrupts the base from that point on; there
is no way back that does not rewrite history, which Hard Rule 2 forbids.

**Failure mode:** the pull appears to succeed. The next pull conflicts across the entire tree.

## Hard rule 2 — the squash commit must stay reachable, recorded and prefix-rooted

`git subtree pull --squash` has no database. It records which upstream commit was last vendored by
writing `git-subtree-split: <sha>` into a **commit message**, and recovers it later by grepping
history back from `HEAD` for that string. The entire memory of "what we last vendored" is one commit
message that has to stay findable. Nothing in git protects it, and nothing reports its loss: the next
pull simply finds no base, treats the prefix as a fresh import, and conflicts across the whole tree
— one upstream release later, with nothing connecting the two events.

**The rule is the property, in three parts:**

1. **Reachable** — a commit carrying `git-subtree-dir: plugins/graph-works` is reachable from the
   branch you are on, and from whatever it lands on.
2. **Recorded** — its `git-subtree-split:` SHA matches the last row of the merge ledger below.
3. **Prefix-rooted** — its tree is upstream's subtree, not this repo. Parts 1 and 2 both hold if
   someone copies the trailers onto a repo-rooted commit, and the next pull then computes its delta
   as "delete everything outside the prefix".

**What this forbids in practice.** No rebase, no amend, no `filter-repo`, no reordering over the
subtree merge — each rewrites or drops the commit holding the note. And no ordinary squash-merge: a
`git merge --squash` collapses a branch into a single commit with **one** parent, so the branch's
commits, including the note, stop being reachable from the target.

**What it permits, and why the rule is stated as a property.** A commit may carry an entire branch's
changes and still satisfy all three parts, provided it keeps the import commit as a **second
parent** — the original object, not a copy, not rewritten:

```bash
git checkout main
TREE=$(git rev-parse <branch>^{tree})
C=$(git commit-tree "$TREE" -p main -p <import-commit> -F msg.txt)
git merge --ff-only "$C"
```

This was verified rather than assumed: both shapes were built and a real `git subtree pull` for a
later release was run against each. Same base found, same new squash commit tree, same conflict set.
A plain `--no-ff` merge of the branch satisfies the rule too, and is the simpler choice when the
history is worth keeping — see the note under step 3 of the post-merge checklist for why it usually
is.

**Never put the trailers on that merge commit.** `find_latest_squash` takes the *first* match and
stops, so it would return the merge — whose tree is the whole repo — and part 3 fails. The trailers
belong only on the import commit, where `git subtree` wrote them.

**Enforcement.** `just subtree-base` asserts all three parts and is part of `just check`. It is the
one fork check inside the gate: `audit-delta` and `plugin-contract` go legitimately red during
in-progress work, this cannot. Naming the property rather than banning every operation that might
break it is only safe *because* a machine asserts it — if that check is ever removed, restore the
blanket prohibition with it.

**Failure mode without the check:** everything looks fine at merge time. The *next* upstream pull
conflicts across the entire tree, because git believes the subtree has no common ancestor with
upstream.

## Expected conflict sites

- **The 11 tier-D files carrying bolted-on pipeline integration** — three under `hooks/`, eight under
  `skills/`. These are the files we have modified most; upstream edits to them conflict by
  construction. `PATCHES.md` gives each one its own entry, with a disposition and a merge
  instruction: it records what our side is meant to say, file by file.
- **The 24 tier-B and tier-C files.** Identity substitutions and small harness-compat fixes.
  Individually trivial; collectively the bulk of the delta. `PATCHES.md` entries #3 and #4.

`just audit-delta` cross-references all of the above against the tree; see the post-merge checklist
below for when to run it.
- **`.claude-plugin/plugin.json` conflicts on every release.** Not "if upstream restructures it" —
  every time. Upstream rewrites `version` at each release, and `version` sits immediately below our
  patched `name` and `description` with no unchanged line between them, so all three land in one
  hunk. Git merges hunks, not lines. Resolve it by taking upstream's `version` and keeping the five
  patched fields listed in `PATCHES.md` entry #1. **Proven by the v6.4.1 drill**, which both this
  section and entry #1 had predicted would fast-apply.

Everything else should apply cleanly. A conflict outside these sites is a signal worth pausing on:
either upstream made a structural change, or a patch went unrecorded in `PATCHES.md`.

**A file in the expected-conflict set that merges *cleanly* still needs checking.** Overlap is
measured per file; git conflicts per hunk, so a file both sides touched in different places merges
silently. The v6.4.1 drill predicted seven conflicts from file-level overlap and got four — the
three that auto-merged were `skills/subagent-driven-development/SKILL.md`, its
`implementer-prompt.md`, and `skills/writing-plans/SKILL.md`. A clean auto-merge is not evidence our
patch survived: `implementer-prompt.md` came out the *same line count* as upstream's, and only
reading line 6 confirmed our `Agent tool (subagent_type: …)` substitution had not been reverted.
After any merge, diff each auto-merged file in this set against both parents before trusting it.

## The vendored test suites

The subtree brought upstream's own test suites in with it. `just test-plugin` runs the offline
subset that **executes code**, as an enforcing part of `just check`. Every suite outside that set is
accounted for below, in one of two tables: excluded on purpose, or arrived with the obra vendor and
never chosen either way. An unrun suite that nobody chose is the state work item
`2026-08-14-spike-vendored-plugin-test-gate` was filed to end.

**Gated — 18 files, ~30s.** `just test-plugin`.

| Group | Files |
| --- | --- |
| `tests/claude-code/` | `test-sdd-workspace.sh` |
| `tests/hooks/` | `test-session-start.sh`, `test-skill-doc-routing.sh` |
| `tests/` (fork-authored) | `test-doc-layout-claims.sh`, `test-entry-point-skills.sh` |
| `skills/shared/` | `resolve-workspace.test.sh` |
| `tests/shell-lint/` | `test-lint-shell.sh` |
| `tests/systematic-debugging/` | `test-find-polluter.sh` |
| `tests/pi/` node | `test-pi-extension.mjs` |
| `tests/brainstorm-server/` node | `ws-protocol`, `helper`, `browser-launcher`, `auth`, `branding`, `server`, `lifecycle` |
| `tests/brainstorm-server/` bash | `start-server.test.sh`, `stop-server.test.sh` |

That set is a near-exact match for the files `PATCHES.md` records as `state: patched`. A failure in
it is *our* bug, not imported upstream red. The eight `tests/claude-code/` hook-behavior suites this
table used to name went with the dormant user-gate hook surface they covered — they were the graft
lineage's, obra ships none of them, and `PATCHES.md` entry #25 records them as `state: removed`.

**node, npm and `uv` are hard requirements**, not a soft skip. The `brainstorm-server` group needs
one `npm ci` in `tests/brainstorm-server/` installing a single package (`ws`); `test-pi-extension`
needs Node's built-in TypeScript stripping (Node 22+); the resolver suite's parity matrix needs
`uv`. A gate that silently skipped `brainstorm-server`'s 134 assertions when a toolchain went
missing would report green while covering nothing.

**Excluded, deliberately.**

| Excluded | Reason |
| --- | --- |
| `tests/claude-code/test-worktree-path-policy.sh` | Documentation grep — six `grep -Fq` calls over two `SKILL.md` files, executing nothing. It broke because our patch *improved* the sentence it string-matches. Gating it makes every legitimate rewording a test edit. |
| `tests/brainstorm-server/windows-lifecycle.test.sh` | ~150s of hard `sleep 75` calls; skips 3 of 12 checks off Windows. Runs from `just test-plugin-slow` — see the checklist below. |
| `tests/claude-code/test-subagent-driven-development.sh`, `…-integration.sh`, `test-worktree-native-preference.sh`, `run-skill-tests.sh` | Invoke a live model through `run_claude`. Non-deterministic, 10–30 minutes, billed per run. |
| `tests/explicit-skill-requests/` (4 runners plus `run-all.sh`, 9 prompts) | Prompt-driven against a live model. Same reason. |
| `tests/claude-code/test-helpers.sh` | A shared library, not a suite. |
| `tests/claude-code/analyze-token-usage.py` | A reporting tool, not a test. |

**Unreviewed, not chosen — the obra vendor's other harness suites.** `tests/antigravity/`,
`tests/codex/`, `tests/codex-plugin-sync/`, `tests/devin/`, `tests/hermes/` (pytest),
`tests/kimi/`, `tests/opencode/`, `tests/version-bump/` and `tests/writing-skills/` arrived with
obra `v6.3.0` and cover harness surfaces this fork does not ship or has not looked at. Nobody has
decided for or against gating them, which is precisely the state
`2026-08-14-spike-vendored-plugin-test-gate` was filed to end; note that `tests/codex/` and
`tests/codex-plugin-sync/` cover files this fork *does* patch — `.codex-plugin/plugin.json`,
`scripts/package-codex-plugin.sh`, and entry #24's `scripts/sync-to-codex-plugin.sh` — so they are
the first ones worth a decision.

`run-skill-tests.sh` deserves a note: upstream's own runner names only three test files, two of
which need a live model. It was never a route to the offline coverage, which is why `test-plugin`
invokes the files directly instead of delegating to it.

**Wiring the live-model suites into a release drill** is a separate decision with its own cost
profile, and has not been taken.

## Post-merge checklist

Run all seven, in order. None is optional. **Steps 1 and 2 were previously listed the other way
round** — see step 2's note; running them in the old order produces a page of false findings.

1. **Append the ledger row** below (date, upstream tag, `git-subtree-split` SHA, our merge commit).
   Get the SHA with:

   ```bash
   git log --format=%B -1 HEAD^2 | grep 'git-subtree-split:'
   ```

   It must equal the SHA you resolved in the preconditions. If it does not, you merged something
   other than the tag you checked.

2. **Update `PATCHES.md`** — add, amend, or retire entries for anything the merge changed.

   **This must come after step 1.** `scripts/audit_delta.py` reads its comparison base from the last
   row of the merge ledger below — not from `PATCHES.md`. Until the new row exists, step 3 compares
   the post-merge tree against the *previous* release and reports every file upstream changed as
   undocumented divergence. The v6.4.1 drill saw exactly this: **10 false findings**, all of them
   files that had merged cleanly from upstream, which went to `clean` the moment the ledger row
   landed.

3. **Run `just audit-delta`** — it cross-checks the ledger against the tree, in both directions: a
   file that diverges with no entry (someone patched and forgot), and an entry whose file no longer
   diverges.

   **A "retired patch" is two different events, and the checker cannot tell them apart.** Either
   upstream adopted the patch — the delta shrank, retire the entry — or the merge dropped it, and a
   fix this fork still needs is now silently gone. Retiring the entry in that second case writes off
   the regression and leaves nothing to find it again. **This step previously called a retired patch
   "the good news case" outright.** Following that cost seven patches in the obra `v6.3.0` re-base,
   among them `hooks/run-hook.cmd`'s `bash -l` (Windows login shell) and
   `root-cause-tracing.md`'s `${CLAUDE_PLUGIN_ROOT}` script path — all reported, all written off,
   none adopted by anyone.

   Tell them apart with a three-way diff before touching the entry. Our patch is the difference
   between the *previous* base and our tree at the previous base — not between our old tree and the
   new upstream, which also contains everything upstream did on its own:

   ```bash
   git show <prev-base>:<file>            > /tmp/theirs-old.txt   # upstream, before
   git show <our-prev-commit>:plugins/graph-works/<file> > /tmp/ours-old.txt  # upstream + our patch
   diff /tmp/theirs-old.txt /tmp/ours-old.txt      # <- this, and only this, is our patch
   ```

   Then check each `>` line of that diff against the merged file. Present → upstream adopted it;
   retire the entry. Absent → the merge dropped it; **re-apply the patch**, and only then decide
   whether the entry still earns its place.

   Advisory: it is not part of `just check`, so nothing runs it for you. Findings are a prompt to
   edit `PATCHES.md`, not a blocker on the merge.

   **It cannot verify every entry.** It is set arithmetic over paths — it never reads a file's
   content against its entry's disposition, so a `patched` entry is satisfied by the file merely
   differing from upstream, whatever it contains. For any `patched` entry whose file is an
   *addition* rather than a modification of an upstream file, diff it against upstream's
   corresponding body by hand and account for every hunk.

4. **Run `just check`** — it must be green.

   If you merged into a fresh worktree, this now provisions itself: `check` and `types` both depend
   on a `sync` recipe (`uv sync --all-packages`). Before that existed, the v6.4.1 drill hit 13
   spurious `untyped-decorator` errors here, because a bare `uv run` does not install workspace
   *members'* dependencies and `mypy --strict` could not resolve Typer's decorators. That was never
   merge-specific — it was how the gate behaved in **any** clean checkout, which is what CI will be
   (ADR-0010). Fixed in the justfile; recorded here only so the drill's finding stays traceable.

   `check` now includes `test-plugin`, so this step runs the 20 vendored offline suites too — see
   "The vendored test suites" above for what is in that set and what is deliberately outside it.
   Before the gate existed, nothing in this repository ran any of them.

5. **Run `just test-plugin-slow`** — the one vendored suite `check` deliberately excludes.

   `windows-lifecycle.test.sh` costs ~150s and skips 3 of its 12 checks off Windows, which is why it
   is not in the gate. A merge is exactly the moment it is worth paying for.

6. **Reinstall the plugin** and confirm it loads:

   ```
   /plugin marketplace add <path-to-this-repo>
   /plugin install graph-works@graph-works
   ```

7. **Smoke-test a skill** — e.g. confirm `graph-works:brainstorming` resolves.

**On steps 6–7.** Both are Claude Code slash commands, not shell — whoever runs this merge in a
terminal, agent or human, cannot execute them there. Run them in an interactive session. The
agent-runnable substitute, which is a **proxy and not the step**, is: every JSON manifest parses
(`marketplace.json`, `plugin.json`, `hooks/hooks.json`); the marketplace `source` path resolves to a
real directory; every `skills/*/` directory holds a `SKILL.md` carrying a `name:` field; and
`skills/brainstorming/SKILL.md` reads `name: brainstorming`. Note `skills/shared/` has no `SKILL.md`
by design — it holds helper scripts, not a skill, and is not a failure of that check.

## Merge ledger

Appended after every merge. This exists because the `git-subtree-split` SHA lives only in a commit
message; copying it here converts fragile in-message state into version-controlled state. If subtree
tracking ever breaks, the base is recoverable from a row in this table instead of gone.

| Date | Upstream tag | `git-subtree-split` | Our merge commit |
|---|---|---|---|
| 2026-08-11 | v6.4.0 | `7cb90bfc909442f5bb0d7aa6e39c12f28c925ced` | `622c1d8` *(subtree add)* |
| 2026-08-17 | v6.3.0 | `b36e0829c6d0140e93cfef2ca599b1b07d4a7797` | `bfd9c46a` *(subtree add)* |

## Recovery — when tracking breaks

Symptom: a `git subtree pull --squash` conflicts across the whole tree, or reports no common
ancestor.

1. Find the last known-good `git-subtree-split` SHA in the ledger above.
2. Confirm the object still exists (upstream objects normally survive even a bad local rewrite):

   ```bash
   git cat-file -t <split-sha>    # expect: commit
   ```

   If it is gone, `git fetch upstream --tags` will bring it back — it is an upstream object.
3. Re-seat the base by making a fresh squash commit that names it, then pull normally. The
   conservative route, if step 3 is at all unclear, is to re-run the original `subtree add` shape
   into a scratch branch and diff the two trees — the ledger tells you exactly which upstream commit
   to add.

Do **not** "fix" this by rebasing or amending. That is what broke it.

## Reversal — extracting the plugin to its own repo

```bash
git subtree split --prefix=plugins/graph-works -b graph-works-standalone
```

This produces a branch whose history contains only the subtree's contents, ready to push to a
dedicated repo.

Note that `SYNC.md` and `PATCHES.md` sit **outside** the prefix, one level up in `plugins/`. They do
not ride along in the split — which is deliberate. They describe the *fork relationship*, and that
relationship is what a split dissolves. Copy them by hand if the standalone repo should keep the
history.
