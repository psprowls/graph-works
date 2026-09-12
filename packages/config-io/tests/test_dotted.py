"""Tests for config_io.dotted — get / set_in / unset_in over nested mappings."""

from __future__ import annotations

from config_io import dotted
from config_io.dotted import merge


def test_get_reads_a_nested_value():
    assert dotted.get({"a": {"b": {"c": 1}}}, "a.b.c") == 1


def test_get_reads_a_top_level_value():
    assert dotted.get({"a": 1}, "a") == 1


def test_get_missing_key_is_none():
    assert dotted.get({"a": {"b": 1}}, "a.z") is None
    assert dotted.get({}, "a.b.c") is None


def test_get_through_a_scalar_is_none():
    # "a.b" is an int, so "a.b.c" cannot exist — this must not raise.
    assert dotted.get({"a": {"b": 1}}, "a.b.c") is None


def test_has_sees_a_present_null():
    # The whole reason `has` exists: `get` returns None for this and for an
    # absent key alike, so a caller cannot tell "set to null" from "unset".
    assert dotted.has({"a": {"b": None}}, "a.b") is True
    assert dotted.get({"a": {"b": None}}, "a.b") is None


def test_has_is_false_for_an_absent_key():
    assert dotted.has({"a": {}}, "a.b") is False
    assert dotted.has({}, "a") is False
    assert dotted.has({"a": {"b": 1}}, "a.z") is False


def test_has_through_a_scalar_is_false_and_does_not_raise():
    # Total, like `get`: no shape of data and no shape of key raises.
    assert dotted.has({"a": {"b": 1}}, "a.b.c") is False


def test_has_sees_a_top_level_key():
    assert dotted.has({"a": 1}, "a") is True


def test_set_in_creates_intermediate_mappings():
    data: dict[str, object] = {}
    dotted.set_in(data, "a.b.c", 1)
    assert data == {"a": {"b": {"c": 1}}}


def test_set_in_replaces_a_non_mapping_intermediate():
    data: dict[str, object] = {"a": 7}
    dotted.set_in(data, "a.b", 1)
    assert data == {"a": {"b": 1}}


def test_set_in_preserves_siblings():
    data: dict[str, object] = {"a": {"keep": 1}}
    dotted.set_in(data, "a.add", 2)
    assert data == {"a": {"keep": 1, "add": 2}}


def test_unset_in_removes_and_prunes_empty_parents():
    data: dict[str, object] = {"a": {"b": {"c": 1}}}
    assert dotted.unset_in(data, "a.b.c") is True
    # Both parents became empty, so both are pruned — a store's
    # omit-when-empty guard must see clean absence, not {"a": {"b": {}}}.
    assert data == {}


def test_unset_in_keeps_a_parent_that_still_has_siblings():
    data: dict[str, object] = {"a": {"b": 1, "c": 2}}
    assert dotted.unset_in(data, "a.b") is True
    assert data == {"a": {"c": 2}}


def test_unset_in_missing_leaf_is_false():
    data: dict[str, object] = {"a": {"b": 1}}
    assert dotted.unset_in(data, "a.z") is False
    assert data == {"a": {"b": 1}}


def test_unset_in_missing_branch_is_false():
    data: dict[str, object] = {"a": 1}
    assert dotted.unset_in(data, "a.b.c") is False
    assert data == {"a": 1}


def test_unset_in_top_level_key():
    data: dict[str, object] = {"a": 1, "b": 2}
    assert dotted.unset_in(data, "a") is True
    assert data == {"b": 2}


# --- merge -----------------------------------------------------------------


def test_merge_recurses_into_mappings_on_both_sides():
    base = {"repositories": {"gw": {"path": "../gw", "ignore": ["a"]}}}
    overlay = {"repositories": {"gw": {"path": "/abs/gw"}}}
    assert merge(base, overlay) == {"repositories": {"gw": {"path": "/abs/gw", "ignore": ["a"]}}}


def test_merge_replaces_a_list_wholesale():
    # Lists are not merged element-wise: a local `ignore:` states the whole list.
    assert merge({"ignore": ["a", "b"]}, {"ignore": ["c"]}) == {"ignore": ["c"]}


def test_merge_replaces_a_scalar():
    assert merge({"topic": "base"}, {"topic": "local"}) == {"topic": "local"}


def test_merge_carries_an_overlay_null_through():
    # D-001: a local null replaces, and `manifest._check_resolved` then applies
    # the one null rule the manifest already states. Dropping it here would
    # make a local null silently inert.
    assert merge({"topic": "base"}, {"topic": None}) == {"topic": None}


def test_merge_copies_keys_present_only_in_base():
    assert merge({"version": 1, "topic": "x"}, {"topic": "y"}) == {"version": 1, "topic": "y"}


def test_merge_replaces_a_base_mapping_with_an_overlay_scalar():
    # Only a mapping on *both* sides recurses.
    assert merge({"layout": {"bundle_dir": "okf"}}, {"layout": "nope"}) == {"layout": "nope"}


def test_merge_replaces_a_base_scalar_with_an_overlay_mapping():
    assert merge({"layout": "nope"}, {"layout": {"bundle_dir": "okf"}}) == {"layout": {"bundle_dir": "okf"}}


def test_merge_mutates_neither_input():
    base = {"a": {"x": 1}}
    overlay = {"a": {"y": 2}}
    merged = merge(base, overlay)
    merged["a"]["z"] = 3
    assert base == {"a": {"x": 1}}
    assert overlay == {"a": {"y": 2}}


def test_merge_of_two_empty_mappings_is_empty():
    assert merge({}, {}) == {}
