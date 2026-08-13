"""Tests for config_io.entries — entry construction, matching, coercion."""

from __future__ import annotations

import pytest
from config_io.entries import (
    ConfigEntry,
    coerce,
    find_entry,
    unknown_key_error,
)
from config_io.errors import (
    InvalidValueError,
    RegistryError,
    StoreValidationError,
    UnknownKeyError,
)

WILDCARD = ConfigEntry(key="roles.*.max_tokens", type="int", default=None, description="Role cap.")
CONCRETE = ConfigEntry(key="roles.scanner.max_tokens", type="int", default=99, description="Scanner cap.")


def test_refusals_share_a_value_error_base():
    assert issubclass(RegistryError, ValueError)
    assert issubclass(UnknownKeyError, RegistryError)
    assert issubclass(InvalidValueError, RegistryError)


def test_store_validation_error_is_not_a_refusal():
    # It is the store's contract signal travelling downward, not a user-facing
    # refusal — catching RegistryError must not swallow it.
    assert not issubclass(StoreValidationError, RegistryError)


def test_write_policy_defaults_to_writable():
    assert ConfigEntry(key="topic", type="str", default=None, description="x").write_policy == "writable"


@pytest.mark.parametrize("policy", ["writable", "link-file", "provenance", "read-only"])
def test_write_policy_vocabulary_is_accepted(policy):
    assert ConfigEntry(key="k", type="str", default=None, description="x", write_policy=policy).write_policy == policy


def test_write_policy_outside_the_vocabulary_is_rejected():
    with pytest.raises(ValueError, match="write_policy"):
        ConfigEntry(key="k", type="str", default=None, description="x", write_policy="sometimes")


def test_allowed_requires_str_type():
    with pytest.raises(ValueError, match="allowed"):
        ConfigEntry(key="bad", type="int", default=1, allowed=("1", "2"), description="x")


def test_exact_match_beats_wildcard_in_either_catalog_order():
    assert find_entry([WILDCARD, CONCRETE], "roles.scanner.max_tokens") is CONCRETE
    assert find_entry([CONCRETE, WILDCARD], "roles.scanner.max_tokens") is CONCRETE


def test_wildcard_matches_only_at_equal_depth():
    assert find_entry([WILDCARD], "roles.linter.max_tokens") is WILDCARD
    assert find_entry([WILDCARD], "roles.linter.nested.max_tokens") is None


def test_find_entry_returns_none_for_an_unknown_key():
    assert find_entry([WILDCARD], "topic") is None


def test_coerce_bool_both_ways():
    entry = ConfigEntry(key="k", type="bool", default=True, description="x")
    assert coerce(entry, "TRUE") is True
    assert coerce(entry, " off ") is False
    with pytest.raises(InvalidValueError, match="boolean"):
        coerce(entry, "maybe")


def test_coerce_int():
    entry = ConfigEntry(key="k", type="int", default=0, description="x")
    assert coerce(entry, "512") == 512
    with pytest.raises(InvalidValueError, match="integer"):
        coerce(entry, "many")


def test_coerce_list_strips_and_drops_blanks():
    entry = ConfigEntry(key="k", type="list[str]", default=[], description="x")
    assert coerce(entry, " main , develop ,, ") == ["main", "develop"]
    assert coerce(entry, "  ") == []


def test_coerce_str_enforces_allowed():
    entry = ConfigEntry(key="k", type="str", default="a", allowed=("a", "b"), description="x")
    assert coerce(entry, "b") == "b"
    with pytest.raises(InvalidValueError, match=r"\['a', 'b'\]"):
        coerce(entry, "c")


def test_coerce_str_without_allowed_passes_through():
    entry = ConfigEntry(key="k", type="str", default=None, description="x")
    assert coerce(entry, "anything at all") == "anything at all"


def test_unknown_key_error_suggests_a_near_miss():
    catalog = [ConfigEntry(key="workflow.commit_strategy", type="str", default=None, description="x")]
    err = unknown_key_error(catalog, "workflow.commit_stragety")
    assert isinstance(err, UnknownKeyError)
    assert "did you mean" in str(err)
    assert "workflow.commit_strategy" in str(err)


def test_unknown_key_error_omits_the_hint_when_nothing_is_close():
    catalog = [ConfigEntry(key="workflow.commit_strategy", type="str", default=None, description="x")]
    assert "did you mean" not in str(unknown_key_error(catalog, "zzzzzzzzzz"))
