# Dispatch rules

One resolved profile selects the skill, interaction mode, prompt tail, agent, model, and launch reasoning effort for a stage. The stage comes from the item's resolved pipeline path (`pipeline.path`, below); work tracker owns every transition through a stage table fixed in code. `gw work next` and `gw work orchestrate` use the same resolver and item attributes.

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

The shared/local manifest read seam selects `workflow.dispatch_rules`. The nonblank path is relative to the workspace root; absolute paths are supported. The selected shared file must exist and contain a YAML mapping. Its optional local sibling inserts `.local` before the extension: `dispatch.local.yaml` or, for `team/rules.yml`, `team/rules.local.yml`. A missing local file adds nothing; an empty, unreadable, or malformed existing file is an error.

```yaml
# dispatch.yaml
pipeline:
  attributes: [stage, type, effort, blast_radius, has_spec, has_plan, spec_stale]
  rules:
    - name: shared-design
      match: {stage: design}
      agent: claude
      model: your-provider-model-id
      reasoning_effort: your-supported-effort
    - name: relay-decision
      match: {stage: finish}
      prompt_tail: >-
        Auto-drive context: relay the merge/PR/hold/discard decision to your
        coordinator rather than asking interactively; merge target is {merge_target}.
  path:
    - name: features-skip-plan
      match: {type: Feature}
      stages: [design, execute, finish]
```

```yaml
# dispatch.local.yaml
pipeline:
  rules:
    - name: local-execution
      match: {stage: execute, effort: [xtra-small, small]}
      agent: codex
```

Model and reasoning-effort values above are placeholders. Both are opaque nonblank strings; Orca and the provider validate support and account access. Omit them to use the selected agent's existing defaults. Never infer a model from a work-item effort such as `small`.

Rules are appended: packaged defaults, shared rules in file order, then local rules in file order. Empty or absent local `rules` adds nothing; `rules: null` is invalid. Rule names are labels, not merge identities. Repeated names never replace entries. This append behavior is specific to dispatch rules and path rules; other workspace lists retain ordinary replacement semantics. Reads and projections never write the combined list into either source file. Programmatic writes use `plan_dispatch_write(..., layer="shared" | "local")` and `apply_dispatch_write(plan)` with validation and source-fingerprint guards.

## Matching and fields

`match` is required. `{}` matches everything. Entries combine with AND; a scalar means equality and a nonempty list means membership. Missing item values fail constrained matches. Null constraints, unknown values, and string booleans are errors. There are no expressions, regexes, or negation.

### Match attributes

| Attribute | Values | Meaning |
| --- | --- | --- |
| `stage` | `design`, `plan`, `execute`, `finish` | The stage about to dispatch; not allowed in a path rule's match |
| `type` | `Bug`, `Feature`, `TechDebt`, `Spike`, `TestGap`, `Epic`, `Release` | Work-item type |
| `effort` | `xtra-small`, `small`, `medium`, `large`, `xtra-large` | Work-item estimate |
| `blast_radius` | `file`, `package`, `domain`, `system` | Work-item blast radius |
| `has_spec`, `has_plan`, `spec_stale` | YAML booleans | From the authoritative artifact state; `spec_stale` is true when a sibling with overlapping `affects` landed since the item's spec baseline |

An omitted `attributes` declaration enables the full vocabulary. An explicit declaration selects a subset; rules cannot match undeclared attributes or invent new ones. Every supplied rule is shape-validated even if it never matches.

### Profile fields

| Profile field | Meaning and validation |
| --- | --- |
| `skill` | Nonblank bare name or `plugin:skill`; availability is checked by the workflow harness |
| `mode` | `attend`, `autonomous`, or `relay`; relay requires a nonblank final tail |
| `prompt_tail` | Appended stage instructions; null removes the tail |
| `agent` | `claude` or `codex`; permissions come from this agent's existing settings |
| `model` | Opaque model ID; null selects the agent default |
| `reasoning_effort` | Opaque launch effort, sent as Orca `--effort`; null uses defaults; a non-null value requires a model |

A rule must set at least one profile field. `permission_mode`, `backend`, and all other unknown fields are rejected. Python model-pool `roles.*`, including `roles.*.model_id`, remain a separate unchanged configuration surface.

Later matching values win independently per field. Omission preserves the inherited value. Null is allowed only for model, reasoning effort, and tail; null agent/skill/mode is invalid even if a later rule could repair it.

An actual agent change clears the inherited model and reasoning effort before applying every field in that rule, independent of YAML key order:

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

Clearing only model while retaining effort is invalid: `Set a model or clear reasoning_effort.` Final validation happens after the cascade, so later rules may supply a required model. Malformed required fields fail during parsing.

## Packaged rules

Packaged defaults are ten attribute-matched rules, folded before shared and local rules. Later matches win, so each specific rule follows the general rule for its stage: `has_spec` and `spec_stale` beat type, and type beats the general rule. Every packaged rule selects Claude with model and reasoning effort unset.

| Name | Match | Skill | Mode | Tail |
| --- | --- | --- | --- | --- |
| `design` | `{stage: design}` | `superpowers:brainstorming` | `attend` | `ATTEND_TAIL` |
| `design-bug` | `{stage: design, type: Bug}` | `superpowers:systematic-debugging` | `attend` | `ATTEND_TAIL` |
| `design-parent` | `{stage: design, type: [Epic, Release]}` | `gw:epic-design` | `attend` | `ATTEND_TAIL` |
| `design-reconcile` | `{stage: design, has_spec: true}` | `gw:reconciling-spec` | `autonomous` | `WORKSPACE_COMMIT_TAIL` |
| `plan` | `{stage: plan}` | `superpowers:writing-plans` | `autonomous` | `WORKSPACE_COMMIT_TAIL` |
| `plan-parent` | `{stage: plan, type: [Epic, Release]}` | `gw:planning-epics` | `autonomous` | `WORKSPACE_COMMIT_TAIL` |
| `plan-reconcile` | `{stage: plan, spec_stale: true}` | `gw:reconciling-spec` | `autonomous` | `WORKSPACE_COMMIT_TAIL` |
| `execute` | `{stage: execute}` | `superpowers:test-driven-development` | `autonomous` | `EXECUTE_TAIL` |
| `execute-planned` | `{stage: execute, has_plan: true}` | `superpowers:subagent-driven-development` | `autonomous` | `EXECUTE_TAIL` |
| `finish` | `{stage: finish}` | `superpowers:finishing-a-development-branch` | `relay` | — |

Tail names are constants in `graph_works_core.workspace.pipeline`. The finish tail is workspace-owned: initialization seeds it in the shared file as a `{stage: finish}` rule, and relay mode requires a nonblank final tail. The execute tail carries the coverage-report obligation, the deferral sentence, a context-hygiene paragraph (no hand-tailing of gate logs, artifacts handed to subagents as files, skills read once) and the gate instructions.

## Pipeline path

`pipeline.path` says which stages an item walks. Packaged path rules, in fold order:

| Name | Match | Stages |
| --- | --- | --- |
| `default` | `{}` | `[design, plan, execute, finish]` |
| `small-bug-like-skips-plan` | `{type: [Bug, TechDebt, TestGap], effort: [xtra-small, small]}` | `[design, execute, finish]` |
| `testgap-enters-at-plan` | `{type: TestGap}` | `[plan, execute, finish]` |
| `small-testgap-enters-at-execute` | `{type: TestGap, effort: [xtra-small, small]}` | `[execute, finish]` |

A path rule has an optional `name`, a required `match` with the dispatch-rule match semantics above, and required `stages`. `stages` is non-empty, an ordered subset of `design, plan, execute, finish` in that order, and ends in `finish`; `done` is implicit. `stage` is refused in a path match because it is routing's output, not its input.

Path rules fold like dispatch rules: packaged, then shared, then local, in file order. The last matching rule wins and its `stages` replace the earlier result outright. `path: []` adds nothing; `path: null` is invalid.

The resolved path drives routing. An item enters at the first stage of its path. A stage absent from the path never dispatches, never gates, and its dispatch rules never fire for that item. A phase off the path is reported as `phase-off-path`. Run `gw work advance <path> --from <observed-phase>` to move it to the next on-path stage, then re-run `next`. This repair does not complete the skipped stage, require its artifact, or run its completion gate; normal guards apply when the destination stage runs. Orchestration schedules the same guarded advance with `mode: repair`, without placement stamps. A decomposing type (`Epic`, `Release`) whose path lacks `plan` is refused as `path-invalid`: decomposition is a stage-table property, not a path choice.

Effort-required is derived from the path rules. When the item leaves `effort` or `blast_radius` unset and the rules those values could select disagree on the stage to dispatch now, routing blocks with `effort-required` (or `attribute-required`) and lists the candidate paths. When the candidates agree on the stage to dispatch now but disagree on the next one, the stage dispatches and `on_complete.phase` is `null`, with `path_candidates` listed. Rules that agree across the unset attribute never block.

Example: with the `features-skip-plan` rule above, a `Feature` walks `design`, `execute`, `finish`. `gw work next` on an unstarted Feature dispatches `design` with `on_complete.phase: execute`; a Feature already at `phase: plan` is repaired to `execute`; the packaged `plan` rules never fire for it.

## Stage artifacts

`pipeline.artifacts` names the file each stage leaves under the item's `references/` directory. Packaged entries:

| Stage | File | Source | Required |
| --- | --- | --- | --- |
| `design` | `01-design.md` | `design` | `true` |
| `plan` | `02-plan.md` | `plan` | `true` |
| `execute` | `03-execute-coverage.md` | `execute-coverage` | `false` |

Keys are `design`, `plan` and `execute` only. Each entry takes `file` (required) and `required` (optional boolean, default `true`). `file` is a bare basename ending `.md`, with no `/` or `\`, not starting with `.`, unique across stages, and not another managed artifact's filename. `required: false` means the artifact is reported but does not gate, as the packaged execute coverage file is. An absent optional artifact is not registered as a source and does not create a plan-table link. `source` is refused: source ids are fixed because `gw:ingest`, the workflow skill's terminal handling and the design-source check read them by id.

A layer's entry for a stage replaces the lower layer's entry for that stage outright, `required` included, so overriding only `file` on `execute` makes coverage gating. `artifacts: {}` adds nothing; `artifacts: null` is invalid.

```yaml
# dispatch.local.yaml
pipeline:
  artifacts:
    design: {file: spec.md}
```

## What is not configurable

The stage vocabulary (`design`, `plan`, `execute`, `finish`, `done`) and the stage table are code: each stage's entry and completion side effects, which stages are read-only and which record results, which require a ledger, child gating for decomposing types, and the `--return` target. A path chooses among stages; it cannot add one or change what a stage does. See the ADR The pipeline path is data; stage semantics are code (graph-works workspace, `okf/adrs/`).

## Retired `variant`

Routing variants are retired. A `variant` key in any dispatch rule's match, or `variant` in `attributes`, is refused at parse time with the replacement named:

| Retired value | Replacement match |
| --- | --- |
| `exploration` | {stage: design} (the general rule; order it before type-specific rules) |
| `diagnosis` | {stage: design, type: Bug} |
| `epic-design` | {stage: design, type: [Epic, Release]} |
| `reconcile` | two rules: {stage: design, has_spec: true} and {stage: plan, spec_stale: true} |
| `single` | {stage: plan} |
| `decompose` | {stage: plan, type: [Epic, Release]} |
| `unplanned` | {stage: execute, has_plan: false} |
| `planned` | {stage: execute, has_plan: true} |
| `branch` | {stage: finish} |

## Explanation, freshness, and launch accounting

`gw work next <path> --json` exposes `dispatch.profile` plus per-field `dispatch.provenance`. Orchestration dispatches expose the same fields and provenance. Each origin names `packaged` or the absolute filename, zero-based rule index, optional name, and reason: `set`, `explicit-null`, or `agent-change`. Human output explains agent/model/effort and resets. Attended workflow reports this profile; it does not change the current session's agent or model.

Run `gw config sync --workspace /path/to/workspace` after external edits. The derived `.gw/cache/config.json` includes ordered custom rules, `dispatch.path` (the full path fold, packaged first, each rule with its `origin`), `dispatch.artifacts` (each stage's resolved `file`, `source`, `required` and `origin`), and `_meta.dispatch_inputs` fingerprints for both manifests and both dispatch files. The fingerprints already cover the `path` and `artifacts` blocks. Existence and content hashes detect optional-file creation/removal and unchanged-mtime edits. The routing hook requests a sync for missing or stale metadata; it never evaluates rules. Invalid input leaves the previous projection intact. A malformed `rules`, `path` or `artifacts` block blocks routing; it never falls back to packaged defaults. Never hand-edit the cache.

Both Python Orca and the plugin launch recipe persist `GW_LAUNCH_V1` plus the unchanged prompt before starting a worker. The envelope freezes agent, model, reasoning effort, dispatch key, and exact placement argv. They compare explicit choices against both `launch.requested` and `launch.effective` (`effort` is the receipt field). Null preferences make no claim about provider-resolved defaults. Missing or mismatched evidence remains unverified, with task/dispatch identity retained. A successful completion needs durable proof before ack/release.

Retries read the original full task spec after restart, rather than resolving edited rules. Recovery must authorize retry and identify placement; a retry may reuse an already allocated worktree. Ambiguous starts never justify a duplicate worker or a second allocation. `worker_done` owns Orca settlement. Terminal access is optional; orchestration handles questions, replies, and completion.

## Initialization and explicit cutover

`gw bootstrap` creates a missing reference and shared dispatch document, and adds `/dispatch.local.yaml` to the workspace root ignore. The seeded document holds the relay rule on `{stage: finish}` and, commented out, an example `path` rule and `artifacts` override. It preserves authored dispatch files and other manifest values, and is idempotent. It creates no local dispatch file unless an explicit local write requests it. Alternate locations report their local filename and required ignore entry; init does not edit another repository's ignore file. Alias/merge shapes that cannot safely accept a narrow reference insertion require an explicit edit.

Legacy keys are refused in **each explicit manifest layer**, including null or empty values hidden by an overlay. There is no translator or compatibility reader. Perform this cutover only for the workspace you intend to change:

1. Save the current `workspace.yaml`, `workspace.local.yaml` (if present), `dispatch.yaml` and `dispatch.local.yaml` (if present) for review. Keep `roles.*`, repositories, `workflow.auto_drive.max_parallel`, `workflow.auto_drive.supervise_merges`, and unrelated settings.
2. Remove these four retired keys from each manifest layer where present: `workflow.pipeline`, `workflow.auto_drive.models`, `workflow.auto_drive.overrides`, `workflow.auto_drive.permission_mode`. For example, run `gw config unset workflow.pipeline --workspace /path/to/workspace`; add `--local` for the local manifest. Repeat for each present key. The removal-only path intentionally defers projection refresh until cleanup is complete.
3. Rewrite every dispatch rule whose match names `variant`, and drop `variant` from any `attributes` declaration, using the replacement the refusal names (the table in Retired `variant` above).
4. Run `gw bootstrap --workspace /path/to/workspace`. This initializes a missing shared file/reference and ignore entry; it does not translate old choices. Recreate the desired rules in `dispatch.yaml` and optional `dispatch.local.yaml`, retaining a finish relay tail when using relay mode.
5. Run `gw config sync --workspace /path/to/workspace`, then inspect `gw work next <path> --workspace /path/to/workspace --json` and `gw work orchestrate <path> --workspace /path/to/workspace --json` before launch.

Changing source code or installing this version never performs this live configuration cutover automatically.
