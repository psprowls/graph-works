"""The five §8.2 acceptance properties, over every v0.1 fixture.

The reference commit is a corpus of **inputs**, not a byte-golden target: the
migration it records was an agent regenerating each document, and a mechanical
rewriter cannot reproduce that (see `fixtures/FIXTURES.md`). So correctness is
asserted as properties of the output rather than as equality with it.

§8.3's "the nonconformant golden is unchanged" guard lives in
`test_catalog.py` (its golden comparison plus its hand-written code set), not
here -- do not re-add a `git diff`-shelling version of it. On a clean tree
such a test passes unconditionally regardless of the golden's content, so it
guards nothing; it only fails when someone stages a change to that path, and
it errors outright wherever there is no `.git` directory (an sdist or wheel
test run).
"""

from __future__ import annotations

from collections import Counter
from datetime import date

import pytest
from helpers import BUNDLES, FIXTURES, legacy_files
from okf_io import Document, _md, bundle, migrate, validate

TODAY = date(2026, 8, 5)


def _removed_lines(document, changes):
    """The 1-based `before` file lines a migration *actually* deleted.

    Gated on *changes*, never on construct presence: §5.4 promises a declined
    citations section is left exactly as written, and rewrite A is
    independent of rewrite B, so a `timestamp` beside a *declined* citations
    section does not make that section removed, and vice versa -- only a
    matching entry in *changes* means that half of the document was actually
    rewritten. Both halves of Property 4 share this rather than each keeping
    their own copy: two copies are exactly how one of them kept the gate and
    the other lost it.
    """
    removed: set[int] = set()
    if "timestamp" in document.fm_raw and any(change.kind == "generated" for change in changes):
        line = document.frontmatter_line("timestamp")
        if line is not None:
            removed.add(line)
    section = _md.citations_section(document.body)
    if section is not None and any(change.kind == "sources" for change in changes):
        offset = document.body_line_offset
        removed.update(range(section.start + offset, section.stop + offset + 1))
    return removed


@pytest.fixture
def legacy(tmp_path):
    """Every v0.1 fixture, copied byte for byte into one loadable bundle.

    Byte copies rather than text copies: `encoding/legacy_crlf.md` is a CRLF
    regression and `read`/`write_text` would quietly normalize it away.
    """
    for path in legacy_files():
        target = tmp_path / path.relative_to(FIXTURES)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    return bundle.load(tmp_path)


def apply(loaded):
    """Migrate for real, then reload. Returns (results, reloaded bundle)."""
    results = migrate(loaded, dry_run=False)
    return results, bundle.load(loaded.root)


def codes(report):
    """Every finding's code, as a sorted list — a multiset, so duplicates count."""
    return sorted(finding.code for finding in report.findings)


def test_the_corpus_is_not_empty(legacy):
    """A property suite that silently walks nothing proves nothing."""
    assert len(legacy.concepts) >= 12
    assert any(migrate(legacy))


# --- Property 1 -------------------------------------------------------------


def test_migrating_the_output_again_changes_nothing(legacy):
    """Idempotent. A rewriter that is not is a rewriter you cannot re-run.

    Both halves of the result are checked stable, not just `changed`: a
    refactor could leave bytes alone on the second pass while still declining
    something fresh on it (a `conflicting-provenance` that first pass somehow
    missed, say). Not currently exploitable -- but asserting only `changed`
    is stable leaves that hole open for a future refactor to fall into.
    """
    first, _ = apply(legacy)
    second = migrate(bundle.load(legacy.root))
    unstable = [result.path for result in second if result.changed]
    assert unstable == [], f"a second pass changed: {unstable}"

    declined_first = {result.path for result in first if result.unmigrated}
    declined_anew = [result.path for result in second if result.unmigrated and result.path not in declined_first]
    assert declined_anew == [], f"a second pass declined paths the first pass did not: {declined_anew}"


# --- Property 2 -------------------------------------------------------------

#: `_rules/legacy.py` keys two independent findings off two independent facts:
#: `legacy.timestamp` off a surviving `timestamp` key, `legacy.body-citations`
#: off `"sources"` staying in `fm.fallbacks`. In `migrate.py`, only
#: `_migrate_timestamp` ever appends `"conflicting-provenance"` or
#: `"not-an-instant"`, and only `_migrate_citations` ever appends
#: `"impure-section"`, `"not-a-resource"`, or `"multiple-citations-sections"` --
#: so a decline reason names exactly one of the two findings it keeps alive.
#: `"unparseable"` names neither: a document whose frontmatter never built
#: cannot carry either finding, which is the one case equality must not cover.
#:
#: The mapping is one-way, not biconditional: `legacy.py` exempts a blank
#: `timestamp` regardless of *why* `"conflicting-provenance"` fired, so a
#: `timestamp: ''` beside a real, differently-dated `generated.at` would land
#: in `TIMESTAMP_DECLINE_REASONS` yet raise no `legacy.timestamp` finding --
#: not a rewriter defect, just `legacy.py` independently exempting the value.
#: No fixture is in that shape, so equality holds today; **do not add one to
#: exercise it** -- it would break the property for a reason unrelated to any
#: regression. A future fixture in that shape means revisiting this property,
#: not the rewriter.
TIMESTAMP_DECLINE_REASONS = frozenset({"conflicting-provenance", "not-an-instant"})
CITATIONS_DECLINE_REASONS = frozenset({"impure-section", "not-a-resource", "multiple-citations-sections"})

#: `edge/legacy_timestamp_blank.md`'s bundle-relative path. `_rules/legacy.py`
#: treats a blank/whitespace-only `timestamp` as absent, so it raises no
#: `legacy.timestamp` and `_migrate_timestamp` declines it with no
#: `Unmigrated` to explain it (see `migrate.py`'s module docstring). That
#: means it changes nothing and declines nothing, so it never appears in
#: `migrate()`'s results at all -- the one document Property 2 must find by
#: walking the bundle rather than the results, and the one document allowed
#: to keep `generated.at` in `fallbacks` with no decline reason on record.
_BLANK_TIMESTAMP_PATH = "edge/legacy_timestamp_blank.md"


def test_migration_clears_the_fallback_flags(legacy):
    """Each fallback is cleared unless *its own* rewrite declined.

    §8.2 states this with no exception; §5.4 mandates that a declined
    construct is left exactly as written, which necessarily keeps its own
    fallback set. Both cannot hold once a refusal exists, and §5.4 governs --
    it is the reviewed behavioural mandate. The two fallbacks are independent
    (§5.4: "Rewrite A is independent"), so the gate is per fallback, not per
    document: a document declined only on citations must still have cleared
    `generated.at`, and one declined only on provenance must still have
    cleared `sources`. Gating on the whole document -- skip if it declined
    anything -- would hide exactly that hybrid case, which
    `edge/legacy_citations_hybrid.md` now exercises.

    Walks *every concept in the reloaded bundle*, not `for result in results`:
    a document that changed nothing and declined nothing is absent from
    `results` by construction (§6, "only concepts that changed or carry an
    `unmigrated` entry"), so a loop over `results` alone structurally cannot
    see one that kept a flag it should have lost. `_BLANK_TIMESTAMP_PATH` is
    exactly that document, and it is the one explicit, documented exception:
    it legitimately keeps `generated.at` because `legacy.py` treats its blank
    `timestamp` as absent, so nothing warns and there was nothing to migrate
    in the first place.
    """
    results, after = apply(legacy)
    declined_by_path = {result.path: result.unmigrated for result in results}
    for concept_id, document in after.concepts.items():
        path = f"{concept_id}.md"
        unmigrated = declined_by_path.get(path, ())
        timestamp_declined = any(entry.reason in TIMESTAMP_DECLINE_REASONS for entry in unmigrated)
        citations_declined = any(entry.reason in CITATIONS_DECLINE_REASONS for entry in unmigrated)
        fallbacks = document.fm.fallbacks
        if not timestamp_declined:
            if path == _BLANK_TIMESTAMP_PATH:
                assert "generated.at" in fallbacks, f"{path} lost its blank-timestamp fallback"
            else:
                assert "generated.at" not in fallbacks, f"{path} still carries generated.at"
        if not citations_declined:
            assert "sources" not in fallbacks, f"{path} still carries sources"


def test_the_only_legacy_findings_left_are_the_ones_a_human_must_resolve(legacy):
    """`validate()` reports each `legacy.*` finding on exactly the documents declined for it.

    §8.2 says flatly that no `legacy.*` finding remains; §5.4 mandates that a
    declined construct is left exactly as written, which necessarily keeps
    warning. The two cannot both hold once a refusal exists, and §5.4 governs:
    it is the behavioural mandate that shipped and was reviewed, and §6.1 lists
    all three decline reasons as equal members of one closed vocabulary.

    Equality holds exactly, checked separately per finding code so a hybrid
    document (rewrite A migrated, rewrite B declined) cannot hide behind the
    construct it didn't fail on. The one carve-out is `"unparseable"`: that
    document's frontmatter never built, so it can carry neither finding, and
    is excluded from both declined sets rather than assumed absent from the
    corpus.
    """
    results, after = apply(legacy)
    timestamp_declined = {
        result.path
        for result in results
        if any(entry.reason in TIMESTAMP_DECLINE_REASONS for entry in result.unmigrated)
    }
    citations_declined = {
        result.path
        for result in results
        if any(entry.reason in CITATIONS_DECLINE_REASONS for entry in result.unmigrated)
    }

    reasons = {entry.reason for result in results for entry in result.unmigrated}
    for reason in ("conflicting-provenance", "impure-section", "not-a-resource"):
        assert reason in reasons, f"the corpus must exercise the {reason} decline path"

    report = validate(after, today=TODAY)
    remaining_timestamp = {f.path for f in report.findings if f.code == "legacy.timestamp"}
    remaining_citations = {f.path for f in report.findings if f.code == "legacy.body-citations"}
    assert remaining_timestamp == timestamp_declined, (
        f"legacy.timestamp: expected {timestamp_declined}, got {remaining_timestamp}"
    )
    assert remaining_citations == citations_declined, (
        f"legacy.body-citations: expected {citations_declined}, got {remaining_citations}"
    )


# --- Property 3 -------------------------------------------------------------


def test_migration_adds_no_errors(legacy):
    before = validate(legacy, today=TODAY)
    _, after_bundle = apply(legacy)
    after = validate(after_bundle, today=TODAY)
    assert len(after.errors) <= len(before.errors)


def test_migration_adds_no_provenance_findings(legacy):
    """What §5.6's appended footnote definitions buy.

    `BodyIndex.footnote_labels` is the union of references and definitions, so
    a definition alone satisfies the §5.1 join in both directions: no
    `footnote-unjoined`, no `source-uncited`. A migrated document validates
    clean without the rewriter inventing a single sentence.
    """
    before = [code for code in codes(validate(legacy, today=TODAY)) if code.startswith("provenance.")]
    _, after_bundle = apply(legacy)
    after = [code for code in codes(validate(after_bundle, today=TODAY)) if code.startswith("provenance.")]
    assert after == before, f"provenance findings moved:\n  before {before}\n  after  {after}"


# --- Property 4 -------------------------------------------------------------


def test_every_line_the_rewriter_did_not_own_survives_verbatim(legacy):
    """Minimal and faithful: comments, prose and neighbouring keys are untouched.

    The check is a subsequence test rather than a diff-size test. Take the
    lines the rewriter actually removed (`_removed_lines`, gated on
    `result.changes` -- a *declined* citations section is not removed just
    because the locator can see it, per §5.4) out of the input, and every
    remaining line must still appear in the output, in order, byte for byte.
    A rewriter that reflowed a neighbouring key, re-wrapped a scalar, dropped
    a comment, or silently dropped a line from a construct it declined to
    touch fails this even when its diff is small.
    """
    for result in migrate(legacy):
        if not result.changed:
            continue
        document = Document.parse(result.before)
        removed = _removed_lines(document, result.changes)

        kept = [
            line
            for number, line in enumerate(result.before.splitlines(keepends=True), start=1)
            if number not in removed
        ]
        produced = iter(result.after.splitlines(keepends=True))
        for line in kept:
            assert any(candidate == line for candidate in produced), (
                f"{result.path}: line {line!r} did not survive verbatim"
            )


def test_the_output_adds_nothing_the_rewriter_did_not_report(legacy):
    """Property 4's other half: nothing not owned survived either.

    The check above proves nothing that should have survived went missing or
    moved; it proves nothing about content that should not be there, which is
    the likelier failure for a rewriter whose central body operation is
    deleting a line range -- exactly what `edge/legacy_citations_trailing.md`
    guards and the one-directional check cannot see (an off-by-one on the
    deletion boundary, or a duplicated survivor, both pass it).

    Every line of `after` must be either a kept line (`before` minus
    `_removed_lines`, shared with the check above so the two cannot drift
    apart on what "removed" means) or one of the rewriter's own additions.
    Additions are **located, not
    predicted**: for a `"generated"` change, `Document.frontmatter_line` on
    the reparsed `after` finds exactly where `generated`/`by`/`at` landed, and
    the line's own text there is what "added" means -- sidestepping the §9
    risk that `at`'s rendered spelling (`Z` preserved verbatim) differs from
    `change.at`'s reported one (`+00:00`, via `_as_text`'s `isoformat()`).
    Same idea for `"sources"`: `frontmatter_line` locates each entry's `id`,
    `resource` and (if present) `title`. The one content-based marker left is
    the footnote definitions, which frontmatter_line cannot reach: a line
    starting `[^<id>]:` for an id in `change.ids` is accepted on the strength
    of §5.6's literal syntax, not a guess.
    """
    for result in migrate(legacy):
        if not result.changed:
            continue
        before_doc = Document.parse(result.before)
        after_doc = Document.parse(result.after)
        removed = _removed_lines(before_doc, result.changes)

        before_lines = result.before.splitlines(keepends=True)
        after_lines = result.after.splitlines(keepends=True)
        kept = Counter(line for number, line in enumerate(before_lines, start=1) if number not in removed)

        added_at: set[int] = set()
        for change in result.changes:
            if change.kind == "generated":
                for keys in (("generated",), ("generated", "by"), ("generated", "at")):
                    line = after_doc.frontmatter_line(*keys)
                    if line is not None:
                        added_at.add(line)
            else:
                line = after_doc.frontmatter_line("sources")
                if line is not None:
                    added_at.add(line)
                for index, source in enumerate(after_doc.fm.sources[: len(change.ids)]):
                    keys_list = [("sources", index, "id"), ("sources", index, "resource")]
                    if source.title:
                        keys_list.append(("sources", index, "title"))
                    for keys in keys_list:
                        line = after_doc.frontmatter_line(*keys)
                        if line is not None:
                            added_at.add(line)
        added_content = {after_lines[number - 1] for number in added_at if number <= len(after_lines)}

        footnote_prefixes = tuple(
            f"[^{identifier}]:" for change in result.changes if change.kind == "sources" for identifier in change.ids
        )

        surplus = Counter(after_lines)
        surplus.subtract(kept)
        for line, count in (+surplus).items():
            if line.strip() == "":
                continue
            if line in added_content:
                continue
            if footnote_prefixes and line.lstrip().startswith(footnote_prefixes):
                continue
            raise AssertionError(f"{result.path}: unexplained surplus line {line!r} (x{count})")


def test_crlf_and_bom_fixtures_survive(legacy):
    """Property 4's encoding half, asserted on the bytes rather than the text."""
    results = {result.path: result for result in migrate(legacy)}
    crlf = results["edge/encoding/legacy_crlf.md"]
    assert crlf.changed
    encoded = crlf.after.encode("utf-8")
    assert encoded.count(b"\n") == encoded.count(b"\r\n"), "a bare LF reached a CRLF document"
    for result in results.values():
        assert result.after.startswith("﻿") == result.before.startswith("﻿")


# --- Property 5 -------------------------------------------------------------


def test_the_output_reparses_and_reserializes_to_itself(legacy):
    for result in migrate(legacy):
        reparsed = Document.parse(result.after)
        assert reparsed.parse_error is None, f"{result.path}: {reparsed.parse_error}"
        assert reparsed.serialize() == result.after, f"{result.path} does not round-trip"


def test_every_change_is_reflected_in_the_reparsed_view(legacy):
    """A byte-clean diff is worthless if the values did not actually land.

    The `"sources"` branch checks `resource` and `title`, not just `id`: a
    corrupted `resource:` beside an intact `id:` would slip past every other
    property here, including the `frontmatter_line`-located check above,
    which only proves *something* landed at that position, never that it is
    the *right* something. Expected values come from the input side --
    `before_doc.fm.sources` and `_md.citations_section(before_doc.body)` --
    not from `migrate.py`'s own `planned` list, so a transcription bug (wrong
    index, a value corrupted between read and write) has an independent value
    to be caught against, mirroring §5.2's mapping rather than restating
    `_migrate_citations`: a linked entry's `resource`/`title` pass through
    from `before_doc.fm.sources`; a non-link entry's `resource` does too, but
    its `title` is dropped. This is not a fully independent check -- the
    write side is a documented passthrough of the read side's already-scanned
    values, so both draw on the same `_md.citations_section` locator -- but
    the compared *value* still comes from `models.py`'s read path, not from
    `migrate.py`'s internal state, so an index/field mix-up or an in-flight
    corruption in the write path is still caught. What it cannot catch:
    `_migrate_citations` substituting a citation's raw text for
    `source.resource` on a non-link entry, since `_scan_citations` sets both
    to that same string for that shape -- the two are indistinguishable from
    fixture corpus values alone.
    """
    for result in migrate(legacy):
        before_doc = Document.parse(result.before)
        reparsed = Document.parse(result.after)
        for change in result.changes:
            if change.kind == "generated":
                assert reparsed.fm.generated is not None
                assert reparsed.fm.generated.by is not None
                assert reparsed.fm.generated.by.raw == change.actor
                assert reparsed.fm.generated.at == change.at
            else:
                after_sources = reparsed.fm.sources
                assert tuple(source.id for source in after_sources) == change.ids

                section = _md.citations_section(before_doc.body)
                assert section is not None, f"{result.path}: no citations section on `before`"
                before_sources = before_doc.fm.sources
                for index, identifier in enumerate(change.ids):
                    citation = section.entries[index]
                    before_source = before_sources[index]
                    after_source = after_sources[index]
                    assert after_source.resource == before_source.resource, (
                        f"{result.path}: source {identifier!r} resource changed"
                    )
                    expected_title = before_source.title if citation.link_target is not None else None
                    assert after_source.title == expected_title, f"{result.path}: source {identifier!r} title changed"


# --- §8.3 regression guards -------------------------------------------------


@pytest.mark.parametrize("name", ["acme_retail", "ga4"])
def test_a_vendored_v02_bundle_has_nothing_to_migrate(name):
    loaded = bundle.load(BUNDLES / name)
    assert migrate(loaded) == ()
    assert validate(loaded, today=TODAY).errors == ()
