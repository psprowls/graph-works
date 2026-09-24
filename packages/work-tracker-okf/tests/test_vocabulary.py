import importlib.resources
import json

import pytest
from work_tracker_okf import vocabulary


def _base_schema() -> dict:
    path = importlib.resources.files("work_tracker_okf") / "assets" / "schema" / "_base.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_schema_enums_and_the_python_frozensets_agree() -> None:
    """C1-F: the enums are hand-authored in both places and reconciled here.
    No build step, no import-time schema read -- the same move
    `okf_io.tests.test_catalog` makes for the rule catalog."""
    properties = _base_schema()["properties"]
    assert frozenset(properties["status"]["enum"]) == vocabulary.DOCUMENT_STATUSES
    assert frozenset(properties["work_status"]["enum"]) == vocabulary.WORK_STATUSES
    assert frozenset(properties["phase"]["enum"]) == vocabulary.PHASES
    assert frozenset(properties["effort"]["enum"]) == vocabulary.EFFORTS
    assert frozenset(properties["blast_radius"]["enum"]) == vocabulary.BLAST_RADII


def test_the_schema_pattern_is_this_modules_pattern_character_for_character() -> None:
    pattern = _base_schema()["properties"]["sources"]["items"]["properties"]["id"]["pattern"]
    assert pattern == vocabulary.SOURCE_ID_PATTERN


def test_the_schema_files_are_exactly_TYPES() -> None:
    directory = importlib.resources.files("work_tracker_okf") / "assets" / "schema"
    stems = {entry.name[: -len(".schema.json")] for entry in directory.iterdir() if entry.name.endswith(".json")}
    assert stems - {"_base"} == vocabulary.TYPES


@pytest.mark.parametrize(
    "value",
    [
        "design",
        "plan",
        "execute-results",
        "execute-transcript",
        "finish-results",
        "finish-receipt",
        "release-notes",
        "design-spec-extra",
    ],
)
def test_is_source_id_accepts(value: str) -> None:
    assert vocabulary.is_source_id(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        "design_spec",
        "Plan",
        "two--words",
        "two-",
        "-two",
        "plan\n",
        " plan",
    ],
)
def test_is_source_id_refuses(value: str) -> None:
    assert vocabulary.is_source_id(value) is False


def test_release_and_parent_capabilities_are_explicit() -> None:
    assert frozenset({"Release", "Epic", "Feature", "Bug", "TechDebt", "TestGap", "Spike"}) == vocabulary.TYPES
    assert frozenset({"Release", "Epic", "Feature"}) == vocabulary.PARENT_TYPES
    assert frozenset({"Release"}) == vocabulary.ROOT_ONLY_TYPES


def test_the_w_k_shrink_landed() -> None:
    """`security` and `perf` are tags now, not types."""
    assert frozenset({"Bug", "TechDebt", "TestGap"}) == vocabulary.BUG_LIKE_TYPES
    assert frozenset({"Bug"}) == vocabulary.DIAGNOSIS_TYPES
    assert vocabulary.BUG_LIKE_TYPES <= vocabulary.TYPES
    assert vocabulary.PARENT_TYPES <= vocabulary.TYPES


def test_terminal_and_small_sets_are_subsets_of_their_axes() -> None:
    assert vocabulary.TERMINAL_STATUSES <= vocabulary.WORK_STATUSES
    assert vocabulary.SMALL_EFFORTS <= vocabulary.EFFORTS


def test_slug_prefixes_cover_exactly_TYPES() -> None:
    """The same reconciliation the schema enums get: an explicit map, asserted
    against the set it must agree with rather than derived from it."""
    assert set(vocabulary.SLUG_PREFIXES) == vocabulary.TYPES


def test_slug_prefixes_are_kebab_case() -> None:
    import re as _re

    for type_name, prefix in vocabulary.SLUG_PREFIXES.items():
        assert _re.fullmatch(r"[a-z]+(-[a-z]+)*", prefix), (type_name, prefix)


def test_the_two_pascal_case_types_kebab_correctly() -> None:
    assert vocabulary.SLUG_PREFIXES["TechDebt"] == "tech-debt"
    assert vocabulary.SLUG_PREFIXES["TestGap"] == "test-gap"


def test_slug_prefixes_is_read_only() -> None:
    with pytest.raises(TypeError):
        vocabulary.SLUG_PREFIXES["Epic"] = "nope"  # type: ignore[index]


def test_the_two_conditional_requirement_keys_are_declared() -> None:
    """`state.mitigated-without-mitigation` and `state.wontfix-without-rationale`
    read keys the item projection does not carry. `related_prs` is deliberately
    absent: `sources[]` is the lane's pointer surface now."""
    properties = _base_schema()["properties"]
    for key in ("mitigation", "rationale"):
        assert properties[key] == {"type": "string", "minLength": 1}, key
    assert "related_prs" not in properties


def test_the_four_conditional_requirements_stay_rules_not_schema_if_then() -> None:
    """`schema_rule` stamps one severity per invocation and these four
    deliberately differ — `superseded` and `mitigated` are errors, `resolved` and
    `wontfix` warns. Moving them into the schema would flatten that."""
    schema = _base_schema()
    assert set(schema["if"]["properties"]) == {"status"}
    assert schema["else"]["required"] == ["effort", "affects"]


def test_the_two_provenance_keys_are_declared() -> None:
    """`worktree` and `branch` sit in the same category as `owner` and
    `resolved_in`: caller-supplied provenance, optional, never enumerated."""
    properties = _base_schema()["properties"]
    required = _base_schema()["required"]
    for key in ("worktree", "branch"):
        assert properties[key] == {"type": "string", "minLength": 1}, key
        assert key not in required


def test_contributed_tags_are_exactly_w_ks_two() -> None:
    """W-K retired `security` and `perf` as *kinds* and made them `Bug` tags.
    This constant is the only place either string is declared, so a human
    editing a Bug meets them through `vocabulary_rule`'s suggestion rather
    than through folklore -- which only works if it stays exactly these two.

    Pinned in the manner the frozensets are pinned against
    `_base.schema.json`: restated here rather than derived, so a set edited
    without intent fails loudly."""
    from work_tracker_okf.vocabulary import CONTRIBUTED_TAGS

    assert [tag.name for tag in CONTRIBUTED_TAGS] == ["perf", "security"]
    assert all(tag.description and not tag.deprecated for tag in CONTRIBUTED_TAGS)
    assert all(tag.replaced_by is None for tag in CONTRIBUTED_TAGS)


def test_contributed_tags_is_non_empty() -> None:
    """`init.py` merges unconditionally rather than guarding on this tuple:
    with a non-empty constant the guard's false branch is unreachable, and an
    unreachable branch is a `--cov-branch` partial miss against a 95% floor.
    This assertion is what makes that safe."""
    from work_tracker_okf.vocabulary import CONTRIBUTED_TAGS

    assert CONTRIBUTED_TAGS
