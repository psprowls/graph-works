from __future__ import annotations

import sys
from datetime import date

from helpers import BUNDLES, FIXTURES
from okf_io import load_bundle
from okf_io.links import build
from okf_io.validate import RuleContext, validate

TODAY = date(2026, 8, 3)


def _bundle():
    return load_bundle(BUNDLES / "acme_retail")


def test_scope_defaults_to_none_and_changes_nothing():
    bundle = _bundle()
    assert validate(bundle, today=TODAY) == validate(bundle, today=TODAY, scope=None)


def test_rule_context_scope_defaults_to_none():
    bundle = _bundle()
    ctx = RuleContext(bundle=bundle, links=build(bundle), today=TODAY)
    assert ctx.scope is None


def test_scope_narrows_per_document_findings_to_the_named_members():
    bundle = _bundle()
    full = validate(bundle, today=TODAY)
    per_document = {f.path for f in full.findings if f.path is not None and f.path.endswith(".md")}
    assert len(per_document) > 1, "fixture must produce findings on more than one member"

    chosen = sorted(per_document)[0]
    scoped = validate(bundle, today=TODAY, scope=frozenset({chosen}))
    narrowed = {f.path for f in scoped.findings if f.path is not None and f.code.split(".", 1)[0] in _SCOPED_TOPICS}
    assert narrowed <= {chosen}


#: The okf-io topics whose rules reach documents through `_common.concepts`.
#: `identity`, `links` and `reserved` do not, and keep full-corpus visibility.
_SCOPED_TOPICS = frozenset({"computation", "frontmatter", "legacy", "lifecycle", "provenance", "trust"})


def test_a_cross_document_rule_still_sees_the_whole_corpus_under_a_scope():
    """`links.broken` reads `ctx.links`, not `concepts(ctx)`. Scoping one member
    must not hide a broken link that originates in another.

    `acme_retail`/`ga4` are vendored, conformant fixtures with no broken links
    at all, so this uses `nonconformant/` -- the fixture built to trigger every
    catalog code, `links.broken` included.
    """
    bundle = load_bundle(FIXTURES / "nonconformant")
    full = validate(bundle, today=TODAY)
    broken_full = full.by_code("links.broken")
    assert broken_full, "fixture must carry at least one broken link"

    some_member = sorted(f"{cid}.md" for cid in bundle.concepts)[0]
    scoped = validate(bundle, today=TODAY, scope=frozenset({some_member}))
    assert scoped.by_code("links.broken") == broken_full


def test_an_empty_scope_silences_every_per_document_rule():
    bundle = _bundle()
    scoped = validate(bundle, today=TODAY, scope=frozenset())
    topics = {f.code.split(".", 1)[0] for f in scoped.findings}
    assert not (topics & _SCOPED_TOPICS)


def test_supplied_links_are_used_instead_of_being_rebuilt(monkeypatch):
    bundle = _bundle()
    graph = build(bundle)

    def explode(_bundle):  # pragma: no cover - the assertion is that this never runs
        raise AssertionError("validate rebuilt the link graph despite links=")

    # `okf_io.validate` is the function, not the submodule (see okf_io's
    # __init__ "Shadowing note"), so `monkeypatch.setattr("okf_io.validate.build", ...)`
    # would resolve to the function's own attributes rather than the module's.
    # Go through `sys.modules`, populated by the top-of-file submodule import.
    monkeypatch.setattr(sys.modules["okf_io.validate"], "build", explode)
    report = validate(bundle, today=TODAY, links=graph)
    assert report.findings


def test_supplied_links_produce_the_same_report_as_a_rebuild():
    bundle = _bundle()
    assert validate(bundle, today=TODAY, links=build(bundle)) == validate(bundle, today=TODAY)
