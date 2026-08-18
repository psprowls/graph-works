# Decisions — the conformant vault

## D-001 — Where does an epic's decisions ledger live?
status: superseded
affects: [2026-03-02-epic-feature-filing-writer]
decided: 2026-03-01 by fixture-author

**Answer:** Beside the item page, as the source repository had it.

## D-002 — Where does an epic's decisions ledger live?
status: answered
affects: [2026-03-02-epic-feature-filing-writer]
decided: 2026-03-02 by fixture-author
supersedes: D-001

**Answer:** Under `references/`, which the ignore recipe already covers.

**Rationale:** No new ignore pattern, and it rides along on archive for free.

## D-003 — What does an assumed entry carry?
status: assumed
affects: [2026-03-02-epic-feature-filing-writer]

**If wrong:** The fixture would need a second entry shape to exercise the parser.
