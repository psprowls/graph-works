# work-tracker-okf contributor guide

## Ownership

This package owns the path-native work domain. Keep workspace discovery,
configuration, process execution, and transactional journaling in
`graph-works-core`; keep CLI parsing and rendering in the CLI packages.

The canonical identity is the complete extensionless bundle-relative path.
Never add basename lookup, date-prefixed aliases, hierarchy frontmatter, or a
fallback from a path to another item.

## Invariants

- Item pages occupy root, `children`, or local `_archive` lanes beneath `work/`.
- `Release` is root-only; `Release`, `Epic`, and `Feature` may own children.
- Hierarchy and archived state come from the physical path.
- Lifecycle state is `work_status`.
- Dependency entries contain `path`, `blocks`, and `needs`.
- Managed artifacts live under the owner's `references/` directory and use the
  filenames in `paths.MANAGED_ARTIFACTS`.
- Decision ledgers belong to the nearest parent-capable item.
- Path mutations cover a complete owned subtree, opaque attachments included.
- Planners are write-free and return refusals as data.

`IGNORE` is the ordinary read and validation lens. `ARCHIVE_IGNORE` exists only
so a move planner can see reference-tree members that must move with an item.

## Compatibility boundary

The one-time work-lane migrator retired with `migration.py` (D-013/D-007): the
retired dialect belongs nowhere in this package now. Production readers,
rules, filing, archiving, reparenting, and indexing stay path-native
unconditionally, and `tests/test_legacy_boundary.py` scans every package's
source for it with no exemption. Any new compatibility behavior must be
implemented as an explicit migration, never as a read-time fallback -- and it
does not belong in this package; the live migration path is
`scripts/migrate_vault_work.py`.

## Testing

The committed fixtures are contracts:

- `fixtures/minimal` is a small nested path-native tree with one intentionally
  malformed page.
- `fixtures/conformant` contains a root Release, nested Epic and Feature, leaf,
  local archive, every lane index, cross-tree dependency, and nested registered
  and opaque attachments.
- `fixtures/nonconformant` triggers every lane catalog code without legacy
  hierarchy fields.

Run:

```bash
uv run --package work-tracker-okf pytest packages/work-tracker-okf/tests \
  --cov=work_tracker_okf --cov-branch --cov-report=term-missing --cov-fail-under=95
```
