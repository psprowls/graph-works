"""Each policy on hand-built layer data."""

from __future__ import annotations

from graph_works_core.agent_config.merge import POLICIES, LayerInput, MergePolicy, layer_gate, merge


def _key(result, *path):
    return next(entry for entry in result.keys if entry.path == path)


def test_deep_merge_and_replace_for_pi() -> None:
    result = merge(
        POLICIES["pi"],
        [
            LayerInput("user", {"a": {"x": 1, "y": 2}, "list": [1, 2]}, True),
            LayerInput("project", {"a": {"y": 3}, "list": [3]}, True),
        ],
        "trusted",
    )
    assert result.effective == {"a": {"x": 1, "y": 3}, "list": [3]}
    assert _key(result, "a", "y").defined_in == ("project", "user")
    assert _key(result, "a", "y").effective_from == ("project",)
    assert _key(result, "a", "x").effective_from == ("user",)


def test_claude_concatenates_every_list_and_dedupes_keeping_first() -> None:
    hook = {"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}
    result = merge(
        POLICIES["claude"],
        [
            LayerInput("user", {"permissions": {"deny": ["A", "B"]}, "hooks": {"Stop": [hook]}}, True),
            LayerInput("project", {"permissions": {"deny": ["B", "C"]}, "hooks": {"Stop": [hook]}}, True),
            LayerInput("managed", {"permissions": {"deny": ["D"]}}, True),
        ],
        "trusted",
    )
    assert result.effective["permissions"] == {"deny": ["A", "B", "C", "D"]}
    assert result.effective["hooks"] == {"Stop": [hook]}
    assert _key(result, "permissions", "deny").effective_from == ("managed", "project", "user")


def test_whole_value_empty_object_replaces_an_existing_object() -> None:
    result = merge(
        POLICIES["claude"],
        [LayerInput("user", {"modelPicker": {"rows": ["a"]}}, True), LayerInput("managed", {"modelPicker": {}}, True)],
        "trusted",
    )
    assert result.effective == {"modelPicker": {}}
    assert _key(result, "modelPicker").effective_from == ("managed",)


def test_ordinary_empty_object_is_a_no_op_over_an_existing_object() -> None:
    result = merge(
        POLICIES["pi"],
        [LayerInput("user", {"settings": {"x": 1}}, True), LayerInput("project", {"settings": {}}, True)],
        "trusted",
    )
    assert result.effective == {"settings": {"x": 1}}
    assert _key(result, "settings").effective_from == ()


def test_object_scalar_type_changes_replace_both_ways() -> None:
    result = merge(
        POLICIES["codex"],
        [LayerInput("user", {"a": {"b": 1}, "c": 5}, True), LayerInput("project", {"a": 7, "c": {"d": 1}}, True)],
        "trusted",
    )
    assert result.effective == {"a": 7, "c": {"d": 1}}
    assert _key(result, "a", "b").effective_from == ()
    assert _key(result, "c").effective_from == ()
    assert _key(result, "c", "d").effective_from == ("project",)


def test_merge_does_not_mutate_a_lower_layer_when_creating_an_intermediate_object() -> None:
    user = {"a": {}}
    result = merge(
        POLICIES["pi"],
        [LayerInput("user", user, True), LayerInput("project", {"a": {"b": 1}}, True)],
        "trusted",
    )
    assert result.effective == {"a": {"b": 1}}
    assert user == {"a": {}}


def test_codex_forbidden_keys_are_skipped_with_a_finding_but_indexed() -> None:
    result = merge(
        POLICIES["codex"],
        [
            LayerInput("user", {"model_provider": "openai"}, True),
            LayerInput("project", {"model_provider": "evil", "model_providers": {"evil": {"base_url": "x"}}}, True),
        ],
        "trusted",
    )
    assert result.effective == {"model_provider": "openai"}
    assert _key(result, "model_provider").defined_in == ("project", "user")
    assert _key(result, "model_provider").effective_from == ("user",)
    assert _key(result, "model_providers", "evil", "base_url").effective_from == ()
    assert {finding.code for finding in result.findings} == {"agent-config.not-overridable"}


def test_codex_denylist_is_complete_and_matches_key_segments() -> None:
    forbidden = {
        "openai_base_url",
        "chatgpt_base_url",
        "apps_mcp_product_sku",
        "responses_api_metadata",
        "model_provider",
        "model_providers",
        "notify",
        "profile",
        "profiles",
        "experimental_realtime_webrtc_call_base_url",
        "experimental_realtime_ws_base_url",
        "otel",
    }
    result = merge(POLICIES["codex"], [LayerInput("project", {key: key for key in forbidden}, True)], "trusted")
    assert result.effective == {}
    assert {key.path for key in result.keys} == {(key,) for key in forbidden}
    assert len(result.findings) == len(forbidden)


def test_wildcard_patterns_match_one_path_segment_and_findings_are_deduplicated() -> None:
    policy = MergePolicy(
        precedence=("user", "project"),
        arrays="replace",
        whole_value_paths=(),
        project_forbidden=(("plugins", "*", "secret"),),
        trust_gated_scopes=frozenset(),
        trust_gated_paths=(),
        source="https://example.test/policy",
    )
    layers = [LayerInput("project", {"plugins": {"name": {"secret": "x", "allowed": "y"}}}, True)]
    result = merge(policy, [*layers, *layers], "trusted")
    assert result.effective == {"plugins": {"name": {"allowed": "y"}}}
    assert len(result.findings) == 1


def test_claude_trust_gated_keys_skip_only_when_not_trusted() -> None:
    layers = [LayerInput("project", {"permissions": {"allow": ["Bash"], "deny": ["Rm"]}}, True)]
    untrusted = merge(POLICIES["claude"], layers, "unrecorded")
    assert untrusted.effective == {"permissions": {"deny": ["Rm"]}}
    assert [finding.code for finding in untrusted.findings] == ["agent-config.trust-gated-key"]
    assert merge(POLICIES["claude"], layers, "unknown").effective["permissions"]["allow"] == ["Bash"]


def test_unapplied_layers_are_indexed_but_do_not_contribute() -> None:
    result = merge(POLICIES["pi"], [LayerInput("project", {"k": 1}, False)], "untrusted")
    assert result.effective == {}
    assert _key(result, "k").defined_in == ("project",) and _key(result, "k").effective_from == ()


def test_absent_layers_do_not_contribute_key_entries() -> None:
    assert merge(POLICIES["pi"], [LayerInput("user", None, True)], "trusted").keys == ()


def test_claude_dedupes_a_first_list_without_a_lower_value() -> None:
    result = merge(POLICIES["claude"], [LayerInput("user", {"list": ["A", "A", "B"]}, True)], "trusted")
    assert result.effective == {"list": ["A", "B"]}


def test_keys_with_dots_stay_single_segments_and_empty_objects_are_leaves() -> None:
    result = merge(
        POLICIES["claude"], [LayerInput("user", {"enabledPlugins": {"gw@market": True}, "env": {}}, True)], "trusted"
    )
    assert ("enabledPlugins", "gw@market") in {key.path for key in result.keys}
    assert _key(result, "env").effective_from == ("user",)
    assert result.effective["env"] == {}


def test_layer_gate_by_trust_state() -> None:
    pi = POLICIES["pi"]
    assert layer_gate(pi, "user", "untrusted") is None
    assert layer_gate(pi, "project", "trusted") is None
    assert layer_gate(pi, "project", "unknown") is None
    assert layer_gate(pi, "project", "untrusted") == "untrusted"
    assert layer_gate(pi, "project", "unrecorded") == "trust-unrecorded"
    assert layer_gate(POLICIES["claude"], "project", "untrusted") is None


def test_policies_cite_sources_and_precedence_matches_the_adapter_order() -> None:
    from plugin_fork_io.adapters import adapter

    for name, policy in POLICIES.items():
        assert policy.source.startswith("https://")
        assert policy.precedence == tuple(layer.scope for layer in adapter(name).config.layers)


def test_claude_model_scope_exceptions_and_origins():
    result = merge(
        POLICIES["claude"],
        [
            LayerInput(
                "user",
                {
                    "availableModels": ["a"],
                    "modelPicker": {"rows": ["a"]},
                    "modelSettings": {"a": {"effortLevel": "low"}, "b": {"effortLevel": "medium"}},
                },
                True,
            ),
            LayerInput(
                "project",
                {
                    "availableModels": ["b"],
                    "modelPicker": {"rows": ["b"]},
                    "modelSettings": {"a": {"effortLevel": "high"}},
                },
                True,
            ),
        ],
        "trusted",
    )
    assert result.effective == {
        "availableModels": ["a", "b"],
        "modelPicker": {"rows": ["a"]},
        "modelSettings": {"a": {"effortLevel": "high"}, "b": {"effortLevel": "medium"}},
    }
    assert _key(result, "availableModels").effective_from == ("project", "user")
    assert _key(result, "modelPicker").defined_in == ("project", "user")
    assert _key(result, "modelPicker").effective_from == ("user",)
    assert _key(result, "modelSettings", "b", "effortLevel").effective_from == ("user",)


def test_claude_managed_models_replace_and_effort_resolves_per_model():
    result = merge(
        POLICIES["claude"],
        [
            LayerInput("user", {"availableModels": ["a"], "modelSettings": {"a": {"effortLevel": "low"}}}, True),
            LayerInput(
                "managed",
                {"availableModels": [], "effortLevel": "high", "modelSettings": {"b": {"effortLevel": "medium"}}},
                True,
            ),
        ],
        "trusted",
    )
    assert result.effective == {
        "availableModels": [],
        "effortLevel": "high",
        "modelSettings": {"a": {"effortLevel": "high"}, "b": {"effortLevel": "medium"}},
    }
    assert _key(result, "availableModels").effective_from == ("managed",)
    assert _key(result, "modelSettings", "a", "effortLevel").defined_in == ("user",)
    assert _key(result, "modelSettings", "a", "effortLevel").effective_from == ("managed",)


def test_claude_restrictive_booleans_survive_managed_relaxation():
    lower = {
        "disableClaudeAiConnectors": True,
        "enableArtifact": False,
        "isolatePeerMachines": True,
        "remoteControlAtStartup": False,
        "useAutoModeDuringPlan": False,
        "syncClaudeAiSkills": False,
        "syncClaudeAiPlugins": False,
    }
    upper = {key: not value for key, value in lower.items()}
    result = merge(
        POLICIES["claude"], [LayerInput("local", lower, True), LayerInput("managed", upper, True)], "trusted"
    )
    assert result.effective == lower
    assert all(key.effective_from == ("local",) for key in result.keys)


def test_claude_project_cannot_set_user_only_switches_or_enable_remote_control():
    data = {
        "useAutoModeDuringPlan": False,
        "syncClaudeAiSkills": False,
        "syncClaudeAiPlugins": False,
        "remoteControlAtStartup": True,
    }
    result = merge(POLICIES["claude"], [LayerInput("project", data, True)], "trusted")
    assert result.effective == {}
    assert all(not key.effective_from for key in result.keys)


def test_claude_legacy_artifact_disable_locks_enable_with_actual_origin():
    result = merge(
        POLICIES["claude"],
        [
            LayerInput("user", {"disableArtifact": True}, True),
            LayerInput("managed", {"enableArtifact": True, "disableArtifact": False}, True),
        ],
        "trusted",
    )
    assert result.effective == {"disableArtifact": True, "enableArtifact": False}
    assert _key(result, "enableArtifact").defined_in == ("managed",)
    assert _key(result, "enableArtifact").effective_from == ("user",)


def test_restrictive_origins_and_derived_effort_keep_authored_metadata():
    result = merge(
        POLICIES["claude"],
        [
            LayerInput("user", {"effortLevel": "low", "disableClaudeAiConnectors": True}, True),
            LayerInput("managed", {"modelSettings": {"a": {"custom": 1}}, "disableClaudeAiConnectors": True}, True),
        ],
        "trusted",
    )
    assert result.effective["modelSettings"] == {"a": {"custom": 1, "effortLevel": "low"}}
    assert _key(result, "modelSettings", "a", "effortLevel").defined_in == ()
    assert _key(result, "modelSettings", "a", "effortLevel").effective_from == ("user",)
    assert _key(result, "disableClaudeAiConnectors").effective_from == ("managed", "user")
