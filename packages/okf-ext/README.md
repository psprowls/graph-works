# okf-ext

Beyond-spec capabilities over any OKF v0.2 bundle. This is **tier 2** of the
workspace: it extends `okf-io` and never modifies it.

| Tier | What it is | Members |
|---|---|---|
| 1. Core | The spec, nothing else | `okf-io` |
| 2. Extension layer | Beyond-spec capabilities over *any* bundle | `okf-ext` — tags, schema validation, render correctness and bundle health today; richer query, budgeted context assembly later |
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

v0.1.0 declares three unconditional dependencies:

- **`okf-io>=0.1,<0.2`** — the bundle model this is built on. Pre-1.0, minor is
  breaking (ADR-0007), hence the ceiling.
- **`ruamel.yaml>=0.18`** — `load_vocabulary` and `load_schemas` both parse YAML
  files. Declared rather than inherited: relying on it arriving through `okf-io`
  breaks the day the core swaps YAML libraries, and importing `okf_io._yaml`
  would couple this package to a private module of the one it sits above.
- **`markdown-it-py>=3.0`** — the `render` capability parses markdown bodies.
  Declared for the same reason `ruamel.yaml` is, and floored where okf-io floors
  it.

  **This one is the documented exception to the extras rule above**, and the
  exception is narrow. Only `render` imports it, so the rule says it should be
  an extra with an `ImportError` naming that extra — but okf-io *hard-depends*
  on `markdown-it-py`, so no installation of okf-ext can be missing it and the
  guard could never fire. An extra whose guard is unfalsifiable is not a
  boundary; it is an unreachable branch, and `just cov` gates at 95% with a thin
  margin. The rule stands as written: it just does not reach a library the core
  already requires.

and one extra:

- **`okf-ext[schemas]` → `jsonschema>=4.18`** — the worked example of the rule
  above. `4.18` is where `referencing` landed, which is what resolves `$ref`
  across schema files; a hand-rolled inliner was rejected in favour of a library
  that is tested for fragments and cycles. Only the `schemas` capability imports
  it, and `okf_ext/schemas/__init__.py` raises an `ImportError` naming the extra
  when it is absent. Neither promotion trigger fires: the dependency is cleanly
  optional, and nothing yet wants the capability without the rest.

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
`okf_ext/<name>/__init__.py`, kept for one minor version.

## Where a rule belongs

`okf-ext` ships four rule-emitting capabilities (`tags`, `schemas`, `render`,
`health`). A fifth belongs here only if it passes one test:

> **Does it hold for any OKF v0.2 bundle, whoever wrote it?**

A malformed callout renders badly in anyone's vault. Two concepts sharing a
title is a problem in anyone's bundle. Those are tier 2.

A rule that encodes **one lane's vocabulary** belongs in that lane's tier-3
package, not here. The two worked examples, both already surveyed and both
deliberately out of scope:

- the thirteen work-lane lifecycle rules that are not schema-expressible —
  they key off `status`, `phase` and `effort` values that exist because one
  workflow defines them
- the five `diataxis.*` gates — they encode one documentation taxonomy's
  section shapes

**The hazard is specific:** a lane rule shipped from tier 2 puts *every*
adopting bundle into a permanent finding state for a vocabulary it never
adopted. A bundle with no `status` key would report a lifecycle violation
forever, and the only remedy would be filtering the code out — at which point
the rule was never really shipped, only shipped-and-suppressed.

The boundary is not about difficulty or about who writes the rule. `health`'s
`log-gap` reads a threshold and a date and is trivial; it is tier 2 because §9
logs exist in every bundle. A lifecycle rule may be equally trivial and is still
tier 3, because `phase: plan` means nothing outside one workflow.

Two consequences worth stating, because both look like counter-examples:

- **A tier-2 rule may still be wrong for a given bundle.** That is what
  `severity=` and `Report.by_code()` are for. "Some bundles will not want this"
  is not the test; "this is only meaningful under one workflow's vocabulary" is.
- **A tier-3 package composes tier-2 rules rather than reimplementing them.**
  `extra_rules=` takes a list, so a lane's validator passes its own rules
  *alongside* `render_rule()` and `health_rule()`. Nothing here needs to know
  the lane exists.

## Boundaries

Three rules, enforced two different ways:

- **The shared layer never imports a capability.** An `import-linter` `layers`
  contract in the root `pyproject.toml`.
- **Capabilities never import each other.** An `import-linter` `independence`
  contract, stated explicitly rather than left to the `a : b` layer syntax's
  same-level semantics — the rule should be legible in the file, not inferred
  from a colon.
- **A capability never imports the top-level `okf_ext` package.** An AST test
  (`tests/test_ext_boundaries.py`), because import-linter cannot express it:
  grimp does not report an import of an ancestor package as a dependency, so a
  `forbidden` contract on it passes even when the import is right there.

The independence clause became load-bearing with `schemas`, the second
capability. Writing the rules before they bite was the point — `schemas` was
added *under* this contract rather than having it retrofitted after two
capabilities had grown into each other. A `layers` contract only checks the
layers it was told to enumerate, so `test_ext_boundaries.py` also derives the
capability set from the filesystem and fails when one is missing from the
contract.

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
