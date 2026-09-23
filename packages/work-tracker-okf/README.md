# work-tracker-okf

Path-native work-item tracking for an OKF v0.2 bundle. The package owns the
work-item filesystem grammar, projection, lifecycle rules, filing, hierarchy,
dependency edges, archives, decision ledgers, and immutable mutation plans.

## Filesystem contract

Every item is identified by its complete extensionless bundle-relative path:

```text
work/release-cutover.md
work/release-cutover/
  references/
  children/
    index.md
    epic-migration.md
    epic-migration/
      references/
      children/
        feature-import.md
        _archive/
          bug-old-link.md
work/_archive/
  index.md
```

`Release` items are root-only. `Release`, `Epic`, and `Feature` may own a
`children/` lane. Every other type is a leaf. An archive is local to the lane
that owned the item, so moving a subtree preserves its identity and ancestry.
Indexes exist at each lane; hierarchy is inferred from the filesystem and is
never duplicated in frontmatter.

The seven types are `Release`, `Epic`, `Feature`, `Bug`, `TechDebt`, `TestGap`,
and `Spike`. Basenames are date-free and carry the type prefix, such as
`release-cutover`, `feature-import`, or `tech-debt-cache`.

## Frontmatter and dependencies

Lifecycle state uses `work_status`. Dependencies are always complete path-keyed
mappings:

```yaml
work_status: in-progress
depends_on:
  - path: work/release-cutover/children/epic-migration/children/spike-schema
    blocks: execute
    needs: resolved
```

There is no string shorthand. Each edge must state `path`, `blocks`, and
`needs`; exact duplicate triples are invalid. Readers retain malformed entries
as `DependencyIssue` values so validation can report them without crashing.

## Owned artifacts

Managed artifacts live beneath the item's owned `references/` directory:

| ID | Filename |
|---|---|
| decisions | `00-decisions.md` |
| design | `01-design.md` |
| plan | `02-plan.md` |
| execute-results | `03-execute-results.md` |
| execute-transcript | `03-execute-transcript.jsonl` |
| finish-results | `04-finish-results.md` |

Use the path helpers with a complete item path:

```python
from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref

item_path = "work/release-cutover/children/epic-migration"
ref = artifact_ref(item_path, MANAGED_ARTIFACTS["design"])
assert ref.rel == f"{item_path}/references/01-design.md"
assert ref.resource == f"/{item_path}/references/01-design.md"
```

Decision ledgers are owned by the nearest `Release`, `Epic`, or `Feature` and
are addressed with `work_tracker_okf.decisions.ledger_ref(owner_path)`.

## Planning and applying changes

Domain writers return immutable plans. Filing is the one small direct writer;
workspace-level composition converts its result to a transactional mutation.

```python
from datetime import date

from okf_ext.shape import load_sections
from work_tracker_okf.filing import FilingSeed, apply, plan_filing

plan = plan_filing(
    root,
    items,
    FilingSeed(
        type="Feature",
        title="Import catalog",
        description="Imports the catalog using the canonical schema.",
        on=date(2026, 8, 23),
        name="import-catalog",
        parent_path="work/release-cutover/children/epic-migration",
        affects=("packages/work-tracker-okf",),
    ),
    load_sections(root / "sections"),
)
if plan.refusal is None:
    apply(plan)
```

Archive, reparent, and Release-adoption planners map the complete owned subtree,
including nested attachments and local archive lanes. The workspace transaction
executor applies those plans atomically and reconciles every affected index.

`IGNORE` is the normal read/validation lens. `ARCHIVE_IGNORE` exposes members
beneath `references/` to the move planner; it must not be used for validation.

Completing design or plan requires the corresponding canonical managed
artifact (`references/01-design.md` or `references/02-plan.md`) to be a regular
file. Advancement refuses before stamping a source, adding a plan row, or
writing the item page when the file is absent or is a directory.

## Validation and tests

`work_tracker_okf.rules.lane_rules()` composes the state, plan, graph,
structure, target, and decision catalogs. Rules derive hierarchy from item
paths and report findings as data.

Run the package gate from the repository root:

```bash
uv run --package work-tracker-okf pytest packages/work-tracker-okf/tests \
  --cov=work_tracker_okf --cov-branch --cov-report=term-missing --cov-fail-under=95
```

## Dispatch ownership

`route(RouteState)` chooses only stage and variant and retains transition
authority. `RouteState.has_spec_doc` and `has_plan_doc` expose the canonical
artifact state used by routing so core can derive dispatch match booleans from
the same snapshot. Core owns skill/agent/model/mode rules and per-field
provenance. Work-item `effort` remains an estimate, separate from launch
`reasoning_effort`.
