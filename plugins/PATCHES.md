# Patches to vendored upstream files

`plugins/graph-works/` is a verbatim `git subtree` of
[obra/superpowers](https://github.com/obra/superpowers), vendored at `v6.3.0` (`b36e0829`) per
ADR-0019. Every deliberate divergence from upstream gets an entry here. The sync ritual in
[`SYNC.md`](./SYNC.md) requires this file to be updated after every merge.

**Two lineages appear in this file, and they are not interchangeable.** The tree is obra's. The
*classification* below is keyed to the 2026-06-09 graft, whose base was
[pcvelz/superpowers](https://github.com/pcvelz/superpowers) `v5.5.0` — a downstream fork of obra —
and `just audit-delta` still reads its grafted file set from that tag (entry #0). So a bare `v5.5.0`
or `v6.4.0` here names a pcvelz release, not an obra one; obra releases are named with their SHA.
Entry #0 has the tag-resolution hazard this creates.

The governing rule for every entry:

> **Patch only the lines identity requires. Take every other line from upstream verbatim, so it
> merges clean.**

This ledger classifies **all 65 files the 2026-06-09 graft took from upstream**, and records a
disposition for each of the 39 that carry a decision. Work item
`2026-08-11-epic-tech-debt-three-way-delta-audit` produced it.

`2026-08-11-...-child-4` has applied this ledger's dispositions. The tree today diverges from
upstream exactly in the files the `patched` blocks of entries #1, #4, #7–#18 claim (some of those
entries also carry `verbatim` blocks, which by definition don't diverge), plus the ours-side
additions entry #19 names. No disposition remains `state: planned`; entry #3's twelve tier-B files
were applied by `2026-08-11-epic-feature-port-native-plugin-surface`. `just audit-delta` checks the
ledger against the tree and will tell you when either of those stops being true.

## How to read an entry

Every entry that names specific files carries at least one machine-readable block, directly under
its heading. `scripts/audit_delta.py` parses these; `just audit-delta` runs it. An entry may carry
more than one block where it covers files in different states — entry #4 splits `patched` from
`verbatim` for two different reasons (two of its twelve files were adopted upstream, two more are
orphaned and unreferenced in the tree). `parse_entries` iterates blocks independently, so multiple
blocks per entry is existing behavior, not a proposed change. (Entry #7 was a second example until
its third amendment collapsed its two blocks into one; the history is kept there.)

```
<!-- audit-delta
state: patched
file: .claude-plugin/plugin.json
-->
```

- **Paths are prefix-relative** — `.claude-plugin/plugin.json`, not
  `plugins/graph-works/.claude-plugin/plugin.json`. That is the form
  `git diff --name-status <upstream-sha> HEAD:plugins/graph-works` emits.
- **`file:` repeats**, one path per line. There is no list syntax.
- **`state:`** is one of:

| State | Meaning |
|---|---|
| `patched` | Diverges from upstream *right now*, deliberately. The checker asserts the divergence is really there; if it is not, upstream has adopted the patch or a merge dropped it — a **retired patch**. |
| `verbatim` | Upstream's, unmodified, and staying that way. |
| `planned` | A disposition this audit decided, not yet applied. No entry carries this state; entry #3, the last holdout, was applied by `2026-08-11-epic-feature-port-native-plugin-surface`. |

An entry that makes no per-file claim carries no block — see entry #5.

---

## Entry #0 — the base derivation

<!-- audit-delta-base
base: v5.5.0
theirs: v6.4.0
grafted-roots: commands/ hooks/ skills/
-->

**Why this entry exists.** The 2026-06-09 graft (`agent-research` `4b5f12aa`) copied upstream files
in without recording which release they came from. That looked like it had cost us the merge base.
It had not: the base is recoverable by hashing, and it has been recovered. Recording the *method*
here matters as much as the result — it is the recovery path if subtree tracking ever breaks again.

**The three trees.**

| Role | Tree |
|---|---|
| **B — base** | `pcvelz/superpowers` **v5.5.0** (`2822989e58fda9c506d0d1cd0a948a983dfa3b02`) |
| **O — ours** | `agent-research` @ `develop`, `plugins/graph-wiki/` |
| **T — theirs** | `pcvelz/superpowers` **v6.4.0**, vendored at `plugins/graph-works` (split `7cb90bfc909442f5bb0d7aa6e39c12f28c925ced`) |

**Both of those tags are pcvelz refs, and that is a live hazard when resolving an obra tag.** The
`upstream` remote is obra, which publishes neither `v5.5.0` nor `v6.4.0` — they survive here only as
local objects from the pcvelz-era fetches. Restore them with
`git fetch https://github.com/pcvelz/superpowers.git --tags` if a clone lacks the base object
`just audit-delta` needs. The names that *do* exist on both sides are worse: 27 of this repo's 75
local `v*` tags are pcvelz's and collide with an obra release of the same number, `v6.3.0` among
them — the local tag is `43765d64` (pcvelz), the vendored obra release is `b36e0829`. `git fetch
--tags` will not correct an existing tag, so **never resolve an obra release through a local tag
name**; go through the remote:

```bash
git ls-remote upstream 'refs/tags/<tag>^{}'
```

**The evidence.** Each of the 65 grafted files was hashed and scored against every upstream tag by
exact blob match:

| Tag | Exact matches |
|---|---|
| **v5.5.0** | **65 / 65** |
| v5.4.0, v6.0.0 | 60 / 65 |
| v6.0.1 | 59 / 65 |
| v6.4.0 | 22 / 65 |

v5.5.0 matches every grafted file byte for byte. The margin is not a judgment call.

**Reproducing it.** B and T are already local objects (`git fetch upstream --tags`). O lives in a
separate repository and needs a local remote — **local config, deliberately not committed**, which
is why the command is written down here:

```bash
git remote add ours-ar /Users/pat/Personal/agent-research 2>/dev/null || true
git fetch ours-ar
git rev-parse ours-ar/develop     # `develop` is the working branch; `main` is stale
```

The grafted set, and the ours × theirs overlap matrix over it:

```bash
B=v5.5.0; T=v6.4.0; O=ours-ar/develop; P=plugins/graph-wiki
blob() { git rev-parse --quiet --verify "$1" 2>/dev/null || echo MISSING; }
git ls-tree -r --name-only $B | grep -E '^(commands|hooks|skills)/' | while read -r p; do
  b=$(blob "$B:$p"); o=$(blob "$O:$P/$p"); t=$(blob "$T:$p")
  os=$([ "$o" = MISSING ] && echo D || { [ "$o" = "$b" ] && echo . || echo M; })
  ts=$([ "$t" = MISSING ] && echo D || { [ "$t" = "$b" ] && echo . || echo M; })
  echo "$os$ts $p"
done | awk '{print $1}' | sort | uniq -c | sort -rn
```

| ours × theirs | Files | Meaning |
|---|---|---|
| `M × M` | **28** | Both sides touched. The real conflict surface. |
| `M × ·` | 7 | Our patch; upstream quiet. Applies clean. |
| `· × M` | 9 | Free — take upstream. |
| `· × ·` | 12 | Free. |
| `D × ·` | 3 | Our deleted commands; upstream still ships them. |
| `D × D` | 3 | Free — `using-superpowers/references/*`, deleted both sides. |
| `D × M` | 1 | `using-superpowers/SKILL.md` — our rename against upstream's edits. |
| `· × D` | 2 | Free. |

**39 files carry a decision; the other 26 are free.**

**Tiering the 35 ours-side modifications.** Intent class is assigned by measuring what fraction of a
file's changed lines mention an identity string (`graph-wiki`, `graph_wiki`, `superpowers`,
`using-workflow`). The distribution is bimodal, which is what makes the rule trustworthy rather than
arbitrary. Boundaries: **B** = every changed line is an identity line; **C** = not all identity and
≤ 6 changed lines; **D** = not all identity and > 6. That yields 12 / 12 / 11, and the 23 remaining
grafted files are ours-side pristine (tier A).

```bash
git diff --no-index --unified=0 <(git show "$B:$p") <(git show "$O:$P/$p") \
  | grep -E '^[+-]' | grep -vE '^(\+\+\+|---)'
```

Run that per modified path; count total changed lines and the subset matching
`-iE 'graph-wiki|graph_wiki|superpowers|using-workflow'`.

---

## Entry #1 — `plugin.json` identity fields

<!-- audit-delta
state: patched
file: .claude-plugin/plugin.json
-->

**File:** `plugins/graph-works/.claude-plugin/plugin.json`
**Added:** 2026-08-11, at the v6.4.0 subtree add
**Reason:** `name` *is* the `graph-works:` namespace users type. Left as upstream's
`superpowers-extended-cc`, every downstream skill develops against the wrong prefix and the
marketplace entry installs the wrong plugin.

| Field | Disposition |
|---|---|
| `name` | **Patched** -> `graph-works` |
| `description` | **Patched** -> ours |
| `author` | **Patched** -> `Patrick Sprowls` (upstream's `email` line dropped) |
| `homepage` | **Patched** -> `https://github.com/psprowls/graph-works` |
| `repository` | **Patched** -> `https://github.com/psprowls/graph-works` |
| `license` | Verbatim — MIT both ways |
| `keywords` | **Patched** -> obra's six plus `wiki`, `knowledge-management`, `work-tracking` (D-007) |
| `env` | **Patched (new)** -> `GRAPH_WORKS_ROOT`, a graph-works-only addition with no obra counterpart |
| `version` | **Verbatim** — see below |

**On `version`.** graph-works ships upstream's version rather than starting at a fresh `0.1.0`. This
is deliberate: it states the lineage honestly — this *is* superpowers `<version>` plus a delta.

> **Corrected 2026-08-14 by the v6.4.1 merge drill.** This paragraph previously also claimed that
> leaving `version` unpatched *"turns a guaranteed conflict into a clean fast-apply, because git sees
> a line we never touched."* **That is false, and the drill proved it: `plugin.json` conflicted.**
> Git resolves merges per *hunk*, not per line, and `version` sits immediately below our patched
> `name` and `description` with no unchanged line between them — so upstream's version bump and our
> identity lines are one hunk, every release, forever. `SYNC.md` carried the same wrong prediction
> and is corrected too.
>
> The **decision** survives the correction: shipping upstream's version number is still right on
> lineage-honesty grounds. Only the merge-cost justification was wrong. Expect a one-hunk conflict
> here at every single release and resolve it as below; do not go looking for what "broke," and do
> not be tempted to patch `version` to something of ours in the belief that it would conflict less.
> It would conflict identically and lose the lineage.

**On merge.** Take upstream's `version`. Take upstream's version of every field outside the patched
set above, and re-apply only these five.

**Reconciled onto obra `v6.3.0` (`b36e0829`) — C2, 2026-08-17.** D-007 settles `keywords`: obra's six
plus `wiki`, `knowledge-management`, `work-tracking` = 9. The `env.GRAPH_WORKS_ROOT` block is a
graph-works-only addition obra has no counterpart for; both sit outside this entry's historical
five-field patched set and are now recorded here. `description` was updated to name
`obra/superpowers`.

---

## Entry #2 — Tier A: ours-side pristine (22 files)

<!-- audit-delta
state: verbatim
file: hooks/examples/pre-commit-check-tasks.sh
file: skills/brainstorming/scripts/helper.js
file: skills/brainstorming/scripts/server.cjs
file: skills/checking-gates/SKILL.md
file: skills/receiving-code-review/SKILL.md
file: skills/specifying-gates/SKILL.md
file: skills/systematic-debugging/CREATION-LOG.md
file: skills/systematic-debugging/condition-based-waiting-example.ts
file: skills/systematic-debugging/condition-based-waiting.md
file: skills/systematic-debugging/defense-in-depth.md
file: skills/systematic-debugging/find-polluter.sh
file: skills/systematic-debugging/test-academic.md
file: skills/systematic-debugging/test-pressure-1.md
file: skills/systematic-debugging/test-pressure-2.md
file: skills/systematic-debugging/test-pressure-3.md
file: skills/test-driven-development/testing-anti-patterns.md
file: skills/verification-before-completion/SKILL.md
file: skills/writing-skills/anthropic-best-practices.md
file: skills/writing-skills/examples/CLAUDE_MD_TESTING.md
file: skills/writing-skills/graphviz-conventions.dot
file: skills/writing-skills/persuasion-principles.md
file: skills/writing-skills/render-graphs.js
-->

**Disposition: take upstream, always.** These grafted files carry no divergence from the base on
our side. Nine of them upstream modified between v5.5.0 and v6.4.0 and two it deleted; all of that
lands free, because there is nothing of ours to conflict with. This is rule 1 working exactly as
intended, and it is the largest single block of the ledger.

**Amendment (2026-08-14) — three files left this entry.**
`skills/using-superpowers/references/{codex,copilot,gemini}-tools.md` were listed here as `D × D`,
deleted on both sides: upstream removed them at v6.4.0 and our side had moved its copies under the
renamed skill directory, so the base path was empty in both trees and nothing could conflict. Entry
#7's third amendment reversed that rename and moved our copies back onto these exact paths, so they
are no longer absent from our tree — they are ours-side additions at paths upstream no longer
populates. **They are now claimed by entry #19** and are removed from the block above; leaving them
in both would make them doubly-classified, which `just audit-delta` reports as an error rather than
redundancy. The tier-A count drops from 26 to 23.

**Amendment (2026-08-15) — one more file left this entry.**
`hooks/hooks-cursor.json` was incorrectly claimed here as an upstream-grafted file; it is in fact an
ours-side addition not present in upstream v6.4.0. **It is now claimed by entry #19** and is removed
from the block above. The tier-A count drops from 23 to 22.

**On merge.** Nothing to do. If a conflict ever appears in one of these paths, something is wrong
somewhere else — most likely a patch landed without a ledger entry, which `just audit-delta` reports
as *undocumented divergence*.

---

## Entry #3 — Tier B: identity-only divergence (4 files; 8 retired)

<!-- audit-delta
state: patched
file: skills/brainstorming/scripts/frame-template.html
file: skills/systematic-debugging/SKILL.md
file: skills/writing-skills/SKILL.md
file: skills/writing-skills/testing-skills-with-subagents.md
-->

**Intent class: identity.** In all twelve, *every single changed line* mentions `graph-wiki`,
`graph_wiki`, `superpowers`, or `using-workflow`. Nothing else differs. The ratios run from 1/1 to
14/14; there is no partial case in this tier, which is what makes the boundary a measurement rather
than an opinion.

**Disposition: `re-apply`** — the namespace has to be right or the plugin resolves under the wrong
prefix.

**Q9 is settled and this entry is applied.** The parent epic's design spec resolved Q9 in favour of a
committed rename and folded the namespace-rename child into
`2026-08-11-epic-feature-port-native-plugin-surface`, which executed this disposition. The earlier
text attributed the Q9 decision to "child 6"; under the design spec's renumbering child 6 is the
merge drill, so that cross-reference was stale and is removed here.

**Applied against upstream v6.4.0, not v5.5.0.** The twelve files' identity strings moved between the
two upstream releases: five markdown files carry `superpowers-extended-cc:` skill references, five
`hooks/examples/` scripts carry `SUPERPOWERS_*` guard variables, `frame-template.html` carries a page
title, and `skills/shared/task-format-reference.md` carries two `docs/superpowers/` routing-file paths.
Each was substituted to the `graph-works` form.

`SUPERPOWERS_ROUTING_GUARD` was deliberately **not** substituted. It is read by
`hooks/pre-agent-model-routing`, `hooks/pre-taskcreate-model-tier`, and
`hooks/pre-askuser-handoff-guard` — all three upstream-verbatim in this fork — so renaming it would
open a 13-occurrence divergence across those three otherwise-pristine files and re-pay it at every
upstream release, for no behavioral gain. Only `hooks/session-start`, which advertised the
never-implemented `GRAPH_WIKI_ROUTING_GUARD`, was corrected to name the variable the hooks actually
read.

**On merge.** Take upstream's line, then re-substitute the namespace. Never resolve by keeping our
side wholesale: our side is upstream v6.4.0 text with strings swapped, so keeping it silently
reverts every upstream improvement in the file.

**Reconciled onto obra `v6.3.0` (`b36e0829`) — C2, 2026-08-17.** Of this entry's twelve files, three
were among the seventeen this reconcile touched — `skills/systematic-debugging/SKILL.md`,
`skills/writing-skills/SKILL.md`, `skills/writing-skills/testing-skills-with-subagents.md` — and the
namespace substitution was re-applied to each against obra's current text: `superpowers:` →
`graph-works:` at every site the disposition names. The other nine files in this entry were not part
of this reconcile's scope and are unchanged by it.

---

## Entry #4 — Tier C: harness-compat fixes (12 files)

<!-- audit-delta
state: patched
file: hooks/run-hook.cmd
file: skills/brainstorming/scripts/stop-server.sh
file: skills/brainstorming/spec-document-reviewer-prompt.md
file: skills/finishing-a-development-branch/SKILL.md
file: skills/systematic-debugging/root-cause-tracing.md
-->

<!-- audit-delta
state: removed
file: skills/test-driven-development/SKILL.md
-->

<!-- audit-delta
state: verbatim
file: skills/dispatching-parallel-agents/SKILL.md
file: skills/subagent-driven-development/code-quality-reviewer-prompt.md
file: skills/subagent-driven-development/spec-reviewer-prompt.md
file: skills/writing-plans/plan-document-reviewer-prompt.md
file: skills/subagent-driven-development/implementer-prompt.md
file: skills/requesting-code-review/code-reviewer.md
-->

**Intent class: harness compatibility.** Not intentional forking, and not accidental drift either —
the two classes the epic anticipated. These are small, correct adaptations to a harness that moved
underneath the vendored text. Every one is ≤ 6 changed lines, and most are 2.

| File | Change |
|---|---|
| `skills/subagent-driven-development/implementer-prompt.md` | `Task tool` → `Agent tool (subagent_type: …)` |
| `skills/requesting-code-review/code-reviewer.md` | adds a `[REVIEW_GUIDANCE]` placeholder ahead of `## What to Check` |
| `skills/systematic-debugging/root-cause-tracing.md` | `./find-polluter.sh` → `bash "${CLAUDE_PLUGIN_ROOT}/…"` |

…and five more of the same character.

**Disposition: `re-apply`, split two ways.** Verified against v6.4.0: upstream adopted two of the
twelve and orphaned two — it did **not** simply leave all twelve untouched.

- **Adopted (2):** `skills/dispatching-parallel-agents/SKILL.md` and
  `skills/writing-plans/plan-document-reviewer-prompt.md` now read `Subagent (general-purpose):` —
  upstream independently rewrote the same dispatch-mechanism line ours targeted, landing on
  near-identical wording. Both are `verbatim` now; re-applying our old text over upstream's would
  revert upstream's own fix.
- **Orphaned (2):** `skills/subagent-driven-development/spec-reviewer-prompt.md` and
  `skills/subagent-driven-development/code-quality-reviewer-prompt.md` still carry the stale `Task
  tool` wording — upstream never touched them — but nothing in the tree dispatches either template
  any more. Patching them would buy a permanent ledger line for no behavior, so they stay `verbatim`,
  unpatched, left to drift with upstream.
- **Re-applied (8):** the remaining eight — `hooks/run-hook.cmd`,
  `skills/brainstorming/scripts/stop-server.sh`,
  `skills/brainstorming/spec-document-reviewer-prompt.md`,
  `skills/finishing-a-development-branch/SKILL.md`, `skills/requesting-code-review/code-reviewer.md`,
  `skills/subagent-driven-development/implementer-prompt.md`,
  `skills/systematic-debugging/root-cause-tracing.md`, and
  `skills/test-driven-development/SKILL.md` — are still needed. Upstream has not adopted any of
  these; each is re-applied by intent against upstream's current surrounding text and carries
  `state: patched`.

**Upstream-contribution candidates.** The candidate set shrinks to the remaining eight: generic,
small, and correct for any consumer, with no graph-works identity in them at all. Opening those PRs
is out of scope for this audit (the epic places contribution in follow-on work); flagging them is
not. Each one accepted upstream retires its own line of this entry — rule 2's *"the delta shrinks
over time"*, and `just audit-delta` will report it as a **retired patch** the release after it lands.
`code-reviewer.md`'s new `[REVIEW_GUIDANCE]` placeholder is one half of a pair completed by a later
task (entry #15's guidance-recall step and placeholder reconciliation).

**On merge.** Small enough to re-apply by hand. If upstream's text has moved on, re-apply the
*intent* (name the current tool, resolve the script path through `${CLAUDE_PLUGIN_ROOT}`), not the
literal diff.

**Reconciled onto obra `v6.3.0` (`b36e0829`) — C2, 2026-08-17.** `implementer-prompt.md` retires:
obra's `v6.3.0` already reads `Subagent (general-purpose):` — obra adopted the fix independently, the
same way upstream v6.4.0 had already adopted it for the two files this entry's "Adopted (2)" bullet
names above. Its `file:` line moved from the `patched` block to the `verbatim` block. `code-reviewer.md`'s
two-line `[REVIEW_GUIDANCE]` placeholder patch re-applied unchanged; obra's `## You Do Not Dispatch
Subagents` section and its `git worktree add` Read-Only Review wording are obra's own and were kept —
the pcvelz-relative diff, not the obra-relative one, is what defines our patch here. The remaining
six re-applied files (`hooks/run-hook.cmd`, `skills/brainstorming/scripts/stop-server.sh`,
`skills/brainstorming/spec-document-reviewer-prompt.md`,
`skills/finishing-a-development-branch/SKILL.md`, `skills/systematic-debugging/root-cause-tracing.md`,
`skills/test-driven-development/SKILL.md`) were not part of this reconcile's seventeen-file scope and
are unchanged by it.

**`code-reviewer.md`'s patch retired — 2026-08-18.** The `[REVIEW_GUIDANCE]` placeholder never gained
a consumer: entry #15's guidance-recall step, the other half of the pair this placeholder was added
for, does not exist in the current `requesting-code-review/SKILL.md` — nothing in the tree fills the
placeholder in. Carrying a dead placeholder for a feature that isn't there bought no behavior, so both
of its two lines (the `[REVIEW_GUIDANCE]` line itself and its entry in the placeholder list) were
removed, and the file is now identical to obra's. Its `file:` line moves from the `patched` block above
to the `verbatim` block. If the guidance-recall feature is ever built, re-add both lines here as a new
patch rather than assuming this entry still covers it.

---

## Entry #5 — The never-grafted upstream files (140 shipping)

*No `audit-delta` block: these files sit outside the grafted roots, so they are outside both of the
checker's file-level checks. Listing 140 paths that must be hand-edited on every upstream release
would be friction with no signal.*

**Disposition: keep verbatim.** The 2026-06-09 graft copied only `commands/`, `hooks/` and
`skills/`. Everything else in the graft's base — **87 files** — was never grafted at all: not
reviewed, not copied, not touched. They arrived here for the first time with the subtree add, and
the tree today ships **140** files outside those three roots, every one of them obra's at `v6.3.0`
(`b36e0829`). Obra carries a per-harness plugin surface pcvelz did not, which is most of the growth.
Three further files out there are ours-side test additions, not upstream's:
`tests/hooks/test-skill-doc-routing.sh` and `tests/test-entry-point-skills.sh`, both claimed by
entry #19, and `tests/test-doc-layout-claims.sh`, which no entry claims — `just audit-delta` reports
it as undocumented divergence.

These files merge free precisely because nothing of ours is in them — **but this entry no longer
claims all 140 are untouched, and never claimed `.claude-plugin/plugin.json`** (patched under entry
#1). A file out here that another entry names is that entry's, not this one's; the checker's
coverage arithmetic runs over grafted paths only, so nothing mechanically reconciles the two. Before
treating a file in this set as free, grep this ledger for its path.

These are `README.md`, `LICENSE`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `CODE_OF_CONDUCT.md`,
`RELEASE-NOTES.md`, `.gitattributes`, `.gitignore`, `.pre-commit-config.yaml`, `.version-bump.json`,
`package.json`, `gemini-extension.json`, `docs/`, `tests/`, `scripts/`, `assets/`, `.github/`, the
harness-plugin roots (`.agents/`, `.codex-plugin/`, `.cursor-plugin/`, `.devin-plugin/`,
`.hermes-plugin/`, `.kimi-plugin/`, `.opencode/`, `.pi/`), and `.claude-plugin/marketplace.json`.
None of them has ever been reviewed by this fork.

**Files that misattribute.** Named here, verified by reading, deliberately **not** acted on:

| File | What it misattributes |
|---|---|
| `.github/FUNDING.yml` | Sponsorship handle `github: [obra]` — a "Sponsor" click on this repo would route money to upstream's maintainer, not this fork. |
| `.github/ISSUE_TEMPLATE/config.yml` | Support contact link points to `https://discord.gg/35wsABTejz`, upstream's own Discord, as this repo's help channel. |
| `.claude-plugin/marketplace.json` | The marketplace names itself `superpowers-dev`; `owner.name`/`owner.email` and the listed plugin's `author.name`/`author.email` are all `Jesse Vincent` / `jesse@fsck.com`, and the plugin entry names itself `superpowers` at `version` `6.3.0` — none of which matches entry #1's patched `plugin.json` (`author.name: "Patrick Sprowls"`, plugin name `graph-works`), nor the sibling harness manifests `.agents/plugins/marketplace.json` and `.codex-plugin/plugin.json`, which *are* patched to the fork's identity — undocumented divergence this ledger does not yet carry an entry for. This is the one row where the inconsistency is internal to the tree rather than merely inherited. |
| `README.md` | Opens "Superpowers is a complete software development methodology for your coding agents" and describes itself throughout as upstream's project, with no mention of graph-works or this fork's actual purpose. Same for `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` and `RELEASE-NOTES.md`. |
| `package.json` | `name: "superpowers"` — no `author` or `repository` field to misattribute a person, but the package name itself disagrees with `plugin.json`'s `graph-works`. |

**Why flagged and not fixed.** Whether any of this matters depends on the epic's open **Q8** —
standalone repo versus subtree inside the monorepo. Under *subtree*, none of these files is ever
served to anyone; `.claude-plugin/plugin.json` is the only one Claude Code reads, and it's already
correct via entry #1. Editing the rest would buy a permanent delete-vs-modify merge tax, on
every future upstream release, against a problem — a stray sponsorship link, a wrong Discord invite
— that may never surface. Under *standalone*, this table is the work-list: every row becomes an edit
worth making the day the repo goes public on its own. Q8 is unresolved, so the honest move is to
write the list and wait.

---

## Entry #6 — The three deleted commands

<!-- audit-delta
state: verbatim
file: commands/brainstorm.md
file: commands/execute-plan.md
file: commands/write-plan.md
-->

**The `D × ·` case.** Our side deleted all three at the graft. Upstream still ships all three,
unchanged between v5.5.0 and v6.4.0. They were **present** in `plugins/graph-works/` at the v6.4.0
subtree add, which restored them byte-identical to that release's text. They are absent from the
tree today — see the obra supersession at the end of this entry.

**What they do.** Each is a one-line dispatcher: `commands/brainstorm.md` invokes the
`brainstorming` skill, `commands/execute-plan.md` invokes `executing-plans`, `commands/write-plan.md`
invokes `writing-plans`. `plugins/graph-works/commands/` ships six commands total —
these three plus `gate-check.md`, `onboard.md`, `specify-gate.md` — and does **not** ship its own
`brainstorm.md`/`execute-plan.md`/`write-plan.md` with different content that these would collide
with; there is no second file in this plugin claiming the same command name. The sibling
`plugins/graph-wiki` plugin (`ours-ar/develop:plugins/graph-wiki/commands/`) was checked for a
namespace collision too — it ships `next.md`, `auto-drive.md`, `file.md`, `archive.md`, `lint.md`,
`log.md`, `bootstrap.md`, `onboard.md`, `proposals.md`, `query.md`, `regen-index.md`, `scan.md`,
`status.md`, `gate-check.md`, `specify-gate.md`, `ingest.md` — a work-item pipeline (`gw work next`/`advance`,
one stage per invocation) that dispatches the same underlying skills (`brainstorming`,
`writing-plans`, `executing-plans` all exist under its own `skills/`) but through its own command
names, none of which is `brainstorm`, `execute-plan`, or `write-plan`. No file-name collision exists
in either plugin.

`plugins/graph-works/README.md:105` documents `write-plan` by name as useful for offloading a
plan-writing session from a long-running one — this is a currently-referenced entry point, not dead
weight left over from the graft.

**Decision: keep.** graph-works still ships the `brainstorming`, `writing-plans`, and
`executing-plans` skills these three commands dispatch to (grafted, covered by earlier entries); the
commands are their direct one-shot on-ramp, distinct in both name and use case from
`graph-wiki`'s work-item-tracked, stage-by-stage pipeline. Nothing in either plugin claims the same
slot. Re-deleting would remove a documented, working entry point to skills we still carry, for no
compensating benefit — there is no collision to resolve by deleting.

**The cost of re-deleting, stated up front.** A deleted file that upstream later modifies produces a
*delete/modify* conflict on every release that touches it. Git cannot resolve that automatically and
will not stop asking: the conflict recurs at each pull until either upstream deletes the file too or
we restore it. Three files × every future release is a standing tax, and it is paid by whoever runs
the sync ritual, who will not be the person who made this decision. Keeping avoids that tax entirely
— this is part of why keep is the right call here, not just a consequence to note if we chose
otherwise.

**On merge.** Nothing — they merge free, as upstream's, same as entry #2's tier-A files.

**Superseded for the obra re-base — 2026-08-17.** The "keep" disposition above was reasoned against
a pcvelz base that shipped all three files. Obra v6.3.0 ships **no `commands/` directory at all**, so
the premise the decision rested on — "they merge free forever, no cost either way" — no longer holds:
there is nothing at these paths for `git subtree add` to bring back, and restoring them would be an
ours-side addition, not a free carry. `2026-08-17-epic-spike-obra-rebase/03-drop-list.md` row 5 calls
for dropping all three; decision **D-008** in
`2026-08-17-epic-re-base-plugins-graph/00-decisions.md` honors the drop-list. The
`2026-08-17-epic-feature-vendor-obra-replay-authored` child therefore did **not** replay
`commands/brainstorm.md`, `commands/execute-plan.md`, or `commands/write-plan.md`, and they are
absent from the tree as of that child.

The one supporting fact above that also expired: `plugins/graph-works/README.md:105` documented
`write-plan` by name. That README was pcvelz's file, vendored verbatim and never patched here; the
obra vendor replaced it with obra's own, which names none of the three. The entry point this entry
described as "currently-referenced" is no longer referenced by anything in the tree.

The `audit-delta` block above is deliberately left as-is. `state: verbatim` files are not in the
checker's `patched` claim set, so three now-absent paths produce no finding; retiring the entry
outright would erase the reasoning, which is what this ledger exists to keep.

---

## Entry #7 — The `using-superpowers` → `using-workflow` rename

<!-- audit-delta
state: patched
file: skills/using-superpowers/SKILL.md
-->

**The `D × M` case — the only one in the tree.** Our side (`ours-ar/develop:plugins/graph-wiki/`)
renamed the skill to `using-workflow` with near-identical content — three changed lines, all
identity: `name: using-superpowers` → `name: using-workflow`, and two prose spots
("Superpowers skills override..." → "Graph-wiki's workflow skills override...") swapping the
product name embedded in running text. Upstream kept the name `using-superpowers` and *modified* the
body between v5.5.0 and v6.4.0 (when this entry was written,
`plugins/graph-works/skills/using-superpowers/SKILL.md` was byte-identical to v6.4.0 — the graft
never diverged it; the file carries the namespace substitution against obra's body today, per the
reconcile note at the end of this entry).

Git sees a delete on our side and a modify on theirs. Resolved naively — by keeping our side — the
rename silently carries upstream's **v5.5.0** text forward forever, and every improvement upstream
makes to that skill between v5.5.0 and v6.4.0 (and every release after) is dropped without a
conflict marker ever appearing, because there is no textual conflict to flag: the paths don't even
match. This is the quietest failure mode in the whole ledger, which is why it gets its own entry
rather than a line in tier B.

**Disposition: `re-apply-reduced` — rebase the rename onto upstream's v6.4.0 body.** Take upstream's
current `using-superpowers/SKILL.md` content in full, apply the three identity substitutions, and
write it out under the `using-workflow` name. Repeat that at every release. Carrying the v5.5.0 text
forward instead would be worse in a way that compounds silently: it freezes the skill's substance at
a five-release-old snapshot while every other renamed or grafted file in this ledger keeps taking
upstream's improvements, and nothing about the file tree would ever surface the gap — no failing
test, no merge conflict, just a skill that quietly stops improving. What we own is the *name and the
three lines*, not the body.

**The other three files of the rename** —
`skills/using-superpowers/references/{codex,copilot,gemini}-tools.md` — were deleted on both sides
independently (upstream removed them too) and are already covered by entry #2's tier-A list, which
notes them for completeness. Only `SKILL.md` carries a decision here.

**Caveat on the three reference files.** Entry #2 classifies them `D × D` by the base-relative-path
method this ledger uses throughout (Entry #0): base-path lookup on our side comes back MISSING
because our side renamed the containing directory to `using-workflow/`, the same rename this entry
tracks. That method cannot see renames — it is not designed to. Content-wise, ours-side actually
*kept and modified* these three files under `skills/using-workflow/references/`, the same character
of change as `SKILL.md` above (tool-name and identity substitutions), and upstream genuinely deleted
its `using-superpowers/references/*` at v6.4.0. Whether that divergence deserves its own disposition,
rather than folding into entry #2, is a question for whoever next revisits this ledger; noting it here
so it is not silently lost the way this entry exists to prevent for `SKILL.md` itself.

**On merge.** Diff upstream's `using-superpowers/SKILL.md` against our `using-workflow/SKILL.md`
ignoring the three identity lines. A non-empty result is an upstream improvement to pull in, not a
conflict to suppress.

**Amendment — the re-apply turned up a fourth thing to carry, not three lines.** Re-applying this
disposition against upstream's current body surfaced that upstream deleted the entire multi-platform
section — the Copilot/Gemini/other-environments paragraphs and the `## Platform Adaptation` section —
somewhere between v5.5.0 and v6.4.0, along with the three `references/*.md` files that section cites
(see the caveat above). This fork re-adds that section verbatim from the ours-side content, because it
is the only citation site for `references/codex-tools.md`, `references/copilot-tools.md`, and
`references/gemini-tools.md`; dropping it would leave three reference files with nothing pointing at
them. The delta this entry now represents is therefore the identity lines described above *plus* that
re-added block — not "three changed lines" as originally scoped. `skills/using-workflow/SKILL.md`
carries a `patched` ledger entry accordingly; `skills/using-superpowers/SKILL.md` is left untouched and
carries a `verbatim` entry. The three `skills/using-workflow/references/*.md` files themselves are not
tracked by this entry — they are recorded under Entry #19.

**Second amendment (2026-08-14) — the delta is six lines plus the block, and the re-apply was a copy,
not a rebase.** A review of this entry against the tree found two things the amendment above does not
account for.

*What the delta actually is.* Diffed in full, `skills/using-workflow/SKILL.md` differs from upstream's
v6.4.0 `using-superpowers/SKILL.md` in exactly four places: the `name:` line; two prose lines carrying
the product name; **two lines naming `GEMINI.md` and `AGENTS.md` in the instruction-priority list**
(lines 22 and 26); and the re-added multi-platform block. The two priority-list lines were unrecorded.
They belong to this entry and are **kept deliberately**: upstream's removal of them was part of the
same editorial move that deleted the platform section and the three `references/*.md` — one decision,
dropping non-Claude-Code platform support — and this fork declines that decision as a whole. Reverting
those two lines while keeping the block would leave the skill citing Gemini and Copilot tooling in one
section and denying it in another. The delta this entry represents is therefore **the four identity /
platform-priority lines plus the re-added block**.

*How they came to be unrecorded.* `skills/using-workflow/SKILL.md` is **byte-identical to the ours-side
donor file**, which is v5.5.0-derived. The disposition above says to take upstream's current body and
apply substitutions to it; what landed was the donor file copied wholesale. It is harmless *this time*
— upstream's only v5.5.0 → v6.4.0 changes to this skill were the three the fork deliberately declines,
so no upstream improvement was dropped — but the check that would have proven that was not the one
performed. **At the next release the same copy would silently drop real improvements**, which is the
failure this entry exists to name. On merge, diff against upstream's body and account for every hunk;
do not copy the ours-side file.

*Why the tooling did not catch it.* `scripts/audit_delta.py`'s `compare()` is set arithmetic over
**paths** — `divergent − claimed`, `claimed − divergent`, `grafted − named`, duplicates. It never reads
content against a disposition, so a `patched` entry is satisfied by the file merely differing from
upstream, whatever it contains. This entry's own `D × M` shape puts it doubly out of reach: our file
sits at a path upstream does not have, so it is an *addition*, and the base-relative-path method
"cannot see renames" (see the caveat above). **`just audit-delta` cannot verify this entry.** Verifying
it is a manual step, and belongs on the post-merge checklist in `plugins/SYNC.md` rather than in the
script. Tracked as a finding on
[the merge-drill re-run](/work/2026-08-14-test-gap-upstream-merge-drill-rerun.md), which also carries
the standing proposal to retire this entry's whole shape by moving our content onto upstream's path.

**Third amendment (2026-08-14) — the rename is reversed; this is no longer a `D × M` entry.** The
proposal above was taken. `skills/using-workflow/` is deleted and its content now lives at upstream's
own path, `skills/using-superpowers/SKILL.md`, with the three `references/*.md` alongside it.

*Why.* Everything above this line describes work needed only because our file sat at a path upstream
does not have. Git tracks by path: same path means every `git subtree pull` performs a three-way merge
on the file we actually ship — upstream's edits either auto-merge or raise a visible conflict, and
either way they *arrive*. A different path means upstream's edits land on a file we do not read, with
no conflict and no signal, forever. That is rule 3 ("track upstream by merge") working mechanically
rather than by discipline, and it is what the first two amendments were compensating for by hand.

*What it costs.* A skill's invocation name follows its **directory**, not its `name:` frontmatter —
the ours-side `skills/writing-diataxis/` declares `name: documentation` and is still invoked as
`writing-diataxis`. So the skill ships as `using-superpowers` rather than `using-workflow`. Its only
functional consumer is `hooks/session-start`, which reads the file by path and injects the body
directly; the rest are prose mentions in `README.md` and the PR template. The `name:` field is set to
`using-superpowers` to match the directory, which also **removes it from the delta** — the divergence
is now four lines plus the block rather than five plus the block.

*What it buys.* The `D × M` class is retired outright rather than shrunk: this was the only entry of
that shape in the tree (see entry #0's tier table), so the failure mode the first two amendments
describe can no longer occur here. It also collapses the duplicate session-start skill the fork was
carrying — both `using-superpowers/` and `using-workflow/` shipped, with byte-identical `description:`
frontmatter, giving the loader two always-on bootstrap skills. The skill count drops by one.

*On rule 1.* This converts an additive file into an in-place patch, which Band-4 rule 1 names as the
only recurring cost. That is the right trade here and not an exception to the rule: rule 1's premise is
that additive divergence is *free*, and this divergence was not free — it was silent, which is worse
than expensive. An in-place patch that conflicts loudly beats an additive file that drifts quietly.

*On merge.* Unchanged in substance and now enforced by git: diff our `using-superpowers/SKILL.md`
against upstream's, account for every hunk, and keep only the four identity/platform lines and the
re-added block. A conflict here is upstream editing text we also touch — resolve it; do not take our
side wholesale.

**Reconciled onto obra `v6.3.0` (`b36e0829`) — C2, 2026-08-17. This entry's premise does not hold on
an obra base.** obra `v6.3.0` ships the full `## Platform Adaptation` section and
`references/{codex,gemini,antigravity,hermes,pi}-tools.md` — the pcvelz v6.4.0 deletion this entry
exists to repair never happened upstream of obra. Only the `superpowers:` → `graph-works:` namespace
substitution was re-applied. Whoever next revises this ledger should retire or rewrite this entry; C2
deliberately did not.

---

## Entry #8 — `hooks/session-start`

<!-- audit-delta
state: patched
file: hooks/session-start
-->

**Tier A — identity only. 2/49 changed lines.** Upstream's SessionStart hook injects the
`using-superpowers` skill body into every session's context. Ours is upstream's file verbatim with
two string substitutions: the header comment names the graph-works plugin, and `session_context`
says "You have graph-works workflows." and cites the skill as `graph-works:using-superpowers`. The
file it reads — `skills/using-superpowers/SKILL.md` — is upstream's own path, which is what makes
entry #7's patched bootstrap skill load at session start.

**History, because this entry used to be Tier D.** Through the pcvelz lineage this hook carried a
whole second pipeline: a `uv run` python block that resolved a workspace through `workspace_io`,
read a config projection for model-routing / effort-map / commit-strategy / manifest-staleness /
legacy-config state, and read the work sidecar through `work_io` for a resume suggestion — spliced
in as five notice blocks plus a `systemMessage` channel carrying the resume text. 192 lines against
upstream's 49.

All of it was removed on 2026-08-20, and none of the removal was a judgement call:

- **The features are retired.** `workflow.model_routing.*`, `workflow.commit_strategy`,
  `workflow.effort_map` and `workflow.enforce_effort` all raise `UnknownKeyError` against
  `graph_works_core.workspace.manifest.CATALOG`. The `gates` hook feature is documented as retired
  in `graph_works_core/hooks.py`. The resume suggestion was retired by decision.
- **The plumbing was already dead.** `workspace_io` and `work_io` do not exist — E7 replaced them
  with `graph_works_core` / `work_tracker_okf` / `config_io`. Both imports raised
  `ModuleNotFoundError`, the block failed open twice over (`except Exception`, then
  `|| RESOLVED="ERR:uv-invocation-failed"`), and the hook emitted the skill body and nothing else.
  Running it produced 3531 bytes with zero occurrences of any notice tag.

The one notice whose feature survives is manifest-staleness, which points at `gw config sync`.
Restoring it — on `config_io` rather than `workspace_io` — is tracked as work item
`2026-08-20-tech-debt-hooks-workspace-plumbing`, together with `hooks/skill-doc-routing`, which has
the same broken import and degrades visibly rather than silently.

**On merge.** This is now among the cheapest files in the tree to re-base: two identity strings.
Re-apply them and stop. Do not reconstruct the notice pipeline from an older revision of this entry
— if upstream grows its own routing notice again, that is a fresh decision, not a re-application.

## Entry #9 — `hooks/examples/post-task-complete-revalidate.sh`

<!-- audit-delta
state: removed
file: hooks/examples/post-task-complete-revalidate.sh
-->

**Removed from the tree.** This file went with the rest of the dormant user-gate hook surface; entry
#25 records the sweep. The tier analysis below is kept as history — it is what the patch *was*, not a
claim about the tree.

**Tier D — substantive, but converges with upstream.** 15/44 identity/changed lines. This is the
example PostToolUse hook that blocks a `TaskUpdate` marking a task `completed` when the transcript
window shows no user verification and no agent self-assessment language (the "silent close" guard).

**What our patch does.** Two things: (1) renames the escape-hatch/trace-log env vars
`SUPERPOWERS_USERGATE_GUARD` / `SUPERPOWERS_USERGATE_TRACE_LOG` to `GRAPH_WORKS_USERGATE_*`
throughout; (2) a user-extensible assessment-vocabulary mechanism — the
`GRAPH_WORKS_USERGATE_KEYWORDS` env var, comma-separated literal tokens appended to the
`assess_re` regex — plus an expanded built-in vocabulary (`assess(ed|ment)?`, `closed`, `reviewed`,
`validated`, etc., "Issue #19") and a comment documenting a rejected digit-counting fallback.

**Upstream at v6.4.0.** Overlapped almost completely. Upstream independently shipped the *identical*
vocabulary expansion and the *identical* extensibility mechanism — same "Issue #19" comment text,
same rejected-fallback note, same code shape — under the name `SUPERPOWERS_USERGATE_KEYWORDS`.
Upstream additionally added `tr -d '\r'` normalization after every `jq -r` extraction (CRLF safety)
that our side never picked up. The substantive feature this patch existed for is no longer a
divergence; only the env-var prefix differed.

**Disposition: upstream's v6.4.0 text was taken whole.** The vocabulary and extensibility feature
is no longer a divergence — upstream's v6.4.0 text covers it byte-for-byte apart from naming.
Upstream's file was taken in full (including the `tr -d '\r'` hardening we never had), then the
mechanical `SUPERPOWERS_USERGATE_*` → `GRAPH_WORKS_USERGATE_*` rename was applied across all
three occurrences (`_GUARD`, `_TRACE_LOG`, `_KEYWORDS`). That rename is a tier-B-style namespace
swap riding inside a tier-D file, not a structural change in its own right. Only the three env-var
names remain ours.

**On merge.** Diff incoming upstream text against ours with the three env-var names normalized to a
common placeholder first; a non-empty diff after that normalization is a real upstream change to
pull in, not noise from the rename.

**Why not `convert-to-sibling`.** Not applicable regardless of additive/non-additive shape: this is
a single example hook script registered once by exact filename in `hooks.json`, not a file upstream
extends via a plugin/discovery mechanism. There is no second slot to add a sibling into — the only
way to change this hook's behavior is to change this file (or fork its filename and repoint the
hooks.json entry, which is a heavier version of the same rename this disposition already does).

---

## Entry #10 — `hooks/hooks.json`

<!-- audit-delta
state: patched
file: hooks/hooks.json
-->

**Tier D — pure structural wiring.** 0/35 identity/changed lines — every changed line is new; there
is no naming-only substrate to anchor to. This file is the plugin's hook-event dispatch table:
which `matcher` (tool name) triggers which `run-hook.cmd` subcommand, for which event
(`SessionStart`, `PreToolUse`, …).

**Disposition: `re-apply-reduced` — APPLIED.** The `Agent` and `AskUserQuestion` matchers merged
free (upstream's v6.4.0 array carries them verbatim, unmodified from v5.5.0). The settled
sub-decision on `pre-taskcreate-model-tier` adopted upstream's attachment point: it fires on
`TaskCreate` only, not duplicated on `Skill`. The remaining delta is narrow and structural: a single
`Skill` matcher prepended to the `PreToolUse` array as its first element, dispatching only
`skill-doc-routing` — our one genuinely novel hook-point, upstream-side unmatched.

**What was implemented.** The `PreToolUse` array now reads:
1. `Skill` → `skill-doc-routing` (ours only)
2. `TaskCreate` → `pre-taskcreate-model-tier` + `pre-taskcreate-commit-strategy` (upstream's)
3. `Agent` → `pre-agent-model-routing` (upstream's)
4. `AskUserQuestion` → `pre-askuser-handoff-guard` (upstream's)

**On merge.** Treat `PreToolUse` as owned jointly. The `Skill` matcher must stay first in the array
and must dispatch `skill-doc-routing` only (never re-attach `pre-taskcreate-model-tier` to `Skill`).
The upstream three matchers stay as-is — take any upstream updates to their structure, content, or
order, but keep ours at the head.

**Reconciled onto obra `v6.3.0` (`b36e0829`) — C2, 2026-08-17.** DEC-1 executed. obra has no
`PreToolUse` array at all, so the four dormant wirings were never present to remove; the file is
obra's `SessionStart` plus our `Skill`/`skill-doc-routing` matcher, and nothing else.

---

## Entry #11 — `skills/brainstorming/scripts/start-server.sh`

<!-- audit-delta
state: patched
file: skills/brainstorming/scripts/start-server.sh
-->

**Tier D — substantive.** 4/24 identity/changed lines. This script boots the brainstorming skill's
local companion server and decides where its per-session state directory lives.

**What our patch does.** Two things in the same `if [[ -n "$PROJECT_DIR" ]] ... else ... fi` block
that computes `SESSION_DIR`: it renames the `--project-dir`-relative path from
`.superpowers/brainstorm/` to `.brainstorming/` (a behavior change, not an addition — existing
sessions under the old path stop being found), and it replaces the bare `/tmp/brainstorm-${SESSION_ID}`
else-branch with a new three-tier resolver (graph-wiki workspace → `<workspace>/brainstorm/`; else a
git repo → `<repo>/.brainstorming/`; else `/tmp/brainstorm-${SESSION_ID}` as before), shelling out to
`shared/resolve-workspace.sh`.

**Upstream at v6.4.0.** Touches the same `if [[ -n "$PROJECT_DIR" ]]` branch we renamed: it adds
`export BRAINSTORM_PORT_FILE="${PROJECT_DIR}/.superpowers/brainstorm/.last-port"` and a matching
`BRAINSTORM_TOKEN_FILE`, both hard-coding the literal `.superpowers/brainstorm/` path our patch
renames to `.brainstorming/`. Upstream also adds a large amount of unrelated new capability further
down the file (`--idle-timeout-minutes`, `--open`, `is_windows_like_shell()`, `umask 077`, a
`SERVER_ID` file, foreground-mode PID handling) that our diff doesn't touch and doesn't conflict
with.

**Disposition: `re-apply-reduced`.** The else-branch three-tier resolver is genuinely additive and
would be `convert-to-sibling`-eligible on its own — deleting our `+` lines there leaves upstream's
`/tmp` fallback working exactly as upstream intends. But the `.superpowers/brainstorm/` →
`.brainstorming/` rename inside the `PROJECT_DIR` branch is not additive: it changes a literal path
string that upstream's own v6.4.0 `BRAINSTORM_PORT_FILE`/`BRAINSTORM_TOKEN_FILE` lines now also
depend on, so a naive reapply leaves the port/token files pointed at the old path while
`SESSION_DIR` moves to the new one — a real (if quiet) inconsistency, not a merge conflict Git would
flag. Re-apply the else-branch resolver in full; re-apply the rename narrower, updating the two new
upstream `export` lines' paths in the same edit rather than leaving them stale.

**On merge.** When upstream changes the `PROJECT_DIR` branch again, check every literal
`.superpowers/brainstorm` string it introduces there and rename each alongside `SESSION_DIR`, not
just the one line our patch historically touched.

**Patched.** Re-applied per the disposition above: the else-branch three-tier resolver is back in
full, and the `.superpowers/brainstorm/` → `.brainstorming/` rename was applied to all three
literals in the `PROJECT_DIR` branch — `SESSION_DIR` plus upstream's two new v6.4.0
`BRAINSTORM_PORT_FILE`/`BRAINSTORM_TOKEN_FILE` `export` lines — so none of the three can drift from
the others.

**Reconciled onto obra `v6.3.0` (`b36e0829`) — C2, 2026-08-17.** Re-applied whole. obra's file differs
from the prior fork base's by one line, so all three `.brainstorming/` literals sit at the same
anchors this entry describes.

---

## Entry #12 — `skills/using-git-worktrees/SKILL.md`

<!-- audit-delta
state: patched
file: skills/using-git-worktrees/SKILL.md
-->

**Tier D — near-total rewrite.** 5/96 identity/changed lines. This skill is the worktree-isolation
gate: detect existing isolation, otherwise create a worktree via native tooling or a `git worktree`
fallback, verify it's gitignored, run project setup, verify a clean test baseline.

**What our patch does.** Turns the skill into the **Code-Change Gate**: a new "Part 1 —
Authorization" check (a direct implement directive must exist before any Write/Edit, else STOP and
stay read-only) ahead of the existing "Part 2 — Isolation" check, which itself flips from
consent-based ("would you like a worktree?") to mandatory (isolation is required once authorized,
main checkout only on explicit user request). It also replaces the legacy
`~/.config/superpowers/worktrees/` global-directory fallback with a `resolve-workspace.sh`-driven
graph-wiki-workspace directory, adds a Step 5 "Exit the Worktree" using a native `ExitWorktree` tool,
and rewrites the Quick Reference / Common Mistakes / Red Flags tables to match.

**Upstream at v6.4.0.** Also rewrites this file heavily, independently: it renumbers every step
(Step 3 Project Setup and Step 4 Verify Baseline both shift down by one once the old "global
directory" fallback item is deleted), drops the `~/.config/superpowers/worktrees/` legacy path
entirely (same deletion target ours retargets to the graph-wiki workspace, worded differently), adds
its own Step 4 "Exit the Worktree" using the same `ExitWorktree` tool concept, and replaces the whole
"Common Mistakes" / "Red Flags" section with a "Common Rationalizations" excuse/reality table. The
two step-renumbering schemes disagree with each other from the point both sides start editing.

**Disposition: `re-apply-reduced`.** Line-for-line reapply is not viable — both sides renumber the
same steps differently and both restructure the same trailing sections into incompatible shapes.
Reduce to the substantive deltas that survive independent of exact step numbers — the two-part
Authorization+Isolation gate, the workspace-resolved directory (retarget upstream's now-deleted
global-directory step, not the `.worktrees/` local-directory logic upstream keeps), and the Step 5/4
`ExitWorktree` addition (upstream ships the concept already; only the exact step number is a
merge-time detail) — and re-express them against upstream's new step numbering and its "Common
Rationalizations" table shape rather than reintroducing "Common Mistakes" as a parallel section.

**On merge.** Re-derive step numbers from upstream's current file at merge time; do not assume any
fixed "Step N" anchor survives from either the last patched version or this entry's description.
Diff the Authorization+Isolation gate content specifically — that is the one piece with no upstream
counterpart at all.

**Why not `convert-to-sibling`.** The patch's own `-` lines rewrite upstream's control flow directly:
the consent prompt ("Would you like me to set up an isolated worktree?") is deleted and replaced with
a mandatory check, and the global-directory fallback step is deleted outright rather than
supplemented. A sibling can only add a new checkpoint beside `using-git-worktrees`, not delete or
invert the one upstream already runs — and upstream has now independently deleted the same
global-directory step, meaning even a "just add the deleted content back on our side" workaround has
nothing left on the other side to coexist with.

**Disposition applied.** The Authorization gate (Part 1) was added whole, with no upstream
counterpart to reconcile against. The Isolation gate (Part 2) wraps upstream's existing Steps 0–4 in
place rather than renumbering them — upstream's own step numbers survive untouched underneath the new
Part 1/Part 2 framing. The directory-selection fallback now runs through the workspace-resolved
directory via `skills/shared/resolve-workspace.sh`, retargeting upstream's deleted global-directory
step rather than reintroducing it. Upstream's `## Step 4: Exit the Worktree` was reused as-is:
confirmed unchanged against the grafted text — Task 8 didn't need to modify it, since it was already
equivalent to what this patch would otherwise have written.

**Reconciled onto obra `v6.3.0` (`b36e0829`) — C2, 2026-08-17.** Re-applied per the reduced
disposition. obra already ships `## Step 4: Exit the Worktree`, which was kept verbatim — the Part
1/Part 2 framing wraps obra's own step numbers without renumbering them.

---

## Entry #13 — `skills/brainstorming/SKILL.md`

<!-- audit-delta
state: patched
file: skills/brainstorming/SKILL.md
-->

**Tier D — substantive, additive-shaped but not additive.** 12/84 identity/changed lines. This is
the design-before-code skill: explore context, offer the visual companion, ask clarifying questions,
propose approaches, present the design, write the spec doc, self-review it, hand off to
`writing-plans`.

**What our patch does.** Adds two new blocks of content — an "Epic-mode note" for `kind: epic` work
items (the design becomes a decomposition spec, one section per anticipated child) and a full
"Auto-file Mode" apparatus (Step 0 mode check, Step 2a early-stub confirm + `gw work file`, Step 3a
finalize-at-spec-time + `gw work advance`, Step 4a pipeline hand-off) — then references those new
steps from inline edits to Checklist items 1, 6, and 9, the "terminal state" sentence, and the
Documentation section's spec-path line, redirecting the save location from
`docs/superpowers/specs/...` to the graph-wiki workspace's `raw/specs/` inbox and inserting a
pipeline-stage STOP guard ahead of the `writing-plans` handoff.

**Upstream at v6.4.0.** Touches a disjoint set of lines: it rewords Checklist item 2 and the whole
"Visual Companion" *offer* framing from upfront-consent to just-in-time (the full rewrite of the
companion's own file is entry #16, not this one), adds a `YAGNI ruthlessly` bullet, folds "Key
Principles" into the approaches section, and adds `modelTier` to the task-template's metadata fence.
None of upstream's changed lines are lines our patch also changed — the two patches land in different
parts of the same file.

**Disposition: `re-apply`.** No line-level collision with upstream's v6.4.0 changes exists, so this
reapplies whole against the new base with only mechanical adjustment (Checklist item 2's renumbered
wording sits undisturbed next to our item 0/1/6/9 edits). It is not `convert-to-sibling` regardless:
several of our `-` lines rewrite existing sentences in place (the item 6 doc-path line, the "terminal
state is invoking writing-plans" sentence, the Documentation section's spec-path line) rather than
only adding new ones, so a sibling could not express those edits without also rewriting upstream's
original text. Item 9 itself is different in kind: its own sentence is untouched — only a new
sub-bullet was added beneath it referencing the auto-file apparatus — so it doesn't add to this count,
but the other three rewrites are enough to disqualify a sibling on their own.

**On merge.** ~~Re-run the reapply as a straight patch against upstream's current Checklist text~~ —
**superseded; see the amendment below.** Confirm item 2's "just-in-time" wording still reads
coherently next to items 0/1's mode-check insertions (they describe different steps and don't need to
agree with each other, but should not be read as contradicting the same offer).

> **Amendment (2026-08-14) — v6.4.1 invalidated this entry's disposition. This file now needs an
> editorial decision at every merge, not a mechanical reapply.**
>
> The disposition above rests on *"no line-level collision with upstream's v6.4.0 changes exists, so
> this reapplies whole against the new base with only mechanical adjustment."* True at v6.4.0. **False
> at v6.4.1**, which restructured the file in two places our patch lives in:
>
> 1. **The Checklist became three lists.** Upstream split one numbered 1–9 list into path-specific
>    **Spike** (5 items), **Bounded** (5 items) and **Architectural** (1–9) lists, prefaced by
>    "Classify first, announce the path." All four of our deltas — step 0, the step 1 sub-bullet, the
>    step 6 doc-path rewrite, the step 9 sub-bullets — were written against the single list.
> 2. **The terminal-state paragraph became path-bound.** Upstream rewrote it to give Architectural,
>    Bounded and Spike each their own terminal state. Our sentence rewrote the single-path version.
>
> **Ruling taken at the drill, and the one to keep re-applying: a pipeline-dispatched invocation is
> always the Architectural path.** The pipeline's design stage exists to produce a spec document, and
> Architectural is the only path that writes one — Spike terminates in a reported recommendation,
> Bounded in direct implementation. Spike and Bounded are standalone-only. Concretely:
>
> - Take upstream's three-list structure whole. Re-apply our step 0 mode check as **Architectural step
>   0** only, and state the path-binding above it.
> - Keep upstream's path-bound terminal-state paragraph verbatim, and add our two auto-file /
>   pipeline-dispatched exceptions *beneath* it as exceptions **on the Architectural path**, rather
>   than rewriting upstream's sentence as the original patch did.
>
> That second move converts a `-` line rewrite into an addition beside upstream's text, which is
> what the convertibility test below asks for. **Re-disposition: `re-apply-reduced`.** The Auto-file
> Mode apparatus (Steps 0, 2a, 3a, 4a) and the item 1/6/9 deltas still re-apply as before; only the
> two structural sites above need the judgement, and the ruling for them is recorded here so the next
> merge does not re-derive it.

**Reconciled onto obra `v6.3.0` (`b36e0829`) — C2, 2026-08-17.** The 2026-08-14 amendment's ruling was
executed for the first time here: obra `v6.3.0` already carries the three-path (Spike/Bounded/
Architectural) checklist split and the path-bound terminal-state paragraph, so the Auto-file deltas
bind to the Architectural list only, and the terminal-state exceptions were added beneath obra's
paragraph rather than rewriting it.

**Why not `convert-to-sibling`.** The doc-path and terminal-state rewrites are `-` lines that replace
upstream's existing text with different text, not lines that sit beside it unchanged — the
disqualifying case the convertibility test names explicitly.

**Patched.** Re-applied per the disposition above: the Epic-mode note and the full Auto-file Mode
apparatus (Steps 0, 2a, 3a, 4a) were inserted, and the three sentences the disposition calls out as
rewrites — the item 6 doc-path line, the "terminal state is invoking writing-plans" sentence, and the
Documentation section's spec-path line — were rewritten in place to match. A sub-bullet was added
beneath Checklist item 9 referencing the auto-file/pipeline hand-off (its own base sentence left
unchanged), and a pipeline-stage STOP guard was inserted ahead of the `writing-plans` hand-off.
Upstream's disjoint v6.4.0 edits — the just-in-time visual-companion wording, the `YAGNI ruthlessly`
bullet, "Key Principles" folded into the approaches section, and `modelTier` added to the
task-template's metadata fence — all survived untouched, as the no-line-level-collision analysis
above predicted.

---

## Entry #14 — `skills/subagent-driven-development/SKILL.md`

<!-- audit-delta
state: patched
file: skills/subagent-driven-development/SKILL.md
-->

**Tier D — substantive, converging with an independently-rewritten upstream.** 19/47
identity/changed lines. This is the orchestrator skill for same-session subagent execution: read the
plan, dispatch an implementer per task, dispatch reviewers, loop on findings, mark tasks complete,
dispatch the final whole-branch review.

**What our patch does.** Inserts a "Step 0: Code-Change Gate" ahead of "The Process" (requiring
`graph-wiki:using-git-worktrees` before any subagent writes code), rewrites the Red Flags' main/master
line into a Code-Change-Gate-framed line, adds a new "Bounded Parallel Dispatch" section (read-only
agents always parallel-safe; implementers only when their `files` lists and `blockedBy` chains are
disjoint) and a new "Escalating Questions to Your Human Partner" section (re-read the plan's "User
decisions (already made)" before any execution-time `AskUserQuestion`), and renames every
`superpowers-extended-cc:` skill reference to `graph-wiki:` throughout, plus one workspace-path
rename (`~/.config/superpowers/hooks/` → `~/.claude/hooks/` in the worked example).

**Upstream at v6.4.0.** Independently rewrites this file almost completely: the two-stage
spec-reviewer/code-quality-reviewer loop becomes a single task-reviewer with a per-plan ledger
(`progress.md`), numbered fix rounds with an escalation cap and adjudication breaker, `sdd-workspace` /
`task-brief` / `review-package` / `re-review-prompt.md` helper scripts, a "Setup" section, a "Common
Rationalizations" table, and — landing separately but essentially verbatim against ours — its own
"Escalating Questions to Your Human Partner" and "Bounded Parallel Dispatch" sections (same bullet
content, near-identical wording, one clause reworded to match upstream's now-single-stage review
model). The two independent additions are close enough to call the same feature landing twice.

**Disposition: `re-apply-reduced`.** Take upstream's "Escalating Questions" and "Bounded Parallel
Dispatch" sections whole — they supersede ours, with only the "own spec + quality review" → "own task
review" wording difference to note as upstream's now-current model. Re-apply the Code-Change Gate
insertion and the `graph-wiki:` renames against upstream's new structure (the gate becomes a step
ahead of upstream's new "Setup" section rather than ahead of the old "Step 0: Load Persisted Tasks").
Drop the old main/master Red Flags line rewrite — upstream's own "Setup" section already states the
no-main/master rule; fold the Code-Change-Gate framing into that instead of maintaining a competing
Red Flags line.

**On merge.** Whenever upstream's ledger/review-package machinery changes again, re-diff the
"Escalating Questions" and "Bounded Parallel Dispatch" sections specifically for continued
near-identical wording before assuming they still need no local edit — the two sides converging once
is not a guarantee they stay converged.

**Why not `convert-to-sibling`.** The Red Flags line is a straight rewrite of upstream's existing
main/master sentence (a `-`/`+` pair, not an addition), and the two "converged" sections could only be
distinguished from upstream's own by wholesale duplication — there is no way to add them "beside"
upstream's now-near-identical versions without producing two competing copies of the same guidance in
one file.

**Disposition applied.** Upstream's "Escalating Questions to Your Human Partner" and "Bounded
Parallel Dispatch" sections were left untouched — they are upstream's now, verbatim, one copy of
each. A new "## Step 0: Code-Change Gate" section was inserted ahead of upstream's restructured
"## Setup" section (the old "Step 0: Load Persisted Tasks" anchor is gone from upstream's file). The
old main/master Red Flags line rewrite was dropped; instead, upstream's own no-main/master sentence
inside "## Setup" was extended in place to name the Code-Change Gate as what enforces it. Every
`superpowers-extended-cc:` reference was renamed to `graph-wiki:`, and the worked example's
`~/.config/superpowers/hooks/` path now reads `~/.claude/hooks/`. Convergence is not permanent: the
two sections landing essentially verbatim on both sides this time is not a guarantee of the next
sync — re-diff "Escalating Questions to Your Human Partner" and "Bounded Parallel Dispatch"
specifically against upstream on every future sync before assuming no local edit is needed.

**Reconciled onto obra `v6.3.0` (`b36e0829`) — C2, 2026-08-17.** Obra's file adopted per the cost
table's take-obra disposition, with two exceptions restored on fork-integrity grounds: the
`superpowers:` → `graph-works:` namespace substitution (entry #3's standing rule), and the
graph-works pipeline integration point this file carries — the Step 0 Code-Change Gate that invokes
`graph-works:using-git-worktrees`. Everything else our patch carried was dropped, as the disposition
directs.

**Step 0 Code-Change Gate heading dropped — 2026-08-18.** The standalone "## Step 0: Code-Change
Gate" section (the `REQUIRED SUB-SKILL` framing calling out `graph-works:using-git-worktrees` before
any subagent writes code) was removed. The invocation itself was not dropped — it moved into "##
Setup" as plain prose: "Ensure the work happens in an isolated workspace: use
graph-works:using-git-worktrees to create one or verify the existing one." The substance of the gate
(the authorization check plus mandatory isolation) still lives entirely inside entry #12's
`using-git-worktrees/SKILL.md`, which this file still calls before Setup proceeds; what changed here
is only the calling convention — a dedicated numbered step versus a Setup-section sentence — not
whether the gate skill runs. A namespace typo introduced in the same edit
(`superpowers:using-git-worktrees`, a name no plugin in this marketplace has) was caught and corrected
back to `graph-works:using-git-worktrees` before landing. This file's `patched` classification stands
unchanged — it still diverges from obra's own Setup text.

---

## Entry #15 — `skills/requesting-code-review/SKILL.md`

<!-- audit-delta
state: verbatim
file: skills/requesting-code-review/SKILL.md
-->

**Tier D — substantive, one colliding line.** 2/45 identity/changed lines. This skill dispatches a
code-reviewer subagent with a precisely crafted context bundle (description, plan/requirements, base
and head SHAs) rather than the coordinator's own session history.

**What our patch does.** Inserts a new numbered step 2, "Recall review-time guidance (diff-scoped)":
determine the work-item slug/phase from the pipeline dispatch context, run `gw guidance suggest`
scoped to the changed paths and the `review` role, and thread the result into a new `{REVIEW_GUIDANCE}`
placeholder on the reviewer template — renumbering the old steps 2 and 3 to 3 and 4. It also rewords
the dispatch-mechanism line ("Use Task tool with `general-purpose` type..." → "Use the Agent tool
with subagent_type: general-purpose...").

**Upstream at v6.4.0.** Trims the intro sentence, rewords the *same* dispatch-mechanism line to
"Dispatch a `general-purpose` subagent, filling the template at
[code-reviewer.md](code-reviewer.md)", and replaces "Integration with Workflows" with a "Common
Rationalizations" table. The dispatch-mechanism line is a genuine collision: both sides independently
reworded the exact same sentence to different text.

**Disposition: `re-apply-reduced`.** The new guidance-recall step and `{REVIEW_GUIDANCE}` placeholder
reapply cleanly as new content (renumbering around upstream's now-different step 2 wording is
mechanical). The dispatch-mechanism line needs a decision, not a blind overwrite in either direction:
reconcile "Use the Agent tool with subagent_type: general-purpose" (ours) against "Dispatch a
`general-purpose` subagent, filling the template at [code-reviewer.md]" (upstream's markdown-linked
form) rather than letting a mechanical reapply silently pick whichever patch applies last.

**Resolved wording.** Upstream's markdown-linked form wins the sentence, with ours' explicit tool
naming folded in — upstream's link carries information a reader needs, ours' tool name is the
harness-compat correction the patch exists for:

> Dispatch a `general-purpose` subagent with the Agent tool (`subagent_type: general-purpose`),
> filling the template at [code-reviewer.md](code-reviewer.md)

**On merge.** Check the dispatch-mechanism sentence specifically on every future upstream sync — it
has now been independently reworded on both sides once already and is exactly the kind of low-salience
line a diff tool will merge silently in the wrong direction.

**Why not `convert-to-sibling`.** The dispatch-mechanism line is directly rewritten by both sides
(two different `+` texts over the same original `-` line), which is a modification of upstream's
existing sentence, not an addition beside it.

---

## Entry #16 — `skills/brainstorming/visual-companion.md`

<!-- audit-delta
state: patched
file: skills/brainstorming/visual-companion.md
-->

**Tier D — substantive, directly colliding rewrite.** 12/41 identity/changed lines. This is the
detailed companion-server protocol doc for the brainstorming skill's optional browser-based visual
aid: starting the server per-platform, the write/wait/read screen loop, content-fragment authoring,
cleanup.

**What our patch does.** Rewrites the entire "Starting a Session" section around workspace-resolved
persistence: replaces literal `scripts/start-server.sh --project-dir /path/to/project` invocations
(across all four platform blocks and the non-loopback-host example) with
`bash "${CLAUDE_PLUGIN_ROOT}/skills/brainstorming/scripts/start-server.sh"`, documents the three-tier
fallback chain (graph-wiki workspace → `.brainstorming/` in the enclosing repo → `/tmp`), and updates
the "Cleaning Up" section's persistence claim and stop-server invocation to match.

**Upstream at v6.4.0.** Independently rewrites the same "Starting a Session" section for a different
reason: a session-key security feature (`?key=…` on every URL, rejecting bare `host:port` requests),
`--open` for auto-opening the browser, a longer idle timeout, and platform-block consolidation
(Codex/Gemini-specific blocks folded away). Both patches touch the exact same lines — the
`start-server.sh` invocation lines, the "Finding connection info" paragraph, the per-platform launch
blocks, the "Note" persistence paragraph — with different, non-composable replacement text.

**Disposition: `re-apply-reduced`.** A blind reapply would either drop the session-key security model
(if ours wins) or the workspace-resolution model (if upstream wins) — both are real, independent
features that must both survive. Reduce to: keep upstream's `--open`/session-key/idle-timeout text
verbatim, and layer the workspace-resolution fallback chain and `${CLAUDE_PLUGIN_ROOT}`-relative
invocation on top of upstream's now-current per-platform blocks rather than reintroducing the
`--project-dir`-only framing either side used before.

**On merge.** Treat "Starting a Session" as jointly owned going forward; diff it specifically, line by
line, since two independent, unrelated features have now collided in it once and the file gives no
structural signal (headings, step numbers) that would make a future collision more visible than this
one was.

**Why not `convert-to-sibling`.** Both sides' `-` lines rewrite the same existing sentences (the
`start-server.sh` invocation form, the persistence note, the per-platform blocks) rather than adding
new ones beside them, and the upstream security feature (URL must carry `?key=`) is exactly the kind
of correctness-affecting behavior change a sibling cannot safely leave un-merged — an outdated sibling
would keep telling the user to share a bare URL upstream's server now rejects.

**Patched.** Reduced per the disposition above: every `start-server.sh` invocation in the surviving
platform blocks and the non-loopback-host example (plus the top-of-section example, which the
grafted file also carries) now reads
`bash "${CLAUDE_PLUGIN_ROOT}/skills/brainstorming/scripts/start-server.sh"`, with `--open` kept
verbatim wherever upstream had it. The persistence "Note" paragraph and the "Cleaning Up" paragraph
were rewritten to state the three-tier fallback chain entry #11 actually implements (`<workspace>/brainstorm/`
→ `.brainstorming/` at the enclosing repo root → `/tmp/brainstorm-<session>`, `--project-dir`
overriding it), rather than the old `--project-dir`-only framing either side used before. Upstream's
session-key paragraph (`?key=…`, bare-`host:port` rejection), the `--open` auto-open sentence, and the
idle-timeout sentence in "The Loop" were left byte-for-byte untouched. `"Starting a Session"` is now
jointly owned per the "On merge" note above — diff it line by line on every future sync, since the
file gives no structural signal that would make the next collision more visible than this one was.

**Reconciled onto obra `v6.3.0` (`b36e0829`) — C2, 2026-08-17.** The collision resolved itself: obra
`v6.3.0` already ships the `?key=…` session-key feature that made this row a three-way collision. But
obra carries **four** platform blocks (Claude Code, Codex, Gemini CLI, Copilot CLI) where the prior
fork base carried one, so the invocation-form replacement was applied to all four plus the top
example, the non-loopback example and `## Cleaning Up`.

---

## Entry #17 — `skills/executing-plans/SKILL.md`

<!-- audit-delta
state: patched
file: skills/executing-plans/SKILL.md
-->

**Tier D — pipeline-stage guard, assessed for `convert-to-sibling`.** 13/30 identity/changed lines.
This is the parallel-session counterpart to `subagent-driven-development`: load persisted tasks,
verify or create an isolated worktree, execute tasks one at a time with acceptance-criteria gates,
hand off to `finishing-a-development-branch`.

**What our patch does.** Inserts a new "Step 0: Code-Change Gate" ahead of the existing "Step 0: Load
Persisted Tasks" (renumbered to "Step 0.5"), requiring `graph-wiki:using-git-worktrees` before any
Write/Edit and folding the existing worktree-existence check into the gate — which means the old
"Step 0.5: Verify Workspace (Worktree Check)" section is deleted outright, not left in place beside
the new gate. It also rewrites the main/master line under `## Remember` to reference the gate, and renames
`superpowers-extended-cc:` references to `graph-wiki:` in the intro Note and the Integration list.

**Assessed for `convert-to-sibling` (per this entry's specific charge).** The gate itself — a
pre-flight check that runs before any other step, structurally identical in shape to how
`using-git-worktrees` is already invoked as a required sub-skill — is the strongest
`convert-to-sibling` candidate in this tier: a wrapper skill could run the gate first and then
delegate to upstream's `executing-plans` unmodified, tolerating upstream's own redundant worktree
check as a harmless no-op re-verification rather than something that must be deleted. That is
genuinely additive in shape. It does not clear the bar for the file as a whole, though: the same patch
also deletes the "Step 0.5: Verify Workspace" section's text outright (a `-` block, not a surviving
duplicate) and rewrites the `## Remember` line and the Integration list's skill names in place, none of
which a sibling can express without altering upstream's own printed text.

**Upstream at v6.4.0.** Adds an unrelated "Consulting the Plan Author" section (use `ListAgents` /
`SendMessage` to ask the plan-writing session directly) and a "do not re-decide what the plan settled"
paragraph inside Step 2's acceptance-criteria gate — neither collides with our changes. It does
collide on one line: the intro "Note" sentence about subagent availability is trimmed by upstream
("Superpowers works best with subagent support...") while ours keeps the original longer sentence and
only renames the skill reference inside it.

**Disposition: `re-apply-reduced`.** Re-apply the gate insertion and the worktree-check-folding as
before, adjusted to sit ahead of upstream's now-slightly-different Step 0; take upstream's trimmed
intro Note wording rather than reapplying ours over it, keeping only the `graph-wiki:` rename inside
whichever wording wins; carry the `## Remember` and Integration renames through unchanged, since neither
collides with upstream's new content.

**On merge.** Re-check the intro Note sentence specifically — it is now a demonstrated collision
point, not a hypothetical one — and confirm the gate still sits ahead of whatever upstream currently
calls "Step 0" before assuming the insertion point hasn't moved.

**Why not `convert-to-sibling`.** As assessed above: the gate's pre-flight shape would clear the bar
alone, but the same patch also deletes upstream's "Step 0.5: Verify Workspace" text outright and
rewrites the intro Note and `## Remember` lines in place — real modifications of upstream's existing
sentences, which disqualify the file as a whole even though its most interesting piece is additive.

**Patched.** A new "### Step 0: Code-Change Gate" section was inserted at the head of "## The
Process," requiring `graph-wiki:using-git-worktrees` before any Write or Edit. The old "Step 0: Load
Persisted Tasks" heading was renumbered to "Step 0.5" — reusing the slot vacated by deleting the
redundant "Step 0.5: Verify Workspace (Worktree Check)" section outright, its worktree-existence check
folded into the gate's own description instead. Upstream's trimmed intro Note wording ("Superpowers
works best with subagent support...") was kept as-is, with only the skill reference renamed to
`graph-wiki:subagent-driven-development`. Every surviving `superpowers-extended-cc:` reference — the
intro Note, the Step 3 hand-off to `finishing-a-development-branch`, and all three entries in the
Integration list — was renamed to `graph-wiki:`; the reference inside the deleted Verify Workspace
section disappeared with the section rather than being renamed. The main/master line under "##
Remember" now names the Code-Change Gate (Step 0) as what enforces it. Upstream's "## Consulting the
Plan Author" section, including its "do not re-decide what the plan settled" paragraph inside Step 2's
acceptance-criteria gate, survived untouched.

**Reconciled onto obra `v6.3.0` (`b36e0829`) — C2, 2026-08-17.** Obra's file adopted per the cost
table's take-obra disposition, with two exceptions restored on fork-integrity grounds: the
`superpowers:` → `graph-works:` namespace substitution (entry #3's standing rule), and the
graph-works pipeline integration point this file carries — the Step 0 Code-Change Gate that invokes
`graph-works:using-git-worktrees`. Everything else our patch carried was dropped, as the disposition
directs.

**Step 0 Code-Change Gate heading dropped — 2026-08-18.** The standalone "### Step 0: Code-Change
Gate" section was removed, and the old "Step 0: Load Persisted Tasks" / "Step 0.5: Verify Workspace"
split collapses back to a single "### Step 1: Load and Review Plan," whose first item is now "Ensure
an isolated workspace: use graph-works:using-git-worktrees to create one or verify the existing one."
The gate skill is still invoked, still first, still correctly namespaced — only the dedicated
numbered-step framing is gone, folded into the load-plan step instead. The authorization-plus-isolation
logic itself is unchanged; it still lives entirely in entry #12's `using-git-worktrees/SKILL.md`. A
namespace typo introduced in the same edit (`superpowers:using-git-worktrees`) was caught and corrected
back to `graph-works:using-git-worktrees` before landing. This file's `patched` classification stands
unchanged — it still diverges from obra's own Step 0/Step 1 text.

---

## Entry #18 — `skills/writing-plans/SKILL.md`

<!-- audit-delta
state: patched
file: skills/writing-plans/SKILL.md
-->

**Tier D — pipeline-stage guard, assessed for `convert-to-sibling`.** 18/28 identity/changed lines.
This is the plan-authoring skill: scope-check, write the plan doc with per-task Goal/Files/Acceptance
Criteria/Verify blocks and `TaskCreate` metadata, self-review, then a hard-gated `AskUserQuestion`
Execution Handoff into `subagent-driven-development` or `executing-plans`.

**What our patch does.** Inserts a "Pipeline-stage guard — check FIRST" paragraph at the top of the
"Execution Handoff" section: if the dispatch brief marks this as the pipeline's `plan` stage (a
specific STOP-line signal), skip the entire Execution Handoff — no `AskUserQuestion`, no
`subagent-driven-development`/`executing-plans` invocation — and instead announce the plan is saved
and stop, handing control back to `graph-wiki:workflow`. Alongside that, the same patch renames
`superpowers`/`superpowers-extended-cc:` skill references to `graph-wiki:` in four places, redirects
the plan-save path from `docs/superpowers/plans/` to the graph-wiki workspace's `raw/plans/` inbox
(in the intro, the `AskUserQuestion` question text, the Task Persistence section, and its worked
example), and updates the internal user-gate reference doc paths.

**Assessed for `convert-to-sibling` (per this entry's specific charge).** The guard paragraph itself
is additive in the narrowest sense — it is inserted whole ahead of the existing `<HARD-GATE>` block
and does not delete or alter any of upstream's `AskUserQuestion`/hard-gate text — but it cannot be
expressed as a genuine sibling the way `using-git-worktrees`'s gate can, because it does not run
*before* `writing-plans` is invoked; it has to interrupt the *same* skill's own control flow partway
through, after the plan has already been written but before the Execution Handoff fires. A wrapper
that runs ahead of `writing-plans` cannot see that state; only a fork of the same file — or an
in-file conditional, which is what this patch already is — can. Combined with the plan-path renames,
which do rewrite upstream's existing sentences in place, the file does not clear the bar even though
the guard paragraph alone is shaped like an addition.

**Upstream at v6.4.0.** Adds "Global Constraints" and "User decisions (already made)" fields to the
plan-header template, a "Deferred decisions" subsection, a mechanical `grep -c` self-check replacing
the prose self-check, `modelTier` in the metadata fence, and a "the executing session can consult this
session via SendMessage" clause next to the Parallel-Session Execution Handoff option — none of which
collide with our changes directly. It does collide once: the `Context:` line naming the worktree-setup
skill is `superpowers:using-git-worktrees` at v5.5.0 and becomes `superpowers-extended-cc:...` at
v6.4.0, while ours independently renames the same line to `graph-wiki:...` — a three-way divergent
edit of one sentence.

**Disposition: `re-apply-reduced`.** Reapply the pipeline-stage guard paragraph as-is — it sits beside
upstream's now-current Execution Handoff content without collision. Reapply the `raw/plans/` path
renames and the user-gate reference-doc path updates the same way; none collide with upstream's
additions. Resolve the `Context:` line by taking `graph-wiki:using-git-worktrees` (our target name)
rather than reapplying blind, since upstream's own value here is itself mid-rename and not the name
this fork uses.

**On merge.** Confirm the guard paragraph still precedes the `<HARD-GATE>` block after any upstream
restructuring of the Execution Handoff section — its correctness depends entirely on running before
`AskUserQuestion` fires, not on any particular heading text.

**Why not `convert-to-sibling`.** The guard has to interrupt `writing-plans`'s own control flow
mid-skill rather than gate entry to it, so no wrapper skill invoked beforehand can express it; and the
plan-path renames are straight rewrites of upstream's existing sentences, which independently
disqualify the file even where the guard paragraph itself is shaped like an addition.

**Patched.** The "Pipeline-stage guard — check FIRST" paragraph was inserted as the first content
under "## Execution Handoff," strictly ahead of upstream's `<HARD-GATE>` block; its correctness is
positional and was re-confirmed after the insert — it still precedes `<HARD-GATE>` with no upstream
restructuring in between. The plan-save path was redirected from `docs/superpowers/plans/` to the
graph-wiki workspace's `<workspace>/raw/plans/` inbox in the intro, the `AskUserQuestion` question
text, `## Task Persistence`, and its worked example, with a `<workspace>/wiki/work/<slug>/NN-plan-plan.md`
pipeline-dispatched override noted alongside the intro and Task Persistence mentions (phrased to match
`brainstorming/SKILL.md`'s equivalent spec-path language). The `Context:` line was resolved to
`graph-wiki:using-git-worktrees` deliberately, taking ours over upstream's own mid-rename value. The
remaining `superpowers-extended-cc:` skill references — the plan-header sub-skill line, both
Execution Handoff invocations, and the Resuming Work slash command — were renamed to `graph-wiki:` to
match. The internal user-gate reference-doc line's absolute
`~/.claude/plugins/marketplaces/superpowers-extended-cc-marketplace/README.md` path was replaced with
a plugin-relative `README.md` reference, consistent with the already-relative `hooks/examples/` and
`docs/user-gate-flow.md` pointers on the same line; that line's hook-script and design-doc paths were
already correct and needed no change. Upstream's "Global Constraints" / "User decisions (already
made)" header fields, the "Deferred decisions" subsection, the mechanical `grep -c` self-check, every
`modelTier` occurrence in the metadata fences, and the SendMessage clause on the Parallel-Session
option all survived untouched.

**Reconciled onto obra `v6.3.0` (`b36e0829`) — C2, 2026-08-17.** Obra's file adopted per the cost
table's take-obra disposition, with two exceptions restored on fork-integrity grounds: the
`superpowers:` → `graph-works:` namespace substitution (entry #3's standing rule), and the
graph-works pipeline integration point this file carries — the Pipeline-stage guard and the
`<workspace>/raw/plans/` inbox path. Everything else our patch carried was dropped, as the
disposition directs.

---

## Entry #19 — ours-side additions

<!-- audit-delta
state: patched
file: hooks/examples/session-end-transcript-capture.sh
file: skills/auto-drive/SKILL.md
file: skills/finishing-relay/SKILL.md
file: skills/graph-works/README.md
file: skills/graph-works/SKILL.md
file: skills/graph-works/references/cross-tool-setup.md
file: skills/graph-works/references/ingest-workflow.md
file: skills/graph-works/references/lifecycle-rules.md
file: skills/graph-works/references/lint-workflow.md
file: skills/graph-works/references/monorepo-principles.md
file: skills/graph-works/references/obsidian-setup.md
file: skills/graph-works/references/page-formats.md
file: skills/graph-works/references/proposal-disposition.md
file: skills/graph-works/references/query-workflow.md
file: skills/graph-works/references/scan-workflow.md
file: skills/graph-works/references/sidecar-schema.md
file: skills/graph-works/references/wiki-schema.md
file: skills/planning-epics/SKILL.md
file: skills/reconciling-spec/SKILL.md
file: skills/workflow/SKILL.md
file: skills/archive/SKILL.md
file: skills/file/SKILL.md
file: skills/ingest/SKILL.md
file: skills/lint/SKILL.md
file: skills/log/SKILL.md
file: skills/onboard/SKILL.md
file: skills/proposals/SKILL.md
file: skills/query/SKILL.md
file: skills/regen-index/SKILL.md
file: skills/scan/SKILL.md
file: skills/status/SKILL.md
file: skills/using-superpowers/references/codex-tools.md
file: skills/using-superpowers/references/copilot-tools.md
file: skills/using-superpowers/references/gemini-tools.md
file: skills/shared/resolve-workspace.sh
file: skills/shared/resolve-workspace.test.sh
file: hooks/skill-doc-routing
file: tests/hooks/test-skill-doc-routing.sh
file: tests/pi/test-pi-extension.mjs
file: tests/test-entry-point-skills.sh
-->

**Why this entry exists.** `scripts/audit_delta.py` derives divergence from `git diff --name-status`
against the upstream base, which reports added files exactly the way it reports modified ones. A file
that exists under a grafted root but carries no `patched` claim anywhere in this ledger is reported as
*undocumented* divergence — the checker cannot tell "we forgot to document this" from "this shouldn't
be here." This entry is the home for ours-side files that have no upstream counterpart at all: net
additions, not patches to something upstream ships.

**Not listed here:** `skills/using-superpowers/SKILL.md`. It is already claimed by entry #7
(`state: patched`). Claiming it a second time here would make it doubly-classified, which
`just audit-delta` treats as an error, not redundancy — so it stays claimed in exactly one place,
entry #7, and this entry cross-references it in prose instead of in a `file:` line.

**What the seven files are.**

- `skills/using-superpowers/references/{codex,copilot,gemini}-tools.md` — the three platform-adaptation
  reference files entry #7 re-adds a citation site for (its "Amendment" paragraph). Upstream deleted
  its own copies of these at v6.4.0, so there is no upstream file at these paths to diverge from and
  they never conflict. **They moved here from entry #2 on 2026-08-14**: entry #7's third amendment
  reversed the skill-directory rename, so these files returned to the base paths where entry #2 had
  classified them `D × D` (deleted both sides). That classification held only while our copies lived
  under `skills/using-workflow/`; now that the paths are populated on our side, they are ours-side
  additions and belong in this entry.
- `skills/shared/resolve-workspace.sh` and its `skills/shared/resolve-workspace.test.sh` — the
  workspace-resolution helper entries #11 and #12 shell out to (`shared/resolve-workspace.sh`,
  `resolve-workspace.sh`-driven directory selection) and its test. Upstream has no equivalent script;
  nothing here patches an upstream file.
- `hooks/skill-doc-routing` — the hook entry #10 wires as the `PreToolUse`/`Skill` matcher's target,
  "our one genuinely novel hook-point, upstream-side unmatched." Upstream ships no file at this path.
- `tests/hooks/test-skill-doc-routing.sh` — that hook's own suite, **added 2026-08-20** by
  `2026-08-20-tech-debt-hooks-workspace-plumbing`. A new file rather than an extension of
  `tests/hooks/test-session-start.sh`, which entry #21's amendment restored to `state: verbatim`:
  coverage for a fork-only hook does not belong in a file being kept conflict-free. It reuses that
  suite's node JSON validator, widened for a bare allow that carries no context and for the two
  branches that carry a `systemMessage`.

**Child 5 extends this entry.** This audit's grafted set covers only what the 2026-06-09 graft
touched; the native surface still outside the grafted roots — 7 more skills, the commands, the hooks,
and the script shims that go with them — is child 5's to port and document. When it lands, those
additions get claimed here too, alongside the six above. Without that extension, child 5's first
commit turns `just audit-delta` red across dozens of undocumented paths the moment those files appear
in the tree.

**Three of these were pulled forward from child 5 into this plan.** `skills/shared/resolve-workspace.sh`
(and its `.test.sh`) and `hooks/skill-doc-routing` were not, strictly, in this child's original scope —
they belong to the native surface child 5 was going to port. They were pulled forward here because
entries #10, #11, and #12 are inert or outright broken without them: entry #10's `Skill` matcher
dispatches a hook file that would not exist, and entries #11 and #12's workspace-resolution logic
shells out to a script that would not exist. Landing the dependents without their dependencies would
have left this child's own patches non-functional. Child 5's scope shrinks by exactly these three
files — one less thing for it to port, one less place for it to duplicate a claim already made
here.

**On `hooks/skill-doc-routing` and `hooks/session-start` — superseded 2026-08-20.** This entry
previously read: "Both hooks invoke `workspace_io`, which the graph-works CLI epic has yet to deliver.
Both are written to fail open when it is absent." That is no longer the state of either file, and the
fail-open framing turned out to understate the cost. `36062c38` removed `session-start`'s `uv run`
block outright with its notice pipeline, and `2026-08-20-tech-debt-hooks-workspace-plumbing` removed
`skill-doc-routing`'s: fail-open there meant a user-visible warning on **every** matching Skill call,
with the routing context never injected.

Neither hook spawns a Python stack now. `skill-doc-routing` resolves through
`skills/shared/resolve-workspace.sh` and reads `<root>/.gw/cache/config.json` with `sed`. That is not
merely cheaper (measured on this checkout: ~66 ms per matching Skill call down to ~25 ms — and that
is the old hook's *fail-fast* path, the only one still reachable; the design spec measures the path
where it resolved at ~670 ms): it is the only form that works from the **installed plugin tree**,
`~/.claude/plugins/.../graph-works/hooks`, where no uv project exists at any ancestor — the case the
hook actually ships into, and one no choice of project root could have fixed. `tests/hooks/test-skill-doc-routing.sh` asserts the absence of both
retired module names and of `uv run` in both hooks, so a reintroduction fails the gate rather than
failing open.

**`commands/onboard.md` replaced two claims on 2026-08-20.** `commands/bootstrap.md` and
`commands/config-init.md` were two ours-side files covering one job — create the workspace, then
configure it — and `bootstrap.md` had gone stale in every claim it made about the landed CLI.
`2026-08-20-tech-debt-unify-bootstrap-config-init` merged them into one file. Both predecessors were
ours-side additions, so this is one `file:` line replacing two, not a `state: removed` case: nothing
upstream ships at any of the three paths.

**On merge.** These seven have no upstream counterpart, so there is nothing to reconcile against on a
sync — they carry forward unchanged unless this fork itself revises them.

**Amended for the commands/agents collapse — 2026-08-27.** Seventeen ours-side
paths left this claim set: `agents/{ingestor,librarian,linter,scanner}.md` and
the thirteen `commands/*.md`. They were not retired patches in the usual sense
— upstream never had them — but ours-side additions we deleted, because neither
directory was in `scripts/package-codex-plugin.sh`'s archive pathspec and
neither is a directory a Codex plugin manifest can declare. Eleven of thirteen
entry points therefore reached no Codex install at all. Every entry point is now
a skill under `skills/`, which is the one directory every harness manifest
declares, so the packaging omission is structurally impossible rather than
merely fixed. The eleven new `skills/<name>/SKILL.md` files and the
`tests/test-entry-point-skills.sh` guard that holds the invariant are claimed
above in their place. The `next` entry point's invocation string became
`/graph-works:workflow` in the same change — the skill carrying that behaviour
was already named `workflow`.
See `work/tech-debt-codex-slash-command-gap`.

**`auto-drive` §3 gained a placement read-back — 2026-08-29.** The coordinator
previously knew only where it *asked* Orca to put a dispatch. Orca derives every
branch it creates as `<host git user slug>/slugify(--name)` — unconditionally,
with no branch control on any of `worker-start`, `orca worktree create`, or
`orca worktree set` — so the planned `worktree.branch` is never the branch that
exists, and a literal planned-vs-actual name diff (what
`work/epic-auto-drive-dispatch-correctness/children/tech-debt-verify-branch-matches-dispatch`
was originally titled for) would fire on every dispatch ever made. §3 instead
gained a new step 3, between `worker-start` and the submission probe, that reads
the landed worktree back — from `worker-start --json`'s `effects[]` when present,
else `worker-show` plus `worktree show` — prints
`dispatched <key> -> <path> on <branch>` for every dispatch, and asserts
placement without consulting a name: path equality for `reuse`/`main`, and
`git merge-base --is-ancestor <base_branch> HEAD` in the observed worktree for
`fork-child`/`create-top-level`. A mismatch halts into §4.2's existing failure
question and skips the probe; a `-N` uniquified worktree is a note, not a halt.
The old steps 3-5 shifted to 4-6 and §2.1's cross-reference moved with them, and
step 2's stale `--name`/branch live-validation item was replaced by the settled
statement. Ours-side file, already claimed above — no `file:` line changes.
See `work/epic-auto-drive-dispatch-correctness/children/tech-debt-verify-branch-matches-dispatch`.

---

## Entry #20 — Declined vendoring: `writing-clearly-and-concisely`

**Intent class: provenance.** The donor plugin (`plugins/graph-wiki` in `agent-research`) ships
`skills/writing-clearly-and-concisely/` and repoints `brainstorming/SKILL.md` at it as
`graph-wiki:writing-clearly-and-concisely`. It is not ours. Upstream superpowers v6.4.0's own
`skills/brainstorming/SKILL.md` reads:

> - Use elements-of-style:writing-clearly-and-concisely skill if available

That is a **cross-plugin reference to a separate `elements-of-style` plugin**, and the donor's copy is
a local vendoring of another plugin's surface, never recorded as such.

**Disposition: declined.** The skill is not ported. Upstream's `elements-of-style:` reference stays
exactly as upstream writes it, including the `if available` conditional. The cost is accepted and
real: where `elements-of-style` is not installed, `brainstorming` degrades to no writing skill at
all. That is preferable to maintaining another plugin's surface against an upstream we do not track,
for a skill peripheral to what this fork is for.

No `audit-delta` block: this entry declines to vendor a file rather than classifying a grafted one.

---

## Entry #21 — Declined vendoring: `writing-diataxis`, upstream unidentified

**Intent class: provenance.** The donor ships `skills/writing-diataxis/`. Unlike
`writing-clearly-and-concisely`, **no upstream reference to it was found** — not in superpowers
v6.4.0, and not as a cross-plugin reference in any donor file. Its true origin is unknown.

**Disposition: declined, and the gap recorded.** The skill is not ported. This entry exists so the
question is asked once and answered honestly: *we do not know where `writing-diataxis` came from.*
Twice now the provenance question has been deferred (see [the epic's design
spec](/work/2026-08-11-epic-graph-works-plugin-fork/01-design-spec.md), which records both);
recording the unknown is what stops a third deferral. Anyone who later wants the skill in this fork
must first establish its upstream, then add a normal vendoring entry here.

No `audit-delta` block, for the same reason as entry #20.

---

## Entry #22 — `auto-drive`/`finishing-relay` doc corrections, and `finishing-a-development-branch`'s trunk case

**Intent class: graph-works pipeline.** Three corrections found by following `skills/auto-drive/SKILL.md`
and `skills/finishing-relay/SKILL.md` as written and watching them fail during the 2026-08-15 auto-drive
run of `2026-08-11-epic-graph-works-core`, plus the trunk-case gap both finishing skills' own comparison
table already named: the escalation reply channel named in `auto-drive` §4.3/§4.4 (and its intro
paragraph) strands a blocked worker because `reply --id` only reaches a `dispatch:…` sender, not the bare
terminal handle an escalation is sent from; §3 step 3's submission probe forbade acting on the one signal
(sibling comparison) that reliably identified an unsent prompt during that run; and neither finishing
skill documented the case where an item's commits land directly on the merge target with nothing to
merge. `auto-drive` §3 also gained the `main` worktree-action mapping the work item's own comparison
table named but its numbered corrections had omitted — same file, same session, same drift.

**It carries no `file:` line.** `skills/finishing-a-development-branch/SKILL.md` is already claimed by
entry #4 (`state: patched`, harness-compat, ≤ 6 changed lines). `scripts/audit_delta.py:183` collects any
path named by more than one entry into `duplicated`, and a non-empty `duplicated` makes `just audit-delta`
non-ok — so a second `file:` claim on this path would turn it red. Entry #19 already establishes the
convention for exactly this situation (`skills/using-superpowers/SKILL.md`, cross-referenced to entry #7
rather than re-claimed): this entry follows it. `skills/auto-drive/SKILL.md` and
`skills/finishing-relay/SKILL.md` need no cross-reference at all — both are already claimed by entry #19
as ours-side additions with no upstream counterpart, and this patch is simply further editing of files
entry #19 already owns.

**What changed.**

- `skills/auto-drive/SKILL.md` — the intro paragraph now states the reply-channel asymmetry by message
  type instead of naming one channel unconditionally; §4.3 gains one sentence explaining *why*
  `reply --id` is correct for a `question` (its sender handle is `dispatch:…`); §4.4 is rewritten to name
  the working channel for an `escalation` (`orca orchestration send --to dispatch:<id>`) and where the
  dispatch id comes from (§2.1's live-derivation, joined on the sender terminal handle); §3 step 3's case
  3 is replaced with the sibling-comparison heuristic, with an explicit note that the heartbeat veto
  (case 1) and "never nudge an unprobed worker" (case 4) are unchanged; §3 gains the `main` worktree-action
  mapping (same as `reuse`), with the fork's `orchestrate`-module gap stated as known and accepted.
- `skills/finishing-relay/SKILL.md` — "Shared-epic-worktree case" is widened to "Trunk case" at R2's
  environment detection, the R4 merge-execution branch, and the R2 "carry forward" cross-reference, so the
  case covers a main-mode item that never had a dedicated branch, not only a shared epic worktree.
- `skills/finishing-a-development-branch/SKILL.md` — four sites, re-authored onto this fork's menu shape
  rather than cherry-picked from upstream (see "Why not a cherry-pick" below): Step 2's environment table
  splits the `GIT_DIR == GIT_COMMON` row by whether the current branch is the base branch; Step 3 gains an
  on-trunk check scoped to the normal-repo case, with its own stated reason (that configuration only
  arises under auto-drive, which routes the finish stage to `finishing-relay` instead of here — not "a
  worktree can never be on the base branch," which is false for a shared epic worktree); Step 4 gains a
  two-option on-trunk menu ahead of the existing three-option one, reusing Step 5's existing Option 2
  (Push and Create PR, detached-HEAD form) and Option 3 (Keep As-Is) verbatim — no new execution prose;
  Quick Reference gains two rows for the on-trunk choices.

**Why not a cherry-pick.** Upstream superpowers' `dd21f6da` documents the same on-trunk case in
`agent-research`'s copy of this skill, but that copy carries graph-wiki's on-trunk patch on an older
superpowers base, while the fork's copy carries superpowers' newer rewrite (three menu options instead of
four, Discard demoted to an explicit-request-only path, a "Common Rationalizations" table). The two
diverged in both directions since `dd21f6da` landed, so its diff does not apply; the trunk case is
re-authored onto the fork's current menu shape instead, reusing only the *mapping* — "push as new branch"
and "leave as-is" are the fork's own Option 2 and Option 3 — not `dd21f6da`'s literal text.

**Why not `convert-to-sibling`** (the disposition every entry in this ledger must assess, per entry #12's
precedent). The patch's `-` lines re-partition upstream's Step 4 menu on a branch condition: on-trunk gets
its own two-option menu presented *instead of* the standard three-option one, not beside it. A sibling
skill can add a checkpoint or options alongside `finishing-a-development-branch`; it cannot make upstream's
own Step 4 print a *different* menu when the current branch equals the base. Same disqualifier entry #12
records for `using-git-worktrees`.

**On merge.** `skills/auto-drive/SKILL.md` and `skills/finishing-relay/SKILL.md` have no upstream
counterpart (entry #19) — nothing to reconcile on a sync. `skills/finishing-a-development-branch/SKILL.md`
re-applies against entry #4's disposition: at merge time, re-locate these four sites against upstream's
current step numbering and re-express the on-trunk menu/table additions there, the same way entry #4 and
entry #12 already instruct for this file's other divergence.

---

## Entry #23 — the vendored test suites the gate runs

<!-- audit-delta
state: patched
file: tests/brainstorm-server/start-server.test.sh
file: tests/brainstorm-server/lifecycle.test.js
-->

<!-- audit-delta
state: verbatim
file: tests/hooks/test-session-start.sh
-->

**Intent class: gate.** Work item `2026-08-14-spike-vendored-plugin-test-gate` ruled that the
offline vendored suites that *execute code* become an enforcing part of `just check`
(`just test-plugin`). Eight assertions failed before that could happen. None was a defect: every one
was a vendored assertion contradicting a divergence this ledger already records as deliberate. Six
sit inside the gated set and are repaired here. The two in excluded files
(`test-fork-validation.sh`, `test-worktree-path-policy.sh`) are left as they are — both are
documentation greps over our own skill prose, and `plugins/SYNC.md` records them as written
non-gates.

**Three identity renames.** Squarely inside this ledger's governing rule — patch only the lines
identity requires:

- `tests/brainstorm-server/start-server.test.sh` (1 line) and
  `tests/brainstorm-server/lifecycle.test.js` (2 lines) looked for session state under
  `.superpowers/brainstorm`. Entry #11's patch to `skills/brainstorming/scripts/start-server.sh`
  renamed that directory to `.brainstorming`; these two files were not carried along with it.
- `tests/claude-code/test-user-gate-hooks.sh` (18 lines) set seven `SUPERPOWERS_*` user-gate
  environment variables that entry #9's patch renamed to `GRAPH_WORKS_*`. **The file did not report
  a failure** — it runs under `set -euo pipefail`, the first stale guard let the hook exit 2, and
  the script died after 5 of its 68 assertions with no summary. Only `hooks/examples/` was renamed;
  `SUPERPOWERS_ROUTING_GUARD` and `SUPERPOWERS_WORKFLOW_GUARD` are still the live names in the four
  native `hooks/*` files, and the tests driving those are untouched.

**One semantic rewrite, and it is not a rename.** `tests/hooks/test-session-start.sh` had three
assertions driving upstream's `docs/superpowers/model-routing.json` and expecting the per-tier
effort map to be emitted implicitly. Entry #8's patch replaced that config source with the
graph-works workspace projection and made the effort notice conditional — `hooks/session-start`
carries the comment *"Unlike upstream, this is NOT implicit"* at the site. The three assertions were
rewritten to assert *that*: the effort notice fires only when the projection sets `effort_map`, it
does not fire for routing alone, and upstream's `model-routing.json` on disk contributes nothing.

Recording this under the rename block would launder a behavior decision into a typo fix, which is
why it is called out separately here.

The rewrite stubs `uv` on `PATH` rather than building a real workspace, because it must: entry #8's
python block imports `workspace_io`, which is not vendored into this tree, so the real resolver
always errors and every notice branch is unreachable through it. The stub exercises the shell-side
notice construction — which is what entry #8 actually patched — and is deterministic regardless of
whether those packages ever land here. It does **not** cover workspace resolution, `config.json`
parsing, or the staleness hash; the test says so at the site. Closing that gap is
`2026-08-11-epic-feature-port-native-plugin-surface`'s to close, not this entry's.

**On merge.** All four are upstream test files and upstream edits them. Re-apply per file: for the
three renames, re-locate the identity strings against upstream's current text and re-substitute —
they are single tokens and should re-apply mechanically. For `test-session-start.sh`, expect a real
conflict whenever upstream touches its effort-notice tests, and resolve it the same way entry #8
resolves the hook itself: keep upstream's skeleton and helpers, re-express our three assertions
against our config source. If upstream ever adopts a conditional effort notice, this entry becomes
retirable — `just audit-delta` will say so by reporting the file as a *retired patch*.

**Reconciled onto obra `v6.3.0` (`b36e0829`) — C2, 2026-08-17.** `tests/hooks/test-session-start.sh`
is ours wholesale; verified a strict superset of obra's five assertions (obra's *Copilot CLI* case
survives under the label *non-Claude-Code SDK client*, same `COPILOT_CLI=1` env). The other three
files in this entry — `tests/brainstorm-server/start-server.test.sh`,
`tests/brainstorm-server/lifecycle.test.js`, `tests/claude-code/test-user-gate-hooks.sh` — were not
part of this reconcile's seventeen-file scope and are unchanged by it.

---

## Entry #24 — Codex distribution config in `sync-to-codex-plugin.sh`

<!-- audit-delta
state: patched
file: scripts/sync-to-codex-plugin.sh
-->

**Intent class: identity.** Work item `2026-08-17-epic-feature-codex-distribution-sync-verify` (C3)
stands up a real, git-clonable Codex distribution repo for *our* plugin using obra's own vendored
sync tool, unmodified in logic — only the two per-repo config constants the tool's own header
comment sanctions editing (`# Config — edit as upstream or canonical plugin shape evolves`), plus
one consequence and one cosmetic follow-on:

1. `FORK` → `psprowls/graph-works-codex-plugin` (our distribution repo, not obra's
   `prime-radiant-inc/openai-codex-plugins`).
2. `DEST_REL` → `.` — the fork repo *is* the plugin. obra nests its plugin at
   `plugins/superpowers/`; the rebase spike proved Codex only recognises
   `.codex-plugin/plugin.json` at the marketplace root and does not recurse into subdirectories
   (`evidence/codex.md`, Attempt 2).
3. `RSYNC_ARGS` → dropped `--delete-excluded`. **Required by edit 2**, not independently chosen.
   With `DEST_REL="."` the apply-phase rsync's destination is the fork clone's repo root, and
   `/.git/` is in `EXCLUDES`, so `--delete-excluded` deletes the destination's `.git` mid-run —
   after which `git status --porcelain "."` fails silently inside `$( )` and the script reports "No
   changes" and exits 0 with no PR, a silent false success. `--filter='P /.git/'` is not a usable
   guard here: macOS ships openrsync (2.6.9-compatible), which ignores protect rules.
4. `SYNC_BRANCH` prefixes → `graph-works` instead of the hardcoded `superpowers` (cosmetic; keeps
   PR branch names legible).

**On merge.** All four edits sit inside the file's own sanctioned config block or are a direct
consequence of it (edit 3 of edit 2). Re-apply all four on the next obra sync; edit 3 must not be
dropped independently of edit 2. Nothing else in the file changed — `EXCLUDES`, the preflight, and
the apply path are all upstream verbatim.

---

## Entry #25 — the retired user-gate surface, and one file that never diverged

<!-- audit-delta
state: removed
file: commands/gate-check.md
file: commands/specify-gate.md
file: hooks/examples/post-agent-return-validate.sh
file: hooks/examples/pre-agent-task-dispatch-validate.sh
file: hooks/examples/pre-task-blockedby-enforce.sh
file: hooks/examples/stop-deflection-guard.sh
file: hooks/examples/stop-revalidate-user-gates.sh
file: skills/shared/task-format-reference.md
file: tests/claude-code/test-user-gate-hooks.sh
-->

<!-- audit-delta
state: verbatim
file: hooks/hooks-cursor.json
-->

**Why this entry exists.** Eight of these nine files were dropped from the tree with the dormant
user-gate hook surface (spike D2's drop list), and the tenth is the vendored suite that covered them.
Their old entries — #3 (Tier B identity), #9 (`post-task-complete-revalidate.sh`) and #23 (the gated
test suites) — kept `state: patched` claims on all of them, so `just audit-delta` reported ten retired
patches every run. The prose in those entries stays where it is: it is the history of what the files
were and why they went.

**Why they are named here rather than simply deleted from the ledger.** The coverage check reads the
*grafted base* (`v5.5.0` under `commands/ hooks/ skills/`), not the current tree. A grafted file is
grafted forever, so removing its `file:` line converts a false "retired patch" into an equally false
"unclassified grafted file". `state: removed` is the state added for exactly this: it names the file,
covers it, and claims nothing. It was introduced with this entry — see `scripts/audit_delta.py`.

**`hooks/hooks-cursor.json` is different, and is `verbatim` for a different reason.** It is still in
the tree; it simply does not diverge from obra `v6.3.0`. Entry #19 listed it among the ours-side
additions, which was true against pcvelz and is not true against obra — obra ships the same file. No
patch was lost here and none is wanted.

**Six of these patches were dropped by the obra `v6.3.0` re-base and restored on 2026-08-20.** None
had been adopted upstream; all six were reported as retired patches by `just audit-delta` and written
off under the old step-3 wording in `SYNC.md`, which called a retired patch "the good news case"
outright. That wording is now corrected. Restored: `hooks/run-hook.cmd` (`bash -l` on all three
Windows dispatch lines), `skills/systematic-debugging/root-cause-tracing.md`
(`${CLAUDE_PLUGIN_ROOT}` script path), `skills/finishing-a-development-branch/SKILL.md` (the on-trunk
menu, the `resolve-workspace.sh` lookup, and `<workspace>/worktrees/` in the provenance check),
`skills/brainstorming/spec-document-reviewer-prompt.md` (the spec artifact path),
`skills/brainstorming/scripts/frame-template.html` and
`skills/brainstorming/scripts/stop-server.sh` (identity strings).

`finishing-a-development-branch/SKILL.md` was re-applied as a *merge*, not a revert: obra added its
own "removal refused" flow to the same file, and that is kept intact alongside our on-trunk work.

**`skills/test-driven-development/SKILL.md`'s patch stays dropped, by decision.** Its Code-Change
Gate section went the same way as the rest, but `13440e14` had already reframed the gate out of
`executing-plans` and `subagent-driven-development`, so restoring it here would reintroduce framing
the fork deliberately moved away from. Recorded as `removed` rather than left as a standing finding.
Note the consequence: nothing in `test-driven-development/SKILL.md` now routes to
`graph-works:using-git-worktrees` at all.

**`tests/hooks/test-session-start.sh` is verbatim again — 2026-08-20.** Its patch existed to cover a
notice pipeline the hook no longer has. Once `hooks/session-start` came back to upstream's file plus
two identity strings, the only divergence left in the suite was a rename of one variable and one test
description (`copilot_home` -> `sdk_home`, "Copilot CLI emits…" -> "non-Claude-Code SDK client
emits…") — no behavioral difference, upstream's naming arguably the more accurate of the two, and a
permanent conflict site bought for nothing. Upstream's suite was restored verbatim and passes against
our hook unchanged, all six assertions.

A retirement guard asserting the removed notice tags stay removed was written and then dropped with
it: keeping it would have re-diverged the file to protect against a reconstruction that entry #8
already forbids in prose. The delta is zero here now, which is the better guarantee.

**`tests/pi/test-pi-extension.mjs` — kept, gated, and an upstream-contribution candidate
(2026-08-20).** Our divergence is one added test plus its two supporting lines (`rename` in the
imports, the `bootstrapSkillPath` const): *"context skips bootstrap injection when the bundled skill
cannot be read"*. Nothing in it is fork-specific — upstream's extension, upstream's path (entry #7
deliberately moved our bootstrap skill onto upstream's own path), upstream's fail-open behavior — so
it is squarely a contribution candidate under entry #4's rule, and accepting it upstream retires this
line.

It was reviewed for deletion on the same reasoning that returned `tests/hooks/test-session-start.sh`
to verbatim, and kept for the opposite reason: what it covers is a *silent fail-open*, and a silent
fail-open in `hooks/session-start` is what hid four dead notice branches and a broken
`workspace_io` import for an entire release cycle. The arrangement it was found in — kept but
unreachable by any runner, since upstream's `package.json` declares no scripts and nothing else named
the directory — was the one arrangement not worth defending. `tests/pi` is now part of
`just test-plugin`, so the test either passes or fails the gate.
