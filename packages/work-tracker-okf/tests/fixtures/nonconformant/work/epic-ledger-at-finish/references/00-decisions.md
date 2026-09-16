# Decisions — the ledger at finish

## D-001 — Does a well-formed entry survive alongside broken ones?
status: answered
affects: [2026-07-14-epic-feature-ledger-citation]
decided: 2026-07-13 by fixture-author

**Answer:** Yes — tolerance is the point.

## D-002 — Is an unrecognized status reported?
status: maybe

**Answer:** Yes, at error, once.

## D-002 — Is a duplicate id reported?
status: open

**Answer:** Yes, and being open at finish is reported separately.

## D-004 — Is a gap in the id sequence reported?
status: answered
supersedes: D-009

**Answer:** Yes: D-003 is missing, and D-009 names no entry at all.

## D-005 — Is a malformed hold reported?
status: answered
affects: [work/epic-ledger-at-finish]
decided: 2026-07-13 by fixture-author
hold: pause
phase: finish

**Answer:** Yes, at error.

## D-006 — Is a stale park with a missing checkpoint reported?
status: open
affects: [work/epic-ledger-at-finish]
hold: park
phase: execute
checkpoint: /work/epic-ledger-at-finish/references/03-execute-checkpoint-D-006.md

**Answer:** Yes: stale at warn, the checkpoint at error, and it is open at finish.
