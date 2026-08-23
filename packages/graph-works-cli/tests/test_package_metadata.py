"""Published dependency metadata must resolve with graph-works-core offline."""

from __future__ import annotations

import subprocess
import sys
import tomllib
import zipfile
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

#: The fake index this test resolves against. Each workspace entry must carry
#: that package's **currently declared** version, or the resolution below is
#: answered by a version that no longer exists and a pin excluding today's
#: real release passes unnoticed -- which is exactly how a stale
#: `code-wiki-okf>=0.3,<0.4` survived that package's bump to 0.4.0.
#: `test_every_stubbed_workspace_version_is_the_declared_one` pins that.
_STUB_VERSIONS: dict[str, tuple[str, ...]] = {
    "click": ("8.0.0",),
    "code-graph-io": ("0.1.0", "0.1.1", "0.2.0", "0.3.0"),
    "code-wiki-okf": ("0.1.0", "0.2.0", "0.3.0", "0.4.0", "0.5.0"),
    "config-io": ("0.1.0",),
    "doc-wiki-okf": ("0.1.0", "0.2.0", "0.2.1", "0.2.2", "0.3.0", "0.3.1", "0.3.2"),
    "langchain-core": ("1.4.0",),
    "models-io": ("0.2.0",),
    "okf-ext": ("0.1.0", "0.4.5", "0.4.6", "0.4.7", "0.4.8", "0.4.9", "0.4.10"),
    "okf-io": ("0.1.1", "0.2.0", "0.2.1", "0.2.2", "0.2.3"),
    "subagents-io": ("0.2.0", "0.2.1"),
    "typer": ("0.12.0",),
    "work-tracker-okf": ("0.1.0", "0.2.0", "0.2.1", "0.2.2", "0.3.0", "0.3.1"),
}


def _metadata_requirements(wheel: Path) -> tuple[Requirement, ...]:
    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        message = BytesParser(policy=default).parsebytes(archive.read(metadata_name))
    return tuple(Requirement(value) for value in message.get_all("Requires-Dist", []))


def _manifest_requirements(relative_manifest: str) -> dict[str, Requirement]:
    with (_WORKSPACE_ROOT / relative_manifest).open("rb") as handle:
        project = tomllib.load(handle)["project"]
    return {
        canonicalize_name(requirement.name): requirement
        for raw in project["dependencies"]
        for requirement in (Requirement(raw),)
    }


@pytest.mark.parametrize(
    ("manifest", "name", "interval"),
    [
        (
            "packages/code-wiki-okf/pyproject.toml",
            "code-graph-io",
            frozenset({">=0.3", "<0.4"}),
        ),
        (
            "packages/graph-works-core/pyproject.toml",
            "code-graph-io",
            frozenset({">=0.3", "<0.4"}),
        ),
        (
            "packages/graph-works-core/pyproject.toml",
            "code-wiki-okf",
            frozenset({">=0.5", "<0.6"}),
        ),
        (
            "packages/graph-works-cli/pyproject.toml",
            "code-graph-io",
            frozenset({">=0.3", "<0.4"}),
        ),
        (
            "packages/graph-works-cli/pyproject.toml",
            "code-wiki-okf",
            frozenset({">=0.5", "<0.6"}),
        ),
    ],
)
def test_breaking_code_contract_has_exact_dependency_intervals(
    manifest: str,
    name: str,
    interval: frozenset[str],
) -> None:
    """A stale lower/upper bound must fail even when another version resolves."""
    requirement = _manifest_requirements(manifest)[name]

    assert requirement.marker is None
    assert requirement.url is None
    assert not requirement.extras
    assert frozenset(str(specifier) for specifier in requirement.specifier) == interval


def _write_stub_wheel(directory: Path, name: str, version: str, *, extras: set[str]) -> None:
    wheel_name = canonicalize_name(name).replace("-", "_")
    dist_info = f"{wheel_name}-{version}.dist-info"
    metadata = f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\n"
    metadata += "".join(f"Provides-Extra: {extra}\n" for extra in sorted(extras))
    wheel = directory / f"{wheel_name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{dist_info}/METADATA", metadata)
        archive.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: graph-works-cli-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(f"{dist_info}/RECORD", "")


def test_built_wheel_resolves_with_core_from_published_metadata_offline(tmp_path: Path) -> None:
    """Reintroducing mutually exclusive CLI/core constraints must break resolution."""
    workspace_root = _WORKSPACE_ROOT
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    for package in ("graph-works-cli", "graph-works-core"):
        subprocess.run(
            [
                "uv",
                "build",
                "--offline",
                "--package",
                package,
                "--wheel",
                "--out-dir",
                str(wheelhouse),
            ],
            cwd=workspace_root,
            check=True,
            capture_output=True,
            text=True,
        )

    cli_wheel = next(wheelhouse.glob("graph_works_cli-*.whl"))
    core_wheel = next(wheelhouse.glob("graph_works_core-*.whl"))
    cli_requirements = _metadata_requirements(cli_wheel)
    core_requirements = _metadata_requirements(core_wheel)
    assert "models-io" in {canonicalize_name(requirement.name) for requirement in cli_requirements}

    extras_by_name: dict[str, set[str]] = {}
    for requirement in (*cli_requirements, *core_requirements):
        name = canonicalize_name(requirement.name)
        extras_by_name.setdefault(name, set()).update(requirement.extras)
    for name, versions in _STUB_VERSIONS.items():
        for version in versions:
            _write_stub_wheel(wheelhouse, name, version, extras=extras_by_name.get(name, set()))

    completed = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--dry-run",
            "--offline",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "--only-binary",
            ":all:",
            "--python",
            sys.executable,
            "--target",
            str(tmp_path / "target"),
            str(cli_wheel),
            "--no-config",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_every_stubbed_workspace_version_is_the_declared_one() -> None:
    """The stub index must publish each workspace package's real version.

    `_STUB_VERSIONS` is hand-maintained, and the resolution test above can
    only prove a pin is satisfiable by *something the stub publishes*. When a
    package is bumped and its stub entry is not, the stub keeps offering the
    superseded version, every dependent pin still resolves against it, and a
    pin that excludes the actual release passes -- a false green precisely
    when the bound has gone wrong. Asserting containment (not equality) keeps
    the older entries, which are what make an over-tight lower bound fail.
    """
    workspace_root = _WORKSPACE_ROOT
    for manifest in sorted(workspace_root.glob("packages/*/pyproject.toml")):
        with manifest.open("rb") as handle:
            project = tomllib.load(handle)["project"]
        stubbed = _STUB_VERSIONS.get(canonicalize_name(project["name"]))
        if stubbed is None:
            continue  # built as a real wheel above, or simply not a dependency
        assert project["version"] in stubbed, (
            f"{project['name']} is {project['version']} but the stub index only publishes "
            f"{stubbed} -- add it, or the resolution test cannot see today's version"
        )
