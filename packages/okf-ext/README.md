# okf-ext

Beyond-spec capabilities over any OKF v0.2 bundle. This is **tier 2** of the
workspace: it extends `okf-io` and never modifies it.

| Tier | What it is | Members |
|---|---|---|
| 1. Core | The spec, nothing else | `okf-io` |
| 2. Extension layer | Beyond-spec capabilities over *any* bundle | `okf-ext` — tags today; schema validation, richer query, budgeted context assembly later |
| 3. Applications | Domain tools that produce or consume bundles | wiki generator, AST→graph tooling, `okf-attest` |

## Dependency policy

`okf-io` carries a hard two-dependency budget because every dependency is a
channel for non-spec behaviour to leak into "this is what OKF v0.2 means in
Python". **That reason does not transfer.** `okf-ext` is beyond-spec by
definition and has no purity claim to protect, and the dependency direction is
one-way — nothing in this list can reach the core.

What does transfer is that everyone pays for an unconditional dependency:

- **Unconditional dependencies stay few, and each is justified here.**
- **Capability-specific dependencies ship as extras** (`okf-ext[schemas]`), and
  the capability's `__init__` raises an `ImportError` naming the extra.
- **Promotion is never triggered by a dependency count** — only by the two
  conditions below.

v0.1.0 declares two:

- **`okf-io>=0.1,<0.2`** — the bundle model this is built on. Pre-1.0, minor is
  breaking (ADR-0007), hence the ceiling.
- **`ruamel.yaml>=0.18`** — `load_vocabulary` parses a YAML file. Declared
  rather than inherited: relying on it arriving through `okf-io` breaks the day
  the core swaps YAML libraries, and importing `okf_io._yaml` would couple this
  package to a private module of the one it sits above.

`requires-python` is `>=3.12`, matching the rest of the workspace.
`okf_io.models.Frontmatter.extra` defaults to `MappingProxyType({})`, which the
dataclass machinery rejects as a mutable default until `mappingproxy` became
hashable in 3.12 — so on 3.11 the packages installed and then died at import.
The floor states what actually works.

Near-duplicate detection deliberately uses stdlib `difflib` rather than
`rapidfuzz`, on merit: `get_close_matches` is built for the "did you mean" job,
tag sets are small (tens to low hundreds) and tags are short, so
`SequenceMatcher`'s O(n²) is a non-issue. `rapidfuzz` is better at multi-word
token reordering (`data quality` vs `quality data`); if that pinches it is a
contained swap inside `normalize.py`/`inventory.py`, taken as an extra.

## Promotion rule

Capabilities are self-contained subpackages of one distribution. A subpackage
**graduates** to its own distribution when either trigger fires:

- it needs a runtime dependency that cannot be cleanly optional, or
- it acquires a consumer that wants it without the rest.

Graduation is a directory move plus a re-export shim in
`okf_ext/<name>/__init__.py`, kept for one minor version. `okf-schemas` is
expected to be **born already promoted** — it has a heavy dependency
(`jsonschema`) on day one — and is out of scope here.

## Boundaries

Two rules, enforced two different ways:

- **The shared layer never imports a capability.** An `import-linter` `layers`
  contract in the root `pyproject.toml`.
- **A capability never imports the top-level `okf_ext` package.** An AST test
  (`tests/test_ext_boundaries.py`), because import-linter cannot express it:
  grimp does not report an import of an ancestor package as a dependency, so a
  `forbidden` contract on it passes even when the import is right there.

The independence-between-siblings clause becomes load-bearing with the second
capability. Writing the rules before they bite is the point — this is the
contract a future capability is added *under*, not one retrofitted after two of
them have grown into each other.

**Honest weakness:** CI is deferred until the repository has a remote, so both
checks run from `just check` — a gate a human or agent must invoke, not one a
forge enforces. That is why the promotion triggers above are written down
rather than left to judgment.

## Composition style

Free functions over frozen data, matching `okf-io` exactly. Nothing is a method
on a rich object; cross-cutting configuration rides in an optional `ExtContext`
rather than a facade, so no subpackage ever depends on a sibling.

## Known limitations

**`inventory()` and the `rename` planners can disagree, silently, about a
non-string tag.** `okf_io`'s frontmatter view coerces a non-string scalar tag
— an int `42`, a float, a bool — into the string `'42'` and records no
`coercion_failures` entry for it (unlike a mapping or a list, which it
refuses and does record). `tags.inventory()` therefore counts `'42'` as a
real tag like any other. Every `tags.plan_*` function, however, reads tag
*positions* from the raw YAML sequence rather than the coerced view — because
a plan's positions must mean something in the sequence `apply()` will
actually edit — and a position holding a non-string value is deliberately
never matched, so `plan_rename(bundle, "42", "forty-two")` against that same
bundle returns an **empty plan with no explanation**. The behaviour on both
sides is individually correct and deliberate: the inventory is not supposed
to normalize away the mess, and the planner is not supposed to risk rewriting
a value that was never really a string on disk. What is missing is a signal
connecting the two — nothing in `TagInventory` marks `'42'` as
non-string-backed, and nothing in `RenamePlan` explains an empty result. A
caller who inventories a bundle, notices a numeric-looking tag, and calls
`plan_rename` on it gets silence, not a reason.

This is left as a documented limitation rather than closed here because a fix
needs one of two larger changes — `inventory()` exposing raw-type information
it currently has no reason to carry, or a fifth `SkipReason` for "counted but
not a string at that position" threaded through every planner — and because
the root cause is `okf_io`'s coercion behaviour, which this package commits to
never modifying (see Boundaries, above; also ADR-0005). A caller who hits this
today has one workaround: inspect the bundle's own `fm_raw` for the concepts
`inventory()` attributes the suspicious tag to, and check whether the value at
that position is actually a `str`.

## Design notes

**`WriteFailure` carries a machine-readable `kind`, not only rendered prose.**
Early drafts of `apply()` left `WriteFailure` with only `path` and `error` (a
rendered message), which meant a caller wanting to retry an I/O failure but
re-plan on a stale one had nothing but substring-matching against `error`.
`WriteFailure` now carries `kind: FailureKind` — `"stale"` is the one worth
re-planning over; `"unwritable"`, `"stage-error"`, and `"commit-error"` are the
ones worth retrying as-is; the rest name the remaining content failures
`apply()` documents. This was cheap to add pre-1.0 (v0.1.0, no consumers yet)
and is exactly the kind of API gap that gets expensive once someone is
matching on `error`'s prose in production.
