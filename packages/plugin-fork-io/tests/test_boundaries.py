"""Mechanical guards for the standalone band-1 package boundary."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
SRC = PACKAGE / "src" / "plugin_fork_io"
PACKAGES_DIR = PACKAGE.parent
ROOT_CONFIG = PACKAGE.parents[1] / "pyproject.toml"
MODULE_NAMES = {
    "__init__.py",
    "adoption.py",
    "acceptance.py",
    "adapters.py",
    "installation.py",
    "config.py",
    "cli.py",
    "inspection.py",
    "git.py",
    "snapshots.py",
    "machine.py",
    "records.py",
    "references.py",
    "adaptations.py",
    "fork.py",
    "previews.py",
    "validation.py",
    "store.py",
    "transactions.py",
    "recovery.py",
    "status.py",
    "merge.py",
    "updates.py",
}
ALLOWED_OS_MEMBERS = {
    "environ",
    "open",
    "fdopen",
    "fsync",
    "replace",
    "chmod",
    "symlink",
    "readlink",
    "lstat",
    "O_RDONLY",
    "close",
    "link",
    "rename",
    "fsencode",
}


def modules() -> list[Path]:
    found = sorted(SRC.glob("*.py"))
    assert found
    return found


def parsed(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def import_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(parsed(path)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def sibling_import_names() -> set[str]:
    names = {
        src_dir.name
        for package in PACKAGES_DIR.iterdir()
        if (package / "src").is_dir()
        for src_dir in (package / "src").iterdir()
        if src_dir.is_dir()
    }
    assert "plugin_fork_io" in names
    return names - {"plugin_fork_io"}


def test_module_set_is_complete():
    assert {module.name for module in modules()} == MODULE_NAMES


@pytest.mark.parametrize("module", modules(), ids=lambda path: path.name)
def test_no_module_imports_another_workspace_package(module):
    assert not (import_roots(module) & sibling_import_names())


@pytest.mark.parametrize("module", modules(), ids=lambda path: path.name)
def test_no_module_reads_a_graph_works_workspace_manifest(module):
    constants = {node.value for node in ast.walk(parsed(module)) if isinstance(node, ast.Constant)}
    assert "workspace.yaml" not in constants
    assert "GRAPH_WORKS_DIR" not in constants


@pytest.mark.parametrize("module", modules(), ids=lambda path: path.name)
def test_no_module_uses_dynamic_imports(module):
    called = {
        node.func.id
        for node in ast.walk(parsed(module))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "__import__" not in called
    assert "importlib" not in import_roots(module)


def test_only_machine_may_import_os_and_only_approved_members_are_used():
    os_importers = {module.name for module in modules() if "os" in import_roots(module)}
    assert os_importers <= {"machine.py"}
    machine = parsed(SRC / "machine.py")
    used = {
        node.attr
        for node in ast.walk(machine)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os"
    }
    used.update(
        alias.name
        for node in ast.walk(machine)
        if isinstance(node, ast.ImportFrom) and node.module == "os"
        for alias in node.names
    )
    assert used <= ALLOWED_OS_MEMBERS


def test_root_import_contracts_register_plugin_fork_io():
    config = tomllib.loads(ROOT_CONFIG.read_text(encoding="utf-8"))
    import_linter = config["tool"]["importlinter"]
    assert "plugin_fork_io" in import_linter["root_packages"]
    contracts = {contract["name"]: contract for contract in import_linter["contracts"]}
    assert (
        "plugin_fork_io"
        in contracts["The foundation is a strict layer — band-1 packages never import each other"]["modules"]
    )
    assert "plugin_fork_io" in contracts["Nothing below band 3 imports the application band"]["source_modules"]
    assert contracts["plugin-fork-io never imports workspace siblings"]["source_modules"] == ["plugin_fork_io"]


def test_only_git_module_may_launch_subprocesses():
    assert {module.name for module in modules() if "subprocess" in import_roots(module)} == {"git.py"}


def test_machine_os_exemptions_are_narrow_and_exercised(tmp_path, monkeypatch):
    from plugin_fork_io.machine import LocalFileSystem, git_environment

    monkeypatch.setenv("GIT_CONFIG_COUNT", "9")
    monkeypatch.setenv("PATH", "/test/path")
    file = tmp_path / "data"
    file.write_bytes(b"data")
    link = tmp_path / "link"
    link.symlink_to("data")
    fs = LocalFileSystem()
    assert fs.mode(file) == file.lstat().st_mode
    assert fs.readlink(link) == "data"
    assert git_environment(tmp_path)["PATH"] == "/test/path"
    assert "GIT_CONFIG_COUNT" not in git_environment(tmp_path)


def test_native_ctypes_directory_promotion_is_confined_to_machine_seam():
    assert {module.name for module in modules() if "ctypes" in import_roots(module)} == {"machine.py"}
    tree = parsed(SRC / "machine.py")
    ctypes_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "ctypes"
    }
    assert ctypes_calls == {"CDLL", "get_errno"}
    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    assert {
        function.name
        for function in functions
        if any(isinstance(node, ast.Name) and node.id == "ctypes" for node in ast.walk(function))
    } == {"rename_directory"}
