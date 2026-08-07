# okf-ext

Beyond-spec capabilities over any OKF v0.2 bundle. This is **tier 2** of the
workspace: it extends `okf-io` and never modifies it.

| Tier | What it is | Members |
|---|---|---|
| 1. Core | The spec, nothing else | `okf-io` |
| 2. Extension layer | Beyond-spec capabilities over *any* bundle | `okf-ext` — tags, schema validation, table read/splice, render correctness, bundle health, search and member moves today; budgeted context assembly later |
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

v0.2.0 declares three unconditional dependencies:

- **`okf-io>=0.1,<0.2`** — the bundle model this is built on. Pre-1.0, minor is
  breaking (ADR-0007), hence the ceiling.
- **`ruamel.yaml>=0.18`** — `load_vocabulary` and `load_schemas` both parse YAML
  files. Declared rather than inherited: relying on it arriving through `okf-io`
  breaks the day the core swaps YAML libraries, and importing `okf_io._yaml`
  would couple this package to a private module of the one it sits above.

  `moves` adds **no dependency of its own**. It needs `markdown-it-py>=3`,
  which `tables` already declares for `okf_ext.body`, and which
  `okf_ext.moves.locate` also imports directly for `reference_definitions` —
  reaching into `okf_io._md` would couple this package to a private module of
  the one it sits above, the same reason `okf_ext.body` parses headings and
  code-block ranges itself rather than reusing the core's parse.
- **`markdown-it-py>=3.0`** — `okf_ext.body` bounds its tolerant table scan with a
  real parse: heading lines and code-block ranges come from markdown-it, and the
  scan runs only over the lines the parser says are prose. A hand-rolled fence
  tracker was rejected — tilde fences, info strings, four-space indented blocks
  and fences nested in list items make a correct one a small parser, and okf-io
  declined to hand-roll exactly this for links. The `render` capability parses
  markdown bodies too. Declared rather than inherited through `okf-io` for the
  same reason `ruamel.yaml` is, and because `okf_io._md` is private, so the
  core's parse cannot be reused even where it would suffice. Floored where
  okf-io floors it.

  **This one is the documented exception to the extras rule above**, and the
  exception is narrow. Only `body`, `render` and `moves.locate` import it, so
  the rule says it should be an extra with an `ImportError` naming that extra
  — but okf-io *hard-depends* on `markdown-it-py`, so no installation of
  okf-ext can be missing it and the guard could never fire. An extra whose
  guard is unfalsifiable is not a boundary; it is an unreachable branch, and
  `just cov` gates at 95% with a thin margin. The rule stands as written: it
  just does not reach a library the core already requires.

and one extra:

- **`okf-ext[schemas]` → `jsonschema>=4.18`** — the worked example of the rule
  above. `4.18` is where `referencing` landed, which is what resolves `$ref`
  across schema files; a hand-rolled inliner was rejected in favour of a library
  that is tested for fragments and cycles. Only the `schemas` capability imports
  it, and `okf_ext/schemas/__init__.py` raises an `ImportError` naming the extra
  when it is absent. Neither promotion trigger fires: the dependency is cleanly
  optional, and nothing yet wants the capability without the rest.

and one capability that needs nothing at all:

- **`search`** — the mirror image of the worked example above, and the case the
  policy's "unconditional dependencies stay few" clause is actually protecting.
  BM25 ranking and snippet extraction are `math`, `re` and `collections`, so
  the capability lands with no extra, no `ImportError` guard in its
  `__init__.py`, and no change to the unconditional list. A capability that
  costs nothing to install is the outcome the rule is aiming at; `schemas`
  shows what to do when one cannot be.

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

The shared layer means "configuration and block-structure primitives", not only
cross-cutting configuration. `okf_ext.body` widened it: `find_section` is not
table-specific — `generators` needs the same heading walk and the `diataxis.*`
gates will too — and the independence contract forbids a capability importing a
sibling, so a `find_section` shipped inside `tables` would force every other
consumer to duplicate the walk or break the rule. `okf_ext.writing` is there for
the same reason on the write side. Both are named in the `layers` contract and
both are listed in `SHARED` in `tests/test_ext_boundaries.py`; a shared module
missing from that set is classified as a capability, which silently makes the
independence contract incomplete.

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

**`search` cannot tokenize non-ASCII text, so a document written in a
non-Latin script is unreachable by any text query.** `TOKEN_RE` is
`[a-zA-Z0-9][a-zA-Z0-9_\-']+`, inherited verbatim from the tool `search` was
ported from. A document titled `数据质量` produces no tokens at all and so
carries an empty term-frequency table: `Filters` and the empty-query browse
find it, and nothing else does. Accented Latin is mangled rather than dropped,
and mangled *symmetrically* — `Müller` becomes `ller` at index time and at
query time alike, so searching the accented spelling works and searching
`muller`, which is what a reader types, does not. Splitting on the accent also
leaves junk behind: `naïve` indexes as `na` and `ve`, both matchable. Widening
the character classes changes tokenization, therefore term frequencies,
therefore every score, which is precisely what `tests/fixtures/bm25_golden.json`
exists to make loud; the change is possible but it is a versioned one, and it
belongs with a new golden and a note about the rankings it moves.

**A `search()` query that cannot be tokenized returns the whole corpus rather
than nothing, and looks identical to a deliberate browse.** An empty query, a
query of nothing but stopwords, and a query in a script `TOKEN_RE` cannot
represent all take the same path: the filtered set, every hit scored `0.0`, in
concept-id order. That is the documented behaviour for the first two — "every
`Metric` tagged `finance`" is a real request, and the empty query is the
natural way to spell it. The third arrives there by accident. An ASCII query
that genuinely matches nothing correctly returns `()`, so the one case that
returns *everything* is the one where the caller's input was never understood.
A caller who needs to tell them apart calls `search.tokenize(text)` first and
treats an empty result as its own case. `search()` does not do this on their
behalf because it cannot know which of the two the caller meant; separating
them is an API change, and it is logged against `parse_query()` rather than
patched in here.

**`moves` repairs OKF references, not `[[wikilinks]]`.** A wikilink is not a
link form OKF v0.2 defines, so `okf_io` does not see one and `LinkGraph`
reports no edge for it — measured directly in one vault: 7 and 5 edges where 25
and 113 wikilinks exist. A wikilink-aware locator inside a bundle-agnostic
capability would embed one vault's dialect in tier 2. Convert wikilinks to
markdown links first; then this capability sees them like any other
destination.

**A reference-style link refuses the whole plan rather than being repaired.**
`[text][ref]`'s destination lives in a `[ref]: dest` definition markdown-it's
block parser consumes, producing no token with a position — so `LinkGraph`
reports the edge while the locator cannot find its text. Rather than
half-repair the document, the count reconciliation turns the shortfall into an
`unlocatable-reference` refusal, and a definition resolving into the moved set
refuses on sight. Raw HTML anchors are the related case: `okf_io._md` yields no
token for them and they are not edges, so a document containing one still
repairs — the reconciliation cannot count what the graph cannot see.

**Reserved files have no count to reconcile against.** `links.build()` iterates
`bundle.concepts` only, so `index.md` and `log.md` are not link sources. Their
bodies are scanned and repaired directly (otherwise every move would lose
hand-authored index entry text), but the safety net that protects every concept
does not cover them. Stated as a known asymmetry rather than papered over.

**`plan_repair` repairs inbound references only.** It does not rebase a moved
member's own outbound references: after a partial `apply` the destination
content was already written with its rebasing, and for a move nothing planned,
the file already sits at its new path with no record of the base it was written
against.

**A reference-link false positive can block an unrelated, legitimate move.**
`locate._scan` does not implement full reference-link grammar: for body text
`[a][ref](notlink.md)`, markdown-it parses one reference link — `[a][ref]`
resolving to whatever `[ref]:` defines — with `(notlink.md)` as trailing
literal text; nothing in the real document links to `notlink.md` at all.
`_scan`, however, matches the bracket-then-paren shape on sight and yields
`notlink.md` as its own candidate destination. The failure mode this produces
is a false `unlocatable-reference` refusal on a document that never cited the
moved file: `LinkGraph` correctly reports zero references from this document
into the moved set, the locator (wrongly) placed one, and the count
reconciliation reads that *excess* as "the locator matched text the graph never
saw" and refuses the whole plan — blocking a legitimate, unrelated move. This
fails **closed**, not corrupt: no reference is silently mismatched or dropped,
a real move is merely refused when it should have gone through. Fixing the
root cause needs `locate._scan` to understand `[text][label]` lookahead,
deliberately out of scope here — `locate.py` has already been through three
review rounds and two regression cycles, and a fourth pass to add
reference-link lookahead risks more than it buys at this stage.
`test_reference_link_with_trailing_parenthetical_is_a_false_positive` in
`test_moves_plan.py` pins the current behaviour.

**A moved asset has one window where a reference can dangle.** An asset rename
consumes its source the instant it commits — a direct `Path.replace`, because
copying a large binary to a temp doubles the I/O and buys no guarantee a single
atomic rename does not already give — so there is no orphan copy to fall back
on. Every markdown member is protected by the opposite ordering (destinations
commit before referrers, and a source is removed last and only once every
referrer naming it has landed), but an asset rename has no deferred, conditional
removal to decline. Two failures can follow a committed asset rename and both
strand it: a *later* asset rename in the same batch failing, which stops the
commit regime before referrers are repaired; or a referrer write failing in
`write_all`, leaving that one referrer still naming the path the rename
consumed. A markdown `commit-error` cannot trigger either case — every markdown
destination commits before the first asset is attempted, and the asset loop
breaks the moment any problem is on the list, so a transient I/O hiccup on one
markdown file cannot orphan every image in the same `plan_move_dir` batch. See
`apply()`'s own docstring for the precise account; recovery is `plan_repair` on
the residual mapping, then deleting the orphan.

**`_prune` removes only directories that directly held a removed source.** An
ancestor that empties as a consequence — move everything out of `a/b/c` and
`a/b` and `a` empties too, though neither ever held a source file — is left in
place. Harmless: `load_bundle` walks files, so an empty directory is invisible
to every reader. Stated rather than papered over.

**A destination is overwritten if a non-member file appears there between plan
and apply.** `Path.replace` does not check for prior existence, so the
plan-time `dest-exists` check is the only guard, and it is a TOCTOU window.
Consistent with this codebase's single-writer assumption; the staleness digest
guards source content drift, not destination novelty.

**`plan_repair` does not refuse a reserved source or an existing destination.**
In repair mode the mapping describes a move that already happened, so
`not-a-member`, `dest-exists`, `reserved-source` and `reserved-dest` are all
skipped — the destination existing is the expected state, not a conflict.
Worth stating because it is the one place the validation rules differ by mode.

`RefusalKind` carries **ten** members, not nine: `reserved-dest` was added
during review to close a gap where a move could fabricate an `index.md` or
`log.md` that `update_index()` would then treat as genuine.

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

**`tables` ships a primitive, not a rule.** It exports no `TOPIC` and no
`CODES`, and emits no `Finding`. `diataxis.no-entries` and "Reference entries
are structurally uniform" are the motivating consumers and both belong to the
`rules` item or to tier 3 — a capability that both parses tables *and* judges
them would make every future rule import the parser through a rule module.

**Downstream adoption is out of scope here.** The work item's "wire the entity
lane's file maps and the work lane's plan tables to one implementation" is not
done in this repository: `wiki-io` and `work-io` live in `agent-research`. This
package ships the primitive and proves it against vendored samples of all three
consumer shapes; each lane's adoption is its own item in its own repository.

**No lane vocabulary ships.** There is no `PLAN_TABLE` or `FILE_MAP` constant. A
`TableSpec` is data a caller constructs; shipping one lane's column names in a
bundle-agnostic package is the same hazard the `rules` item names for lifecycle
rules.
