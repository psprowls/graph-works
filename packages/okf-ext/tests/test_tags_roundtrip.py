"""Byte fidelity in both YAML dialects: the property this feature rests on.

Style preservation is the fragile part -- the whole rename mechanism depends
on in-place `CommentedSeq` mutation behaving as expected across ruamel
versions. A diff measured in lines is what makes a regression visible rather
than merely present.
"""

from __future__ import annotations

import difflib

import pytest
from ext_helpers import bundle_copy, snapshot
from okf_ext.tags.rename import apply, plan_normalize, plan_rename
from okf_io import load_bundle


def changed_lines(before: bytes, after: bytes) -> int:
    left = before.decode("utf-8").splitlines(keepends=True)
    right = after.decode("utf-8").splitlines(keepends=True)
    return sum(
        1
        for line in difflib.unified_diff(left, right, n=0)
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )


@pytest.mark.parametrize(
    ("old", "new", "member", "expected_lines"),
    [
        # Flow: one line in, one line out.
        ("revenue", "net-revenue", "flow.md", 2),
        # Block: one sequence entry, one line each side of the diff.
        ("ga4", "analytics", "block.md", 2),
    ],
)
def test_a_rename_touches_exactly_the_tag_line(tmp_path, old, new, member, expected_lines):
    root = bundle_copy(tmp_path)
    before = snapshot(root)
    bundle = load_bundle(root)
    apply(bundle, plan_rename(bundle, old, new))
    after = snapshot(root)
    assert changed_lines(before[member], after[member]) == expected_lines


def test_every_file_the_plan_did_not_name_is_byte_identical(tmp_path):
    root = bundle_copy(tmp_path)
    before = snapshot(root)
    bundle = load_bundle(root)
    result = apply(bundle, plan_normalize(bundle))
    after = snapshot(root)
    untouched = set(before) - set(result.written)
    assert untouched
    for name in untouched:
        assert after[name] == before[name], f"{name} changed and the plan never named it"


def test_replanning_after_reload_converges_to_an_empty_plan(tmp_path):
    """This covers *re-planning against a freshly reloaded bundle*, not
    reapplying the same plan object -- see
    `test_reapplying_the_same_plan_is_refused_as_stale_against_the_same_bundle`
    in `test_tags_rename_apply.py` for that other, genuinely different case.
    After normalizing and reloading from disk, a fresh `plan_normalize` call
    finds nothing left to normalize. A canonicaliser that needed two passes
    would make every plan a lie about what it does."""
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    apply(bundle, plan_normalize(bundle))
    settled = snapshot(root)
    reloaded = load_bundle(root)
    assert plan_normalize(reloaded).is_empty
    assert apply(reloaded, plan_normalize(reloaded)).written == ()
    assert snapshot(root) == settled


def test_a_renamed_document_still_parses_and_still_round_trips(tmp_path):
    """ "Always valid, never lossy" -- re-loading and re-serializing an edited
    document must return its own bytes."""
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    apply(bundle, plan_rename(bundle, "ga4", "analytics"))
    reloaded = load_bundle(root)
    document = reloaded.concepts["block"]
    assert document.parse_error is None
    assert "analytics" in document.fm.tags
    assert document.serialize().encode("utf-8") == (root / "block.md").read_bytes()


# --- Adversarial probes --------------------------------------------------


def test_a_flow_rename_and_its_inverse_restore_the_exact_original_bytes(tmp_path):
    """Not merely equivalent content -- the exact original bytes, including
    whatever dialect noise ruamel would otherwise introduce on a second
    pass."""
    root = bundle_copy(tmp_path)
    original = (root / "flow.md").read_bytes()

    bundle = load_bundle(root)
    apply(bundle, plan_rename(bundle, "revenue", "net-revenue"))
    assert (root / "flow.md").read_bytes() != original

    reloaded = load_bundle(root)
    apply(reloaded, plan_rename(reloaded, "net-revenue", "revenue"))
    assert (root / "flow.md").read_bytes() == original


def test_a_block_rename_and_its_inverse_restore_the_exact_original_bytes(tmp_path):
    root = bundle_copy(tmp_path)
    original = (root / "block.md").read_bytes()

    bundle = load_bundle(root)
    apply(bundle, plan_rename(bundle, "ga4", "analytics"))
    assert (root / "block.md").read_bytes() != original

    reloaded = load_bundle(root)
    apply(reloaded, plan_rename(reloaded, "analytics", "ga4"))
    assert (root / "block.md").read_bytes() == original
