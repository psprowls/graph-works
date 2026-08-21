"""Published dependency metadata must resolve with graph-works-core offline."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

_STUB_VERSIONS: dict[str, tuple[str, ...]] = {
    "click": ("8.0.0",),
    "code-graph-io": ("0.1.0", "0.1.1"),
    "code-wiki-okf": ("0.1.0", "0.2.0", "0.3.0"),
    "config-io": ("0.1.0",),
    "doc-wiki-okf": ("0.1.0", "0.2.0", "0.2.1", "0.3.0", "0.3.1"),
    "langchain-core": ("1.4.0",),
    "models-io": ("0.2.0",),
    "okf-ext": ("0.1.0", "0.4.5", "0.4.6", "0.4.7", "0.4.8", "0.4.9"),
    "okf-io": ("0.1.1", "0.2.0", "0.2.1", "0.2.2", "0.2.3"),
    "subagents-io": ("0.2.0", "0.2.1"),
    "typer": ("0.12.0",),
    "work-tracker-okf": ("0.1.0", "0.2.0", "0.2.1", "0.2.2"),
}


def _metadata_requirements(wheel: Path) -> tuple[Requirement, ...]:
    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        message = BytesParser(policy=default).parsebytes(archive.read(metadata_name))
    return tuple(Requirement(value) for value in message.get_all("Requires-Dist", []))


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
    workspace_root = Path(__file__).resolve().parents[3]
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
