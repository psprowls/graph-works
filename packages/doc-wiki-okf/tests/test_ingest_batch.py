"""A directory in, a manifest of ingest units out.

Ported from `wiki-io`'s `test_batch_ingest_brief.py`; the `state_gate` assertions
become the seam this package injects.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from doc_wiki_okf.ingest.batch import enumerate_batch_units, plan_batch_brief


def _gate(repo: Path, /, *, workspace: Path) -> Mapping[str, Any]:
    return {"stale": False}


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "wiki").mkdir()
    (tmp_path / "material").mkdir()
    return tmp_path


def _write(path: Path, text: str = "# Doc\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_brief_is_none_for_a_non_directory(tmp_path):
    workspace = _workspace(tmp_path)
    _write(workspace / "a.md")
    assert plan_batch_brief(workspace / "a.md", kind="specs", repo=workspace, workspace_root=workspace) is None


def test_any_directory_briefs_under_an_explicit_kind(tmp_path, tmp_path_factory):
    """S-I: `raw/<kind>/` no longer defines a batch -- the caller does."""
    workspace = _workspace(tmp_path)
    elsewhere = tmp_path_factory.mktemp("downloads")
    _write(elsewhere / "a.md")
    _write(elsewhere / "b.md")

    brief = plan_batch_brief(elsewhere, kind="articles", repo=workspace, workspace_root=workspace)

    assert brief is not None
    assert brief.kind_folder == "articles"
    assert [u.rel for u in brief.units] == ["a.md", "b.md"]


def test_a_flat_kind_enumerates_files_recursively_sorted(tmp_path):
    workspace = _workspace(tmp_path)
    root = workspace / "material" / "specs"
    _write(root / "b.md")
    _write(root / "a.md")
    _write(root / "nested" / "c.md")

    units = enumerate_batch_units("specs", root)

    assert [u.rel for u in units] == ["a.md", "b.md", "nested/c.md"]
    assert {u.unit_type for u in units} == {"file"}
    assert all(u.path.is_absolute() for u in units)


def test_skills_enumerates_immediate_subdirs_only(tmp_path):
    workspace = _workspace(tmp_path)
    root = workspace / "material" / "skills"
    _write(root / "foo" / "SKILL.md")
    _write(root / "bar" / "SKILL.md")
    _write(root / "loose-note.md")

    units = enumerate_batch_units("skills", root)

    assert [u.rel for u in units] == ["bar", "foo"]
    assert {u.unit_type for u in units} == {"dir"}


def test_examples_enumerates_subdirs_plus_loose_files(tmp_path):
    workspace = _workspace(tmp_path)
    root = workspace / "material" / "examples"
    _write(root / "demo-app" / "index.ts")
    _write(root / "loose.md")

    units = enumerate_batch_units("examples", root)

    assert {u.rel: u.unit_type for u in units} == {"demo-app": "dir", "loose.md": "file"}


def test_archive_assets_and_dotfiles_are_excluded(tmp_path):
    workspace = _workspace(tmp_path)
    root = workspace / "material" / "specs"
    _write(root / "keep.md")
    _write(root / "_archive" / "old.md")
    _write(root / "assets" / "img.md")
    _write(root / ".DS_Store", "junk")
    assert [u.rel for u in enumerate_batch_units("specs", root)] == ["keep.md"]

    skills = workspace / "material" / "skills"
    _write(skills / "good" / "SKILL.md")
    (skills / "_archive").mkdir()
    (skills / ".hidden").mkdir()
    assert [u.rel for u in enumerate_batch_units("skills", skills)] == ["good"]


def test_an_empty_kind_folder_briefs_to_nothing(tmp_path):
    workspace = _workspace(tmp_path)
    root = workspace / "material" / "articles"
    root.mkdir()

    brief = plan_batch_brief(root, kind="articles", repo=workspace, workspace_root=workspace)

    assert brief is not None
    assert brief.unit_count == 0
    assert brief.units == ()
    assert brief.limited is False


def test_the_default_limit_truncates_to_ten(tmp_path):
    workspace = _workspace(tmp_path)
    root = workspace / "material" / "specs"
    for i in range(12):
        _write(root / f"s{i:02d}.md")

    brief = plan_batch_brief(root, kind="specs", repo=workspace, workspace_root=workspace)

    assert brief is not None
    assert brief.total_count == 12
    assert brief.unit_count == 10
    assert brief.limited is True
    assert [u.rel for u in brief.units] == [f"s{i:02d}.md" for i in range(10)]


def test_a_limit_at_or_above_the_count_is_not_limited(tmp_path):
    workspace = _workspace(tmp_path)
    root = workspace / "material" / "specs"
    for i in range(5):
        _write(root / f"s{i:02d}.md")

    exact = plan_batch_brief(root, kind="specs", repo=workspace, workspace_root=workspace, limit=5)
    generous = plan_batch_brief(root, kind="specs", repo=workspace, workspace_root=workspace, limit=10)

    assert exact is not None and exact.limited is False and exact.unit_count == 5
    assert generous is not None and generous.limited is False and generous.unit_count == 5


def test_no_limit_ingests_all(tmp_path):
    workspace = _workspace(tmp_path)
    root = workspace / "material" / "specs"
    for i in range(12):
        _write(root / f"s{i:02d}.md")

    brief = plan_batch_brief(root, kind="specs", repo=workspace, workspace_root=workspace, limit=None)

    assert brief is not None
    assert brief.unit_count == 12
    assert brief.limited is False


def test_as_data_is_the_legacy_dict(tmp_path):
    workspace = _workspace(tmp_path)
    root = workspace / "material" / "specs"
    _write(root / "a.md")

    brief = plan_batch_brief(root, kind="specs", repo=workspace, workspace_root=workspace, state_gate=_gate)

    assert brief is not None
    data = brief.as_data()
    assert data == {
        "is_batch": True,
        "kind_folder": "specs",
        "root": str(root),
        "unit_count": 1,
        "total_count": 1,
        "limited": False,
        "units": [{"path": str(root / "a.md"), "rel": "a.md", "unit_type": "file"}],
        "state_gate": {"stale": False},
    }
    assert json.loads(json.dumps(data)) == data


def test_a_relative_path_resolves_against_the_repo(tmp_path):
    workspace = _workspace(tmp_path)
    _write(workspace / "material" / "specs" / "a.md")

    brief = plan_batch_brief(Path("material/specs"), kind="specs", repo=workspace, workspace_root=workspace)

    assert brief is not None
    assert brief.kind_folder == "specs"
