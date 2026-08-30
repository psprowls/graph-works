"""`graph_works_core.hooks.apply` over a temp repo_root and a temp scripts_dir
-- one scenario per behavior the ported merge/remove primitive locks: enable,
disable, idempotency, dedup, and the unrelated-content round trip.

`transcript` is the only feature since `gates` was retired, so it is the
subject of every scenario. The multi-wiring cases `gates` used to carry are
gone with it -- `for_feature` still returns a tuple, and the enable/disable
loops still iterate, but nothing exercises them at length > 1 any more."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from graph_works_core import hooks
from graph_works_core.hooks import HooksError, HookWiring, apply


def _write_script(scripts_dir: Path, name: str) -> None:
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / name).write_text("#!/bin/sh\n", encoding="utf-8")


def _transcript_scripts_dir(tmp_path: Path) -> Path:
    scripts = tmp_path / "scripts"
    _write_script(scripts, "session-end-transcript-capture.sh")
    return scripts


def _settings_file(repo_root: Path) -> Path:
    return repo_root / ".claude" / "settings.local.json"


def _settings(repo_root: Path) -> dict:
    return json.loads(_settings_file(repo_root).read_text(encoding="utf-8"))


def _seed_settings(repo_root: Path, data: object) -> None:
    path = _settings_file(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_hook_internal_type_and_default_script_helpers_cover_runtime_variants(tmp_path: Path) -> None:
    assert hooks._default_scripts_dir().is_dir()
    assert [hooks._json_type(value) for value in (True, "s", 1.5, object())] == [
        "boolean",
        "string",
        "number",
        "object",
    ]
    assert hooks._validate_settings(tmp_path / "settings.json", {"hooks": {"Stop": [{}]}}) == {"hooks": {"Stop": [{}]}}


def test_enable_uses_packaged_default_script_directory(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    result = apply("enable", "transcript", repo_root)
    assert result.added == ("session-end-transcript-capture.sh",)


def test_settings_read_oserror_is_wrapped(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    target = _settings_file(repo_root)
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    real_read = Path.read_text

    def fail_read(path: Path, *args, **kwargs):
        if path == target:
            raise OSError("unreadable")
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_read)
    with pytest.raises(HooksError, match="could not read settings"):
        apply("disable", "transcript", repo_root)


def test_enable_registers_the_feature_hook_under_its_event(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    scripts = _transcript_scripts_dir(tmp_path)

    result = apply("enable", "transcript", repo_root, scripts_dir=scripts)

    assert result.changed is True
    assert result.added == ("session-end-transcript-capture.sh",)
    assert result.removed == ()
    assert result.skipped == ()

    settings = _settings(repo_root)
    # No feature manages `permissions` since `gates` was retired; `apply` must
    # not invent the block on the way past.
    assert "permissions" not in settings
    session_end = settings["hooks"]["SessionEnd"]
    assert len(session_end) == 1
    assert session_end[0]["matcher"] == ""
    assert "session-end-transcript-capture.sh" in session_end[0]["hooks"][0]["command"]


def test_double_enable_is_idempotent(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    scripts = _transcript_scripts_dir(tmp_path)

    first = apply("enable", "transcript", repo_root, scripts_dir=scripts)
    second = apply("enable", "transcript", repo_root, scripts_dir=scripts)

    assert first.changed is True
    assert second.changed is False
    assert second.added == ()
    assert second.skipped == ("session-end-transcript-capture.sh",)


def test_disable_removes_exactly_what_enable_added(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    scripts = _transcript_scripts_dir(tmp_path)

    enabled = apply("enable", "transcript", repo_root, scripts_dir=scripts)
    disabled = apply("disable", "transcript", repo_root, scripts_dir=scripts)

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
                "SessionEnd": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": 'bash "/x/session-end-transcript-capture.sh"'},
                            {"type": "command", "command": 'bash "/x/unrelated.sh"'},
                        ],
                    }
                ],
            }
        },
    )
    scripts = _transcript_scripts_dir(tmp_path)

    result = apply("disable", "transcript", repo_root, scripts_dir=scripts)

    assert "session-end-transcript-capture.sh" in result.removed
    settings = _settings(repo_root)
    session_end = settings["hooks"]["SessionEnd"]
    assert len(session_end) == 1
    assert len(session_end[0]["hooks"]) == 1
    assert "unrelated.sh" in session_end[0]["hooks"][0]["command"]
    assert settings["hooks"]["PreToolUse"] == [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": 'bash "/other/thing.sh"'}]}
    ]


def test_disable_when_absent_is_a_noop_and_does_not_create_the_settings_file(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = apply("disable", "transcript", repo_root)

    assert result.changed is False
    assert result.removed == ()
    assert not _settings_file(repo_root).exists()


def test_a_user_owned_permissions_deny_survives_a_write(tmp_path):
    """The retired `gates` feature managed `permissions.deny: ["EnterPlanMode"]`.

    Nothing manages it now, so the entry is ordinary user content: a `disable`
    that actually rewrites the file must carry it through untouched. Seeded
    alongside a real hook so the write path runs -- a no-op `disable` would
    prove nothing, because it never writes.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_settings(
        repo_root,
        {
            "hooks": {
                "SessionEnd": [
                    {
                        "matcher": "",
                        "hooks": [{"type": "command", "command": 'bash "/x/session-end-transcript-capture.sh"'}],
                    }
                ]
            },
            "permissions": {"deny": ["EnterPlanMode"]},
        },
    )
    scripts = _transcript_scripts_dir(tmp_path)

    result = apply("disable", "transcript", repo_root, scripts_dir=scripts)

    assert result.changed is True
    assert result.removed == ("session-end-transcript-capture.sh",)
    settings = _settings(repo_root)
    assert settings["permissions"]["deny"] == ["EnterPlanMode"]


def test_enable_raises_hookserror_when_the_script_file_is_missing(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    empty_scripts = tmp_path / "empty-scripts"
    empty_scripts.mkdir()

    with pytest.raises(HooksError):
        apply("enable", "transcript", repo_root, scripts_dir=empty_scripts)

    assert not _settings_file(repo_root).exists()


def test_malformed_settings_json_raises_hookserror(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    target = _settings_file(repo_root)
    target.parent.mkdir()
    target.write_text("{", encoding="utf-8")

    with pytest.raises(HooksError, match="is not valid JSON"):
        apply("disable", "transcript", repo_root)


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ([], "top-level JSON value must be an object, got array"),
        ({"hooks": []}, "`hooks` must be an object, got array"),
        ({"hooks": {"Stop": {}}}, "`hooks.Stop` must be an array, got object"),
        ({"hooks": {"Stop": [None]}}, "`hooks.Stop[0]` must be an object, got null"),
        (
            {"hooks": {"Stop": [{"hooks": {}}]}},
            "`hooks.Stop[0].hooks` must be an array, got object",
        ),
        (
            {"hooks": {"Stop": [{"hooks": [None]}]}},
            "`hooks.Stop[0].hooks[0]` must be an object, got null",
        ),
        (
            {"hooks": {"Stop": [{"hooks": [{"command": 7}]}]}},
            "`hooks.Stop[0].hooks[0].command` must be a string, got integer",
        ),
        ({"permissions": []}, "`permissions` must be an object, got array"),
        ({"permissions": {"deny": {}}}, "`permissions.deny` must be an array, got object"),
        (
            {"permissions": {"deny": [7]}},
            "`permissions.deny[0]` must be a string, got integer",
        ),
    ],
)
def test_malformed_settings_shapes_raise_hookserror(tmp_path, settings, message):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_settings(repo_root, settings)

    with pytest.raises(HooksError, match=re.escape(message)):
        apply("disable", "transcript", repo_root)


def test_settings_write_failure_raises_hookserror(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    scripts = _transcript_scripts_dir(tmp_path)
    target = _settings_file(repo_root)
    real_write_text = Path.write_text

    def fail_target_write(path: Path, *args, **kwargs):
        if path == target:
            raise OSError("disk full")
        return real_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_target_write)

    with pytest.raises(HooksError, match=r"could not write settings.*disk full"):
        apply("enable", "transcript", repo_root, scripts_dir=scripts)


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
                "SessionEnd": [
                    {
                        "matcher": "SomeOtherMatcher",
                        "hooks": [{"type": "command", "command": 'bash "/x/unrelated-session-end-hook.sh"'}],
                    },
                    {
                        "matcher": "",
                        "hooks": [{"type": "command", "command": 'bash "/x/session-end-transcript-capture.sh"'}],
                    },
                ]
            },
            "permissions": {"allow": ["Read"]},
            "env": {"FOO": "bar"},
        },
    )
    scripts = _transcript_scripts_dir(tmp_path)

    result = apply("disable", "transcript", repo_root, scripts_dir=scripts)

    assert "session-end-transcript-capture.sh" in result.removed
    settings = _settings(repo_root)
    assert settings["hooks"]["SessionEnd"] == [
        {
            "matcher": "SomeOtherMatcher",
            "hooks": [{"type": "command", "command": 'bash "/x/unrelated-session-end-hook.sh"'}],
        }
    ]
    assert settings["permissions"] == {"allow": ["Read"]}
    assert settings["env"] == {"FOO": "bar"}


def test_apply_raises_hookserror_on_unknown_action(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    scripts = _transcript_scripts_dir(tmp_path)

    with pytest.raises(HooksError):
        apply("bogus", "transcript", repo_root, scripts_dir=scripts)  # type: ignore[arg-type]


def test_for_feature_raises_hookserror_on_unknown_feature():
    with pytest.raises(HooksError):
        HookWiring.for_feature("bogus")  # type: ignore[arg-type]


def test_default_scripts_dir_resolves_every_wiring_script():
    from graph_works_core.hooks import _default_scripts_dir

    default_dir = _default_scripts_dir()
    # A source checkout resolves to the native plugin, never the vendored fork.
    assert default_dir.as_posix().endswith("/plugins/graph-works-native/hooks/examples")
    for wiring in HookWiring.for_feature("transcript"):
        assert (default_dir / wiring.script).is_file()


def _assert_wheel_enables_every_hook(wheel: Path, tmp_path: Path) -> None:
    site_packages = tmp_path / "site-packages"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(site_packages)

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    smoke = """
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from graph_works_core import apply_init, plan_init
from graph_works_core.hooks import apply

repo_root = Path(sys.argv[1])
transcript = apply("enable", "transcript", repo_root)
settings = json.loads(transcript.settings_path.read_text(encoding="utf-8"))
commands = [
    hook["command"]
    for entries in settings["hooks"].values()
    for entry in entries
    for hook in entry["hooks"]
]
assert transcript.added == ("session-end-transcript-capture.sh",)
assert len(commands) == 1
assert all("graph_works_core/_hook_scripts/" in command for command in commands)
assert all("GRAPH_WORKS_PYTHON=" in command for command in commands)

layout = apply_init(plan_init(repo_root / ".works", today=date(2026, 8, 23), topic="Hook test")).layout
work_path = "work/feature-installed-hook"
page = layout.bundle_dir / f"{work_path}.md"
page.parent.mkdir(parents=True, exist_ok=True)
page.write_text("---\\ntype: Feature\\n---\\n", encoding="utf-8")
layout.cache_dir.mkdir(parents=True, exist_ok=True)
(layout.cache_dir / "active-work.json").write_text(
    json.dumps({"path": work_path, "phase": "execute"}) + "\\n",
    encoding="utf-8",
)
source = repo_root / "session.jsonl"
source.write_text('{"event":"installed"}\\n', encoding="utf-8")
environment = os.environ.copy()
environment["GRAPH_WORKS_DIR"] = str(layout.root)
environment["GRAPH_WORKS_TRANSCRIPT_CAPTURE_TRACE_LOG"] = str(repo_root / "trace.log")
completed = subprocess.run(
    commands[0],
    shell=True,
    cwd=repo_root,
    env=environment,
    input=json.dumps({"session_id": "installed-wheel", "transcript_path": str(source)}),
    capture_output=True,
    text=True,
)
assert completed.returncode == 0, completed.stderr
destination = layout.bundle_dir / work_path / "references" / "03-execute-transcript.jsonl"
assert destination.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
"""
    environ = os.environ.copy()
    environ["PYTHONPATH"] = str(site_packages)
    completed = subprocess.run(
        [sys.executable, "-c", smoke, str(repo_root)],
        cwd=tmp_path,
        env=environ,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_built_wheel_enables_every_hook_from_packaged_scripts(tmp_path):
    """A wheel install must not depend on the monorepo's plugin checkout."""
    workspace_root = Path(__file__).resolve().parents[3]
    dist_dir = tmp_path / "dist"
    subprocess.run(
        [
            "uv",
            "build",
            "--package",
            "graph-works-core",
            "--wheel",
            "--out-dir",
            str(dist_dir),
        ],
        cwd=workspace_root,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(dist_dir.glob("graph_works_core-*.whl"))

    _assert_wheel_enables_every_hook(wheel, tmp_path)


def test_sdist_builds_a_wheel_with_every_packaged_hook(tmp_path):
    """The source distribution must carry everything needed for its wheel."""
    workspace_root = Path(__file__).resolve().parents[3]
    sdist_dir = tmp_path / "sdist"
    subprocess.run(
        [
            "uv",
            "build",
            "--package",
            "graph-works-core",
            "--sdist",
            "--out-dir",
            str(sdist_dir),
        ],
        cwd=workspace_root,
        check=True,
        capture_output=True,
        text=True,
    )
    sdist = next(sdist_dir.glob("graph_works_core-*.tar.gz"))
    wheel_dir = tmp_path / "wheel-from-sdist"
    completed = subprocess.run(
        ["uv", "build", str(sdist), "--wheel", "--out-dir", str(wheel_dir)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    wheel = next(wheel_dir.glob("graph_works_core-*.whl"))

    _assert_wheel_enables_every_hook(wheel, tmp_path / "smoke")
