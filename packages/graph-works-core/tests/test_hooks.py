"""`graph_works_core.hooks.apply` over a temp repo_root and a temp scripts_dir
-- one scenario per behavior the ported merge/remove primitive locks: enable,
disable, idempotency, dedup, the gates permissions.deny pairing, and the
unrelated-content round trip."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from graph_works_core.hooks import HooksError, HookWiring, apply


def _write_script(scripts_dir: Path, name: str) -> None:
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / name).write_text("#!/bin/sh\n", encoding="utf-8")


def _gates_scripts_dir(tmp_path: Path) -> Path:
    scripts = tmp_path / "scripts"
    _write_script(scripts, "post-task-complete-revalidate.sh")
    _write_script(scripts, "stop-revalidate-user-gates.sh")
    return scripts


def _transcript_scripts_dir(tmp_path: Path) -> Path:
    scripts = tmp_path / "scripts"
    _write_script(scripts, "session-end-transcript-capture.sh")
    return scripts


def _settings_file(repo_root: Path) -> Path:
    return repo_root / ".claude" / "settings.local.json"


def _settings(repo_root: Path) -> dict:
    return json.loads(_settings_file(repo_root).read_text(encoding="utf-8"))


def _seed_settings(repo_root: Path, data: dict) -> None:
    path = _settings_file(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_enable_gates_adds_both_hooks_and_the_enterplanmode_deny(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    scripts = _gates_scripts_dir(tmp_path)

    result = apply("enable", "gates", repo_root, scripts_dir=scripts)

    assert result.changed is True
    assert result.added == ("post-task-complete-revalidate.sh", "stop-revalidate-user-gates.sh")
    assert result.removed == ()
    assert result.skipped == ()

    settings = _settings(repo_root)
    assert settings["permissions"]["deny"] == ["EnterPlanMode"]
    post_tool_use = settings["hooks"]["PostToolUse"]
    assert len(post_tool_use) == 1
    assert post_tool_use[0]["matcher"] == "TaskUpdate"
    assert "post-task-complete-revalidate.sh" in post_tool_use[0]["hooks"][0]["command"]
    stop = settings["hooks"]["Stop"]
    assert len(stop) == 1
    assert stop[0]["matcher"] == ""
    assert "stop-revalidate-user-gates.sh" in stop[0]["hooks"][0]["command"]


def test_enable_transcript_adds_the_hook_with_no_deny_line(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    scripts = _transcript_scripts_dir(tmp_path)

    result = apply("enable", "transcript", repo_root, scripts_dir=scripts)

    assert result.changed is True
    assert result.added == ("session-end-transcript-capture.sh",)

    settings = _settings(repo_root)
    assert "permissions" not in settings
    session_end = settings["hooks"]["SessionEnd"]
    assert len(session_end) == 1
    assert "session-end-transcript-capture.sh" in session_end[0]["hooks"][0]["command"]


def test_double_enable_is_idempotent(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    scripts = _gates_scripts_dir(tmp_path)

    first = apply("enable", "gates", repo_root, scripts_dir=scripts)
    second = apply("enable", "gates", repo_root, scripts_dir=scripts)

    assert first.changed is True
    assert second.changed is False
    assert second.added == ()
    assert second.skipped == ("post-task-complete-revalidate.sh", "stop-revalidate-user-gates.sh")


def test_disable_removes_exactly_what_enable_added(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    scripts = _gates_scripts_dir(tmp_path)

    enabled = apply("enable", "gates", repo_root, scripts_dir=scripts)
    disabled = apply("disable", "gates", repo_root, scripts_dir=scripts)

    assert disabled.changed is True
    assert disabled.removed == enabled.added
    settings = _settings(repo_root)
    assert "hooks" not in settings
    assert "permissions" not in settings


def test_disable_trims_a_mixed_hooks_entry_and_leaves_other_events_alone(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_settings(
        repo_root,
        {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": 'bash "/other/thing.sh"'}]}
                ],
                "Stop": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": 'bash "/x/stop-revalidate-user-gates.sh"'},
                            {"type": "command", "command": 'bash "/x/unrelated.sh"'},
                        ],
                    }
                ],
            }
        },
    )
    scripts = _gates_scripts_dir(tmp_path)

    result = apply("disable", "gates", repo_root, scripts_dir=scripts)

    assert "stop-revalidate-user-gates.sh" in result.removed
    settings = _settings(repo_root)
    stop = settings["hooks"]["Stop"]
    assert len(stop) == 1
    assert len(stop[0]["hooks"]) == 1
    assert "unrelated.sh" in stop[0]["hooks"][0]["command"]
    assert settings["hooks"]["PreToolUse"] == [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": 'bash "/other/thing.sh"'}]}
    ]


def test_disable_when_absent_is_a_noop_and_does_not_create_the_settings_file(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    scripts = _gates_scripts_dir(tmp_path)

    result = apply("disable", "gates", repo_root, scripts_dir=scripts)

    assert result.changed is False
    assert result.removed == ()
    assert not _settings_file(repo_root).exists()


def test_disable_preserves_a_user_owned_deny_when_no_gates_hook_was_removed(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_settings(repo_root, {"permissions": {"deny": ["EnterPlanMode"]}})
    scripts = _gates_scripts_dir(tmp_path)

    result = apply("disable", "gates", repo_root, scripts_dir=scripts)

    assert result.changed is False
    assert result.removed == ()
    settings = _settings(repo_root)
    assert settings["permissions"]["deny"] == ["EnterPlanMode"]


def test_enable_raises_hookserror_when_the_script_file_is_missing(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    empty_scripts = tmp_path / "empty-scripts"
    empty_scripts.mkdir()

    with pytest.raises(HooksError):
        apply("enable", "gates", repo_root, scripts_dir=empty_scripts)

    assert not _settings_file(repo_root).exists()


def test_unrelated_existing_settings_content_round_trips_through_enable(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_settings(
        repo_root,
        {
            "hooks": {
                "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": 'bash "/other/thing.sh"'}]}]
            },
            "permissions": {"allow": ["Read"]},
            "env": {"FOO": "bar"},
        },
    )
    scripts = _transcript_scripts_dir(tmp_path)

    apply("enable", "transcript", repo_root, scripts_dir=scripts)

    settings = _settings(repo_root)
    assert settings["hooks"]["PreToolUse"] == [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": 'bash "/other/thing.sh"'}]}
    ]
    assert settings["permissions"] == {"allow": ["Read"]}
    assert settings["env"] == {"FOO": "bar"}
    assert "session-end-transcript-capture.sh" in settings["hooks"]["SessionEnd"][0]["hooks"][0]["command"]


def test_disable_leaves_an_unrelated_entry_on_the_same_event_untouched(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_settings(
        repo_root,
        {
            "hooks": {
                "Stop": [
                    {
                        "matcher": "SomeOtherMatcher",
                        "hooks": [{"type": "command", "command": 'bash "/x/unrelated-stop-hook.sh"'}],
                    },
                    {
                        "matcher": "",
                        "hooks": [{"type": "command", "command": 'bash "/x/stop-revalidate-user-gates.sh"'}],
                    },
                ]
            },
            "permissions": {"allow": ["Read"]},
            "env": {"FOO": "bar"},
        },
    )
    scripts = _gates_scripts_dir(tmp_path)

    result = apply("disable", "gates", repo_root, scripts_dir=scripts)

    assert "stop-revalidate-user-gates.sh" in result.removed
    settings = _settings(repo_root)
    assert settings["hooks"]["Stop"] == [
        {"matcher": "SomeOtherMatcher", "hooks": [{"type": "command", "command": 'bash "/x/unrelated-stop-hook.sh"'}]}
    ]
    assert settings["permissions"] == {"allow": ["Read"]}
    assert settings["env"] == {"FOO": "bar"}


def test_apply_raises_hookserror_on_unknown_action(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    scripts = _gates_scripts_dir(tmp_path)

    with pytest.raises(HooksError):
        apply("bogus", "gates", repo_root, scripts_dir=scripts)  # type: ignore[arg-type]


def test_for_feature_raises_hookserror_on_unknown_feature():
    with pytest.raises(HooksError):
        HookWiring.for_feature("bogus")  # type: ignore[arg-type]


@pytest.mark.xfail(
    reason="plugins/graph-works/hooks/examples/ lands with epic/graph-works-plugin-fork's merge to main",
    strict=False,
)
def test_default_scripts_dir_resolves_every_wiring_script():
    from graph_works_core.hooks import _default_scripts_dir

    default_dir = _default_scripts_dir()
    for feature in ("gates", "transcript"):
        for wiring in HookWiring.for_feature(feature):
            assert (default_dir / wiring.script).is_file()
