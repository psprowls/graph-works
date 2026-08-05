"""The two §8 acceptance properties, over every fixture.

Property 2 is the honest proof of the two-layer model. Property 1 is
guaranteed by the clean short-circuit, so it exists to catch a regression in
dirty-tracking, not to prove ruamel fidelity.
"""

from __future__ import annotations

import difflib

import pytest
from helpers import all_concept_files, fixture_id, mutable_files, read
from okf_io import Document


def changed_lines(before: str, after: str) -> list[str]:
    diff = list(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), n=0))
    return [line for line in diff[2:] if line.startswith(("+", "-"))]


@pytest.mark.parametrize("path", all_concept_files(), ids=fixture_id)
def test_noop_roundtrip_is_byte_identical(path):
    """Property 1. A bundle walk never dirties a file it only read."""
    text = read(path)
    assert Document.parse(text).serialize() == text


@pytest.mark.parametrize("path", mutable_files(), ids=fixture_id)
def test_mutation_touches_only_the_intended_line(path):
    """Property 2. Asserted against the WHOLE diff, not just the intended line.

    A weaker assertion would let the library quietly rewrite neighbouring lines
    on every save — exactly the failure mode spec §10 names.
    """
    text = read(path)
    doc = Document.parse(text)
    had_status = "status" in doc.fm_raw
    already_deprecated = doc.fm_raw.get("status") == "deprecated"

    doc.set("status", "deprecated")
    changed = changed_lines(text, doc.serialize())

    # Match the key line exactly. A substring test would also exempt any line
    # that merely contains "status" (e.g. an `order_status` field).
    unrelated = [line for line in changed if not line[1:].lstrip().startswith("status:")]
    assert not unrelated, f"{fixture_id(path)}: mutation rewrote unrelated lines:\n{''.join(changed)}"

    added = [line for line in changed if line.startswith("+")]
    removed = [line for line in changed if line.startswith("-")]
    if already_deprecated:
        # A handful of fixtures (e.g. acme_retail's legacy metrics) already
        # carry `status: deprecated`. Setting it to the value it already holds
        # is a true no-op: serialize() reproduces the source byte-for-byte, so
        # there is nothing to add or remove. This is a stronger, not weaker,
        # instance of the property -- zero changed lines is a subset of "only
        # the status line changed".
        assert not added and not removed, f"{fixture_id(path)}: expected a true no-op, got +{added} -{removed}"
    else:
        assert len(added) == 1, f"expected one added line, got {added}"
        assert len(removed) == (1 if had_status else 0), f"unexpected removals: {removed}"


@pytest.mark.parametrize("path", mutable_files(), ids=fixture_id)
def test_mutated_document_reparses_to_the_new_value(path):
    """A byte-clean diff is worthless if the result no longer parses."""
    doc = Document.parse(read(path))
    doc.set("status", "deprecated")
    assert Document.parse(doc.serialize()).fm.status == "deprecated"


@pytest.mark.parametrize("path", mutable_files(), ids=fixture_id)
def test_mutating_any_key_leaves_a_valid_document(path):
    """Property 2 generalized past `status`.

    The status-only form of this property passes vacuously on the ga4 dialect,
    because `status` is absent there and so is appended at the end of the
    block, where the splice can never misalign. Every key that ALREADY exists
    on a line ruamel re-renders differently -- which is the whole ga4 corpus,
    with its pre-folded plain scalars -- exercises the alignment path instead.
    A misalignment there duplicates the edited key and eats its neighbour.
    """
    text = read(path)
    keys = [str(k) for k in Document.parse(text).fm_raw]

    for key in keys:
        doc = Document.parse(text)
        doc.set(key, "sentinel-value")
        out = doc.serialize()

        reparsed = Document.parse(out)
        assert reparsed.parse_error is None, (
            f"{fixture_id(path)}: set({key!r}) produced unparseable YAML:\n{reparsed.parse_error}"
        )
        assert reparsed.fm_raw.get(key) == "sentinel-value", f"{fixture_id(path)}: set({key!r}) did not take effect"
        assert list(reparsed.fm_raw) == keys, (
            f"{fixture_id(path)}: set({key!r}) changed the key set:\n  before {keys}\n  after  {list(reparsed.fm_raw)}"
        )


@pytest.mark.parametrize("path", mutable_files(), ids=fixture_id)
def test_deleting_any_key_leaves_a_valid_document(path):
    """The delete-side twin of the above."""
    text = read(path)
    keys = [str(k) for k in Document.parse(text).fm_raw]

    for key in keys:
        doc = Document.parse(text)
        doc.delete(key)
        reparsed = Document.parse(doc.serialize())

        assert reparsed.parse_error is None, f"{fixture_id(path)}: delete({key!r}) produced unparseable YAML"
        assert list(reparsed.fm_raw) == [k for k in keys if k != key], (
            f"{fixture_id(path)}: delete({key!r}) left the wrong key set: {list(reparsed.fm_raw)}"
        )
