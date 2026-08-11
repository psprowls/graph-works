# nonconformant_repo

The synthetic repository `nonconformant/`'s golden resolves `affects` entries
and plan-action tokens against. Two or three real files, and nothing else — it
exists so `targets.affects-missing` and `plan.action-target-missing` have both a
hit and a miss to report, without the golden depending on the live tree.

A **sibling** of the vault, never inside it (C5-J): `load_bundle` walks
everything under the root, and an in-vault `_repo/` would need `IGNORE` to grow
— the exact contract these tests exist to exercise.
