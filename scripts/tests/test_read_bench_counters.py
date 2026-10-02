"""`scripts/read_bench_counters.py`: structural counts for reads, independent of host load."""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import okf_io
import pytest
from graph_works_core.workspace.provenance import probe_git
from okf_io import build_link_graph, load_bundle

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from read_bench_counters import Counts, counting, is_git  # noqa: E402

PAGE = "---\ntype: Concept\ntitle: A\n---\n\nSee [b](/b.md).\n"


def _bundle(root: Path, names: tuple[str, ...] = ("a", "b", "c")) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        (root / f"{name}.md").write_text(PAGE, encoding="utf-8", newline="\n")
    return root


def test_counts_start_at_zero() -> None:
    with counting() as counts:
        pass
    assert counts.as_dict() == {"files_parsed": 0, "link_graph_builds": 0, "git_calls": 0}


def test_every_parse_counts_once(tmp_path: Path) -> None:
    root = _bundle(tmp_path / "b")
    with counting() as counts:
        okf_io.parse(PAGE)
        load_bundle(root)
    assert counts.files_parsed == 4


def test_link_graph_counted_through_a_rebound_name_and_validate(tmp_path: Path) -> None:
    bundle = load_bundle(_bundle(tmp_path / "b"))
    with counting() as counts:
        build_link_graph(bundle)
        build_link_graph(bundle)
        assert counts.link_graph_builds == 2
        okf_io.validate(bundle, today=date(2026, 10, 1))
    assert counts.link_graph_builds >= 3  # validate() builds its own graph when none is passed
    assert counts.files_parsed == 0


def test_built_graph_is_still_a_real_link_graph(tmp_path: Path) -> None:
    links = importlib.import_module("okf_io.links")
    bundle = load_bundle(_bundle(tmp_path / "b"))
    with counting():
        graph = build_link_graph(bundle)
    assert isinstance(graph, links.LinkGraph)
    assert graph.backlinks["b"] == ("a", "b", "c")


def test_git_counted_through_core_probe_git(tmp_path: Path) -> None:
    with counting() as counts:
        probe_git(tmp_path, "--version")
        subprocess.run([sys.executable, "-c", "pass"], check=True)
    assert counts.git_calls == 1


def test_git_counted_by_absolute_path() -> None:
    git = shutil.which("git")
    assert git is not None
    with counting() as counts:
        subprocess.run([git, "--version"], capture_output=True, check=True)
    assert counts.git_calls == 1


@pytest.mark.parametrize(
    ("args", "shell", "executable", "expected"),
    [
        (["git", "status"], False, None, True),
        (("/usr/bin/git", "status"), False, None, True),
        ([r"C:\Program Files\Git\cmd\git.exe", "status"], False, None, True),
        ("git status --porcelain", True, None, True),
        (["anything"], False, "/usr/local/bin/git", True),
        ([sys.executable, "-c", "pass"], False, None, False),
        (["gitk"], False, None, False),
        ([], False, None, False),
        ("", True, None, False),
    ],
)
def test_is_git(args: object, shell: bool, executable: object, expected: bool) -> None:
    assert is_git(args, shell=shell, executable=executable) is expected


def test_patches_are_restored_after_an_exception(tmp_path: Path) -> None:
    document = importlib.import_module("okf_io.document").Document
    links = importlib.import_module("okf_io.links")
    before = (document.__dict__["parse"], links.LinkGraph, subprocess.Popen)
    with pytest.raises(ValueError, match="boom"), counting():
        raise ValueError("boom")
    assert (document.__dict__["parse"], links.LinkGraph, subprocess.Popen) == before
    with counting() as counts:
        pass
    okf_io.parse(PAGE)
    assert counts.files_parsed == 0


def test_counting_is_not_reentrant() -> None:
    with counting(), pytest.raises(RuntimeError, match="reentrant"), counting():
        pass
    with counting() as counts:
        okf_io.parse(PAGE)
    assert counts.files_parsed == 1


def test_counts_is_a_plain_dataclass() -> None:
    assert Counts(1, 2, 3).as_dict() == {"files_parsed": 1, "link_graph_builds": 2, "git_calls": 3}
