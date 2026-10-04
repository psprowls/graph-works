"""Acceptance tests for `scripts/package_units.py`.

Run with `uv run pytest scripts/tests`.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from package_units import ALIASES, ARMS, UNITS, gate_manifest, main, mypy_command, resolve

REPO = Path(__file__).resolve().parents[2]


def test_units_cover_every_package_dir_once() -> None:
    dirs = {p.parent.name for p in (REPO / "packages").glob("*/pyproject.toml")}
    covered = {u.name for u in UNITS} | set(ALIASES)
    assert covered == dirs
    assert len({u.name for u in UNITS}) == len(UNITS) == 16


def test_every_unit_path_exists() -> None:
    for unit in UNITS:
        for rel in (*unit.src, *unit.lint):
            assert (REPO / rel).is_dir(), (unit.name, rel)
        if unit.testpath:
            assert (REPO / unit.testpath).is_dir(), (unit.name, unit.testpath)


def test_okf_ext_is_an_alias_and_its_paths_ride_with_okf_io() -> None:
    okf = resolve("okf-ext")
    assert okf.name == "okf-io"
    assert okf.src == ("packages/okf-io/src", "packages/okf-ext/src")
    assert okf.lint == ("packages/okf-io", "packages/okf-ext")
    assert okf.uv_flags == ()
    assert okf.testpath == ""


def test_resolve_unknown_raises() -> None:
    with pytest.raises(KeyError):
        resolve("nope")


def test_floors() -> None:
    assert {u.name: u.floor for u in UNITS if u.floor != 95} == {"code-graph-io": 90}


def test_mypy_command_shape() -> None:
    cmd = mypy_command(resolve("models-io"), "win32")
    assert cmd == [
        "uv",
        "run",
        "--package",
        "models-io",
        "--extra",
        "bedrock",
        "--extra",
        "vercel",
        "mypy",
        "--strict",
        "--platform",
        "win32",
        "--cache-dir",
        ".mypy_cache/win32/models-io",
        "packages/models-io/src",
    ]


def test_mypy_jobs_lists_every_unit_on_every_arm(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["mypy-jobs"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(UNITS) * len(ARMS) == 32
    labels = [line.split(" ", 1)[0] for line in lines]
    assert labels == [f"{arm}/{u.name}" for arm in ARMS for u in UNITS]
    assert all(" mypy --strict --platform " in line for line in lines)


def test_env_is_evaluable_shell(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["env", "okf-ext"]) == 0
    out = capsys.readouterr().out
    bash_cmd = out + '\nprintf "%s|%s|%s|%s|%s|%s|%s" "$UNIT" "$SRC" "$LINT" "$TESTPATH" "$MODULES" "$FLOOR" "$UVFLAGS"'
    done = subprocess.run(
        ["bash", "-c", bash_cmd],
        check=True,
        capture_output=True,
        text=True,
    )
    assert done.stdout == (
        "okf-io|packages/okf-io/src packages/okf-ext/src|packages/okf-io packages/okf-ext||"
        "--cov=okf_io --cov=okf_ext|95|"
    )


def test_env_unknown_unit_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["env", "nope"]) == 1
    assert "unknown package 'nope'" in capsys.readouterr().err


def test_names(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["names"]) == 0
    assert capsys.readouterr().out.split() == [u.name for u in UNITS]


def test_cli_entrypoint_runs() -> None:
    script_path = str(REPO / "scripts/package_units.py")
    done = subprocess.run(
        [sys.executable, script_path, "names"],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0 and "graph-works-serve" in done.stdout
    assert shlex.split(done.stdout)[0] == "okf-io"


@pytest.mark.parametrize("mode", [["names"], ["mypy-jobs"], ["env", "config-io"], ["gate-json"]])
def test_stdout_has_no_carriage_returns(mode: list[str]) -> None:
    done = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "package_units.py"), *mode], cwd=REPO, capture_output=True
    )
    assert done.returncode == 0
    assert b"\r" not in done.stdout


def test_gate_manifest_units_and_dependencies_match_the_tables() -> None:
    from affected_packages import dependency_graph

    data = gate_manifest(REPO, cpu_count=12, jobs=3)
    assert [unit["name"] for unit in data["units"]] == [*(unit.name for unit in UNITS), "plugin"]
    graph = dependency_graph(REPO)
    for unit in data["units"][:-1]:
        assert set(unit["depends_on"]) == graph[unit["name"]], unit["name"]
    assert data["units"][-1] == {
        "name": "plugin",
        "inputs": ["plugins/**"],
        "depends_on": ["graph-works-cli"],
        "command": "just test-plugin",
        "env": {},
    }


def test_gate_manifest_shape() -> None:
    data = gate_manifest(REPO, cpu_count=12, jobs=3)
    assert data["version"] == 1 and data["jobs"] == 3
    assert data["repo_wide"] == {"command": "just gate-repo-wide"}
    assert data["setup"] == {"command": "just preflight sync"}
    assert "uv.lock" in data["shared_inputs"] and "justfile" in data["shared_inputs"]
    okf = next(unit for unit in data["units"] if unit["name"] == "okf-io")
    assert okf["inputs"] == ["packages/okf-io/**", "packages/okf-ext/**", "scripts/**"]
    assert okf["command"] == "just _gate-unit okf-io"
    assert okf["env"] == {"PYTEST_XDIST_AUTO_NUM_WORKERS": "4"}


def test_gate_manifest_worker_count_has_a_floor_of_one() -> None:
    data = gate_manifest(REPO, cpu_count=1, jobs=3)
    assert all(unit["env"] == {"PYTEST_XDIST_AUTO_NUM_WORKERS": "1"} for unit in data["units"][:-1])


def test_every_unit_input_matches_a_tracked_file() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.splitlines()
    data = gate_manifest(REPO, cpu_count=12, jobs=3)
    for unit in data["units"]:
        for pattern in unit["inputs"]:
            prefix = pattern.removesuffix("/**")
            assert any(path.startswith(prefix + "/") for path in tracked), (unit["name"], pattern)
    # Shared patterns may match nothing until a future addition must invalidate all units.
    assert "conftest.py" in data["shared_inputs"]


def test_gate_json_stdout_is_pure_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GW_COV_JOBS", "2")
    done = subprocess.run(
        [sys.executable, "scripts/package_units.py", "gate-json"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    data = json.loads(done.stdout)
    assert data["version"] == 1 and data["jobs"] == 2


def test_root_conftest_addition_and_deletion_invalidate_every_manifest_unit() -> None:
    from graph_works_core.orchestrate import gate_git, gate_units

    data = gate_manifest(REPO, cpu_count=12, jobs=3)
    manifest = gate_units.parse_manifest(json.dumps(data))
    listing = gate_git.ls_tree(REPO, "HEAD")
    # This absent shared path must still be declared: pytest loads it if added later.
    absent = {path: leaf for path, leaf in listing.items() if path != "conftest.py"}
    present = {**absent, "conftest.py": next(iter(listing.values()))}
    before = gate_units.unit_hashes(manifest, absent, ())
    added = gate_units.unit_hashes(manifest, present, ())
    deleted = gate_units.unit_hashes(manifest, absent, ())
    assert set(before) == {unit["name"] for unit in data["units"]}
    assert len(before) == 17
    assert all(before[name] != added[name] for name in before)
    assert all(added[name] != deleted[name] for name in before)
    assert deleted == before
