from __future__ import annotations

from graph_works_core.orchestrate.gate import ScopedCommand, expand_scoped
from graph_works_core.workspace.gate_config import ScopedGate

SCOPED = ScopedGate("packages/*", "just check-pkg {name}")


def test_paths_map_to_sorted_distinct_roots() -> None:
    got = expand_scoped(SCOPED, ["packages/okf-io/src/okf_io/x.py", "packages/config-io", "packages/okf-io/tests"])
    assert got == ScopedCommand("just check-pkg config-io && just check-pkg okf-io", ("config-io", "okf-io"), ())


def test_an_unmatched_path_refuses_with_it_named() -> None:
    got = expand_scoped(SCOPED, ["packages/okf-io", "plugins/gw/skills/workflow", "AGENTS.md"])
    assert got.command is None and got.uncovered == ("plugins/gw/skills/workflow", "AGENTS.md")


def test_a_path_shorter_than_the_root_glob_is_uncovered() -> None:
    assert expand_scoped(SCOPED, ["packages"]).uncovered == ("packages",)


def test_multi_segment_roots() -> None:
    got = expand_scoped(ScopedGate("apps/*/pkg", "make {name}"), ["apps/web/pkg/src/a.ts"])
    assert got.names == ("pkg",) and got.command == "make pkg"


def test_no_code_paths_is_uncovered_not_empty_success() -> None:
    assert expand_scoped(SCOPED, []).command is None


def test_an_unsafe_name_is_shell_quoted_and_never_executes(tmp_path) -> None:
    import subprocess

    marker = tmp_path / "pwned"
    name = "a; touch pwned $(touch pwned)"
    got = expand_scoped(ScopedGate("packages/*", "echo {name}"), [f"packages/{name}/x.py"])
    assert got.command is not None and got.names == (name,)
    assert got.command.startswith("echo '") and "$(" in got.command
    out = subprocess.run(["sh", "-c", got.command], capture_output=True, text=True, check=True, cwd=tmp_path).stdout
    assert out.strip() == name and not marker.exists()


def test_a_safe_name_stays_byte_identical() -> None:
    assert expand_scoped(SCOPED, ["packages/okf-io/x"]).command == "just check-pkg okf-io"


def test_implicit_scoped_pending_hash_covers_the_expanded_command(env) -> None:
    import json

    from _gate_helpers import NOW
    from graph_works_core.orchestrate import gate_units
    from graph_works_core.orchestrate.gate import run_gate_run

    result = run_gate_run(env.layout, env.path, scope="scoped", now=NOW, token="a1b2c3d4", spawn=env.spawn)
    record = json.loads(env.spawned[0].read_text(encoding="utf-8"))
    assert result.command == "echo a"
    assert record["units"][0]["command"] == "echo a"
    assert record["manifest_hash"] == gate_units.implicit_manifest("true").digest
    assert record["units"][0]["hash"] == gate_units.tree_hash(env.tree, "echo a")
    assert record["units"][0]["hash"] != gate_units.tree_hash(env.tree, "true")


def test_equal_implicit_scoped_command_keeps_the_scoped_marker(env) -> None:
    import json

    from _gate_helpers import NOW
    from graph_works_core.orchestrate import gate_units
    from graph_works_core.orchestrate.gate import run_gate_run

    env.set_manifest(
        gate="    gate:\n      full: 'echo a'\n      scoped:\n"
        "        roots: packages/*\n        command: 'echo {name}'\n"
    )
    run_gate_run(env.layout, env.path, scope="scoped", now=NOW, token="a1b2c3d4", spawn=env.spawn)
    record = json.loads(env.spawned[0].read_text(encoding="utf-8"))
    assert record["implicit"] is True and record["scope"] == "scoped"
    assert record["units"][0]["hash"] == gate_units.tree_hash(env.tree, "echo a")
