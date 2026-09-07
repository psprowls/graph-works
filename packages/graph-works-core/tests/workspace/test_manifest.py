"""`workspace.yaml` — a thin manifest, stored through config-io."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from config_io import ConfigEntry, PlainYamlStore, ReadOnlyKeyError, Resolved, UnknownKeyError, resolve_key
from graph_works_core.workspace.errors import WorkspaceError, WorkspaceNotFound
from graph_works_core.workspace.layout import layout_for
from graph_works_core.workspace.manifest import (
    CATALOG,
    MANIFEST_VERSION,
    WORKSPACE_DIR_ENV,
    checked,
    checked_bool,
    checked_int,
    checked_str,
    defaults,
    read,
    render_initial,
    resolve_checked_all,
    resolve_checked_key,
    set_value,
)

TODAY = date(2026, 8, 13)


def _write(tmp_path, text):
    path = tmp_path / "workspace.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_catalog_carries_exactly_the_documented_keys():
    assert [entry.key for entry in CATALOG] == [
        "version",
        "initialized_at",
        "topic",
        "layout.bundle_dir",
        "layout.config_dir",
        "layout.cache_dir",
        "layout.worktrees_dir",
        "repositories.*.path",
        "repositories.*.ignore",
        "ignore",
        "state_gate.enabled",
        "state_gate.branches",
        "roles.*.model_id",
        "roles.*.backend",
        "roles.*.region",
        "roles.*.max_tokens",
        "roles.*.max_concurrency",
        "workflow.pipeline.*.skill",
        "workflow.pipeline.*.mode",
        "workflow.pipeline.*.prompt_tail",
        "workflow.auto_drive.max_parallel",
        "workflow.auto_drive.permission_mode",
        "workflow.auto_drive.supervise_merges",
        "workspace.dir",
    ]


def test_the_write_policies_match_the_table():
    policies = {entry.key: entry.write_policy for entry in CATALOG}
    assert policies["version"] == "read-only"
    assert policies["initialized_at"] == "provenance"
    assert policies["topic"] == "writable"
    assert policies["layout.cache_dir"] == "writable"


def test_the_discovery_override_is_documented_as_env_only():
    entry = next(entry for entry in CATALOG if entry.key == "workspace.dir")
    assert entry.kind == "env-only"
    assert entry.env_var == WORKSPACE_DIR_ENV == "GRAPH_WORKS_DIR"


def test_a_missing_manifest_is_not_a_workspace(tmp_path):
    with pytest.raises(WorkspaceNotFound) as excinfo:
        read(tmp_path / "workspace.yaml")
    assert "workspace.yaml" in str(excinfo.value)


def test_a_minimal_manifest_resolves_every_layout_key_to_its_default(tmp_path):
    manifest = read(_write(tmp_path, "version: 1\n"))
    assert manifest == defaults()


def test_an_override_is_read_back(tmp_path):
    manifest = read(_write(tmp_path, "version: 1\nlayout:\n  bundle_dir: wiki\n"))
    assert manifest.bundle_dir == "wiki"
    assert manifest.config_dir == ".gw"


def test_an_explicit_null_on_cache_dir_reads_as_absent_rather_than_refused(tmp_path):
    """`layout.cache_dir`/`layout.worktrees_dir` now default to `None`, so a
    hand-written `null` is just the ordinary absent value -- not the
    deliberate-null-hides-a-real-default case `_check_resolved` refuses."""
    manifest = read(_write(tmp_path, "version: 1\nlayout:\n  cache_dir: null\n  worktrees_dir: null\n"))
    assert manifest.cache_dir is None
    assert manifest.worktrees_dir is None


def test_a_topic_and_a_stamp_are_read_back(tmp_path):
    manifest = read(_write(tmp_path, 'version: 1\ntopic: "My Works"\ninitialized_at: "2026-08-13"\n'))
    assert manifest.topic == "My Works"
    assert manifest.initialized_at == "2026-08-13"


def test_a_blank_topic_reads_as_absent(tmp_path):
    assert read(_write(tmp_path, 'version: 1\ntopic: "  "\n')).topic is None


@pytest.mark.parametrize("raw", ["version: 2\n", "version: 0\n"])
def test_a_foreign_version_is_refused_with_no_migration_offered(tmp_path, raw):
    with pytest.raises(WorkspaceError) as excinfo:
        read(_write(tmp_path, raw))
    assert "no migration path" in str(excinfo.value)


@pytest.mark.parametrize("raw", ["version: true\n", 'version: "1"\n', "version:\n  a: b\n"])
def test_a_non_integer_version_is_refused(tmp_path, raw):
    with pytest.raises(WorkspaceError) as excinfo:
        read(_write(tmp_path, raw))
    assert "must be an integer" in str(excinfo.value)


def test_an_empty_manifest_takes_the_default_version(tmp_path):
    assert read(_write(tmp_path, "")).version == MANIFEST_VERSION


def test_a_manifest_that_is_not_yaml_is_refused_as_a_workspace_error(tmp_path):
    with pytest.raises(WorkspaceError):
        read(_write(tmp_path, "version: [unclosed\n"))


def test_a_manifest_that_is_not_a_mapping_is_refused(tmp_path):
    with pytest.raises(WorkspaceError):
        read(_write(tmp_path, "- one\n- two\n"))


def test_the_env_only_key_never_leaks_into_the_resolved_manifest(tmp_path):
    path = _write(tmp_path, "version: 1\n")
    assert read(path, environ={WORKSPACE_DIR_ENV: "/elsewhere"}) == defaults()


def test_a_write_preserves_every_other_key(tmp_path):
    path = _write(tmp_path, 'version: 1\ninitialized_at: "2026-08-13"\nlayout:\n  bundle_dir: wiki\n')
    set_value(path, "topic", "My Works")
    manifest = read(path)
    assert manifest.topic == "My Works"
    assert manifest.initialized_at == "2026-08-13"
    assert manifest.bundle_dir == "wiki"
    assert manifest.version == 1


def test_a_layout_override_round_trips_through_the_write_path(tmp_path):
    path = _write(tmp_path, "version: 1\n")
    set_value(path, "layout.cache_dir", "var/cache")
    assert read(path).cache_dir == "var/cache"


def test_a_repository_path_round_trips_through_the_write_path(tmp_path):
    path = _write(tmp_path, "version: 1\n")
    set_value(path, "repositories.agent-workspace.path", "../..")
    from config_io import PlainYamlStore, expand_wildcards

    store = PlainYamlStore(path)
    assert expand_wildcards(CATALOG, store=store) == ["repositories.agent-workspace.path"]
    assert resolve_key(CATALOG, "repositories.agent-workspace.path", store=store, environ={}).value == "../.."


def test_a_repository_ignore_round_trips_through_the_write_path(tmp_path):
    path = _write(tmp_path, "version: 1\nrepositories:\n  agent-workspace:\n    path: ../..\n")
    set_value(path, "repositories.agent-workspace.ignore", "tmp/**, *.lock")
    from config_io import PlainYamlStore

    store = PlainYamlStore(path)
    assert resolve_key(CATALOG, "repositories.agent-workspace.ignore", store=store, environ={}).value == [
        "tmp/**",
        "*.lock",
    ]


def test_render_initial_carries_repositories_and_ignore_content_through_the_hand_rendered_yaml(tmp_path):
    text = render_initial(
        today=TODAY,
        repositories={"agent-workspace": "../..", "other-repo": "../other-repo"},
        ignore=["tmp/**", "*.lock"],
    )
    manifest_path = _write(tmp_path, text)
    import yaml as _yaml

    parsed = _yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert parsed["repositories"] == {
        "agent-workspace": {"path": "../.."},
        "other-repo": {"path": "../other-repo"},
    }
    assert parsed["ignore"] == ["tmp/**", "*.lock"]
    # And every rendered value is still readable back through the catalog.
    from config_io import PlainYamlStore

    store = PlainYamlStore(manifest_path)
    assert resolve_key(CATALOG, "repositories.agent-workspace.path", store=store, environ={}).value == "../.."
    assert resolve_key(CATALOG, "ignore", store=store, environ={}).value == ["tmp/**", "*.lock"]


def test_render_initial_quotes_a_repository_name_needing_it(tmp_path):
    text = render_initial(today=TODAY, repositories={"needs: quoting": "../.."})
    manifest_path = _write(tmp_path, text)
    import yaml as _yaml

    parsed = _yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert parsed["repositories"] == {"needs: quoting": {"path": "../.."}}


def test_state_gate_defaults_when_the_block_is_absent(tmp_path):
    path = _write(tmp_path, "version: 1\n")
    store = PlainYamlStore(path)
    assert resolve_key(CATALOG, "state_gate.enabled", store=store, environ={}).value is True
    assert resolve_key(CATALOG, "state_gate.branches", store=store, environ={}).value == ["main"]


def test_a_set_roles_write_leaves_repositories_ignore_and_state_gate_content_intact(tmp_path):
    # `PlainYamlStore.write` re-dumps the whole file through `yaml.safe_dump`
    # (see `packages/config-io/src/config_io/store.py`) rather than splicing
    # like okf-io's writer, so quoting/flow-style is not preserved byte for
    # byte across an unrelated write — only the parsed content is a contract.
    text = (
        "version: 1\n"
        "repositories:\n"
        "  agent-workspace:\n"
        "    path: ../..\n"
        "ignore:\n"
        '  - "tmp/**"\n'
        "state_gate:\n"
        "  enabled: true\n"
        "  branches: [main]\n"
    )
    path = _write(tmp_path, text)
    set_value(path, "roles.librarian.model_id", "some-model")
    import yaml as _yaml

    after = _yaml.safe_load(path.read_text(encoding="utf-8"))
    assert after["repositories"] == {"agent-workspace": {"path": "../.."}}
    assert after["ignore"] == ["tmp/**"]
    assert after["state_gate"] == {"enabled": True, "branches": ["main"]}
    assert after["roles"]["librarian"]["model_id"] == "some-model"


def test_the_version_is_hand_edit_only(tmp_path):
    with pytest.raises(ReadOnlyKeyError):
        set_value(_write(tmp_path, "version: 1\n"), "version", "2")


def test_an_unknown_key_is_refused_by_the_catalog(tmp_path):
    with pytest.raises(UnknownKeyError):
        set_value(_write(tmp_path, "version: 1\n"), "state_gate.bogus", "false")


def test_the_initial_manifest_parses_back_and_carries_no_layout_block(tmp_path):
    text = render_initial(today=TODAY, topic="My Works")
    assert "layout" not in text
    manifest = read(_write(tmp_path, text))
    assert manifest.version == MANIFEST_VERSION
    assert manifest.initialized_at == "2026-08-13"
    assert manifest.topic == "My Works"
    assert manifest.bundle_dir == "okf"


@pytest.mark.parametrize("topic", [None, "", "   "])
def test_the_initial_manifest_omits_an_absent_topic(tmp_path, topic):
    text = render_initial(today=TODAY, topic=topic)
    assert "topic" not in text
    assert read(_write(tmp_path, text)).topic is None


def test_a_topic_needing_quoting_survives_the_hand_rendered_yaml(tmp_path):
    text = render_initial(today=TODAY, topic="works: the sequel  # really")
    assert read(_write(tmp_path, text)).topic == "works: the sequel  # really"


def test_the_catalog_declares_the_five_role_override_fields():
    keys = [entry.key for entry in CATALOG if entry.key.startswith("roles.")]
    assert keys == [
        "roles.*.model_id",
        "roles.*.backend",
        "roles.*.region",
        "roles.*.max_tokens",
        "roles.*.max_concurrency",
    ]


def test_every_role_override_defaults_to_none():
    # A non-None default would materialize an absent field and shadow the
    # packaged value, which is exactly what partial override must not do.
    for entry in CATALOG:
        if entry.key.startswith("roles."):
            assert entry.default is None, entry.key


def test_the_backend_override_is_a_closed_vocabulary():
    entry = next(entry for entry in CATALOG if entry.key == "roles.*.backend")
    assert entry.allowed == ("bedrock", "vercel", "claude_code")


def test_every_wildcard_catalog_entry_is_a_role_pipeline_or_repository_entry():
    # `roles.py` and `pipeline.py` each regroup `expand_wildcards` results and
    # each filters by its own prefix; `repositories.*` is read directly by
    # `code_wiki_okf.config.load_config`, not through this catalog's
    # `Manifest` — it exists here only so `gw config get/set` can reach it.
    # This is the assumption that makes the pair (now trio) exhaustive: a
    # fourth wildcard family added with no consumer would be silently
    # dropped, and this test is what reports it.
    for entry in CATALOG:
        if "*" in entry.key:
            assert entry.key.startswith(("roles.", "workflow.pipeline.", "repositories.")), entry.key


def test_role_keys_expand_only_for_roles_present_in_the_file(tmp_path):
    from config_io import PlainYamlStore, expand_wildcards

    path = _write(
        tmp_path,
        "version: 1\nroles:\n  scanner:\n    model_id: zai.glm-5\n    max_tokens: 900\n",
    )
    assert sorted(expand_wildcards(CATALOG, store=PlainYamlStore(path))) == [
        "roles.scanner.max_tokens",
        "roles.scanner.model_id",
    ]


def test_a_roles_block_leaves_the_manifest_untouched(tmp_path):
    path = _write(tmp_path, "version: 1\nroles:\n  scanner:\n    model_id: zai.glm-5\n")
    assert read(path) == defaults()


# --- the read-time validation gate ------------------------------------------
#
# `_routing_rules` in commands/orchestrate.py already states the policy this
# implements: a hand-edited manifest bypasses config-io's set-time checks, so
# a reader that trusts stored values is trusting a file nothing validated.


def _layout(tmp_path, text="version: 1\n"):
    (tmp_path / "workspace.yaml").write_text(text, encoding="utf-8")
    return layout_for(tmp_path)


def _resolved(tmp_path, key, text):
    """One key resolved out of a hand-written manifest, as a reader sees it."""
    (tmp_path / "workspace.yaml").write_text(text, encoding="utf-8")
    store = PlainYamlStore(tmp_path / "workspace.yaml")
    return resolve_key(CATALOG, key, store=store, environ={})


def test_checked_refuses_a_value_outside_the_entrys_allowed(tmp_path):
    resolved = _resolved(
        tmp_path,
        "workflow.pipeline.exploration.mode",
        "version: 1\nworkflow:\n  pipeline:\n    exploration:\n      mode: yolo\n",
    )
    assert resolved.origin == "manifest"
    with pytest.raises(WorkspaceError) as excinfo:
        checked(resolved, source=tmp_path / "workspace.yaml")
    assert "workflow.pipeline.exploration.mode" in str(excinfo.value)
    assert "'yolo'" in str(excinfo.value)


def test_checked_refuses_a_non_string_where_str_is_declared(tmp_path):
    resolved = _resolved(
        tmp_path,
        "workflow.pipeline.single.skill",
        "version: 1\nworkflow:\n  pipeline:\n    single:\n      skill: 3\n",
    )
    with pytest.raises(WorkspaceError, match="expects a string"):
        checked(resolved, source=tmp_path / "workspace.yaml")


def test_checked_refuses_a_non_integer_where_int_is_declared(tmp_path):
    resolved = _resolved(
        tmp_path,
        "workflow.auto_drive.max_parallel",
        'version: 1\nworkflow:\n  auto_drive:\n    max_parallel: "4"\n',
    )
    with pytest.raises(WorkspaceError, match="expects an integer"):
        checked(resolved, source=tmp_path / "workspace.yaml")


def test_checked_refuses_a_bool_where_int_is_declared(tmp_path):
    # `isinstance(True, int)` is True, so without the carve-out this check
    # passes the exact value it exists to catch: `max_parallel: true` became 1.
    resolved = _resolved(
        tmp_path,
        "workflow.auto_drive.max_parallel",
        "version: 1\nworkflow:\n  auto_drive:\n    max_parallel: true\n",
    )
    with pytest.raises(WorkspaceError, match="expects an integer"):
        checked(resolved, source=tmp_path / "workspace.yaml")


def test_checked_accepts_a_well_typed_manifest_value(tmp_path):
    resolved = _resolved(
        tmp_path,
        "workflow.auto_drive.max_parallel",
        "version: 1\nworkflow:\n  auto_drive:\n    max_parallel: 5\n",
    )
    assert checked(resolved, source=tmp_path / "workspace.yaml") == 5


def test_checked_validates_the_two_types_the_catalog_does_not_yet_use():
    # `checked` validates against `ConfigEntry.type`, not against today's
    # catalog: a `bool` or `list[str]` key added later is covered on arrival.
    bool_entry = ConfigEntry(key="x.flag", type="bool", default=None, description="d")
    list_entry = ConfigEntry(key="x.names", type="list[str]", default=None, description="d")
    source = Path("workspace.yaml")
    with pytest.raises(WorkspaceError, match="expects a boolean"):
        checked(Resolved("x.flag", "yes", "manifest", bool_entry), source=source)
    assert checked(Resolved("x.flag", True, "manifest", bool_entry), source=source) is True
    with pytest.raises(WorkspaceError, match="expects a list of strings"):
        checked(Resolved("x.names", "a,b", "manifest", list_entry), source=source)
    with pytest.raises(WorkspaceError, match="expects a list of strings"):
        checked(Resolved("x.names", ["a", 3], "manifest", list_entry), source=source)
    assert checked(Resolved("x.names", ["a", "b"], "manifest", list_entry), source=source) == ["a", "b"]


def test_checked_leaves_an_env_value_alone():
    # config-io's documented rule is that env reads are fail-open. This design
    # does not relitigate it; the gate is for stored values only.
    entry = ConfigEntry(key="x.n", type="int", default=1, description="d", env_var="X_N")
    assert checked(Resolved("x.n", "not-an-int", "env", entry), source=Path("workspace.yaml")) == "not-an-int"


def test_checked_leaves_a_default_alone():
    entry = ConfigEntry(key="x.n", type="int", default=None, description="d")
    assert checked(Resolved("x.n", None, "default", entry), source=Path("workspace.yaml")) is None


def test_checked_int_reads_a_good_value_and_the_catalog_default(tmp_path):
    assert checked_int(_layout(tmp_path), "workflow.auto_drive.max_parallel") == 2
    layout = _layout(tmp_path, "version: 1\nworkflow:\n  auto_drive:\n    max_parallel: 5\n")
    assert checked_int(layout, "workflow.auto_drive.max_parallel") == 5


def test_checked_int_refuses_an_explicit_null(tmp_path):
    # This key has a real default (2), so a workspace can inherit it while
    # believing it set something. `workflow.pipeline.*` deliberately differs —
    # see the note in `workspace_pipeline`.
    layout = _layout(tmp_path, "version: 1\nworkflow:\n  auto_drive:\n    max_parallel: null\n")
    with pytest.raises(WorkspaceError, match="explicitly null"):
        checked_int(layout, "workflow.auto_drive.max_parallel")


def test_checked_str_reads_a_good_value_and_the_catalog_default(tmp_path):
    assert checked_str(_layout(tmp_path), "workflow.auto_drive.permission_mode") == "bypassPermissions"
    layout = _layout(tmp_path, "version: 1\nworkflow:\n  auto_drive:\n    permission_mode: default\n")
    assert checked_str(layout, "workflow.auto_drive.permission_mode") == "default"


def test_checked_str_refuses_a_non_string_and_an_explicit_null(tmp_path):
    layout = _layout(tmp_path, "version: 1\nworkflow:\n  auto_drive:\n    permission_mode: 3\n")
    with pytest.raises(WorkspaceError, match="expects a string"):
        checked_str(layout, "workflow.auto_drive.permission_mode")
    layout = _layout(tmp_path, "version: 1\nworkflow:\n  auto_drive:\n    permission_mode: null\n")
    with pytest.raises(WorkspaceError, match="explicitly null"):
        checked_str(layout, "workflow.auto_drive.permission_mode")


def test_checked_bool_reads_a_good_value_and_the_catalog_default(tmp_path):
    assert checked_bool(_layout(tmp_path), "workflow.auto_drive.supervise_merges") is False
    layout = _layout(tmp_path, "version: 1\nworkflow:\n  auto_drive:\n    supervise_merges: true\n")
    assert checked_bool(layout, "workflow.auto_drive.supervise_merges") is True


def test_checked_bool_refuses_an_int_a_string_and_an_explicit_null(tmp_path):
    # `isinstance(True, int)` is true, so the type gate is asymmetric: an int
    # must not read back as a bool the way `max_parallel: true` must not read
    # back as 1. `1` is the value this test exists to catch.
    layout = _layout(tmp_path, "version: 1\nworkflow:\n  auto_drive:\n    supervise_merges: 1\n")
    with pytest.raises(WorkspaceError, match="expects a boolean"):
        checked_bool(layout, "workflow.auto_drive.supervise_merges")
    layout = _layout(tmp_path, "version: 1\nworkflow:\n  auto_drive:\n    supervise_merges: yes-please\n")
    with pytest.raises(WorkspaceError, match="expects a boolean"):
        checked_bool(layout, "workflow.auto_drive.supervise_merges")
    layout = _layout(tmp_path, "version: 1\nworkflow:\n  auto_drive:\n    supervise_merges: null\n")
    with pytest.raises(WorkspaceError, match="explicitly null"):
        checked_bool(layout, "workflow.auto_drive.supervise_merges")


def test_resolve_checked_key_refuses_a_hand_edited_invalid_type(tmp_path):
    layout = _layout(tmp_path, "version: 1\nlayout:\n  cache_dir: []\n")

    with pytest.raises(WorkspaceError, match=r"layout\.cache_dir: expects a string"):
        resolve_checked_key(layout, "layout.cache_dir", environ={})


def test_resolve_checked_all_refuses_a_hand_edited_explicit_null(tmp_path):
    layout = _layout(tmp_path, "version: 1\nlayout:\n  bundle_dir: null\n")

    with pytest.raises(WorkspaceError, match=r"layout\.bundle_dir: is explicitly null"):
        resolve_checked_all(layout, environ={})


# --- the seeded relay tail --------------------------------------------------


def test_the_initial_manifest_can_carry_a_relay_tail(tmp_path):
    from graph_works_core.workspace.layout import layout_for
    from graph_works_core.workspace.pipeline import RELAY_TAIL_SEED, pipeline_table

    text = render_initial(today=TODAY, relay_tail=RELAY_TAIL_SEED)
    path = _write(tmp_path, text)
    assert read(path).version == MANIFEST_VERSION
    # The round trip is the point: it proves the seeded string is a value the
    # catalog accepts and the table layers, not a string that merely got written.
    assert pipeline_table(layout=layout_for(tmp_path))["branch"].prompt_tail == RELAY_TAIL_SEED


def test_the_initial_manifest_omits_the_workflow_block_without_a_relay_tail(tmp_path):
    text = render_initial(today=TODAY)
    assert "workflow" not in text
    assert read(_write(tmp_path, text)).version == MANIFEST_VERSION


@pytest.mark.parametrize("tail", ["", "   "])
def test_a_blank_relay_tail_writes_no_key(tmp_path, tail):
    # Same shape as the topic guard: blank is absent, not an empty string.
    assert "workflow" not in render_initial(today=TODAY, relay_tail=tail)


def test_a_relay_tail_needing_quoting_survives_the_hand_rendered_yaml(tmp_path):
    from graph_works_core.workspace.layout import layout_for
    from graph_works_core.workspace.pipeline import pipeline_table

    hostile = "Auto-drive context: relay it  # not a comment; target {merge_target}"
    _write(tmp_path, render_initial(today=TODAY, relay_tail=hostile))
    assert pipeline_table(layout=layout_for(tmp_path))["branch"].prompt_tail == hostile
