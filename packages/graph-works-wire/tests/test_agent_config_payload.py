"""The agent-config projection preserves the report's explicit public shape."""

from __future__ import annotations

import json
from pathlib import Path

from graph_works_core.agent_config import (
    AgentConfig,
    AgentConfigReport,
    Finding,
    KeyEntry,
    Layer,
    TrustStatus,
)
from graph_works_core.agent_config.records import ProjectAgentConfig
from graph_works_wire.agent_config import agent_config_payload

GOLDEN = Path(__file__).parent / "fixtures" / "agent_config.golden.json"


def _report() -> AgentConfigReport:
    root = Path("/w/repo")
    layer = Layer(
        "project",
        root / ".claude/settings.json",
        "json",
        True,
        "ok",
        None,
        "committed",
        True,
        None,
        {"enabledPlugins": {"gw@market": True}},
    )
    agent = AgentConfig(
        "claude",
        Path("/h/.claude"),
        TrustStatus("trusted", Path("/h/.claude.json"), "/w", "ancestor", False),
        (layer,),
        {"enabledPlugins": {"gw@market": True}},
        (KeyEntry(("enabledPlugins", "gw@market"), ("project",), ("project",)),),
        (Finding("agent-config.git", "git-missing", None),),
        "https://example.invalid/policy",
    )
    return AgentConfigReport((ProjectAgentConfig(root, True, (agent,)), ProjectAgentConfig(Path("/gone"), False, ())))


def _report_with_absent_optionals() -> AgentConfigReport:
    layer = Layer(
        "user", Path("/h/.codex/config.toml"), "toml", False, "absent", None, "outside-repo", False, None, None
    )
    agent = AgentConfig(
        "codex",
        Path("/h/.codex"),
        TrustStatus("unknown", None, None, None, True),
        (layer,),
        {},
        (),
        (Finding("agent-config.parse", "not present", "/h/.codex/config.toml"),),
        "https://example.invalid/codex-policy",
    )
    return AgentConfigReport((ProjectAgentConfig(Path("/w/empty"), True, (agent,)),))


def test_payload_matches_golden_and_dumps_without_an_encoder() -> None:
    payload = agent_config_payload(_report())
    text = json.dumps(payload, indent=2, sort_keys=False) + "\n"

    assert json.loads(text) == json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_payload_keeps_trust_git_and_key_segments_explicit() -> None:
    agent = agent_config_payload(_report())["projects"][0]["agents"][0]

    assert agent["trust"]["match"] == "ancestor"
    assert agent["layers"][0]["git"] == "committed"
    assert agent["keys"][0]["path"] == ["enabledPlugins", "gw@market"]


def test_payload_keeps_absent_optional_fields_as_null() -> None:
    agent = agent_config_payload(_report_with_absent_optionals())["projects"][0]["agents"][0]

    assert agent["trust"] == {
        "state": "unknown",
        "record_path": None,
        "matched_key": None,
        "match": None,
        "assumed": True,
    }
    assert agent["layers"][0]["data"] is None


def test_payload_preserves_layer_parse_error_and_exclusion_reason() -> None:
    layer = Layer(
        "project",
        Path("/w/untrusted/.pi/settings.json"),
        "json",
        True,
        "error",
        "invalid JSON",
        "untracked",
        False,
        "untrusted",
        None,
    )
    agent = AgentConfig(
        "pi",
        Path("/h/.pi/agent"),
        TrustStatus("untrusted", Path("/h/.pi/trust.json"), "/w/untrusted", "exact", False),
        (layer,),
        {},
        (),
        (),
        "https://example.invalid/pi-policy",
    )
    payload = agent_config_payload(AgentConfigReport((ProjectAgentConfig(Path("/w/untrusted"), True, (agent,)),)))

    projected_layer = payload["projects"][0]["agents"][0]["layers"][0]
    assert projected_layer["parse_error"] == "invalid JSON"
    assert projected_layer["excluded_reason"] == "untrusted"
