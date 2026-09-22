---
name: onboard
description: Invoked explicitly as /gw:onboard. Locates or creates the graph-works workspace, then configures it — one guided walk covering workspace location, topic, the repository it catalogs, and optional session transcript capture. Writes the workspace manifest and repo settings through `gw`; explicit invocation only, never on inference.
---

# Onboard a graph-works workspace

One command for one job: find the workspace or create it, then turn on the optional
features it supports. Ask with AskUserQuestion, then immediately apply the answer by
running the matching `gw` command — never edit a workspace or config file directly.
All writes land in the committed workspace manifest (`<root>/workspace.yaml`), the
workspace itself, or the repo's `.claude/settings.local.json`; `gw` is the sole writer.

## Ground rules

- Run `gw` from the repo. `gw` being on PATH doesn't prove it's this repo's build —
  a stale entry point can own the name. Verify identity, not presence: `gw util
  describe-surface --json >/dev/null 2>&1 || echo "gw is not graph-works-cli —
  use: uv run --package graph-works-cli gw ..."`.
- When step 0 finds no workspace, Q1–Q3 and the apply step are required — that's the
  creation path, not a toggle. The feature questions after it (Q4) are optional: "No"
  runs nothing and moves on.
- If a `gw` command errors, show the user the exact stderr and stop that step — do
  not improvise file edits.
- NEVER commit anything. `gw` writes working-tree files only.

## Step 0: Locate the workspace

The workspace resolves in this order: an explicit `--workspace` path, then the
`GRAPH_WORKS_DIR` environment variable, then a `.git` walk-up from the current
directory, defaulting to `<repo>/.works`. An out-of-repo workspace has no repository of
its own until Q3b creates one.

Run:

```
gw config list
```

- **It succeeds** — a workspace already exists. Say that creation is already
  satisfied, and skip straight to Q4 — past Q1–Q3 and past the apply step, which
  exist only to create one. Do not re-create anything.
  `gw config list` does not report the resolved root: `workspace.dir` names a path
  only when it is tagged `[env]`, meaning `GRAPH_WORKS_DIR` is set. Report that path
  when it appears; otherwise say a workspace was found without naming one — never
  guess or re-derive it.
- **It errors** with `no workspace.yaml here, so this is not a graph-works workspace` —
  the error names the exact path it resolved. Report that path and continue to Q1.

## Q1: Where should the workspace live?

AskUserQuestion: "Where should the graph-works workspace live?"
- "`<repo>/.works` (recommended)" → the default; no `--workspace` flag needed
- "Somewhere else" → ask for the path, in-repo or out-of-repo, and pass it as
  `--workspace "<path>"`

Both shapes are supported; Q3 is what makes an out-of-repo workspace work.

## Q2: What is this workspace about?

AskUserQuestion: "What topic should this workspace carry?" — a short display name,
e.g. the repo name or "platform monorepo". This becomes `--topic`.

## Q3: Which repository does this workspace catalog?

AskUserQuestion: "Which repository should this workspace catalog?"
- Default: the repository the `.git` walk-up finds from the current directory.
- Otherwise: ask for the path.

Asked explicitly rather than inferred. When the workspace lives outside the code it
catalogs, the walk-up from the *workspace* resolves to the workspace's own repository,
not to the code — and the workspace is then born declaring no repositories at all, so
`gw scan` finds nothing. The answer becomes `--repo-root "<path>"`.

## Preview, then apply

Show the plan before writing anything:

```
gw bootstrap --topic "<topic>" --repo-root "<repo path>" --dry-run
```

Add `--workspace "<path>"` when Q1 gave a non-default location. The command writes
nothing and prints the directories and files it would create. Expect repeats:
each installer previews the bundle scaffold independently, so `index.md`, `log.md`
and `tags.yaml` are listed once per installer. That is the plan reported
faithfully, not a fault — say so rather than letting it read as one.

Show that output, confirm with the user, then run the same command **without**
`--dry-run` to apply it. Report the resulting workspace path.

## Q3b: Workspace repository

Runs only on the creation path (the same gate as Q1–Q3; a found workspace skips it).

1. Run `git -C "<workspace path>" rev-parse --is-inside-work-tree`.
2. Success — the workspace is already inside a git repository. Say so and move on.
3. Failure — AskUserQuestion: "Initialize a local git repository in the workspace?"
   - "Yes" → run `git init "<workspace path>"`. Local only: no remote, no commit.
   - "No" → run nothing; note that workspace edits will be untracked.

The closing report lists the `git init` line verbatim when it was run, and lists the
step as skipped otherwise. This is the only git-affecting step in this skill; the
Ground rules' "NEVER commit anything" still holds — an init is not a commit.

## Q4: Session transcript capture

One-line intro: a SessionEnd hook copies the session transcript into the active work
item's directory whenever `gw work advance` has stamped an active-work pointer.

AskUserQuestion: "Enable session transcript capture for the active work item?"
- "Yes" → run `gw config hooks enable transcript`
- "No" → run nothing

`transcript` is the whole hook-feature vocabulary — `HookFeature` in
`graph_works_cli.config_cli` declares that one value and `HookWiring.for_feature`
refuses any other. Off-switch: `gw config hooks disable transcript`.

## Closing

Report in one short block:

- The exact `gw` command lines run — copy them verbatim.
- The settings file paths those commands printed.
- Any step skipped.
- The off-switch: `gw config hooks disable transcript`.
- `gw config list` as the way to review every setting later.

Then offer the settings this walk deliberately does not ask about, as
copy-pasteable lines. They all have working defaults, and walking them would be
roughly sixty prompts:

```
# Where things live inside the workspace (defaults are right for nearly every workspace).
# `config_dir` is the control plane and the .gitignore anchor; `cache_dir` and
# `worktrees_dir` derive from it when unset, so setting `config_dir` alone moves
# the whole control plane as a unit.
gw config set layout.bundle_dir okf
gw config set layout.config_dir .gw
gw config set layout.cache_dir .gw/cache
gw config set layout.worktrees_dir .gw/worktrees

# Shared dispatch rules (default reference; optional sibling: dispatch.local.yaml)
gw config get workflow.dispatch_rules
# Edit pipeline.rules in the referenced document or its local sibling, then:
gw config sync

# The auto-drive shell
gw config set workflow.auto_drive.max_parallel 2

# Per-role model overrides for the Python subagent pool
gw config set roles.<role>.model_id <model-id>
gw config set roles.<role>.backend <backend>
gw config set roles.<role>.region <region>
gw config set roles.<role>.max_tokens <n>
gw config set roles.<role>.max_concurrency <n>
```

Dispatch rules select skill, mode, prompt_tail, agent, model and reasoning_effort
with a `match` mapping (for example, `{variant: planned}`). Shared rules apply
first; `dispatch.local.yaml` appends machine-local rules for the default path.
For a custom reference, use its `.local` sibling. Permissions remain the selected agent's existing settings;
configure them in that agent's settings. See
`../auto-drive/references/dispatch-configuration.md` for the dispatch guide.

Do not commit. Do not re-ask any question.

## Command

- `gw bootstrap --topic "<topic>" [--workspace "<path>"] [--repo-root "<path>"] [--dry-run] [--json]`
- `gw config hooks enable transcript` / `gw config hooks disable transcript`
- `gw config list`, `gw config set <key> <value>`

## Reference

→ `../graph-works/SKILL.md`
