from __future__ import annotations

import importlib.resources
from datetime import date
from pathlib import Path

from okf_ext.schemas import load_schemas, schema_rule
from okf_ext.sections import section_rule
from okf_ext.shape import load_sections
from okf_io import load_bundle, parse
from okf_io import validate as okf_validate
from repositories_okf.git import FileChange, RangeFacts
from repositories_okf.resources import SEED_RELATIVE_PATHS
from repositories_okf.snapshots import CHANGED_FILES_CAP, Snapshot, render_snapshot, snapshot_path, summary

ASSETS = importlib.resources.files("repositories_okf") / "assets"
OLD, NEW, BASE = "1" * 40, "3" * 40, "2" * 40


def _range(**overrides: object) -> RangeFacts:
    values: dict[str, object] = {
        "old": OLD,
        "new": NEW,
        "base": OLD,
        "rewritten": False,
        "commits": 42,
        "changes": (FileChange("M", "src/a.py"), FileChange("D", "README.md"), FileChange("R", "src/b.py", "src/c.py")),
        "merges": ("Merge pull request #7",),
        "tags": ("v2.2.0", "v2.3.0"),
    }
    values.update(overrides)
    return RangeFacts(**values)  # type: ignore[arg-type]


def _advance(**overrides: object) -> Snapshot:
    return Snapshot(
        name="demo",
        commit=NEW,
        fetched_at="2026-09-29T20:40:00Z",
        describe="v2.3.0-14-g3333333",
        commit_date="2026-09-10T14:02:11+00:00",
        previous=OLD,
        range=_range(**overrides),
    )


def _validate(tmp_path: Path, rel: str, text: str) -> tuple[object, ...]:
    root = tmp_path / "bundle"
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# b\n", encoding="utf-8", newline="")
    (root / rel).write_text(text, encoding="utf-8", newline="")
    rules = [
        schema_rule(load_schemas(str(ASSETS / "schema")), severity="error"),
        section_rule(load_sections(str(ASSETS / "sections")), severity="error"),
    ]
    return tuple(okf_validate(load_bundle(root), today=date(2026, 9, 29), extra_rules=rules).errors)


def test_the_owned_file_set_is_nine_files() -> None:
    assert SEED_RELATIVE_PATHS == (
        "schema/_base-repository.schema.json",
        "schema/ManagedRepository.schema.json",
        "schema/ReferenceRepository.schema.json",
        "schema/RepositorySnapshot.schema.json",
        "schema/RepositoryChangelog.schema.json",
        "sections/ManagedRepository.yaml",
        "sections/ReferenceRepository.yaml",
        "sections/RepositorySnapshot.yaml",
        "sections/RepositoryChangelog.yaml",
    )


def test_path_label_day_and_summary() -> None:
    snapshot = _advance()
    assert (
        snapshot.path == snapshot_path("demo", "2026-09-29", NEW) == "repositories/demo/snapshots/2026-09-29-3333333.md"
    )
    assert (snapshot.label, snapshot.day, snapshot.baseline) == ("v2.3.0-14-g3333333", "2026-09-29", False)
    assert summary(snapshot) == "42 commits, 3 files, v2.3.0"
    assert summary(_advance(rewritten=True, base=BASE, tags=())) == "42 commits, 3 files, rewritten"
    baseline = Snapshot(name="demo", commit=NEW, fetched_at="2026-09-29T20:40:00Z")
    assert (baseline.baseline, baseline.label, summary(baseline)) == (True, "3333333", "baseline")


def test_an_advance_snapshot_renders_every_section_and_validates(tmp_path: Path) -> None:
    snapshot = _advance()
    text = render_snapshot(snapshot)
    data = parse(text).fm_data(dates="iso")
    assert data["type"] == "RepositorySnapshot"
    assert data["repository"] == "/repositories/demo.md"
    assert (data["commit"], data["previous"], data["merge_base"], data["rewritten"]) == (NEW, OLD, OLD, False)
    assert data["range"] == {"commits": 42, "files_changed": 3}
    assert data["description"] == "42 commits, 3 files, v2.3.0"
    for heading in (
        "## Range",
        "## Tags crossed",
        "## Merges",
        "## Changed files",
        "## Flagged pages",
        "## Doc impact",
    ):
        assert heading in text
    assert "- `R` `src/b.py` → `src/c.py`" in text
    assert (
        "](" not in text.split("## Changed files", 1)[1].split("## Flagged pages", 1)[0]
    )  # never links into the clone
    assert _validate(tmp_path, snapshot.path, text) == ()


def test_a_baseline_snapshot_has_only_range_and_doc_impact(tmp_path: Path) -> None:
    snapshot = Snapshot(name="demo", commit=NEW, fetched_at="2026-09-29T20:40:00Z", describe=None)
    text = render_snapshot(snapshot)
    data = parse(text).fm_data(dates="iso")
    assert data["previous"] is None and data["merge_base"] is None and "describe" not in data
    assert data["range"] == {"commits": 0, "files_changed": 0}
    assert "## Tags crossed" not in text and "## Doc impact" in text
    assert _validate(tmp_path, snapshot.path, text) == ()


def test_a_rewritten_range_says_so_and_names_the_merge_base() -> None:
    text = render_snapshot(_advance(rewritten=True, base=BASE))
    assert "rewritten" in text.split("## Tags crossed", 1)[0]
    assert f"`{BASE[:7]}`" in text


def test_changed_files_are_capped_with_the_full_count_stated() -> None:
    changes = tuple(FileChange("M", f"src/f{i:04}.py") for i in range(CHANGED_FILES_CAP + 5))
    text = render_snapshot(_advance(changes=changes))
    listed = [line for line in text.splitlines() if line.startswith("  - `M`")]
    assert len(listed) == CHANGED_FILES_CAP
    assert f"Showing {CHANGED_FILES_CAP} of {CHANGED_FILES_CAP + 5} changed files." in text
