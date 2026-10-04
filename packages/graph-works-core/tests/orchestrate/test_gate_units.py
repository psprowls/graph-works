"""gate_units: the repository's unit manifest, its validation, and the unit hash."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from graph_works_core.orchestrate import gate_git
from graph_works_core.orchestrate import gate_units as gu
from graph_works_core.workspace.gate_config import RepoGate

LEAF = {c: gate_git.GitLeaf("100644", c * 40) for c in "abcdef"}


def manifest(**overrides: object) -> str:
    data: dict[str, object] = {
        "version": 1,
        "jobs": 2,
        "repo_wide": {"command": "just gate-repo-wide"},
        "setup": {"command": "just sync"},
        "shared_inputs": ["uv.lock"],
        "units": [
            {"name": "lib", "inputs": ["packages/lib/**"], "depends_on": [], "command": "just u lib", "env": {}},
            {
                "name": "app",
                "inputs": ["packages/app/**"],
                "depends_on": ["lib"],
                "command": "just u app",
                "env": {"B": "2", "A": "1"},
            },
            {"name": "other", "inputs": ["packages/other/**"], "depends_on": [], "command": "just u other"},
        ],
    }
    data.update(overrides)
    return json.dumps(data)


LISTING = {
    "uv.lock": LEAF["a"],
    "packages/lib/src/x.py": LEAF["b"],
    "packages/app/src/y.py": LEAF["c"],
    "packages/other/z.py": LEAF["d"],
    "README.md": LEAF["e"],
}


def test_valid_manifest_parses() -> None:
    m = gu.parse_manifest(manifest())
    assert m.jobs == 2 and m.repo_wide == "just gate-repo-wide" and m.setup == "just sync"
    assert [u.name for u in m.units] == ["lib", "app", "other"]
    assert m.units[1].env == (("A", "1"), ("B", "2"))
    assert m.units[2].env == () and m.units[2].depends_on == ()
    assert len(m.digest) == 64 and not m.implicit


def test_repo_wide_and_setup_are_optional() -> None:
    data = json.loads(manifest())
    del data["repo_wide"], data["setup"]
    m = gu.parse_manifest(json.dumps(data))
    assert m.repo_wide is None and m.setup is None


def test_non_json_is_units_command_failed() -> None:
    with pytest.raises(gu.ManifestError) as caught:
        gu.parse_manifest("Resolved 3 packages\n{}")
    assert caught.value.reason == "units-command-failed"


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"version": 2}, "version"),
        ({"version": True}, "version"),
        ({"jobs": "2"}, "jobs"),
        ({"jobs": 0}, "jobs"),
        ({"jobs": True}, "jobs"),
        ({"units": {}}, "units"),
        ({"units": []}, "units"),
        ({"units": [None]}, "units"),
        ({"units": [{"name": "Lib", "inputs": ["a/**"], "command": "c"}]}, "name"),
        ({"units": [{"name": "a", "inputs": "a/**", "command": "c"}]}, "repository-relative"),
        ({"units": [{"name": "a", "inputs": [1], "command": "c"}]}, "repository-relative"),
        ({"units": [{"name": "a", "inputs": ["a/**"], "depends_on": "a", "command": "c"}]}, "depends_on"),
        ({"units": [{"name": "a", "inputs": ["a/**"], "depends_on": [1], "command": "c"}]}, "depends_on"),
        ({"units": [{"name": "a", "inputs": ["a/**"], "depends_on": ["a"], "command": "c"}]}, "cycle"),
        (
            {
                "units": [
                    {"name": "a", "inputs": ["a/**"], "command": "c"},
                    {"name": "a", "inputs": ["b"], "command": "c"},
                ]
            },
            "duplicate",
        ),
        ({"units": [{"name": "a", "inputs": ["a/**"], "depends_on": ["zz"], "command": "c"}]}, "zz"),
        (
            {
                "units": [
                    {"name": "a", "inputs": ["a/**"], "depends_on": ["b"], "command": "c"},
                    {"name": "b", "inputs": ["b/**"], "depends_on": ["a"], "command": "c"},
                ]
            },
            "cycle",
        ),
        ({"units": [{"name": "a", "inputs": ["/abs/**"], "command": "c"}]}, "repository-relative"),
        ({"units": [{"name": "a", "inputs": ["C:/abs/**"], "command": "c"}]}, "repository-relative"),
        ({"units": [{"name": "a", "inputs": ["C:relative/**"], "command": "c"}]}, "repository-relative"),
        ({"units": [{"name": "a", "inputs": [r"a\relative\**"], "command": "c"}]}, "repository-relative"),
        ({"units": [{"name": "a", "inputs": ["../up/**"], "command": "c"}]}, "repository-relative"),
        ({"units": [{"name": "a", "inputs": [""], "command": "c"}]}, "repository-relative"),
        ({"units": [{"name": "a", "inputs": ["a/**"], "command": " "}]}, "command"),
        ({"units": [{"name": "a", "inputs": ["a/**"], "command": "c", "env": {"K": 1}}]}, "env"),
        ({"units": [{"name": "a", "inputs": ["a/**"], "command": "c", "env": []}]}, "env"),
        ({"shared_inputs": ["../x"]}, "repository-relative"),
        ({"repo_wide": "c"}, "repo_wide"),
        ({"repo_wide": None}, "repo_wide"),
        ({"repo_wide": {"command": ""}}, "repo_wide"),
        ({"setup": None}, "setup"),
        ({"setup": {"command": 1}}, "setup"),
    ],
)
def test_validation_failures_are_units_invalid(overrides: dict[str, object], fragment: str) -> None:
    with pytest.raises(gu.ManifestError) as caught:
        gu.parse_manifest(manifest(**overrides))
    assert caught.value.reason == "units-invalid"
    assert fragment in str(caught.value)


@pytest.mark.parametrize("text", ["[]", "null", "1", '"a"'])
def test_manifest_must_be_an_object(text: str) -> None:
    with pytest.raises(gu.ManifestError) as caught:
        gu.parse_manifest(text)
    assert caught.value.reason == "units-invalid"


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_json_constants_are_units_command_failed(constant: str) -> None:
    with pytest.raises(gu.ManifestError) as caught:
        gu.parse_manifest(manifest().replace('"version": 1', f'"version": {constant}'))
    assert caught.value.reason == "units-command-failed"


@pytest.mark.parametrize(
    "text",
    [
        '{"version": 2, ' + manifest()[1:],
        manifest().replace('"env": {}', '"env": {"K": "1", "K": "2"}'),
    ],
)
def test_duplicate_json_keys_are_units_invalid(text: str) -> None:
    with pytest.raises(gu.ManifestError) as caught:
        gu.parse_manifest(text)
    assert caught.value.reason == "units-invalid"
    assert "duplicate" in str(caught.value)


def test_manifest_rejects_unpaired_unicode_surrogate() -> None:
    text = manifest().replace("just u app", "just u \ud800")
    with pytest.raises(gu.ManifestError) as caught:
        gu.parse_manifest(text)
    assert caught.value.reason == "units-invalid"


def test_manifest_digest_is_canonical_json() -> None:
    text = manifest()
    reordered = dict(reversed(list(json.loads(text).items())))
    assert gu.parse_manifest(text).digest == gu.parse_manifest(json.dumps(reordered, indent=2)).digest
    assert gu.parse_manifest(text).digest != gu.parse_manifest(manifest(jobs=3)).digest


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("packages/lib/**", "packages/lib/src/x.py", True),
        ("packages/lib/**", "packages/lib/x.py", True),
        ("packages/lib/**", "packages/lib/new\nline.py", True),
        ("packages/lib/**", "packages/lib", False),
        ("packages/lib/**", "packages/library/x.py", False),
        ("packages/*/pyproject.toml", "packages/lib/pyproject.toml", True),
        ("packages/*/pyproject.toml", "packages/lib/sub/pyproject.toml", True),
        ("**/conftest.py", "conftest.py", False),
        ("**/conftest.py", "a/b/conftest.py", True),
        ("uv.lock", "uv.lock", True),
        ("uv.lock", "x/uv.lock", False),
        ("scripts/?.py", "scripts/a.py", True),
        ("scripts/?.py", "scripts/ab.py", False),
        ("**.py", "a/b.py", True),
        ("a/**/b.py", "a/b.py", False),
        ("a/**/b.py", "a/x/y/b.py", True),
        ("a.[ch]", "a.[ch]", False),
        ("a.[ch]", "a.c", True),
        ("a.[!ch]", "a.py", False),
        ("a.[!ch]", "a.x", True),
        ("a.[!ch]", "a.h", False),
        ("a.[c-h]", "a.e", True),
    ],
)
def test_glob_match(pattern: str, path: str, expected: bool) -> None:
    assert gu.glob_match(pattern, path) is expected


def test_closures() -> None:
    m = gu.parse_manifest(manifest())
    assert gu.dependency_closure(m, "app") == ("app", "lib")
    assert gu.reverse_closure(m, {"lib"}) == frozenset({"lib", "app"})
    assert gu.units_matching(m, ["packages/app/src/y.py", "README.md"]) == frozenset({"app"})


def test_transitive_diamond_closures_and_duplicate_dependencies() -> None:
    units = [
        {"name": "root", "inputs": ["root/**"], "command": "root"},
        {"name": "left", "inputs": ["left/**"], "depends_on": ["root"], "command": "left"},
        {"name": "right", "inputs": ["right/**"], "depends_on": ["root"], "command": "right"},
        {"name": "tip", "inputs": ["tip/**"], "depends_on": ["right", "left", "right"], "command": "tip"},
    ]
    m = gu.parse_manifest(manifest(units=units))
    assert m.units[-1].depends_on == ("left", "right")
    assert gu.dependency_closure(m, "tip") == ("left", "right", "root", "tip")
    assert gu.reverse_closure(m, iter(["root"])) == frozenset({"root", "left", "right", "tip"})
    assert gu.reverse_closure(m, ()) == frozenset()
    assert gu.units_matching(m, iter(["root/a", "tip/b"])) == frozenset({"root", "tip"})


def deep_chain_units() -> list[dict[str, object]]:
    return [
        {
            "name": f"u{i}",
            "inputs": [f"units/{i}/**"],
            "depends_on": [f"u{i + 1}"] if i < 1099 else [],
            "command": "check",
        }
        for i in range(1100)
    ]


def test_deep_acyclic_graph_parses_and_closes_without_recursion() -> None:
    m = gu.parse_manifest(manifest(units=deep_chain_units()))
    closure = gu.dependency_closure(m, "u0")
    assert len(closure) == 1100
    assert set(closure) == {f"u{i}" for i in range(1100)}


def test_deep_cycle_is_a_typed_validation_refusal() -> None:
    units = deep_chain_units()
    units[-1]["depends_on"] = ["u0"]
    with pytest.raises(gu.ManifestError) as caught:
        gu.parse_manifest(manifest(units=units))
    assert caught.value.reason == "units-invalid"
    assert "cycle" in str(caught.value)


def test_hash_ignores_unrelated_paths() -> None:
    m = gu.parse_manifest(manifest())
    before = gu.unit_hashes(m, LISTING, ())
    after = gu.unit_hashes(m, {**LISTING, "README.md": LEAF["f"], "packages/other/z.py": LEAF["f"]}, ())
    assert before["lib"] == after["lib"] and before["app"] == after["app"]
    assert before["other"] != after["other"]


@pytest.mark.parametrize(
    "change",
    [
        {"packages/app/src/y.py": LEAF["f"]},  # own input
        {"packages/lib/src/x.py": LEAF["f"]},  # transitive dependency's input
        {"uv.lock": LEAF["f"]},  # shared input
        {"packages/app/src/new.py": LEAF["f"]},  # an added file
    ],
)
def test_hash_changes_with_its_closure(change: dict[str, gate_git.GitLeaf]) -> None:
    m = gu.parse_manifest(manifest())
    assert gu.unit_hashes(m, LISTING, ())["app"] != gu.unit_hashes(m, {**LISTING, **change}, ())["app"]


def test_hash_covers_extra_inputs_command_and_env() -> None:
    m = gu.parse_manifest(manifest())
    base = gu.unit_hashes(m, LISTING, ())["app"]
    assert gu.unit_hashes(m, LISTING, ("README.md",))["app"] != base
    changed_cmd = gu.parse_manifest(manifest().replace("just u app", "just u2 app"))
    assert gu.unit_hashes(changed_cmd, LISTING, ())["app"] != base
    changed_env = gu.parse_manifest(manifest().replace('"B": "2"', '"B": "3"'))
    assert gu.unit_hashes(changed_env, LISTING, ())["app"] != base


def test_hash_is_order_independent() -> None:
    m = gu.parse_manifest(manifest())
    reordered = dict(reversed(list(LISTING.items())))
    assert gu.unit_hashes(m, LISTING, ()) == gu.unit_hashes(m, reordered, ())


def test_hash_changes_on_deleted_file() -> None:
    m = gu.parse_manifest(manifest())
    deleted = {path: sha for path, sha in LISTING.items() if path != "packages/lib/src/x.py"}
    before = gu.unit_hashes(m, LISTING, ())
    after = gu.unit_hashes(m, deleted, ())
    assert before["lib"] != after["lib"]
    assert before["app"] != after["app"]
    assert before["other"] == after["other"]


def test_implicit_manifest_and_tree_hash() -> None:
    m = gu.implicit_manifest("just check")
    assert m.implicit and [u.name for u in m.units] == [gu.TREE_UNIT] and m.repo_wide is None
    assert gu.tree_hash("a" * 40, "just check") != gu.tree_hash("a" * 40, "just check2")
    assert gu.tree_hash("a" * 40, "just check") != gu.tree_hash("b" * 40, "just check")


def fake_run(code: int, out: str, err: str = "") -> Callable[[str, Path], tuple[int, str, str]]:
    def run(command: str, cwd: Path) -> tuple[int, str, str]:
        return code, out, err

    return run


def test_load_manifest_runs_the_command_in_the_worktree(tmp_path: Path) -> None:
    calls: list[tuple[str, Path]] = []

    def run(command: str, cwd: Path) -> tuple[int, str, str]:
        calls.append((command, cwd))
        return 0, manifest(), ""

    m = gu.load_manifest("emit", tmp_path, run=run)
    assert [u.name for u in m.units] == ["lib", "app", "other"]
    assert calls == [("emit", tmp_path)]


def test_load_manifest_nonzero_exit_is_units_command_failed(tmp_path: Path) -> None:
    error = "\n".join(f"line {i}" for i in range(12))
    with pytest.raises(gu.ManifestError) as caught:
        gu.load_manifest("emit", tmp_path, run=fake_run(3, manifest(), error))
    assert caught.value.reason == "units-command-failed"
    assert "exit 3" in str(caught.value) and "line 11" in str(caught.value)
    assert "line 0" not in str(caught.value)


def test_unit_with_no_matching_input_refuses(tmp_path: Path) -> None:
    gate = RepoGate("just check", None, units="emit")
    bad = manifest().replace("packages/other/**", "packages/0ther/**")
    with pytest.raises(gu.ManifestError) as caught:
        gu.resolve_unit_state(gate, tmp_path, "t" * 40, git=None, run=fake_run(0, bad), listing=LISTING)
    assert caught.value.reason == "units-invalid" and "other" in str(caught.value)


def test_resolve_unit_state_without_units_is_the_implicit_tree_unit(tmp_path: Path) -> None:
    state = gu.resolve_unit_state(RepoGate("just check", None), tmp_path, "a" * 40, git=None)
    assert state.manifest.implicit
    assert dict(state.hashes) == {gu.TREE_UNIT: gu.tree_hash("a" * 40, "just check")}


def test_resolve_unit_state_requires_a_full_command_for_implicit_units(tmp_path: Path) -> None:
    with pytest.raises(gu.ManifestError, match=r"gate\.full") as caught:
        gu.resolve_unit_state(RepoGate(None, None), tmp_path, "a" * 40, git=None)
    assert caught.value.reason == "units-invalid"


def test_resolve_unit_state_hashes_with_extra_inputs(tmp_path: Path) -> None:
    gate = RepoGate("just check", None, units="emit", extra_inputs=("README.md",))
    state = gu.resolve_unit_state(gate, tmp_path, "t" * 40, git=None, run=fake_run(0, manifest()), listing=LISTING)
    assert dict(state.hashes) == gu.unit_hashes(state.manifest, LISTING, ("README.md",))


def test_resolve_unit_state_reads_git_when_listing_is_not_injected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, str]] = []

    def listing(worktree: Path, tree: str, *, git: object) -> dict[str, gate_git.GitLeaf]:
        calls.append((worktree, tree))
        return LISTING

    monkeypatch.setattr(gate_git, "ls_tree", listing)
    state = gu.resolve_unit_state(
        RepoGate("full", None, units="emit"), tmp_path, "HEAD", git=None, run=fake_run(0, manifest())
    )
    assert calls == [(tmp_path, "HEAD")]
    assert set(state.hashes) == {"lib", "app", "other"}


def test_run_capture_executes_in_worktree_with_captured_utf8_output(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(manifest(), encoding="utf-8", newline="\n")
    # This command is accepted by both supported shell arms.
    code = "from pathlib import Path; print(Path('manifest.json').read_text(encoding='utf-8'))"
    command = f'"{sys.executable}" -c "{code}"'
    state = gu.load_manifest(command, tmp_path)
    assert state.jobs == 2


def test_run_capture_windows_uses_comspec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, manifest(), "")

    monkeypatch.setattr(gu.sys, "platform", "win32")
    monkeypatch.setenv("COMSPEC", "custom-cmd")
    monkeypatch.setattr(gu.subprocess, "run", run)
    assert gu.load_manifest("emit", tmp_path).jobs == 2
    assert calls == [["custom-cmd", "/c", "emit"]]


def test_load_manifest_startup_failure_is_units_command_failed(tmp_path: Path) -> None:
    with pytest.raises(gu.ManifestError, match="could not run") as caught:
        gu.load_manifest("emit", tmp_path / "missing-worktree")
    assert caught.value.reason == "units-command-failed"


@pytest.mark.parametrize("source", ["shared", "extra"])
def test_class_globs_in_common_inputs_invalidate_every_unit(source: str) -> None:
    m = gu.parse_manifest(manifest(shared_inputs=["config.[cy]"] if source == "shared" else []))
    extra = ("config.[!p]",) if source == "extra" else ()
    listing = {**LISTING, "config.c": LEAF["a"], "config.p": LEAF["b"]}
    before = gu.unit_hashes(m, listing, extra)
    included = gu.unit_hashes(m, {**listing, "config.c": LEAF["f"]}, extra)
    excluded = gu.unit_hashes(m, {**listing, "config.p": LEAF["f"]}, extra)
    assert all(before[name] != included[name] for name in ("lib", "app", "other"))
    assert before == excluded
