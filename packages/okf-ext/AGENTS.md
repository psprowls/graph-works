# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this package is

`okf-ext` is tier 2 of the workspace: beyond-spec capabilities over *any* OKF
v0.2 bundle. It depends on `okf-io` (tier 1, the spec and nothing else) and is
depended on by tier-3 applications (wiki generator, AST→graph tooling,
`okf-attest`). The dependency direction is one-way and load-bearing — see
`README.md`'s "Dependency policy" and "Where a rule belongs" sections before
adding anything here, especially before adding a rule that "seems generically
useful." okf-io's own architecture (the two-layer document model, byte
fidelity, the rule catalog) is documented in `packages/okf-io/AGENTS.md` — do
not re-derive it here; this file only covers what okf-ext adds and how it
polices itself.

The capabilities today: `tags`, `schemas`, `render`, `health`, `sections`,
`placement` (rule-emitting — each claims a `TOPIC` and a `CODES` tuple in
okf-io's namespace); `tables`, `moves`, `generators`, `proposals`, `bundle`,
`logs` (writers/primitives — no `TOPIC`, no `Finding`s, by design); and
`search` (answers questions, ships no rule at all). A shared layer
(`context.py`, `body.py`, `writing.py`, `splice.py`, `shape/`) sits underneath
all of them.

## Commands

Tests and types run from the **repo root**, not from inside this package —
`okf-io` and `okf-ext` share the root pytest `testpaths` and a plain `uv run`
(unlike, e.g., `code-graph-io`, which resolves its own dependency closure and
runs under `uv run --package`). From the workspace root:

```bash
uv run pytest packages/okf-ext/tests                       # this package's tests only
uv run pytest packages/okf-ext/tests/test_tags_rename_plan.py
uv run pytest -k "moves and repair"
uv run mypy --strict --platform linux packages/okf-io/src packages/okf-ext/src   # both packages, linux arm
uv run mypy --strict --platform win32 packages/okf-io/src packages/okf-ext/src   # both packages, win32 arm — together, the `just types` recipe's two okf-io/okf-ext lines
```

`just cov` gates `okf_io` + `okf_ext` together at 95% branch coverage in one
run (`--cov=okf_io --cov=okf_ext ... --cov-fail-under=95`), so a coverage
failure's percentage is global across both packages, not okf-ext alone.

`just contracts` runs `uv run lint-imports` — the internal package-boundary
check described in `README.md`'s "Boundaries" section. Read that section
before adding a module. It is **opt-in**, not run in CI (there is no CI yet,
ADR-0010) and not part of `just check`'s default path independent of a human
invoking it — actually it *is* listed in `just check`'s dependency chain, so
running `just` / `just check` does exercise it. What it enforces:

- A `layers` contract: the shared layer (`okf_ext.context`, `okf_ext.body`,
  `okf_ext.splice`/`okf_ext.writing`, `okf_ext.shape`) never imports a
  capability.
- An `independence` contract: capabilities never import each other (stated
  explicitly per-module rather than relying on `layers`' same-level
  semantics).
- **Neither contract can catch a capability importing the top-level
  `okf_ext` package** — `grimp` (import-linter's backend) does not treat an
  import of an *ancestor* package as a reportable dependency, so a `forbidden`
  contract naming `okf_ext` as forbidden reports KEPT even when the import is
  right there in the file. That third rule is enforced only by an AST walk in
  `tests/test_ext_boundaries.py` (part of the normal pytest run, not
  `lint-imports`).
- `tests/test_ext_boundaries.py` also derives the capability set from the
  filesystem and fails if a capability directory exists but isn't named in
  the root `pyproject.toml`'s `layers`/`independence` contracts — a `layers`
  contract only checks the layers it's told to enumerate, so a forgotten
  addition is otherwise invisible (`lint-imports` reports the same
  "1 kept, 0 broken" whether or not the new capability is covered).

If you add a new capability directory under `src/okf_ext/`, you must add it
to **all three** places: the `layers` entry and the `independence.modules`
list in the root `pyproject.toml`, and (implicitly, since it's filesystem-derived)
it will then be picked up by `test_ext_boundaries.py` — but the fixture-driven
tests in that file (`capability` parametrization,
`test_every_capability_on_disk_is_covered_by_these_tests`, the per-capability
`test_the_documented_*_surface_is_present`) are hardcoded lists that must be
extended by hand.

## Architecture

### Capability shape

Every capability is a self-contained subpackage of one distribution
(`okf-ext`), not a separate package. A capability "graduates" to its own
distribution only when it needs a runtime dependency that can't be cleanly
optional, or acquires a consumer that wants it without the rest — never
merely because the dependency list grew. Graduation is a directory move plus
a re-export shim kept for one minor version (see README "Promotion rule" for
two worked examples of this shim-and-remove recipe already executed:
`SectionSpec`/`TypeSections`/etc. moving `sections` → `shape` in 0.4.0, and
`proposals.placement` renaming to `proposal_path` in 0.4.6).

Composition style matches okf-io: free functions over frozen dataclasses,
nothing is a method on a rich object. Cross-cutting configuration rides in an
optional `ExtContext` (from `okf_ext.context`) rather than a facade — this is
precisely what lets no capability ever need to import a sibling.

### Where a rule belongs (mechanism vs. vocabulary)

The single most important judgment call in this package, and the one most
likely to be gotten wrong by someone adding "just one more validation": a
rule belongs in okf-ext's `_rules`-equivalent capabilities only if it holds
for **any** OKF v0.2 bundle, regardless of who wrote it or what workflow they
follow. A rule that keys off values from one lane's vocabulary (a `status`
enum, a `phase` field, a documentation taxonomy's section names) belongs in
that lane's tier-3 package, composed in via okf-io's `extra_rules=`, never
shipped from here. The reasoning is not about triviality — `health.log-gap`
is a trivial rule and is tier 2 (§9 logs exist in every bundle); a lifecycle
rule can be equally trivial and is still tier 3 (`phase: plan` means nothing
outside one workflow). Shipping a lane rule from tier 2 would put every
*other* adopting bundle into a permanent finding state for a vocabulary it
never adopted, with no remedy short of filtering the code out entirely.

`sections` and `placement` are the two worked examples of splitting mechanism
from vocabulary within one capability: `sections` ships the declaration
*format* (a section can be required/optional, templated, owned by a
generator) but ships no section names — no `FEATURE_SECTIONS` constant, no
Diátaxis skeleton. `placement` ships a rule that checks a page sits in its
type's declared directory, but takes the `{type: directory}` map as a
parameter and holds no lane name and no taxonomy of its own.

`tables`, `generators`, `proposals`, and `bundle` deliberately ship as
primitives with **no** `TOPIC` and **no** `CODES` — each could parse/write/plan
something and also judge it, but doing both from one capability would force
every future rule to import a writer or parser through a rule module. If
you're tempted to add a `Finding` inside one of these four, that's a signal
the rule belongs in a different capability (or tier 3) instead.

`proposals` fails closed on a changed merge. Before `plan_propose` plans a
replacement body it asks the supplied renderer to reproduce the live
proposal's current body from that proposal's own `description` and
`sources[]`, and refuses with `unrenderable-body` on anything but an exact
match. This is the one place the capability declines work it could have done,
and it is deliberate: `HEADER` is shared by every renderer and a body can hold
prose no ledger field carries, so a mismatch is the only signal available that
a replacement would drop bytes. The guard runs *after* the malformed,
`already-decided` and unchanged/no-op paths, so idempotence and the older
refusals both outrank it. It calls the renderer it was given and knows nothing
about lanes or headings — keep it that way.

### The two-axis ownership safety property (generators)

`generators` is the one capability that both reads a declaration (`shape`)
and rewrites documents, and it ships **two** ownership axes together
deliberately, because either one alone is a way to destroy a document:
key-level frontmatter ownership says which keys a generator may claim,
section-level ownership says which `## ` headings it may claim. `prose` is
the default on both axes — a declaration silent about a key or section gets
the reading that cannot destroy anything; the machine only claims territory
by explicit grant. The two axes are also asymmetric on delete semantics: an
*omitted* owned frontmatter key is deleted on every run (a key is a small
recomputed scalar/list, so "the run's values are the whole truth" is safe),
but an *omitted* `generated` section is left exactly as it stood (a section
is prose; inventing or removing one is `sections.plan_sections`'s job, a
separate step). Getting this backwards — e.g. treating an unsupplied section
like an unsupplied key — silently destroys hand-written prose.

### Shared layer: what belongs there, and how it grew

The shared layer (importable by every capability, importing none of them) is
not a fixed set decided up front — it grew by hoisting code *out of* a
capability the moment a second capability needed the same primitive, because
the independence contract forbids one capability importing a sibling
directly:

- `okf_ext.body` — heading/section walk (`find_section`) and the tolerant
  table scan bounded by a real `markdown-it-py` parse. Needed by `tables`
  originally, then `generators`, then the render capability.
- `okf_ext.writing` — the single write engine (`write_all`, `PendingWrite`).
  `PendingWrite.rendered` accepts `str | bytes` (added for `bundle` and
  `proposals`) rather than a second binary write path, because `write_all`
  commits writes in the order given and `okf_ext.moves` depends on that
  single order for its no-dangling-reference invariant — two engines would
  mean two orders with no way to interleave them.
- `okf_ext.splice` — five generic line-list primitives (dominant newline,
  trailing-newline state, assembly, insertion, the separating blank),
  hoisted out of `tables/splice.py` when `sections` needed the same five.
- `okf_ext.shape` — declaration *types* (`SectionSpec`, `TypeSections`,
  `SectionSet`, `load_sections`), hoisted out of `sections` when
  `generators` needed to read the same declaration `sections` seeds/validates
  against. This is the first shared module that reads files, and the README
  is explicit that widening "shared" to include it was a deliberate,
  written-down move, not scope creep.

If you find yourself needing a helper that another capability already wrote
privately, the answer is almost always "hoist it to the shared layer and add
it to `SHARED` in `tests/test_ext_boundaries.py`" — not "import the sibling
capability" (forbidden by the independence contract) and not "duplicate it"
(exactly the failure mode the hoists above were done to prevent).

### Dependency posture

Three unconditional dependencies: `okf-io` (pinned `>=0.1,<0.2`-style per
ADR-0007's minor-is-breaking pre-1.0 policy — check `pyproject.toml` for the
current ceiling), `ruamel.yaml` (declared independently rather than relying
on okf-io's transitive copy, because `okf_io._yaml` is private), and
`markdown-it-py` (same reasoning; it's also the one documented exception to
"capability-specific deps ship as extras," because okf-io hard-depends on it
too, so an `ImportError` guard here could never fire — an unfalsifiable guard
is dead code against a 95%-coverage floor). `schemas` is the only capability
behind an extra (`okf-ext[schemas]` → `jsonschema>=4.18`), and
`okf_ext/schemas/__init__.py` raises `ImportError` naming the extra when it's
missing — that's the pattern to follow if a future capability needs a real
optional dependency. `search`, `sections`, `generators`, `proposals`, and
`bundle` all needed nothing new at all — that's the target outcome, not the
exception.

### Known sharp edges worth reading before touching related code

The README's "Known limitations" section is long and specific; skim it before
working in `tags`, `search`, or `moves` in particular — several are silent
failure modes with no signal at the API level:

- `tags.inventory()` and `tags.plan_rename()` can silently disagree about a
  non-string tag (e.g. an int `42` coerced to `'42'` by okf-io): inventory
  counts it, the planner's position-based matching never matches it, and
  `plan_rename` returns an empty plan with **no explanation**.
- `search` cannot tokenize non-ASCII text at all (`TOKEN_RE` is ASCII-only) —
  non-Latin-script documents are invisible to every query but the empty one.
  A query that fails to tokenize (empty, all-stopwords, or non-ASCII) returns
  the *whole corpus* scored `0.0`, indistinguishable from a deliberate browse.
- `moves` only repairs OKF-defined link forms, not `[[wikilinks]]` — okf-io's
  `LinkGraph` has no edge for a wikilink, so a wikilink-heavy vault (measured:
  7 real edges vs. 113 wikilinks in one fixture) needs conversion to markdown
  links first. This is reported (`render.wikilink-target`, `MovePlan.stranded`),
  not silent.
- `moves`' reference-link scanner is a heuristic, not full grammar — it can
  produce a false-positive candidate destination and refuse an otherwise
  legitimate, unrelated move (fails closed, pinned by a named regression test
  in `test_moves_plan.py`).

Do not attempt to "fix" any of these opportunistically inside an unrelated
change — each is deliberate-and-documented with an explicit reason closing it
is out of scope for this package (usually: the true fix requires either
changing okf-io's coercion behavior, which this package commits to never
doing, or a versioned change to tokenization/golden fixtures). If a task
touches one, read its full README paragraph first.
