"""Key-level ownership: three classes, one defined by omission."""

from __future__ import annotations

from datetime import date

from okf_ext.generators.frontmatter import key_edits
from okf_ext.generators.model import KeyEdit
from okf_ext.shape import FrontmatterOwnership

OWNERSHIP = FrontmatterOwnership(owned=("title", "sources"), provenance=("content_hash",))


def test_a_supplied_owned_key_that_differs_is_a_set():
    edits = key_edits({"title": "old"}, OWNERSHIP, {"title": "new"})
    assert edits == (KeyEdit(key="title", action="set", value="new"),)


def test_a_supplied_owned_key_that_matches_is_not_an_edit():
    """Idempotence is checked per key, not per document."""
    assert key_edits({"title": "same"}, OWNERSHIP, {"title": "same"}) == ()


def test_an_owned_key_the_run_omits_is_deleted():
    """The run's values are the whole truth. That is what lets a dependency
    that no longer applies disappear on its own, with no second mechanism."""
    assert key_edits({"sources": ["a"]}, OWNERSHIP, {}) == (KeyEdit(key="sources", action="delete", value=None),)


def test_an_owned_key_absent_from_both_is_not_an_edit():
    assert key_edits({}, OWNERSHIP, {}) == ()


def test_a_provenance_key_the_run_omits_survives():
    """A run that does not recompute a hash must not wipe it. Folding
    provenance into `owned` makes exactly that failure silent."""
    assert key_edits({"content_hash": "abc"}, OWNERSHIP, {}) == ()


def test_a_supplied_provenance_key_is_written():
    edits = key_edits({"content_hash": "abc"}, OWNERSHIP, {"content_hash": "def"})
    assert edits == (KeyEdit(key="content_hash", action="set", value="def"),)


def test_an_undeclared_key_is_never_touched():
    """Everything undeclared is the human's. Defining the human set by
    omission makes the disjointness structural -- there is no second list."""
    assert key_edits({"status": "reviewed", "notes": "mine"}, OWNERSHIP, {}) == ()


def test_a_none_valued_key_is_compared_by_presence_not_by_get():
    """A `.get(key)` returning `None` for an absent key would read as equal
    to a supplied `None` and drop a legitimate set."""
    assert key_edits({}, OWNERSHIP, {"title": None}) == (KeyEdit(key="title", action="set", value=None),)
    assert key_edits({"title": None}, OWNERSHIP, {"title": None}) == ()


def test_edits_come_back_in_declaration_order():
    edits = key_edits({}, OWNERSHIP, {"content_hash": "h", "sources": ["a"], "title": "t"})
    assert [edit.key for edit in edits] == ["title", "sources", "content_hash"]


def test_a_declaration_owning_nothing_produces_nothing():
    assert key_edits({"title": "x"}, FrontmatterOwnership(), {}) == ()


def test_a_type_mismatch_creates_an_edit():
    """A caller supplying a `str` where the document holds a `date` will see an
    edit on every run: `fm_raw` holds real `date` objects, and the string does
    not equal the date. No coercion happens here -- the docstring says so."""
    disk = {"title": date(2026, 8, 7)}
    supplied = {"title": "2026-08-07"}
    edits = key_edits(disk, OWNERSHIP, supplied)
    assert edits == (KeyEdit(key="title", action="set", value="2026-08-07"),)
