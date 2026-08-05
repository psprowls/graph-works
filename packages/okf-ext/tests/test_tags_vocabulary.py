from __future__ import annotations

import pytest
from ext_helpers import BAD_DANGLING, BAD_VERSION, VOCABULARY
from okf_ext.tags.vocabulary import VocabularyError, load_vocabulary


def write(tmp_path, text):
    target = tmp_path / "_tags.yaml"
    target.write_text(text, encoding="utf-8")
    return target


def test_the_corpus_vocabulary_loads():
    vocab = load_vocabulary(VOCABULARY)
    assert vocab.allowed == frozenset({"metric", "finance", "data-quality", "revenue"})
    assert dict(vocab.deprecated) == {"kpi": "metric"}


def test_allowed_and_deprecated_are_disjoint():
    """A deprecated tag is known but not allowed — that is what lets
    `tags.deprecated` and `tags.unknown` mean different things."""
    vocab = load_vocabulary(VOCABULARY)
    assert vocab.allowed.isdisjoint(vocab.deprecated)
    assert "kpi" in vocab.known


def test_descriptions_are_kept_and_optional():
    vocab = load_vocabulary(VOCABULARY)
    assert vocab.descriptions["metric"] == "A measurable quantity."
    assert "revenue" not in vocab.descriptions


def test_source_is_the_filename_because_that_is_what_a_finding_cites():
    assert load_vocabulary(VOCABULARY).source == "_tags.yaml"


def test_a_str_path_is_accepted_and_coerced():
    """`load_vocabulary("some/path.yaml")` is the natural call a caller who
    has a path as a plain string will make. Before this coercion it raised a
    bare `AttributeError: 'str' object has no attribute 'name'` -- the only
    caller error in this module that was not a legible `VocabularyError`."""
    vocab = load_vocabulary(str(VOCABULARY))
    assert vocab.source == "_tags.yaml"
    assert vocab.allowed == frozenset({"metric", "finance", "data-quality", "revenue"})


def test_an_unknown_version_raises():
    with pytest.raises(VocabularyError, match="version"):
        load_vocabulary(BAD_VERSION)


def test_a_dangling_replaced_by_raises():
    with pytest.raises(VocabularyError, match="replaced_by"):
        load_vocabulary(BAD_DANGLING)


def test_invalid_yaml_raises(tmp_path):
    with pytest.raises(VocabularyError, match="valid YAML"):
        load_vocabulary(write(tmp_path, "version: 1\ntags: [unclosed\n"))


@pytest.mark.parametrize(
    "text",
    [
        "",  # empty file -> parses to None
        "just a plain scalar\n",  # a bare scalar document
        "- just\n- a\n- list\n",  # a top-level sequence
    ],
    ids=["empty-file", "scalar-document", "list-document"],
)
def test_a_non_mapping_document_raises(tmp_path, text):
    """Every shape a YAML document can take other than a mapping goes
    through the same `isinstance(data, Mapping)` branch."""
    with pytest.raises(VocabularyError, match="mapping"):
        load_vocabulary(write(tmp_path, text))


def test_a_missing_version_raises(tmp_path):
    with pytest.raises(VocabularyError, match="version"):
        load_vocabulary(write(tmp_path, "tags:\n  - name: metric\n"))


def test_tags_must_be_a_sequence(tmp_path):
    with pytest.raises(VocabularyError, match="sequence"):
        load_vocabulary(write(tmp_path, "version: 1\ntags: metric\n"))


def test_an_entry_must_be_a_mapping(tmp_path):
    with pytest.raises(VocabularyError, match="mapping"):
        load_vocabulary(write(tmp_path, "version: 1\ntags:\n  - metric\n"))


def test_an_entry_must_have_a_non_empty_name(tmp_path):
    with pytest.raises(VocabularyError, match="name"):
        load_vocabulary(write(tmp_path, "version: 1\ntags:\n  - description: no name\n"))
    with pytest.raises(VocabularyError, match="name"):
        load_vocabulary(write(tmp_path, "version: 1\ntags:\n  - name: '   '\n"))


def test_a_duplicate_name_raises(tmp_path):
    text = "version: 1\ntags:\n  - name: metric\n  - name: metric\n"
    with pytest.raises(VocabularyError, match="declared twice"):
        load_vocabulary(write(tmp_path, text))


def test_replaced_by_on_a_live_entry_raises(tmp_path):
    """It reads as an instruction that will never fire — a silent no-op is
    worse than a refusal."""
    text = "version: 1\ntags:\n  - name: metric\n  - name: kpi\n    replaced_by: metric\n"
    with pytest.raises(VocabularyError, match="deprecated"):
        load_vocabulary(write(tmp_path, text))


def test_a_deprecated_entry_may_have_no_replacement(tmp_path):
    text = "version: 1\ntags:\n  - name: kpi\n    deprecated: true\n"
    assert dict(load_vocabulary(write(tmp_path, text)).deprecated) == {"kpi": None}


def test_an_empty_tags_list_is_a_legitimate_vocabulary(tmp_path):
    vocab = load_vocabulary(write(tmp_path, "version: 1\ntags: []\n"))
    assert vocab.allowed == frozenset()


def test_a_missing_file_raises_oserror_not_vocabularyerror(tmp_path):
    """A path that is not there is a caller error of a different kind, and
    turning it into a format error would misdescribe it."""
    with pytest.raises(OSError):
        load_vocabulary(tmp_path / "absent.yaml")


# --- Edge cases beyond the plan's own tests: inputs the fixture corpus
# cannot express, added because prior tasks in this plan shipped defects
# that only surfaced on inputs like these. ---


def test_a_null_tags_raises(tmp_path):
    """`tags:` with no value parses as `None`, which is not a sequence —
    distinct from omitting the key entirely (see below), which defaults."""
    with pytest.raises(VocabularyError, match="sequence"):
        load_vocabulary(write(tmp_path, "version: 1\ntags:\n"))


def test_tags_key_missing_defaults_to_an_empty_vocabulary(tmp_path):
    """Omitting `tags:` altogether is not an error: a vocabulary that only
    declares its version is a legitimate, if useless, one."""
    vocab = load_vocabulary(write(tmp_path, "version: 1\n"))
    assert vocab.allowed == frozenset()
    assert dict(vocab.deprecated) == {}


def test_a_string_version_raises(tmp_path):
    """`"1"` is not `1` — YAML's type coercion is not this loader's problem
    to paper over."""
    with pytest.raises(VocabularyError, match="version"):
        load_vocabulary(write(tmp_path, 'version: "1"\ntags: []\n'))


def test_a_non_string_name_raises(tmp_path):
    with pytest.raises(VocabularyError, match="name"):
        load_vocabulary(write(tmp_path, "version: 1\ntags:\n  - name: 123\n"))


def test_replaced_by_pointing_at_a_deprecated_tag_raises(tmp_path):
    """A replacement that is itself deprecated (not allowed) is just as
    dangling as one that does not exist at all — `allowed` is the only
    valid landing spot for a `replaced_by`."""
    text = (
        "version: 1\n"
        "tags:\n"
        "  - name: kpi\n"
        "    deprecated: true\n"
        "    replaced_by: legacy-metric\n"
        "  - name: legacy-metric\n"
        "    deprecated: true\n"
    )
    with pytest.raises(VocabularyError, match="not an allowed tag"):
        load_vocabulary(write(tmp_path, text))


def test_a_name_declared_both_live_and_deprecated_raises(tmp_path):
    """Proves the `allowed`/`deprecated` disjointness invariant is actually
    established at load time, not merely assumed: a vocabulary that tries to
    list the same tag both ways is rejected outright rather than silently
    picking a winner."""
    text = "version: 1\ntags:\n  - name: metric\n  - name: metric\n    deprecated: true\n"
    with pytest.raises(VocabularyError, match="declared twice"):
        load_vocabulary(write(tmp_path, text))


# --- Round 2: defects a spec review found in the fixes above, neither caught
# by the plan's own tests nor by the first round of edge cases. ---


def test_non_utf8_bytes_raise_vocabularyerror_not_unicodedecodeerror(tmp_path):
    """A vocabulary file that isn't UTF-8 is a content problem like any
    other, not a third kind of failure the loader's documented contract
    (`VocabularyError` for content, `OSError` for a missing path) has no
    slot for. Pattern matches okf-io's `test_bundle.py::
    test_a_non_utf8_member_becomes_unreadable_not_an_exception`."""
    target = tmp_path / "_tags.yaml"
    target.write_bytes(b"version: 1\ntags:\n  - name: metric\xff\xfe\n")
    with pytest.raises(VocabularyError, match="UTF-8"):
        load_vocabulary(target)


@pytest.mark.parametrize(
    "literal",
    [
        "'true'",  # quoted -> the string "true", not the boolean
        "'false'",  # quoted -> the string "false", not the boolean
        "'no'",
        "'0'",
        "0",  # unquoted -> the integer 0, falsy but not a boolean
        "1",  # unquoted -> the integer 1, truthy but not a boolean
        "[x]",  # a list
    ],
)
def test_a_non_boolean_deprecated_raises(tmp_path, literal):
    """`deprecated` gates a rename instruction later tasks act on, so it
    must be a real boolean — a truthiness test would let a hand-written or
    tool-stringified `"false"`/`"no"`/`"0"` silently deprecate a tag, the
    worst failure mode in this package. Note unquoted `0` is falsy but still
    wrong: before this fix it read as live "by accident of falsiness", which
    is exactly the bug being pinned shut here."""
    text = f"version: 1\ntags:\n  - name: metric\n    deprecated: {literal}\n"
    with pytest.raises(VocabularyError, match="deprecated"):
        load_vocabulary(write(tmp_path, text))


def test_deprecated_true_and_false_still_work(tmp_path):
    """The fix narrows acceptance to real booleans; it must not reject the
    real booleans themselves."""
    text = "version: 1\ntags:\n  - name: metric\n  - name: kpi\n    deprecated: true\n"
    vocab = load_vocabulary(write(tmp_path, text))
    assert vocab.allowed == frozenset({"metric"})
    assert dict(vocab.deprecated) == {"kpi": None}

    live_only = load_vocabulary(write(tmp_path, "version: 1\ntags:\n  - name: metric\n"))
    assert live_only.allowed == frozenset({"metric"})


@pytest.mark.parametrize("literal", ["true", "false", "1", "0", "[metric]"])
def test_a_non_string_replaced_by_raises(tmp_path, literal):
    """Same posture as `deprecated`: `replaced_by` names a tag, so it must
    be a real string — an unquoted boolean, an integer, or a list is not a
    tag name, even though each could be coerced into one."""
    text = f"version: 1\ntags:\n  - name: kpi\n    deprecated: true\n    replaced_by: {literal}\n"
    with pytest.raises(VocabularyError, match="replaced_by"):
        load_vocabulary(write(tmp_path, text))


def test_an_empty_replaced_by_raises(tmp_path):
    """`replaced_by: ""` (or all-whitespace) is not "replaced by nothing" —
    that shape is spelled by omitting the key — so it is rejected rather
    than silently becoming a rename to the empty string."""
    text = "version: 1\ntags:\n  - name: kpi\n    deprecated: true\n    replaced_by: ''\n"
    with pytest.raises(VocabularyError, match="replaced_by"):
        load_vocabulary(write(tmp_path, text))

    text_ws = "version: 1\ntags:\n  - name: kpi\n    deprecated: true\n    replaced_by: '   '\n"
    with pytest.raises(VocabularyError, match="replaced_by"):
        load_vocabulary(write(tmp_path, text_ws))


# --- Round 3: the type-strictness principle applied to `deprecated` and
# `replaced_by` wasn't carried through to the fields beside them, or to
# unrecognized keys — reopening the same silent-no-op failure class through
# the key name instead of the value type. ---


def test_an_unknown_top_level_key_raises(tmp_path):
    """`Tags:` (capitalized) is not `tags:` — without this check it parses
    as an unrecognized key plus a missing `tags`, which defaults to an
    empty-but-valid vocabulary. That silently drops every declared tag."""
    with pytest.raises(VocabularyError, match="unknown"):
        load_vocabulary(write(tmp_path, "version: 1\nTags:\n  - name: metric\n"))


@pytest.mark.parametrize(
    "text",
    [
        # `replace_by` for `replaced_by`: the entry still loads and is still
        # marked deprecated, just with the rename instruction silently
        # dropped -- the exact failure mode the whole strictness pass exists
        # to close off.
        "version: 1\ntags:\n  - name: kpi\n    deprecated: true\n    replace_by: metric\n",
        # An entirely unrelated key.
        "version: 1\ntags:\n  - name: metric\n    colour: red\n",
    ],
    ids=["replace_by-typo", "unrelated-key"],
)
def test_an_unknown_entry_key_raises(tmp_path, text):
    with pytest.raises(VocabularyError, match="unknown"):
        load_vocabulary(write(tmp_path, text))


@pytest.mark.parametrize(
    "literal",
    ["1.0", "true", "false"],
    ids=["float", "bool-true", "bool-false"],
)
def test_a_non_integer_version_raises(tmp_path, literal):
    """`1.0 == 1` and `True == 1` in Python, so a bare `!=` comparison would
    accept both as the supported version. Same reasoning as the existing
    string-version test, extended to the other two types Python's `==`
    smooths over."""
    with pytest.raises(VocabularyError, match="version"):
        load_vocabulary(write(tmp_path, f"version: {literal}\ntags: []\n"))


@pytest.mark.parametrize(
    "literal",
    ["[a, b]", "{nested: true}"],
    ids=["list", "mapping"],
)
def test_a_non_string_description_raises(tmp_path, literal):
    """Without this check a list or mapping is `str()`-coerced into its
    Python repr and stored as the description a human reads -- e.g.
    `"['a', 'b']"` -- consistent with the same requirement already placed on
    `deprecated` and `replaced_by` in this loop."""
    text = f"version: 1\ntags:\n  - name: metric\n    description: {literal}\n"
    with pytest.raises(VocabularyError, match="description"):
        load_vocabulary(write(tmp_path, text))


def test_names_are_compared_exactly_not_normalized(tmp_path):
    """`Metric` and `metric` load as two distinct allowed tags, even though
    `okf_ext.tags.normalize.canonical` would fold them together. This is
    deliberate (see the module docstring): the vocabulary is the authority on
    exact spelling, and a vocabulary declaring both is itself the mess
    `vocabulary_rule` and normalization-driven renames exist to surface --
    canonicalizing here would hide that. Pinned so a later reader does not
    "fix" this into canonicalization."""
    text = "version: 1\ntags:\n  - name: Metric\n  - name: metric\n"
    vocab = load_vocabulary(write(tmp_path, text))
    assert vocab.allowed == frozenset({"Metric", "metric"})
