"""Tests for config_io.registry's read path — precedence, shadowing, listing."""

from __future__ import annotations

import pytest
from config_fakes import DictStore, LayeredDictStore
from config_io.entries import ConfigEntry
from config_io.errors import UnknownKeyError
from config_io.registry import expand_wildcards, resolve_all, resolve_key

CATALOG = (
    ConfigEntry(key="topic", type="str", default=None, description="Display name."),
    ConfigEntry(
        key="workflow.commit_strategy",
        type="str",
        default="per-task",
        allowed=("per-task", "at-end"),
        env_var="DEMO_COMMIT_STRATEGY",
        description="Commit cadence.",
    ),
    ConfigEntry(key="state_gate.enabled", type="bool", default=True, description="Gate on/off."),
    ConfigEntry(key="roles.*.max_tokens", type="int", default=None, description="Role token cap."),
    ConfigEntry(key="plugin.backend_overrides.*", type="str", default=None, description="Per-plugin backend."),
    ConfigEntry(
        key="DEMO_LOCK_TIMEOUT_MS",
        type="int",
        default=30_000,
        kind="env-only",
        env_var="DEMO_LOCK_TIMEOUT_MS",
        description="Lock timeout.",
    ),
)


def test_default_origin():
    got = resolve_key(CATALOG, "workflow.commit_strategy", store=DictStore(), environ={})
    assert (got.value, got.origin, got.shadowed) == ("per-task", "default", None)


def test_stored_origin():
    store = DictStore({"workflow": {"commit_strategy": "at-end"}})
    got = resolve_key(CATALOG, "workflow.commit_strategy", store=store, environ={})
    assert (got.value, got.origin, got.shadowed) == ("at-end", "manifest", None)


def test_env_beats_stored_and_reports_the_shadowed_value():
    store = DictStore({"workflow": {"commit_strategy": "at-end"}})
    got = resolve_key(CATALOG, "workflow.commit_strategy", store=store, environ={"DEMO_COMMIT_STRATEGY": "per-task"})
    assert (got.value, got.origin, got.shadowed) == ("per-task", "env", "at-end")


def test_env_with_nothing_stored_shadows_nothing():
    got = resolve_key(
        CATALOG, "workflow.commit_strategy", store=DictStore(), environ={"DEMO_COMMIT_STRATEGY": "at-end"}
    )
    assert (got.origin, got.shadowed) == ("env", None)


def test_env_only_entry_never_shadows():
    # Nothing store-kind is involved, so there is nothing to hide behind.
    store = DictStore({"DEMO_LOCK_TIMEOUT_MS": 1})
    got = resolve_key(CATALOG, "DEMO_LOCK_TIMEOUT_MS", store=store, environ={"DEMO_LOCK_TIMEOUT_MS": "5000"})
    assert (got.value, got.origin, got.shadowed) == (5000, "env", None)


def test_env_only_entry_ignores_the_store_entirely():
    store = DictStore({"DEMO_LOCK_TIMEOUT_MS": 1})
    got = resolve_key(CATALOG, "DEMO_LOCK_TIMEOUT_MS", store=store, environ={})
    assert (got.value, got.origin) == (30_000, "default")


def test_env_value_is_coerced_like_the_other_tiers():
    got = resolve_key(CATALOG, "DEMO_LOCK_TIMEOUT_MS", store=DictStore(), environ={"DEMO_LOCK_TIMEOUT_MS": "5000"})
    assert got.value == 5000


def test_malformed_env_value_falls_back_to_the_raw_string():
    # Reads are fail-open: the consumer surfaces the problem, resolution does
    # not raise on the way past.
    got = resolve_key(CATALOG, "DEMO_LOCK_TIMEOUT_MS", store=DictStore(), environ={"DEMO_LOCK_TIMEOUT_MS": "abc"})
    assert (got.value, got.origin) == ("abc", "env")


def test_blank_env_value_does_not_win():
    store = DictStore({"workflow": {"commit_strategy": "at-end"}})
    got = resolve_key(CATALOG, "workflow.commit_strategy", store=store, environ={"DEMO_COMMIT_STRATEGY": "   "})
    assert (got.value, got.origin) == ("at-end", "manifest")


def test_environ_is_required():
    # No os.environ fallback: omitting it is a TypeError, not a silent read of
    # the live process environment.
    with pytest.raises(TypeError):
        resolve_key(CATALOG, "topic", store=DictStore())


def test_unknown_key_raises():
    with pytest.raises(UnknownKeyError, match="did you mean"):
        resolve_key(CATALOG, "workflow.commit_stragety", store=DictStore(), environ={})


def test_wildcard_key_resolves_from_the_store():
    store = DictStore({"roles": {"scanner": {"max_tokens": 512}}})
    got = resolve_key(CATALOG, "roles.scanner.max_tokens", store=store, environ={})
    assert (got.value, got.origin) == (512, "manifest")


def test_expand_wildcards_handles_the_head_plus_field_shape():
    store = DictStore({"roles": {"scanner": {"max_tokens": 512}, "linter": {"other": 1}}})
    assert expand_wildcards(CATALOG, store=store) == ["roles.scanner.max_tokens"]


def test_expand_wildcards_handles_the_head_only_shape():
    store = DictStore({"plugin": {"backend_overrides": {"orca": "cli", "local": "subprocess"}}})
    assert sorted(expand_wildcards(CATALOG, store=store)) == [
        "plugin.backend_overrides.local",
        "plugin.backend_overrides.orca",
    ]


def test_expand_wildcards_skips_a_non_mapping_block():
    assert expand_wildcards(CATALOG, store=DictStore({"roles": "not a mapping"})) == []


def test_expand_wildcards_on_an_empty_store_is_empty():
    assert expand_wildcards(CATALOG, store=DictStore()) == []


def test_resolve_all_covers_every_concrete_key_plus_expansions():
    store = DictStore({"roles": {"scanner": {"max_tokens": 512}}})
    keys = [r.key for r in resolve_all(CATALOG, store=store, environ={})]
    assert keys == [
        "topic",
        "workflow.commit_strategy",
        "state_gate.enabled",
        "DEMO_LOCK_TIMEOUT_MS",
        "roles.scanner.max_tokens",
    ]


def test_resolve_all_carries_origins_through():
    store = DictStore({"topic": "My Wiki"})
    by_key = {r.key: r for r in resolve_all(CATALOG, store=store, environ={})}
    assert by_key["topic"].origin == "manifest"
    assert by_key["state_gate.enabled"].origin == "default"


# --- the local layer -------------------------------------------------------


def test_a_key_set_in_both_layers_resolves_local_and_shadows_the_base():
    store = LayeredDictStore(
        base={"workflow": {"commit_strategy": "per-task"}},
        overlay={"workflow": {"commit_strategy": "at-end"}},
    )
    got = resolve_key(CATALOG, "workflow.commit_strategy", store=store, environ={})
    assert (got.value, got.origin, got.shadowed) == ("at-end", "local", "per-task")


def test_a_key_set_only_in_the_overlay_resolves_local_with_no_shadow():
    store = LayeredDictStore(overlay={"topic": "Laptop"})
    got = resolve_key(CATALOG, "topic", store=store, environ={})
    assert (got.value, got.origin, got.shadowed) == ("Laptop", "local", None)


def test_a_key_set_only_in_the_base_still_resolves_manifest():
    store = LayeredDictStore(base={"topic": "Committed"})
    got = resolve_key(CATALOG, "topic", store=store, environ={})
    assert (got.value, got.origin, got.shadowed) == ("Committed", "manifest", None)


def test_a_key_in_neither_layer_resolves_default():
    got = resolve_key(CATALOG, "state_gate.enabled", store=LayeredDictStore(), environ={})
    assert (got.value, got.origin, got.shadowed) == (True, "default", None)


def test_env_still_outranks_the_local_layer_and_shadows_the_merged_value():
    # Precedence: env var > local explicit > base explicit > default.
    store = LayeredDictStore(
        base={"workflow": {"commit_strategy": "per-task"}},
        overlay={"workflow": {"commit_strategy": "at-end"}},
    )
    got = resolve_key(
        CATALOG,
        "workflow.commit_strategy",
        store=store,
        environ={"DEMO_COMMIT_STRATEGY": "per-task"},
    )
    assert (got.value, got.origin, got.shadowed) == ("per-task", "env", "at-end")


def test_resolve_all_expands_a_wildcard_key_that_exists_only_in_the_overlay():
    store = LayeredDictStore(overlay={"roles": {"planner": {"max_tokens": 4096}}})
    got = {item.key: (item.value, item.origin) for item in resolve_all(CATALOG, store=store, environ={})}
    assert got["roles.planner.max_tokens"] == (4096, "local")


def test_a_plain_store_never_reports_the_local_origin():
    store = DictStore({"topic": "Committed"})
    assert resolve_key(CATALOG, "topic", store=store, environ={}).origin == "manifest"
