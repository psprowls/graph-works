# Dispatch rules

One resolved profile selects the skill, interaction mode, prompt tail, agent,
model, and launch reasoning effort for a stage. Work tracker still selects the
stage and variant and owns all transitions. `gw work next` and `gw work
orchestrate` use the same resolver and item attributes.

## Files and ownership

```yaml
# workspace.yaml
version: 1
workflow:
  dispatch_rules: dispatch.yaml
  auto_drive:
    max_parallel: 6
    supervise_merges: true
```

The shared/local manifest read seam selects `workflow.dispatch_rules`. The
nonblank path is relative to the workspace root; absolute paths are supported.
The selected shared file must exist and contain a YAML mapping. Its optional
local sibling inserts `.local` before the extension: `dispatch.local.yaml`
or, for `team/rules.yml`, `team/rules.local.yml`. A missing local file adds no
rules; an empty, unreadable, or malformed existing file is an error.

```yaml
# dispatch.yaml
pipeline:
  attributes: [stage, variant, type, effort, blast_radius, has_spec, has_plan]
  rules:
    - name: shared-design
      match: {stage: design}
      agent: claude
      model: your-provider-model-id
      reasoning_effort: your-supported-effort
    - name: relay-decision
      match: {variant: branch}
      prompt_tail: >-
        Auto-drive context: relay the merge/PR/hold/discard decision to your
        coordinator rather than asking interactively; merge target is {merge_target}.
```

```yaml
# dispatch.local.yaml
pipeline:
  rules:
    - name: local-execution
      match: {stage: execute, effort: [xtra-small, small]}
      agent: codex
```

Model and reasoning-effort values above are placeholders. Both are opaque
nonblank strings; Orca and the provider validate support and account access.
Omit them to use the selected agent's existing defaults. Never infer a model
from a work-item effort such as `small`.

Rules are appended: packaged defaults, shared rules in file order, then local
rules in file order. Empty or absent local `rules` adds nothing; `rules: null`
is invalid. Rule names are labels, not merge identities. Repeated names never
replace entries. This append behavior is specific to dispatch rules; other
workspace lists retain ordinary replacement semantics. Reads and projections
never write the combined list into either source file. Programmatic writes use
`plan_dispatch_write(..., layer="shared" | "local")` and
`apply_dispatch_write(plan)` with validation and source-fingerprint guards.

## Matching and fields

`match` is required. `{}` matches everything. Entries combine with AND; a
scalar means equality and a nonempty list means membership. Missing item
values fail constrained matches. Null constraints, unknown values, and string
booleans are errors. There are no expressions, regexes, or negation.

| Attribute | Values |
| --- | --- |
| `stage` | `design`, `plan`, `execute`, `finish` |
| `variant` | `exploration`, `diagnosis`, `reconcile`, `epic-design`, `decompose`, `single`, `planned`, `unplanned`, `branch` |
| `type` | `Bug`, `Feature`, `TechDebt`, `Spike`, `TestGap`, `Epic`, `Release` |
| `effort` | Work-item estimate: `xtra-small`, `small`, `medium`, `large`, `xtra-large` |
| `blast_radius` | `file`, `package`, `domain`, `system` |
| `has_spec`, `has_plan` | YAML booleans from the authoritative artifact state |

An omitted `attributes` declaration enables the full vocabulary. An explicit
declaration selects a subset; rules cannot match undeclared attributes or
invent new ones. Every supplied rule is shape-validated even if it never matches.

| Profile field | Meaning and validation |
| --- | --- |
| `skill` | Nonblank bare name or `plugin:skill`; availability is checked by the workflow harness |
| `mode` | `attend`, `autonomous`, or `relay`; relay requires a nonblank final tail |
| `prompt_tail` | Appended stage instructions; null removes the tail |
| `agent` | `claude` or `codex`; permissions come from this agent's existing settings |
| `model` | Opaque model ID; null selects the agent default |
| `reasoning_effort` | Opaque launch effort, sent as Orca `--effort`; null uses defaults; a non-null value requires a model |

A rule must set at least one profile field. `permission_mode`, `backend`, and
all other unknown fields are rejected. Python model-pool `roles.*`, including
`roles.*.model_id`, remain a separate unchanged configuration surface.

Later matching values win independently per field. Omission preserves the
inherited value. Null is allowed only for model, reasoning effort, and tail;
null agent/skill/mode is invalid even if a later rule could repair it.

An actual agent change clears the inherited model and reasoning effort before
applying every field in that rule, independent of YAML key order:

```yaml
pipeline:
  rules:
    - match: {}
      agent: claude
      model: your-provider-model-id
      reasoning_effort: your-supported-effort
    - match: {stage: execute}
      agent: claude             # same agent: preserves model and effort
    - match: {stage: execute}
      agent: codex              # agent change: clears both
    - match: {stage: execute}
      agent: claude             # changing back does not resurrect either
    - match: {stage: execute}
      model: null
      reasoning_effort: null    # explicit reset, including provenance
```

Clearing only model while retaining effort is invalid: `Set a model or clear
reasoning_effort.` Final validation happens after the cascade, so later rules
may supply a required model. Malformed required fields fail during parsing.

All nine variants have packaged defaults: Claude with model/effort unset;
exploration/diagnosis/epic-design attend, branch relays, and the others run
autonomously. The branch tail is workspace-owned; initialization seeds it in
the shared file. Execution variants retain the coverage-report obligation.

## Explanation, freshness, and launch accounting

`gw work next <path> --json` exposes `dispatch.profile` plus per-field
`dispatch.provenance`. Orchestration dispatches expose the same fields and
provenance. Each origin names `packaged` or the absolute filename, zero-based
rule index, optional name, and reason: `set`, `explicit-null`, or `agent-change`.
Human output explains agent/model/effort and resets. Attended workflow reports
this profile; it does not change the current session's agent or model.

Run `gw config sync --workspace /path/to/workspace` after external edits.
The derived `.gw/cache/config.json` includes ordered custom rules and
`_meta.dispatch_inputs` fingerprints for both manifests and both dispatch
files. Existence and content hashes detect optional-file creation/removal and
unchanged-mtime edits. The routing hook requests a sync for missing or stale
metadata; it never evaluates rules. Invalid input leaves the previous projection
intact. Never hand-edit the cache.

Both Python Orca and the plugin launch recipe persist `GW_LAUNCH_V1` plus the
unchanged prompt before starting a worker. The envelope freezes agent, model,
reasoning effort, dispatch key, and exact placement argv. They compare explicit
choices against both `launch.requested` and `launch.effective` (`effort` is the
receipt field). Null preferences make no claim about provider-resolved defaults.
Missing or mismatched evidence remains unverified, with task/dispatch identity
retained. A successful completion needs durable proof before ack/release.

Retries read the original full task spec after restart, rather than resolving
edited rules. Recovery must authorize retry and identify placement; a retry may
reuse an already allocated worktree. Ambiguous starts never justify a duplicate
worker or a second allocation. `worker_done` owns Orca settlement. Terminal
access is optional; orchestration handles questions, replies, and completion.

## Initialization and explicit cutover

`gw bootstrap` creates a missing reference and shared dispatch document with
the relay rule, and adds `/dispatch.local.yaml` to the workspace root ignore.
It preserves authored dispatch files and other manifest values, and is
idempotent. It creates no local dispatch file unless an explicit local write
requests it. Alternate locations report their local filename and required
ignore entry; init does not edit another repository's ignore file. Alias/merge
shapes that cannot safely accept a narrow reference insertion require an
explicit edit.

Legacy keys are refused in **each explicit manifest layer**, including null or
empty values hidden by an overlay. There is no translator or compatibility
reader. Perform this cutover only for the workspace you intend to change:

1. Save the current `workspace.yaml`, `workspace.local.yaml` (if present), and
   custom routing values for review. Keep `roles.*`, repositories,
   `workflow.auto_drive.max_parallel`, `workflow.auto_drive.supervise_merges`,
   and unrelated settings.
2. Remove these four retired keys from each layer where present:
   `workflow.pipeline`, `workflow.auto_drive.models`,
   `workflow.auto_drive.overrides`, `workflow.auto_drive.permission_mode`.
   For example, run `gw config unset workflow.pipeline --workspace /path/to/workspace`;
   add `--local` for the local manifest. Repeat for each present key. The
   removal-only path intentionally defers projection refresh until cleanup is complete.
3. Run `gw bootstrap --workspace /path/to/workspace`. This initializes the new
   shared file/reference and ignore entry; it does not translate old choices.
   Recreate the desired rules in `dispatch.yaml` and optional
   `dispatch.local.yaml`, retaining a branch relay tail when using relay mode.
4. Run `gw config sync --workspace /path/to/workspace`, then inspect
   `gw work next <path> --workspace /path/to/workspace --json` and
   `gw work orchestrate <path> --workspace /path/to/workspace --json` before launch.

Changing source code or installing this version never performs this live
configuration cutover automatically.
