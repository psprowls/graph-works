"""Role to model: packaged catalog, workspace override, explicit argument.

These tests pin two live roles' packaged values: `code_reader` fully, and
`prose_refresher`'s `model_id` and `max_concurrency` in four override tests.
That couples tests about resolution mechanics to tuning decisions about those
roles, so a retune breaks them. Accepted rather than fixed here: the dead
entries this file used to lean on (`preflight`, `scanner`) are exactly what
let the catalog drift, and swapping in live roles is the smaller change.
Decoupling them is its own work item.
"""

from __future__ import annotations

import tomllib

import pytest
from graph_works_core.agent_substrate import roles
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import layout_for
from langchain_core.language_models import BaseChatModel
from models_io import GatewayAccessDenied
from subagents_io.roles import RoleBinding, RoleSpec


def _workspace(tmp_path, manifest_text=None):
    if manifest_text is not None:
        (tmp_path / "workspace.yaml").write_text(manifest_text, encoding="utf-8")
    return layout_for(tmp_path)


class _Recorder:
    """Stands in for a models-io constructor and records how it was called."""

    def __init__(self):
        self.calls = []

    def __call__(self, model_id, **kwargs):
        self.calls.append((model_id, kwargs))
        return object()


def test_packaged_only_resolution_needs_no_layout():
    spec = roles.role_spec("code_reader")
    assert spec == RoleSpec(
        model_id="minimax.minimax-m2.5",
        backend="bedrock",
        region="us-east-1",
        max_tokens=2048,
        max_concurrency=3,
    )


def test_a_workspace_override_wins_over_the_packaged_entry(tmp_path):
    layout = _workspace(tmp_path, "version: 1\nroles:\n  prose_refresher:\n    model_id: zai.glm-5\n")
    assert roles.role_spec("prose_refresher", layout=layout).model_id == "zai.glm-5"


def test_a_partial_override_keeps_every_field_it_does_not_set(tmp_path):
    # The field that matters: an unset override field must not clobber a
    # packaged one.
    layout = _workspace(tmp_path, "version: 1\nroles:\n  prose_refresher:\n    max_tokens: 900\n")
    spec = roles.role_spec("prose_refresher", layout=layout)
    assert spec.max_tokens == 900
    assert spec.model_id == "moonshotai.kimi-k2.5"
    assert spec.max_concurrency == 10


def test_an_explicit_null_override_is_dropped_rather_than_applied(tmp_path):
    layout = _workspace(tmp_path, "version: 1\nroles:\n  prose_refresher:\n    model_id:\n")
    assert roles.role_spec("prose_refresher", layout=layout).model_id == "moonshotai.kimi-k2.5"


def test_a_workspace_only_role_is_its_own_definition(tmp_path):
    layout = _workspace(tmp_path, "version: 1\nroles:\n  auditor:\n    model_id: zai.glm-5\n")
    assert roles.role_spec("auditor", layout=layout).model_id == "zai.glm-5"


def test_a_missing_manifest_resolves_packaged_only(tmp_path):
    assert roles.role_spec("prose_refresher", layout=_workspace(tmp_path)).model_id == "moonshotai.kimi-k2.5"


def test_an_unknown_role_raises_key_error_naming_the_key_that_would_fix_it():
    with pytest.raises(KeyError) as excinfo:
        roles.role_spec("nobody")
    message = str(excinfo.value)
    assert "roles.nobody.model_id" in message
    assert "workspace.yaml" in message


def test_a_role_that_resolves_without_a_model_id_raises_key_error(tmp_path):
    layout = _workspace(tmp_path, "version: 1\nroles:\n  auditor:\n    max_tokens: 10\n")
    with pytest.raises(KeyError) as excinfo:
        roles.role_spec("auditor", layout=layout)
    assert "model_id" in str(excinfo.value)


def test_explicit_overrides_win_over_both_sources(tmp_path):
    layout = _workspace(tmp_path, "version: 1\nroles:\n  prose_refresher:\n    model_id: zai.glm-5\n")
    spec = roles.role_spec("prose_refresher", layout=layout, model_override="x.y", backend_override="vercel")
    assert (spec.model_id, spec.backend) == ("x.y", "vercel")


def test_bedrock_is_the_default_backend_and_carries_the_resolved_region(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(roles, "make_bedrock_llm", recorder)
    roles.make_llm("code_reader")
    assert recorder.calls == [("minimax.minimax-m2.5", {"region": "us-east-1", "max_tokens": 2048})]


def test_an_absent_region_is_left_to_the_provider_default(monkeypatch, tmp_path):
    # RoleSpec.region is str | None and "us-east-1" is make_bedrock_llm's own
    # default — a second copy of that literal here is what band 1 declined to
    # write, so the keyword is omitted rather than defaulted.
    recorder = _Recorder()
    monkeypatch.setattr(roles, "make_bedrock_llm", recorder)
    layout = _workspace(tmp_path, "version: 1\nroles:\n  auditor:\n    model_id: zai.glm-5\n    backend: bedrock\n")
    roles.make_llm("auditor", layout=layout)
    assert recorder.calls == [("zai.glm-5", {"max_tokens": None})]


def test_the_vercel_backend_reaches_the_gateway_with_the_credential_hint(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(roles, "make_gateway_llm", recorder)
    monkeypatch.setenv(roles.GATEWAY_API_KEY_ENV, "sk-test")
    roles.make_llm("code_reader", backend_override="vercel")
    model_id, kwargs = recorder.calls[0]
    assert model_id == "minimax.minimax-m2.5"
    assert kwargs == {"api_key": "sk-test", "credential_hint": "AI_GATEWAY_API_KEY", "max_tokens": 2048}


def test_the_gateway_refuses_when_the_credential_is_unset(monkeypatch):
    monkeypatch.delenv(roles.GATEWAY_API_KEY_ENV, raising=False)
    with pytest.raises(GatewayAccessDenied) as excinfo:
        roles.make_llm("code_reader", backend_override="vercel")
    assert roles.GATEWAY_API_KEY_ENV in str(excinfo.value)


def test_role_binding_pairs_the_spec_with_a_factory_the_caller_calls(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(roles, "make_bedrock_llm", recorder)
    binding = roles.role_binding("prose_refresher")
    assert isinstance(binding, RoleBinding)
    assert binding.spec.max_concurrency == 10
    assert recorder.calls == []  # a factory, not a constructed model
    binding.make_llm()
    assert len(recorder.calls) == 1


def test_make_llm_really_constructs_a_chat_model():
    # No monkeypatch: the wiring, end to end, through the packaged catalog.
    assert isinstance(roles.make_llm("code_reader"), BaseChatModel)


def test_a_pipeline_override_is_not_a_role_override(tmp_path):
    layout = _workspace(
        tmp_path,
        "version: 1\nworkflow:\n  pipeline:\n    branch:\n      skill: other\n"
        "roles:\n  prose_refresher:\n    model_id: zai.glm-5\n",
    )
    assert roles.workspace_roles(layout) == {"prose_refresher": {"model_id": "zai.glm-5"}}


def test_a_manifest_at_an_unsupported_version_refuses_role_resolution(tmp_path):
    # The same gate `manifest.read` applies. Silently honoring overrides out of
    # a manifest this package refuses to read is the disagreement D4 removes.
    layout = _workspace(tmp_path, "version: 2\nroles:\n  scanner:\n    model_id: zai.glm-5\n")
    with pytest.raises(WorkspaceError) as excinfo:
        roles.role_spec("scanner", layout=layout)
    assert "version 2 is not supported" in str(excinfo.value)


def test_a_manifest_with_no_version_key_still_resolves_overrides(tmp_path):
    layout = _workspace(tmp_path, "roles:\n  scanner:\n    model_id: zai.glm-5\n")
    assert roles.role_spec("scanner", layout=layout).model_id == "zai.glm-5"


def test_an_unparseable_manifest_raises_a_workspace_error(tmp_path):
    layout = _workspace(tmp_path, "roles: [unclosed\n")
    with pytest.raises(WorkspaceError):
        roles.role_spec("scanner", layout=layout)


@pytest.fixture
def cold_packaged_catalog():
    """A cold `_packaged_roles`, restored cold afterwards.

    The cache is process-wide and this is the only test that cares whether the
    parse actually ran.
    """
    roles._packaged_roles.cache_clear()
    yield
    roles._packaged_roles.cache_clear()


def test_the_packaged_catalog_is_parsed_once_and_copied_per_call(monkeypatch, cold_packaged_catalog):
    parses = []
    real_load = tomllib.load

    def counting_load(handle):
        parses.append(1)
        return real_load(handle)

    monkeypatch.setattr(roles.tomllib, "load", counting_load)
    first = roles.packaged_roles()
    first["prose_refresher"]["model_id"] = "mutated"
    second = roles.packaged_roles()
    assert len(parses) == 1
    assert second["prose_refresher"]["model_id"] == "moonshotai.kimi-k2.5"


def test_an_unknown_backend_override_is_refused_by_name():
    # `_construct` treats anything that is not "vercel" as Bedrock, so without
    # this gate a typo is a silent Bedrock call.
    with pytest.raises(WorkspaceError) as excinfo:
        roles.role_spec("code_reader", backend_override="bedrok")
    message = str(excinfo.value)
    assert "code_reader" in message
    assert "bedrok" in message
    assert "['bedrock', 'claude_code', 'vercel']" in message


def test_a_hand_edited_unknown_backend_is_refused_the_same_way(tmp_path):
    # `config_io` applies a catalog entry's `allowed=` on write, not on read,
    # so a hand-edited manifest reaches `_construct` exactly as the argument does.
    layout = _workspace(tmp_path, "version: 1\nroles:\n  code_reader:\n    backend: bedrok\n")
    with pytest.raises(WorkspaceError) as excinfo:
        roles.role_spec("code_reader", layout=layout)
    assert "bedrok" in str(excinfo.value)


def test_construct_raises_for_claude_code_with_no_brief_short_circuit(tmp_path):
    # Any role other than ingestor explicitly set to claude_code has no
    # caller-side handoff wired for it -- constructing a model for it must
    # fail loudly, not silently fall into the Bedrock branch by elimination.
    layout = _workspace(tmp_path, "version: 1\nroles:\n  librarian:\n    backend: claude_code\n")
    with pytest.raises(WorkspaceError) as excinfo:
        roles.make_llm("librarian", layout=layout)
    assert "has no model to construct" in str(excinfo.value)


def test_ingestor_defaults_to_the_claude_code_backend_with_no_override():
    assert roles.role_spec("ingestor").backend == "claude_code"
