# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Package-level guidance for `okf-io`. The workspace root `../../AGENTS.md` covers
the `just` gate, the two-layer model, the pipeline shape, the workspace
invariants and the fixture contract — this file is the module-level map beneath
that.

## Running things from here

`uv` and `pytest` both resolve upward to the workspace root, so these work
unchanged from this directory:

```bash
uv run pytest tests/test_index.py -q          # one module
uv run pytest tests/test_links.py -k resolve  # one subset
uv run mypy --strict --platform linux src     # this package only
uv run mypy --strict --platform win32 src
```

`uv run pytest` with no path still collects the whole suite — `testpaths` is set
in the root `pyproject.toml`, which is also pytest's rootdir. `just` recipes only
exist at the root.

## The dependency stack

Imports run strictly downward. Reading in this order is how the package makes
sense:

```
_md        markdown-it token stream -> headings, links, list items, code blocks
_yaml      frontmatter split, style sniffing, round-trip load/dump
_edit      line-range splicing for body writes
  |
models     Frontmatter: the frozen typed view (uses _md for the §13.1 fallback)
document   Document: raw text + CommentedMap, and frontmatter-level splicing
derive     pure functions over Frontmatter (trust tier, status, staleness)
  |
bundle     one-walk directory load
links      the link graph over a Bundle
validate   Finding / Report / RuleContext / the runner
_rules/*   the nine topic modules
  |
index, log the two index/log writers
migrate    the v0.1 -> v0.2 rewriter (third writer)
```

**Two documented inversions**, not one:

- `validate._registry()` imports `_rules` lazily inside the function, because
  `_rules` imports `Finding` and `RuleContext` from `validate`. That's the
  documented bend in the `_rules` <-> `validate` edge.
- `_rules/reserved.py` imports `iso_date` from `log` — a rules module reaching
  *past* `validate` down into the writers tier the diagram draws below it.
  `log.py` does not import `validate` or `_rules` back, so there's no cycle,
  but the tiers above are not a strict partition: read `reserved.py` knowing
  it pulls from `log`.

`_md`, `_yaml` and `_edit` are internal (underscore-prefixed). Nothing outside
the package should import them, and nothing inside them may import upward.

## Two splices, at two levels

All three writers preserve bytes the same way, but the mechanisms are separate:

- **Frontmatter** — `document._splice(orig, pristine, mutated)` diffs ruamel's
  render of the pristine data against its render of the mutated data and maps
  the changed lines back onto the original. Returns `None` when the changed
  lines can't be anchored, and `serialize()` then re-emits the whole block.
- **Body** — `_edit.Edit` is a replacement of a 1-based inclusive line range;
  an insertion is the empty range `end == start - 1`. `index.py` and `log.py`
  compute their changes as ranges over the original lines and copy everything
  else through verbatim. `migrate.py` is the third: it deletes a `# Citations`
  range and appends footnote definitions, and touches nothing between them.
  **Never re-render a region in order to change one line inside it** — that's
  how a writer destroys prose it never meant to touch.

`_yaml.sniff_style()` infers per-document emitter settings (mapping indent,
sequence indent, dash offset, padded flow mappings, newline) from the source
text. It is best-effort: because the splice never re-emits an unchanged line, a
wrong guess costs formatting on the edited line, not the file.

`PREFERRED_KEY_ORDER` in `models.py` governs **newly created documents and newly
inserted keys only**. Existing documents are never reordered.

## The `Frontmatter` view

`doc.fm` is a frozen dataclass tree (`Actor`, `Generated`, `Verified`, `Source`,
`Parameter`, `Executor`, `Attester`, `UsageWindow`). Absent fields are `None` or
empty. Lenient normalization happens *here*, when the view is built — not in a
constructor, and never as a parse gate.

Three fields are the seam downstream code fires off, and they're why rules read
the view instead of re-reading `fm_raw`:

- `extra` — unknown keys, **mirrored** rather than moved, which is what keeps
  `not:`, `from:` and `class:` readable when attribute mapping would break.
- `coercion_failures` — dotted paths whose raw value was the wrong shape. The
  raw value stays reachable through `fm_raw`.
- `fallbacks` — which ADR-0003 read-time fallbacks fired, so a consumer can tell
  a fallback-derived value from an authored one. `_rules/legacy.py` keys off
  this rather than re-scanning the body, which would disagree with the view on a
  migrated document that kept its old `# Citations` prose. `migrate.py` keys off
  it too, which is what makes reader, validator and writer one decision rather
  than three.

`Mapping` defaults are `MappingProxyType`, not `dict` — a plain dict default
would make a directly-constructed `Frontmatter` writable while a built one isn't.

## Link resolution

`links.py` splits into small pure functions worth knowing by name before writing
anything link-shaped: `is_external`, `parse_destination`, `resolve_path`,
`resolve_reference`, `absolute_form`, and `build`.

The subtlety that bites: a §6.2 value like `report:v2.sql` is a plausible
relative filename but reads as `scheme:opaque` under RFC 3986 §4.2, so it is
**external**. Writing `./report:v2.sql` is what forces path interpretation.

`Link.target` is `None` both for an external destination and for a relative path
that escapes the bundle root; `Link.external` tells them apart, and only the
second is broken. Broken links are `warn`, never `error` (ADR-0004) — which is
why `Report.ok` stays `True` for a bundle whose only problem is a dead link.

Backlinks are **computed** from the one walk, never written into a neighbour's
frontmatter. Writing them dirties every neighbour on each edit.

## The rule catalog

9 topics, 21 rule functions, 31 codes — verified against `_rules/__init__.py`'s
`RULES_BY_TOPIC` and `CODES_BY_TOPIC` mappings and each module's own `RULES`/
`CODES` tuples; the module name is the code prefix, asserted mechanically in
`test_catalog.py`. Per-module tally (rules / codes): `computation` 4/5,
`frontmatter` 4/7, `identity` 1/1, `legacy` 1/2, `lifecycle` 2/3, `links` 1/1,
`provenance` 2/4, `reserved` 2/4, `trust` 4/4.

A rule is `Callable[[RuleContext], Iterable[Finding]]`. `RuleContext` carries
`bundle`, `links`, `today` and `scope` — and nothing that touches the
filesystem or a clock. Use `_rules/_common.py`'s `concepts(ctx)` (yields in
sorted id order) and `member_path(concept_id)`; nothing in this package
iterates a mapping raw, because findings are sorted after collection and
output must not depend on dict order or on what order the filesystem handed
back files.

`RuleContext.scope` — and `validate(scope=)`, which sets it — is one of
okf-io's extension points (see the root `AGENTS.md`): a `frozenset[str] | None`
of bundle-relative member paths that constrains *per-document* iteration only.
A rule reasoning across documents (`links.broken`, a duplicate-title grouping)
must still read the whole bundle regardless of scope, or it would report an
artifact of the scope rather than the bundle. `validate(links=)` is a related
but separate parameter — passing a pre-built `LinkGraph` is an optimisation
that skips recomputing it, not a change in behaviour.

`_common.py` is underscore-prefixed precisely to keep it out of the registry —
adding a non-rule helper module means prefixing it too.

Two guardrails on `extra_rules=` worth understanding before touching the runner:

- An external rule emitting a built-in topic prefix raises `ValueError`, checked
  **when a colliding finding is actually yielded**, not at registration. A rule
  that only sometimes collides passes cleanly on a bundle that never triggers it.
- An exception raised *by a rule* propagates. Tolerance is a promise about bundle
  **content**; swallowing a plugin bug would produce a silently incomplete report.

`strict=True` promotes every `warn` to `error` in one pass after collection.
Because severity is data, this needs no second code namespace.

Note: `README.md` at the package root currently says "29 conformance rules" —
that figure is stale against the source; trust the counts above (and
`_rules/__init__.py` directly) over the README.

## Tests

One module per source module (`test_document.py`, `test_links.py`, `test_log.py`
…), one per rule topic (`test_rules_trust.py` …), plus:

- `test_roundtrip.py` — the two §8 acceptance properties over every fixture.
  Property 2 asserts against the **whole** diff, not just the intended line; a
  weaker assertion would let the library quietly rewrite neighbours on save.
- `test_catalog.py` — prefix-equals-module, the hand-written error-code set, and
  the golden `nonconformant/` walk.
- `test_package.py` — the exact `__all__` surface and that `__version__` matches
  installed package metadata.
- `test_dialects.py` — **non-gating diagnostic.** Force-re-dumps every fixture
  through ruamel and reports the byte-identical ratio. It always passes; a
  dialect quirk is a documented finding, not a failure, because the splice never
  re-emits an unchanged line.
- `test_migrate_properties.py` — the five §8.2 migration acceptance properties
  over every v0.1 fixture, plus §8.3's guards on what must *not* have changed.
  Separate from `test_migrate.py` for the same reason `test_roundtrip.py` is
  separate from `test_document.py`: corpus-wide properties, not unit tests.

`tests/helpers.py` holds fixture discovery (`conftest.py` is nearly empty by
design). Fixtures deliberately unparseable are named in `helpers.MALFORMED`.
