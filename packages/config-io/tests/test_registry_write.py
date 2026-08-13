"""Tests for config_io.registry's write path — refusals, rollback, persistence."""

from __future__ import annotations

import json

import pytest
from config_fakes import DictStore, InvalidAfterWriteStore, LossyStore
from config_io.entries import ConfigEntry
from config_io.errors import (
    EnvOnlyKeyError,
    InvalidValueError,
    LinkFileKeyError,
    ProvenanceKeyError,
    ReadOnlyKeyError,
    RegistryError,
    SecretKeyError,
    UnknownKeyError,
)
from config_io.projection import PROJECTION_FILENAME
from config_io.registry import set_key, unset_key
from config_io.store import PlainYamlStore

CATALOG = (
    ConfigEntry(key="topic", type="str", default=None, description="Display name."),
    ConfigEntry(
        key="workflow.commit_strategy",
        type="str",
        default="per-task",
        allowed=("per-task", "at-end"),
        description="Commit cadence.",
    ),
    ConfigEntry(key="state_gate.enabled", type="bool", default=True, description="Gate on/off."),
    ConfigEntry(key="state_gate.branches", type="list[str]", default=["main"], description="Gated branches."),
    ConfigEntry(key="roles.*.max_tokens", type="int", default=None, description="Role token cap."),
    ConfigEntry(
        key="repo-directory",
        type="str",
        default=None,
        write_policy="link-file",
        write_hint="Edit the machine-local link file by hand.",
        description="Repo link.",
    ),
    ConfigEntry(key="initialized_at", type="str", default=None, write_policy="provenance", description="Stamped."),
    ConfigEntry(
        key="workflow.auto_drive.overrides",
        type="list",
        default=None,
        write_policy="read-only",
        description="Structured override rules.",
    ),
    ConfigEntry(
        key="DEMO_LOCK_TIMEOUT_MS",
        type="int",
        default=30_000,
        kind="env-only",
        env_var="DEMO_LOCK_TIMEOUT_MS",
        description="Lock timeout.",
    ),
    ConfigEntry(
        key="DEMO_API_KEY",
        type="str",
        default=None,
        kind="env-only",
        env_var="DEMO_API_KEY",
        secret=True,
        description="Credential.",
    ),
)


# --- the happy path ---------------------------------------------------------


def test_set_writes_and_returns_the_resolved_value():
    store = DictStore()
    got = set_key(CATALOG, "topic", "My Wiki", store=store)
    assert (got.key, got.value, got.origin) == ("topic", "My Wiki", "manifest")
    assert store.data == {"topic": "My Wiki"}


def test_set_coerces_bool_and_list_into_nested_blocks():
    store = DictStore()
    set_key(CATALOG, "state_gate.enabled", "false", store=store)
    set_key(CATALOG, "state_gate.branches", "main,develop", store=store)
    assert store.data == {"state_gate": {"enabled": False, "branches": ["main", "develop"]}}


def test_set_writes_a_wildcard_key():
    store = DictStore()
    set_key(CATALOG, "roles.scanner.max_tokens", "512", store=store)
    assert store.data == {"roles": {"scanner": {"max_tokens": 512}}}


def test_set_rejects_a_value_outside_allowed():
    with pytest.raises(InvalidValueError, match="at-end"):
        set_key(CATALOG, "workflow.commit_strategy", "sometimes", store=DictStore())


def test_unset_removes_and_prunes():
    store = DictStore()
    set_key(CATALOG, "state_gate.enabled", "false", store=store)
    assert unset_key(CATALOG, "state_gate.enabled", store=store) is True
    assert store.data == {}


def test_unset_of_an_unstored_key_is_false_and_writes_nothing():
    store = DictStore({"topic": "keep"})
    before = json.dumps(store.data)
    assert unset_key(CATALOG, "workflow.commit_strategy", store=store) is False
    assert json.dumps(store.data) == before


# --- the refusal taxonomy, driven by policy rather than by key name ---------


def test_unknown_key_is_refused_first():
    with pytest.raises(UnknownKeyError, match="did you mean"):
        set_key(CATALOG, "workflow.commit_stragety", "at-end", store=DictStore())


def test_link_file_policy_refusal_carries_the_catalog_hint():
    with pytest.raises(LinkFileKeyError) as excinfo:
        set_key(CATALOG, "repo-directory", "/x", store=DictStore())
    assert "Edit the machine-local link file by hand." in str(excinfo.value)


def test_provenance_policy_refusal():
    with pytest.raises(ProvenanceKeyError, match="provenance"):
        set_key(CATALOG, "initialized_at", "2020-01-01", store=DictStore())


def test_read_only_policy_refusal():
    with pytest.raises(ReadOnlyKeyError, match="read-only"):
        set_key(CATALOG, "workflow.auto_drive.overrides", "[]", store=DictStore())


def test_secret_refusal():
    with pytest.raises(SecretKeyError, match="never stored"):
        set_key(CATALOG, "DEMO_API_KEY", "sk-x", store=DictStore())


def test_env_only_refusal():
    with pytest.raises(EnvOnlyKeyError, match="DEMO_LOCK_TIMEOUT_MS"):
        set_key(CATALOG, "DEMO_LOCK_TIMEOUT_MS", "1", store=DictStore())


def test_refusals_apply_to_unset_too():
    with pytest.raises(ReadOnlyKeyError):
        unset_key(CATALOG, "workflow.auto_drive.overrides", store=DictStore())


def test_a_refusal_without_a_hint_names_no_file():
    # config-io keeps zero file names; remediation wording is the caller's.
    with pytest.raises(ProvenanceKeyError) as excinfo:
        set_key(CATALOG, "initialized_at", "2020-01-01", store=DictStore())
    message = str(excinfo.value)
    assert ".yaml" not in message and ".json" not in message


def test_a_refusal_names_the_concrete_key_not_the_wildcard_pattern():
    catalog = (
        ConfigEntry(key="roles.*.model_id", type="str", default=None, write_policy="read-only", description="x"),
    )
    with pytest.raises(ReadOnlyKeyError, match=r"roles\.scanner\.model_id"):
        set_key(catalog, "roles.scanner.model_id", "haiku", store=DictStore())


def test_refusal_beats_coercion():
    # The policy check runs before any I/O and before coercion, so a refused
    # key with a garbage value reports the refusal, not the bad value.
    with pytest.raises(ReadOnlyKeyError):
        set_key(CATALOG, "workflow.auto_drive.overrides", "not-a-list", store=DictStore())


# --- validate-and-restore ---------------------------------------------------


def test_a_value_that_invalidates_the_store_is_rolled_back():
    store = InvalidAfterWriteStore("state_gate.branches", [], {"topic": "keep"})
    before = json.dumps(store.data)
    with pytest.raises(InvalidValueError, match="branches"):
        set_key(CATALOG, "state_gate.branches", "  ", store=store)
    assert json.dumps(store.data) == before


def test_rollback_leaves_a_pre_existing_projection_untouched(tmp_path):
    store = InvalidAfterWriteStore("state_gate.branches", [], {"topic": "keep"})
    target = tmp_path / PROJECTION_FILENAME
    set_key(CATALOG, "topic", "My Wiki", store=store, projection=target)
    before = target.read_bytes()
    with pytest.raises(InvalidValueError):
        set_key(CATALOG, "state_gate.branches", "  ", store=store, projection=target)
    assert target.read_bytes() == before


# --- the persistence check --------------------------------------------------


def test_a_dropped_key_is_rolled_back_and_reported():
    # LossyStore's serialiser has a fixed allowlist, so `search` vanishes
    # without an error — exactly the failure this check exists for.
    catalog = (
        *CATALOG,
        ConfigEntry(key="search.provider", type="str", default=None, description="Not a serialized block."),
    )
    store = LossyStore(allowed={"topic", "state_gate"}, data={"topic": "keep"})
    before = json.dumps(store.data)
    with pytest.raises(RegistryError, match="did not survive"):
        set_key(catalog, "search.provider", "bm25", store=store)
    assert json.dumps(store.data) == before


def test_absence_because_the_value_equals_the_default_is_not_a_drop():
    # A store may legitimately omit a key whose value equals the declared
    # default; that is equivalent to unset, not a lost write.
    catalog = (
        *CATALOG,
        ConfigEntry(key="search.provider", type="str", default="bm25", description="Omitted when default."),
    )
    store = LossyStore(allowed={"topic", "state_gate"})
    got = set_key(catalog, "search.provider", "bm25", store=store)
    assert got.value == "bm25"
    assert "search" not in store.data


# --- the projection keyword -------------------------------------------------


def test_set_regenerates_the_projection_when_given_one(tmp_path):
    target = tmp_path / PROJECTION_FILENAME
    set_key(CATALOG, "topic", "My Wiki", store=DictStore(), projection=target)
    assert json.loads(target.read_text(encoding="utf-8"))["topic"] == "My Wiki"


def test_set_writes_no_projection_when_not_given_one(tmp_path):
    # D4's accepted cost: write => regenerate holds only when the caller opts
    # in. Documented in the README, asserted here.
    set_key(CATALOG, "topic", "My Wiki", store=DictStore())
    assert list(tmp_path.iterdir()) == []


def test_unset_regenerates_the_projection_when_given_one(tmp_path):
    target = tmp_path / PROJECTION_FILENAME
    store = DictStore()
    set_key(CATALOG, "topic", "My Wiki", store=store, projection=target)
    unset_key(CATALOG, "topic", store=store, projection=target)
    assert "topic" not in json.loads(target.read_text(encoding="utf-8"))


def test_unset_of_an_unstored_key_does_not_touch_the_projection(tmp_path):
    target = tmp_path / PROJECTION_FILENAME
    store = DictStore()
    set_key(CATALOG, "topic", "My Wiki", store=store, projection=target)
    before = target.read_bytes()
    assert unset_key(CATALOG, "workflow.commit_strategy", store=store, projection=target) is False
    assert target.read_bytes() == before


def test_end_to_end_against_the_shipped_yaml_store(tmp_path):
    # The package is usable with no caller: PlainYamlStore + a projection.
    store = PlainYamlStore(tmp_path / "config.yaml")
    target = tmp_path / ".derived" / PROJECTION_FILENAME
    set_key(CATALOG, "topic", "My Wiki", store=store, projection=target)
    set_key(CATALOG, "state_gate.branches", "main,develop", store=store, projection=target)
    assert store.read() == {"topic": "My Wiki", "state_gate": {"branches": ["main", "develop"]}}
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["state_gate"]["branches"] == ["main", "develop"]
    assert payload["_meta"]["source_sha256"] is not None
