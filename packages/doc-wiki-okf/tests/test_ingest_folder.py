"""A directory in, a manifest out — and a refusal that still explains itself."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from doc_wiki_okf.ingest.folder import (
    ERROR_FILE_COUNT,
    LARGE_FILE_BYTES,
    WARN_FILE_COUNT,
    plan_folder_brief,
)

GATE = {"stale": False}


def _gate(repo: Path, /, *, workspace: Path) -> Mapping[str, Any]:
    return GATE


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _folder(tmp_path: Path, count: int, *, size: int = 1) -> Path:
    root = tmp_path / "raw" / "examples" / "demo"
    for i in range(count):
        _write(root / f"f{i:03d}.md", "x" * size)
    return root


def test_a_small_folder_reports_its_manifest(tmp_path):
    root = tmp_path / "raw" / "examples" / "demo"
    _write(root / "a.md", "# A\n\nalpha")
    _write(root / "b.py", "print('b')\n")

    brief = plan_folder_brief(root, repo=tmp_path, workspace_root=tmp_path)

    assert brief.ok is True
    assert brief.root == root
    assert brief.file_count == 2
    assert brief.total_size == len("# A\n\nalpha") + len("print('b')\n")
    assert [f.path for f in brief.files] == ["a.md", "b.py"]
    assert [f.language for f in brief.files] == ["markdown", "python"]
    assert brief.representative_file == "b.py"  # no README, no index.*: the largest
    assert brief.warnings == ()
    assert brief.refusals == ()
    assert brief.state_gate is None


def test_the_gate_rides_through(tmp_path):
    root = _folder(tmp_path, 1)
    brief = plan_folder_brief(root, repo=tmp_path, workspace_root=tmp_path, state_gate=_gate)
    assert brief.state_gate == GATE


def test_a_readme_is_the_representative(tmp_path):
    root = tmp_path / "raw" / "examples" / "demo"
    _write(root / "README.md", "# R\n")
    _write(root / "big.py", "y" * 500)

    brief = plan_folder_brief(root, repo=tmp_path, workspace_root=tmp_path)

    assert brief.representative_file == "README.md"


def test_an_empty_folder_has_no_representative(tmp_path):
    root = tmp_path / "raw" / "examples" / "empty"
    root.mkdir(parents=True)

    brief = plan_folder_brief(root, repo=tmp_path, workspace_root=tmp_path)

    assert brief.ok is True
    assert brief.file_count == 0
    assert brief.representative_file is None


def test_a_crowded_folder_warns_on_size(tmp_path):
    root = _folder(tmp_path, WARN_FILE_COUNT + 1)

    brief = plan_folder_brief(root, repo=tmp_path, workspace_root=tmp_path)

    assert [w.kind for w in brief.warnings] == ["folder-size"]
    assert str(WARN_FILE_COUNT) in brief.warnings[0].detail
    assert brief.ok is True


def test_a_large_file_warns_on_its_own(tmp_path):
    root = tmp_path / "raw" / "examples" / "demo"
    _write(root / "huge.json", "z" * (LARGE_FILE_BYTES + 1))

    brief = plan_folder_brief(root, repo=tmp_path, workspace_root=tmp_path)

    assert [w.kind for w in brief.warnings] == ["large-file"]
    assert "huge.json" in brief.warnings[0].detail


def test_both_warnings_fire_together_in_the_legacy_order(tmp_path):
    root = _folder(tmp_path, WARN_FILE_COUNT + 1)
    _write(root / "huge.json", "z" * (LARGE_FILE_BYTES + 1))

    brief = plan_folder_brief(root, repo=tmp_path, workspace_root=tmp_path)

    assert [w.kind for w in brief.warnings] == ["folder-size", "large-file"]
    assert brief.as_data()["warnings"] == ["folder_size", "large_file"]


def test_a_refused_folder_still_reports_the_facts_that_explain_it(tmp_path):
    root = _folder(tmp_path, ERROR_FILE_COUNT + 1)

    brief = plan_folder_brief(root, repo=tmp_path, workspace_root=tmp_path)

    assert brief.ok is False
    assert [r.kind for r in brief.refusals] == ["folder-too-large"]
    assert brief.file_count == ERROR_FILE_COUNT + 1
    assert brief.total_size == ERROR_FILE_COUNT + 1
    assert brief.files == ()
    assert brief.representative_file is None
    assert brief.warnings == ()


def test_a_refused_folder_as_data_is_the_legacy_sentinel(tmp_path):
    root = _folder(tmp_path, ERROR_FILE_COUNT + 1)

    data = plan_folder_brief(root, repo=tmp_path, workspace_root=tmp_path, state_gate=_gate).as_data()

    assert data == {
        "is_folder": True,
        "_error": f"folder has {ERROR_FILE_COUNT + 1} files (>{ERROR_FILE_COUNT}); pass a specific file instead",
        "state_gate": {"stale": False},
    }
    assert json.loads(json.dumps(data)) == data


def test_an_accepted_folder_as_data_is_the_legacy_dict(tmp_path):
    root = tmp_path / "raw" / "examples" / "demo"
    _write(root / "a.md", "# A\n")

    data = plan_folder_brief(root, repo=tmp_path, workspace_root=tmp_path).as_data()

    assert data == {
        "is_folder": True,
        "file_count": 1,
        "total_size": 4,
        "files": [{"path": "a.md", "size": 4, "language": "markdown"}],
        "representative_file": "a.md",
        "warnings": [],
        "state_gate": None,
    }
    assert json.loads(json.dumps(data)) == data


def test_a_relative_path_resolves_against_the_repo(tmp_path):
    root = tmp_path / "raw" / "examples" / "demo"
    _write(root / "a.md", "# A\n")

    brief = plan_folder_brief(Path("raw/examples/demo"), repo=tmp_path, workspace_root=tmp_path)

    assert brief.root == root
    assert brief.file_count == 1
