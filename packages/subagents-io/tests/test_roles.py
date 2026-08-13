"""Role resolution: a table-driven pass over the merge and both KeyErrors."""

from __future__ import annotations

import dataclasses

import pytest
from subagents_io.roles import RoleBinding, RoleSpec, resolve_role_spec

PACKAGED = {
    "model_id": "vendor.model-1:0",
    "region": "us-west-2",
    "max_tokens": 4096,
    "max_concurrency": 5,
}


def test_packaged_only_resolves_every_field():
    spec = resolve_role_spec("librarian", PACKAGED)
    assert spec == RoleSpec(
        model_id="vendor.model-1:0",
        backend="bedrock",
        region="us-west-2",
        max_tokens=4096,
        max_concurrency=5,
    )


def test_a_partial_override_merges_field_by_field():
    spec = resolve_role_spec("librarian", PACKAGED, {"max_tokens": 128})
    assert spec.max_tokens == 128
    # Everything the override did not set keeps its packaged value.
    assert spec.model_id == "vendor.model-1:0"
    assert spec.region == "us-west-2"
    assert spec.max_concurrency == 5


def test_an_override_only_role_is_the_definition():
    spec = resolve_role_spec("newcomer", None, {"model_id": "vendor.model-2:0"})
    assert spec.model_id == "vendor.model-2:0"
    assert spec.backend == "bedrock"
    assert spec.region is None
    assert spec.max_tokens is None
    assert spec.max_concurrency == 3


def test_absent_from_both_sources_raises_keyerror_naming_the_role():
    with pytest.raises(KeyError) as excinfo:
        resolve_role_spec("ghost", None, None)
    assert excinfo.value.args == ("ghost",)


def test_override_absent_defaults_to_none():
    # The third positional is optional; a packaged-only caller passes two args.
    assert resolve_role_spec("librarian", PACKAGED).model_id == "vendor.model-1:0"


def test_a_merge_with_no_model_id_raises_a_message_carrying_keyerror():
    with pytest.raises(KeyError) as excinfo:
        resolve_role_spec("partial", None, {"max_tokens": 10})
    assert "has no model_id" in str(excinfo.value)


def test_model_override_beats_both_sources():
    spec = resolve_role_spec(
        "librarian",
        PACKAGED,
        {"model_id": "vendor.workspace:0"},
        model_override="vendor.forced:0",
    )
    assert spec.model_id == "vendor.forced:0"


def test_model_override_supplies_a_model_id_the_merge_lacks():
    # The no-model_id KeyError must not fire when the override provides one.
    spec = resolve_role_spec("partial", None, {"max_tokens": 10}, model_override="vendor.forced:0")
    assert spec.model_id == "vendor.forced:0"
    assert spec.max_tokens == 10


def test_backend_override_beats_both_sources():
    spec = resolve_role_spec(
        "librarian",
        {**PACKAGED, "backend": "bedrock"},
        {"backend": "vercel"},
        backend_override="bedrock",
    )
    assert spec.backend == "bedrock"


def test_the_override_can_set_the_backend_without_an_override_kwarg():
    spec = resolve_role_spec("librarian", PACKAGED, {"backend": "vercel"})
    assert spec.backend == "vercel"


def test_region_has_no_provider_default():
    # "us-east-1" is a Bedrock fact and lives in models-io, not here.
    assert resolve_role_spec("librarian", {"model_id": "m"}).region is None


def test_max_concurrency_is_coerced_to_int():
    # The legacy call site did int(cfg.get("max_concurrency", 3)); a manifest
    # is YAML and can hand back a string.
    assert resolve_role_spec("librarian", {"model_id": "m", "max_concurrency": "7"}).max_concurrency == 7


def test_the_spec_is_frozen():
    spec = resolve_role_spec("librarian", PACKAGED)
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.model_id = "nope"


def test_the_binding_is_frozen_and_calls_its_factory():
    calls = []

    def factory():
        calls.append(1)
        return "llm"

    binding = RoleBinding(spec=resolve_role_spec("librarian", PACKAGED), make_llm=factory)
    assert binding.make_llm() == "llm"
    assert binding.make_llm() == "llm"
    assert len(calls) == 2  # a factory, not a cached client
    with pytest.raises(dataclasses.FrozenInstanceError):
        binding.spec = None
