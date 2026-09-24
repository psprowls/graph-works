# okf-ext

Beyond-spec capabilities over any OKF v0.2 bundle. This is **tier 2** of the
workspace: it extends `okf-io` and never modifies it.

| Tier | What it is | Members |
|---|---|---|
| 1. Core | The spec, nothing else | `okf-io` |
| 2. Extension layer | Beyond-spec capabilities over *any* bundle | `okf-ext` — tags, schema validation, body-section declarations, table read/splice, render correctness, bundle health, search, member moves, generator-side regeneration, the proposal ledger, additive bundle setup, page placement and locked log appends today; budgeted context assembly later |
| 3. Applications | Domain tools that produce or consume bundles | wiki generator, AST→graph tooling, `okf-attest` |

## Platform

`locking.py` is the shared portable exclusive-lock primitive for this
workspace: `fcntl.flock` on POSIX, `msvcrt.locking` on Windows, chosen per
`sys.platform` and imported only inside the branch that uses it, never at
module scope. It exists because `work_tracker_okf.decisions`,
`graph_works_core.work.commands`, and this package's own `logs` module each
took the same five lines of `fcntl.flock` before consolidating here — every
caller of `locked()` is portable as a result. See `gw util platform` for the
live, per-capability answer on the running host.

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
  breaking (ADR 2026-08-02-versioning-independent-static), hence the ceiling.
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
  shows what to do when one cannot be. `sections` is the second of these:
  `ruamel.yaml` and `markdown-it-py` are both already unconditional, so it too
  ships with no extra and no guard.

  `generators` is the third of these: it regenerates documents through the
  shared write engine and the declaration types from `okf_ext.shape`, both
  already declared, so it too ships with no extra and no guard.

  `proposals` is the fourth: it writes documents through `okf-io` and the shared
  write engine, so it too ships with no extra and no guard.

  `bundle` is the fifth: it creates files through the shared write engine and
  reads `okf_io.parse` and `ruamel.yaml` to decide whether one is already
  there, all three already declared, so it too ships with no extra and no
  guard.

  `logs` is the sixth: it appends through `okf-io`'s own `append_log_entry` and
  the shared `okf_ext.locking`, plus stdlib `tempfile`, so it too ships with no
  extra and no guard.

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

The same shim-for-one-minor-version recipe applies in reverse when a type
hoists **out of** a capability and into the shared layer, not only when a
capability graduates out of this distribution. **Current instance:**
`SectionSpec`, `TypeSections`, `SectionSet`, `SectionError` and
`load_sections` moved from `okf_ext.sections` to `okf_ext.shape` in `0.4.0`.
`from okf_ext.sections import SectionSpec` still works — `okf_ext.sections`
re-exports every moved name — but the canonical home is `okf_ext.shape` now,
and the shim comes out one minor version after the move, at `0.5.0`. Import
from `okf_ext.shape` directly in new code.

The recipe applies just as well to a rename inside one module, not only to a
move between modules. **Current instance:** `okf_ext.proposals.placement`
renamed to `proposal_path` in `0.4.6` (ADR 2026-08-21-a-colliding-name — a colliding name moves on
the side without an invariant). `placement` stays as an additive alias —
`placement is proposal_path` — and comes out at `0.5.0` alongside the
`okf_ext.sections` shim above. Import `proposal_path` directly in new code.

## Where a rule belongs

`okf-ext` ships six rule-emitting capabilities (`tags`, `schemas`, `render`,
`health`, `sections`, `placement`). A seventh belongs here only if it passes one
test:

> **Does it hold for any OKF v0.2 bundle, whoever wrote it?**

A malformed callout renders badly in anyone's vault. A log with no dated
section is stale in anyone's bundle. Those are tier 2.

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

`sections` is the worked example of the mechanism/vocabulary split. The
declaration format holds for any bundle; the section *names* do not ship. There
is no `FEATURE_SECTIONS` constant and no Diátaxis skeleton in this package, for
the same reason there is no `PLAN_TABLE` in `tables` — shipping one lane's
section names from tier 2 would put every adopting bundle into a permanent
finding state for a vocabulary it never adopted.

`placement` is the same split on the other axis, and the harder-won case: it
arrived as one tier-3 package's house rule, checking that a page sits in the
directory its type's schema declares. What made it hoistable is that every
decision it makes comes from the bundle's own declarations. `placement_rule`
takes a `{type: directory}` map and holds no lane name, no type name and no
taxonomy; a bundle that declares nothing gets no findings. Even the one case
declarations cannot resolve — two types sharing a directory, one directly under
it and one below — is an injected `depth` map rather than a constant here. It
ships at `warn` like every other factory, because ADR 2026-08-07-pascalcase-types is explicit that a
directory annotation is a writer-side hint and a page sitting somewhere
unexpected is a legal page; the caller whose own reconciler cannot recover is
the one that passes `error`.

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

`okf_ext.splice` widened it a second time, on the write side. The generic
line-list primitives — dominant newline, trailing-newline state, assembly,
insertion, the separating blank — are not table-specific, and `sections` needs
all five; leaving them private inside `tables/splice.py` would have forced a
second verbatim copy of a helper set. It sits beside `okf_ext.writing` in the
`layers` contract (neither imports the other) and is listed in `SHARED` in
`tests/test_ext_boundaries.py`.

The shared layer means "configuration, block-structure primitives, and the
declaration shapes more than one capability reads". `okf_ext.shape` is the
fourth hoist and the first shared module that **reads files** — a widening
stated rather than smuggled: the layer already held `okf_ext.context`, which
is configuration, and a declaration loader is configuration that happens to
live on disk. `okf_ext.sections` seeds and validates against that declaration
and `okf_ext.generators` regenerates from it; leaving the types inside
`sections` would force `generators` to duplicate exactly the code whose value
is that both capabilities agree about what a declaration means.
`okf_ext.sections` re-exports every moved name for one minor version — the
graduation recipe above, run in reverse.

`okf_ext.locking` is the fifth hoist and the first shared module that takes an
**OS-level lock** -- a widening stated rather than smuggled, the same way
`okf_ext.shape` stated that the layer now reads files. `work_tracker_okf`'s
ledger lock, `graph_works_core.work.commands`'s decision lock, and
`okf_ext.logs`'s log lock were three byte-identical `fcntl.flock` idioms, one
per package that could reach `okf_ext`; the independence contract forbids
`okf_ext.logs` reaching sideways for it, so `okf_ext` -- the lowest package
all three sites share -- is where it lands. It sits beside `okf_ext.splice`
and `okf_ext.writing` in the `layers` contract and is listed in `SHARED` in
`tests/test_ext_boundaries.py`. It needs no entry in the "capability that
needs nothing at all" list above: it is not a capability, and it adds no
dependency, no extra, and no `ImportError` guard -- its `fcntl` and `msvcrt`
imports are both branch-local, inside `locked()`, never at module scope.

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
real tag like any other. Every `tags.plan_*` function — `plan_rename`,
`plan_merge`, `plan_normalize`, `plan_from_vocabulary`, and `plan_strip` alike
— however, reads tag *positions* from the raw YAML sequence rather than the
coerced view — because a plan's positions must mean something in the
sequence `apply()` will actually edit — and a position holding a non-string
value is deliberately never matched, so `plan_rename(bundle, "42",
"forty-two")` against that same bundle returns an **empty plan with no
explanation**, and `plan_strip(bundle, ["42"])` the same. The behaviour on both
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
never modifying (see Boundaries, above; also ADR 2026-08-02-workspace-layering). A caller who hits this
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
destination. **The blindness is reported, not silent**: `okf_ext.render`'s
`render.wikilink-target` flags a dangling wikilink at lint time, vault-wide,
and every plan carries `MovePlan.stranded` — the inbound wikilinks into its
own moved set that nothing here can reach. The count lives on the plan rather
than in each caller, so all five relocating lanes inherit it; `stranded()` is
public for a caller holding a mapping rather than a plan, which is what
`doc-wiki-okf migrate`'s preview needs. Both surfaces are built over the
shared `okf_ext.body.wikilinks` locator, and neither rewrites anything.

**A reference-style link refuses the whole plan rather than being repaired.**
`[text][ref]`'s destination lives in a `[ref]: dest` definition markdown-it's
block parser consumes, producing no token with a position — so `LinkGraph`
reports the edge while the locator cannot find its text. Rather than
half-repair the document, the count reconciliation turns the shortfall into an
`unlocatable-reference` refusal, and a definition resolving into the moved set
refuses on sight. Raw HTML anchors are the related case: `okf_io._md` yields no
token for them and they are not edges, so a document containing one still
repairs — the reconciliation cannot count what the graph cannot see.

A footnote definition (`[^id]: dest`) is one instance of this, not a
separate case — markdown-it's core reference-definition grammar doesn't
distinguish a `^`-prefixed label from any other, so a *bare-destination*
footnote definition parses as the same `[label]: dest` shape and inherits the
same refusal, `RefDef.href`/`.line` computed but never rewritten. It only
bites the bare form, though: `[^id]: [Title](dest)` — the shape
`okf_io.migrate` itself emits (`migrate.py:405`, §5.6) — isn't a valid
reference-definition destination at all (CommonMark requires a bare URL
there), so markdown-it leaves it as ordinary prose containing an inline link,
which the locator already finds and the rebase path already rewrites
correctly. A `sources[].id` cited via `provenance.footnote_join`
(`okf_io/_rules/provenance.py`) should use that bracketed-link style for
exactly this reason. Tracked as `work/bug-moves-refuses-footnote-definitions`.

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

**A caller supplying a string where the document holds a date rewrites that
key on every run.** `fm_raw` holds real `date` objects, and
`"2026-08-07" != date(2026, 8, 7)`, so the per-key idempotence check sees a
change forever and every run reports an edit that changes nothing meaningful.
Callers supply native types; nothing here coerces on their behalf, for the
reason `okf_io.models._str_tuple` gives about coercion laundering a broken
value into a plausible one.

**Ownership is per type, not per document.** A single page that wants one
section frozen has no way to say so; the declaration is the only lever, and
it moves every document of that type at once. A per-document override would
need a frontmatter key, which would need a name, which is a vocabulary
decision tier 2 does not get to make.

**A renamed heading on an optional generated section is reported nowhere a
caller must look.** It produces a `section-missing` skip in the
`ApplyResult`, which a caller may ignore, and no `Finding`, because
`sections.missing` only fires for required sections. The alternative is a
rule in a capability that ships none.

**Nothing detects a section that stopped being generated.** Flipping
`ownership: generated` to `prose` leaves the machine's last render sitting
there as prose, indistinguishable from something a human wrote. Detecting it
would need content provenance this capability does not record.

**Supplied content containing a line that parses as a declared heading is
written at the wrong place.** `regenerate_body` re-locates each section by
heading against the body *as amended by the previous edit*, and the scan is
first-match in document order with no notion of "the heading this function
just wrote". So if a run supplies content for one section that itself
contains a line reading as a *later* declared heading at a declared level,
that later section is found early — at the injected line — and written
there, while the real heading further down keeps its stale content, leaving
two headings of that name. It is not detected: a detect-and-refuse guard was
considered and deliberately deferred, not forgotten (see `_locate`'s
docstring in `regenerate.py`). Supplying content that cannot be mistaken for
a declared heading is the caller's responsibility.

**A `proposals` merge refuses rather than replacing a body it cannot first
reproduce, and the refusal has no override.** `plan_propose`'s changed-merge
branch renders the live proposal's own `description` and `sources[]` through
the renderer it was handed and requires the result to equal the body on disk
byte for byte. Anything else refuses with `unrenderable-body` and plans
nothing. The check is deliberately blunt, because the two signals that would
let it be subtler do not exist: `HEADER` is shared by every renderer, so it
cannot say which contract wrote a body, and a body may carry prose — a
migration-era counterexample, a hand-added qualification — that no `sources[]`
key holds, so no renderer can reconstruct it from the ledger.

The cost is that a legitimate change refuses too. A renderer whose output
depends on context that has since moved (a proposal whose target has appeared
since it was filed, so a mode-sensitive renderer now writes "Update existing"
where the body says "Create new"), a whitespace difference, or a hand-edited
body all refuse identically. There is deliberately **no force flag and no
corpus-repair command**: the recovery is to open the document, decide what the
unmatched bytes are worth, move anything worth keeping into `sources[]`, and
re-render it under a separate reviewed operation. A refusal is recoverable in
a way a silent replacement is not.

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

**`PendingWrite` carries `str | bytes`, and there is still one write engine.**
The staging loop already wrote *bytes* to the temp file — it manufactured them
from a `str` at the last moment — so admitting a binary payload is a one-line
change to what a `PendingWrite` may hold, not a second I/O regime. The field
keeps the name `rendered`: it names the value's role ("the whole file, as it
will be written"), and a byte string is a rendering of a file as much as a
character string is. A separate `PendingBinaryWrite` with its own staging loop
was rejected for a reason not visible from the type signature — `write_all`
commits in the order given, and `okf_ext.moves` bases its "no partial outcome
leaves a dangling reference" invariant on that order. Two sequences means two
orders, with no way to interleave them. `okf_ext.proposals.Write.text` mirrors
the widening, create-only; `okf_ext.bundle.plan_install` deliberately does not
(its inputs are a package's own text declarations, and its byte-compare has
nothing to compare a binary value against). See ADR 2026-08-21-a-pending-write. — okf-ext 0.4.7

**`tables` ships a primitive, not a rule.** It exports no `TOPIC` and no
`CODES`, and emits no `Finding`. `diataxis.no-entries` and "Reference entries
are structurally uniform" are the motivating consumers and both belong to the
`rules` item or to tier 3 — a capability that both parses tables *and* judges
them would make every future rule import the parser through a rule module.

**Downstream adoption is out of scope here.** The work item's "wire the entity
lane's file maps and the work lane's plan tables to one implementation" is not
done here. This package ships the primitive and proves it against vendored
samples of all three consumer shapes; each lane's adoption is its own item.

**No lane vocabulary ships.** There is no `PLAN_TABLE` or `FILE_MAP` constant. A
`TableSpec` is data a caller constructs; shipping one lane's column names in a
bundle-agnostic package is the same hazard the `rules` item names for lifecycle
rules.

See `okf_ext.generators.__doc__` for a worked example of calling the
capability end to end.

**Two halves ship together because either one alone is a way to break a
document.** Key-level frontmatter ownership says which keys a generator may
claim; section ownership says which `## ` headings it may claim. A generator
that owns the right keys but flattens hand-written prose into its render has
still destroyed the page, and a generator that carries every section through
untouched but stomps a human-edited `status` key has done the same thing to
the other half. Shipping the merge without both axes would be shipping half a
safety property.

**`prose` is the ownership default**, on both axes: a declaration that says
nothing about a key or a section gets the reading that cannot destroy
anything, so the machine only claims territory by an explicit grant in
`sections/`. That is the same direction of default `sections` already made
for required/optional; `generators` makes it again for who-may-write.

**An omitted `owned` frontmatter key is deleted; an omitted `generated`
section is left alone.** The asymmetry is deliberate, not an oversight. A
frontmatter key is a small scalar or list a run recomputes wholesale each
time, so "the run's values are the whole truth" is safe and useful — it is
what lets a dependency that no longer applies disappear on its own without a
caller remembering to delete it by hand. A section is a block of prose;
`regenerate_body` never invents or removes one for a concept — `plan_sections`
is the capability that creates a required section, and that is a separate step
by design (see Boundaries). The one exception is an **index** document, which
has no scaffolder at all: `plan_sections` walks `bundle.concepts`, so a granted
index section that is absent could never be written. `plan_regenerate` passes
`create_missing=True` on that path alone, and appends the section at the end of
the body. Leaving an unsupplied `generated` section exactly as
it stood — the machine's own last render, most of the time — is the
non-destructive reading; silently blanking it on every run a caller happens
not to recompute that section would be the frontmatter behaviour applied
somewhere it does not belong.

**`template` implies `seeded_is_complete`.** A template section's content is
its declared placeholder by construction — the generator supplies nothing for
it, `_content` returns the placeholder unconditionally — so without
`seeded_is_complete=True` baked into the load, every required template
section would report `sections.unfilled` forever, for being in exactly the
state it is supposed to be in.

**No rule ships**, for the reason `tables` ships none: a capability that both
writes documents and judges them would make every future rule import the
writer through a rule module. The seeding axis this capability's output feeds
is already covered — `sections.missing` and `sections.unfilled` read the same
declaration.
