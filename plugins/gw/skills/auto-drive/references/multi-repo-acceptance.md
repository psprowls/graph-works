# Multi-repository native acceptance

Use disposable repositories and a disposable workspace. Record every Orca
repository/worktree identity as it is created; cleanup must use only that list.
Never use a production branch or real work item for this procedure.

1. Initialize two Git repositories with a main branch and one tiny executable
   check each. Declare them by name in a fresh workspace. Create one execute
   Epic and two execute-ready children, each with its repository and affects.
2. Read `gw work orchestrate <epic> --json`. Keep top-level repo as root
   metadata; inspect each dispatch's repo independently. Process each deferred
   preparation serially with the launch adapter's `prepare` command. Verify
   native repository, branch, parent and base; record at the preparation's
   owner phase. Verify scalar own stamp and foreign map stamp, then replan.
3. Verify each child has its repository-local anchor as parent, base and merge
   target. Create native child worktrees, observe and record actual placement
   (Orca may sanitize branch names). Run each declared check before merging
   that child's commit into its own anchor. Mark fixture children terminal and
   owner finish; distinguish these fixture transitions from worker execution.
4. Read next and orchestrate: both must expose the same deterministic target
   list. Integrate only the first target. Run `finish-receipt.py record` for
   that repository; `inspect` must show incomplete proof naming the other.
   Attempt advance with `--no-infer-worktree`; verify refusal and persisted
   owner still at finish, not merely the proposed phase in the JSON response.
5. Restart the controller/helper process. Inspect again without remerging the
   first repository. Check and integrate the second target, record it, then
   inspect complete evidence. Advance exactly once; verify done/resolved and
   own-repository scalar resolved_in. Repeating advance must not change state.
6. Preserve JSON observations and receipt evidence, then remove only recorded
   native worktrees and repository setups. Remove the temporary fixture roots
   after their evidence is retained. Do not remove unrelated Orca resources.

For a source checkout, run the adapters from any working directory with the
explicit project environment (replace all placeholders):

```bash
python3 <plugin>/skills/auto-drive/references/launch-worker.py prepare \
  --plan-file <plan.json> --owner <owner_path> --repo-name <name> \
  --workspace <workspace>
uv run --project <source-checkout> --package graph-works-core python \
  <plugin>/skills/finishing-relay/references/finish-receipt.py record \
  <owner_path> --workspace <workspace> --repo <name>
uv run --project <source-checkout> --package graph-works-core python \
  <plugin>/skills/finishing-relay/references/finish-receipt.py inspect \
  <owner_path> --workspace <workspace>
```

The same receipt procedure applies to attended and relay finishes. A PR,
hold, or discard does not count as integration and must never advance. There
is no cross-repository atomic merge: already verified receipts remain useful
if a later target needs repair. Native Windows and installed-runtime checks
must be recorded separately from source and fake-Orca tests.

## Recorded execution, 2026-09-23

Passed on macOS with Orca 1.4.209 using two new repositories and four native
worktrees. Preparation created own and foreign anchors, safely normalized
branch names, persisted scalar/map stamps and replanned. Both children had
verified repository/parent/base/merge-target placement. Each tiny Python
module check passed before its fast-forward merge into its anchor.

After the code anchor merged into main, receipt inspection remained incomplete
for UI. Real advance refused `finish-incomplete`; persisted owner remained
in-progress/finish. Fresh-process inspection returned identical proof. After
UI's check, merge and record, inspection verified both entries. One advance
resolved the owner, and the repeat refused terminal state. Own code result:
`a89a4877ab85fbeb95a2318eeadb95d448433ef4`; UI result:
`ee61829631fb58dcc4f32c45bf60607f9042c730`. All four native worktrees were removed
and both temporary repository setups unregistered using the public CLI.

Execution evidence is retained in the implementation ledger at
`.superpowers/sdd/02-plan/host-acceptance.md`, `host-acceptance.json` and
`host-evidence/`. This exercised native creation/placement and real Git/core
lifecycle; no worker agent was launched. Child terminal transitions were
fixture edits. Native Windows and live installed-package adapter runtime
remain unverified; automated source-project runtime coverage includes an
external working directory.
