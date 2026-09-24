# Path-native work lint catalog

`gw work lint` evaluates the live OKF work tree without writing it. Findings
carry `code`, `severity`, `message`, `spec`, canonical page `path`, and `line`.
Use the exact finding message for item-specific detail; this page maps the
stable catalog to the responsible repair.

## State

- `state.in-progress-without-owner` — name an owner before execution.
- `state.resolved-without-ref` — add the merge or commit reference.
- `state.superseded-without-link` — name the superseding canonical work path.
- `state.mitigated-without-mitigation` — record the mitigation.
- `state.wontfix-without-rationale` — record why the item will not be fixed.
- `state.phase-status-incoherent` — use `gw work advance`; do not hand-compose
  incompatible phase and work-status values.
- `state.stuck-open`, `state.stuck-accepted` — review or advance aged work.
- `state.archive-eligible` — fires only for a top-level item whose whole subtree is terminal; preview and then archive that root.

## Plan

- `plan.accepted-without-plan` — restore the canonical `plan` source and
  `references/02-plan.md` artifact.
- `plan.table-malformed` — repair the `## Plan` table shape.
- `plan.done-when-missing` — supply a testable Done when cell.
- `plan.action-target-missing` — repair the referenced repository target.

## Graph

- `graph.depends-on-missing` — point the edge at an existing canonical path.
- `graph.depends-on-invalid` — use a complete `path`/`blocks`/`needs` mapping.
- `graph.depends-on-cycle` — break the reported phase-level cycle.
- `graph.depends-on-not-sibling` — keep dependency edges within the intended
  ownership boundary or explicitly reparent the subtree.
- `graph.epic-without-children` — file or reparent at least one owned child.

## Structure

- `structure.illegal-lane` — move the page to the lane its physical owner
  permits.
- `structure.release-nested` — keep Release items at the work root.
- `structure.children-on-leaf` — move children beneath a Release, Epic, or
  Feature.
- `structure.owned-directory-missing` — restore the directory beside the page.
- `structure.prefix-type-mismatch` — align the stable basename prefix and type.
- `structure.source-id-duplicate` — keep one source entry per id.
- `structure.source-escape` — keep source resources beneath the item’s owned
  `references/` directory.
- `structure.source-missing` — restore the registered artifact or remove the
  stale source through the owning workflow.
- `structure.index-entry-missing`, `structure.index-entry-stale`,
  `structure.index-entry-duplicate`, `structure.index-entry-non-direct` — run
  `gw work regen-index`; never hand-author a filesystem projection.

## Targets

- `targets.affects-missing` — correct the repository path or package name.
- `targets.source-id-mismatch` — make the source id match its managed artifact
  filename.

## Decisions

- `decisions.ledger-missing` — restore the owning parent’s
  `references/00-decisions.md`.
- `decisions.entry-invalid` — repair the malformed ledger entry.
- `decisions.cite-missing` — correct or remove the unknown decision citation.
- `decisions.open-at-finish` — answer or supersede every open decision.
- `decisions.supersedes-invalid` — repair the replacement chain.

## Command repairs

Use canonical paths throughout:

```bash
gw work next <work-path> --json
gw work advance <work-path>
gw work reparent <work-path> --parent <new-parent-path>
gw work archive --dry-run <work-path>
gw work regen-index --json
gw work lint --json
```
