"""Corpus-wide byte-fidelity properties for the vocabulary merge.

Separate from `test_tags_vocabulary_merge.py` for the reason okf-io's
`test_roundtrip.py` is separate from `test_document.py`: these assert
properties over a whole corpus, not the behaviour of one branch. They are the
acceptance pair for D-2's contract -- *a vocabulary merge is byte-exact
outside the entries it adds, or it does not happen* -- and every fixture in
`ext_helpers.VOCABULARY_FIXTURES` is run through both.
"""

from __future__ import annotations

import difflib

import ext_helpers
import pytest
from okf_ext.body import split_lines
from okf_ext.tags import TagDefinition, apply_vocabulary, load_vocabulary, plan_vocabulary_merge

NEW = TagDefinition(name="security", description="A defect with a security impact.")


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("trailing", [False, True])
@pytest.mark.parametrize(
    "text",
    [
        "version: 1\ntags:\n  - name: alpha\n    description: First.\ntags:\n  - name: beta\n    description: Second.",
        "\ufeff\ufefftags: []\nversion: 1",
    ],
    ids=["duplicate-keys", "double-bom"],
)
def test_malformed_anchors_are_repeatedly_refused_without_changing_bytes(tmp_path, text, newline, trailing):
    path = tmp_path / "tags.yaml"
    original = (text.replace("\n", newline) + (newline if trailing else "")).encode("utf-8")
    path.write_bytes(original)
    for _ in range(2):
        plan = plan_vocabulary_merge(path, [NEW])
        assert plan.added == ()
        assert plan.before == plan.after == original.decode("utf-8")
        assert len(plan.refusals) == 1
        assert plan.refusals[0].kind == "foreign-content"
        result = apply_vocabulary(plan)
        assert result.written == ()
        assert result.failed == plan.refusals
        assert path.read_bytes() == original


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("trailing", [False, True])
def test_single_bom_empty_flow_merge_preserves_bytes_and_is_idempotent(tmp_path, newline, trailing):
    path = tmp_path / "tags.yaml"
    ending = newline if trailing else ""
    original = ("\ufefftags: []" + newline + "version: 1" + ending).encode("utf-8")
    path.write_bytes(original)
    plan = plan_vocabulary_merge(path, [NEW])
    assert plan.added == ("security",)
    assert plan.refusals == ()
    expected = (
        "\ufefftags:"
        + newline
        + "  - name: security"
        + newline
        + "    description: A defect with a security impact."
        + newline
        + "version: 1"
        + ending
    ).encode("utf-8")
    assert plan.after.encode("utf-8") == expected
    assert apply_vocabulary(plan).written == ("tags.yaml",)
    assert path.read_bytes() == expected
    assert load_vocabulary(path).allowed == frozenset({"security"})
    second = plan_vocabulary_merge(path, [NEW])
    assert second.added == ()
    assert second.refusals == ()
    assert second.after.encode("utf-8") == expected


def definitions_already_in(path) -> list[TagDefinition]:
    """Every entry the file already declares, as `TagDefinition`s.

    Reconstructed from the loaded `Vocabulary` rather than hand-written, so
    the property covers whatever the fixtures happen to hold today rather
    than a list that can drift from them.
    """
    vocab = load_vocabulary(path)
    return [
        TagDefinition(
            name=name,
            description=vocab.descriptions.get(name, ""),
            deprecated=name in vocab.deprecated,
            replaced_by=vocab.deprecated.get(name),
        )
        for name in sorted(vocab.known)
    ]


@pytest.mark.parametrize("name", sorted(ext_helpers.VOCABULARY_FIXTURES))
def test_a_merge_that_adds_nothing_is_byte_identical(tmp_path, name):
    """Property 1. Every entry already present, contributed again: not one
    byte moves. This is the second-install case every shared bundle takes."""
    path = ext_helpers.vocabulary_copy(tmp_path, name)
    plan = plan_vocabulary_merge(path, definitions_already_in(path))
    assert plan.added == ()
    assert plan.after == plan.before
    assert plan.after.encode("utf-8") == path.read_bytes()


@pytest.mark.parametrize("name", ext_helpers.ANCHORABLE_VOCABULARIES)
def test_a_merge_that_adds_something_changes_only_the_added_lines(tmp_path, name):
    """Property 2. The diff between `before` and `after` is insertions and
    nothing else -- with two tolerated exceptions per D-2's byte-fidelity
    contract ("a vocabulary merge is byte-exact outside the entries it adds").

    Exception 1: The scaffold's `tags: []` → `tags:` rewrite is required
    because a YAML block sequence cannot follow an empty flow sequence.

    Exception 2: Adding a trailing newline to the previously-final line when
    the file originally had none is a mechanical consequence of inserting
    content after it; this preserves the file's trailing-newline state (absent)
    on the new true final line. Only bom.yaml exhibits this case.
    """
    path = ext_helpers.vocabulary_copy(tmp_path, name)
    plan = plan_vocabulary_merge(path, [NEW])
    assert plan.added == ("security",)

    before_lines = list(split_lines(plan.before))
    after_lines = list(split_lines(plan.after))
    opcodes = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False).get_opcodes()
    changes = [op for op in opcodes if op[0] != "equal"]

    for tag, i1, i2, j1, _j2 in changes:
        if tag == "insert":
            continue
        assert tag == "replace", f"{name}: a merge deleted lines, which it must never do"
        before_normalized = [line.rstrip("\r\n").lstrip("﻿") for line in before_lines[i1:i2]]
        # Defensive: exactly one line in the input is being replaced, and it's the first
        # line checked in the output (SequenceMatcher groups the modified line with
        # subsequent insertions into a single replace operation).
        assert i2 - i1 == 1, f"{name}: a replace operation must rewrite exactly one line in the input"
        # Tolerate the flow-to-block rewrite of the scaffold
        if before_normalized == ["tags: []"]:
            assert after_lines[j1].rstrip("\r\n").lstrip("﻿") == "tags:", (
                f"{name}: the flow-to-block rewrite's first line must be 'tags:' (got "
                f"{after_lines[j1].rstrip(chr(10) + chr(13)).lstrip(chr(65279))})"
            )
        # Tolerate adding a trailing newline to the final line when the file had none.
        # The replaced span's first line should be the modified original, subsequent lines are new entries.
        elif len(before_lines[i1:i2]) == 1 and before_lines[i1:i2][0] == before_lines[-1]:
            # Check that the first line of the replacement span only had a newline added, not content
            before_replaced_line_normalized = before_lines[i1].rstrip("\r\n").lstrip("﻿")
            after_replaced_line_normalized = after_lines[j1].rstrip("\r\n").lstrip("﻿")
            assert after_replaced_line_normalized == before_replaced_line_normalized, (
                f"{name}: final line content changed, not just a newline added"
            )
        else:
            raise AssertionError(
                f"{name}: a merge rewrote lines other than the scaffold's flow-to-block or "
                f"a final newline addition: {before_normalized}"
            )


@pytest.mark.parametrize("name", ext_helpers.ANCHORABLE_VOCABULARIES)
def test_every_merged_fixture_reloads_and_carries_the_new_tag(tmp_path, name):
    """Property 3. The merge's own output goes back through the strict loader
    that reads it on the next install -- the check that would have caught a
    quoting or indentation bug the two diff properties cannot see."""
    path = ext_helpers.vocabulary_copy(tmp_path, name)
    plan = plan_vocabulary_merge(path, [NEW])
    reloaded = tmp_path / "reloaded.yaml"
    reloaded.write_bytes(plan.after.encode("utf-8"))

    vocab = load_vocabulary(reloaded)
    assert "security" in vocab.allowed
    assert vocab.descriptions["security"] == NEW.description
    # Nothing the file already declared was lost on the way through.
    assert load_vocabulary(path).known <= vocab.known
