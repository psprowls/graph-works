"""The scan worklist wire protocol survives an actual process boundary."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from code_graph_io.testing import raw_conn
from code_wiki_okf.config import load_config
from graph_works_core.workspace.layout import CACHE_DIRNAME, DEFAULT_CONFIG_DIR, MANIFEST_FILENAME
from ruamel.yaml import YAML

PACKAGE_URI = "pkg:acme/demo/widgets"
FILLED_PURPOSE = "Widgets exposes the demo package's public behavior."


def _gw() -> Path:
    """Use this test environment's installed console-script entry point."""
    entry_point = Path(sys.executable).parent / "gw"
    assert entry_point.is_file()
    return entry_point


def _run_gw(*args: str) -> subprocess.CompletedProcess[str]:
    """Run one independent CLI process with captured machine-readable output."""
    return subprocess.run([str(_gw()), *args], capture_output=True, check=False, text=True)


def _git(repo: Path, *args: str) -> None:
    """Create the one commit that gives scan a real provenance head."""
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True, text=True)


def _seed_scan_workspace(tmp_path: Path) -> tuple[Path, Path]:
    """Bootstrap a workspace and seed its real graph database with one package."""
    repository = tmp_path / "repo"
    package_manifest = repository / "packages" / "widgets" / "pyproject.toml"
    package_manifest.parent.mkdir(parents=True)
    package_manifest.write_text("[project]\nname = 'widgets'\n", encoding="utf-8")
    _git(tmp_path, "init", repository.name)
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "initial")

    workspace = tmp_path / ".works"
    bootstrap = _run_gw("bootstrap", "--topic", "Scan", "--workspace", str(workspace))
    assert bootstrap.returncode == 0, bootstrap.stderr

    bundle_dir = workspace / "okf"
    manifest_path = workspace / MANIFEST_FILENAME
    graph_dir = workspace / DEFAULT_CONFIG_DIR / CACHE_DIRNAME
    declarations_dir = workspace / DEFAULT_CONFIG_DIR
    pristine = load_config(
        bundle_dir, config_path=manifest_path, graph_dir=graph_dir, declarations_dir=declarations_dir
    )
    yaml = YAML()
    yaml.preserve_quotes = True
    with manifest_path.open(encoding="utf-8") as handle:
        manifest_doc = yaml.load(handle)
    manifest_doc["repositories"] = {"demo": {"path": str(repository)}}
    manifest_doc["state_gate"] = {"enabled": False}
    with manifest_path.open("w", encoding="utf-8") as handle:
        yaml.dump(manifest_doc, handle)
    connection = raw_conn(pristine.graph_dir / "code.db", create=True)
    try:
        with connection:
            repository_uri = "repo:acme/demo"
            connection.execute(
                "INSERT INTO nodes (id, kind, name, path, line, attrs_json, uri, repo) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?, ?)",
                (
                    1,
                    "repository",
                    "demo",
                    "",
                    json.dumps({"owner": "acme", "url": "https://example.com/acme/demo", "uri": repository_uri}),
                    repository_uri,
                    repository_uri,
                ),
            )
            connection.execute(
                "INSERT INTO nodes (id, kind, name, path, line, attrs_json, uri, repo) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?, ?)",
                (
                    2,
                    "package",
                    "widgets",
                    "packages/widgets",
                    json.dumps({"language": "python", "version": "1", "uri": PACKAGE_URI}),
                    PACKAGE_URI,
                    repository_uri,
                ),
            )
            connection.execute("INSERT INTO edges (src, dst, kind) VALUES (?, ?, ?)", (1, 2, "contains"))
    finally:
        connection.close()
    return workspace, bundle_dir


def _bundle_files(bundle_dir: Path) -> dict[Path, bytes]:
    """Return every landed bundle file as bytes for no-write refusal checks."""
    return {path.relative_to(bundle_dir): path.read_bytes() for path in bundle_dir.rglob("*") if path.is_file()}


def test_scan_emit_and_apply_round_trip_across_processes_and_refuse_unsafe_handoffs(tmp_path: Path) -> None:
    """A stale or incompatible worklist must stop before changing the landed bundle."""
    workspace, bundle_dir = _seed_scan_workspace(tmp_path)

    emitted_process = _run_gw("scan", "--emit-worklist", "--workspace", str(workspace))
    assert emitted_process.returncode == 0, emitted_process.stderr
    emitted = json.loads(emitted_process.stdout)
    assert set(emitted) == {
        "worklist_path",
        "briefs_dir",
        "results_dir",
        "short_head",
        "entities_written",
        "entities_deleted",
        "entity_errors",
        "mirror_created",
        "mirror_updated",
        "mirror_moved",
        "mirror_deleted",
        "mirror_declined",
        "mirror_stranded",
        "mirror_skipped_repos",
        "mirror_errors",
    }
    scan_cache = (workspace / DEFAULT_CONFIG_DIR / CACHE_DIRNAME / "scan").resolve()
    worklist_path = Path(emitted["worklist_path"]).resolve()
    briefs_dir = Path(emitted["briefs_dir"]).resolve()
    results_dir = Path(emitted["results_dir"]).resolve()
    assert worklist_path == scan_cache / "worklist.json"
    assert briefs_dir == scan_cache / "briefs"
    assert results_dir == scan_cache / "results"

    worklist = json.loads(worklist_path.read_text(encoding="utf-8"))
    package_task = next(task for task in worklist["prose_tasks"] if task["uri"] == PACKAGE_URI)
    page_path = bundle_dir / "repositories" / "demo" / "packages" / "widgets.md"
    pristine_bundle = _bundle_files(bundle_dir)
    # Emit writes this structural page. Only apply may land the task's prose.
    assert FILLED_PURPOSE not in page_path.read_text(encoding="utf-8")
    stale_process = _run_gw(
        "scan",
        "--apply",
        "--results-dir",
        str(results_dir),
        "--short-head",
        "different-head",
        "--workspace",
        str(workspace),
    )
    assert stale_process.returncode == 2
    assert _bundle_files(bundle_dir) == pristine_bundle
    assert FILLED_PURPOSE not in page_path.read_text(encoding="utf-8")

    original_worklist = worklist_path.read_bytes()
    incompatible_worklist = re.sub(rb'("schema_version": )\d+', rb"\g<1>999", original_worklist, count=1)
    assert incompatible_worklist != original_worklist
    assert set(json.loads(incompatible_worklist)) == set(worklist)
    assert {key for key, value in json.loads(incompatible_worklist).items() if value != worklist[key]} == {
        "schema_version"
    }
    worklist_path.write_bytes(incompatible_worklist)

    incompatible_process = _run_gw(
        "scan",
        "--apply",
        "--results-dir",
        str(results_dir),
        "--short-head",
        emitted["short_head"],
        "--workspace",
        str(workspace),
    )
    assert incompatible_process.returncode == 4
    assert _bundle_files(bundle_dir) == pristine_bundle
    assert FILLED_PURPOSE not in page_path.read_text(encoding="utf-8")

    worklist_path.write_bytes(original_worklist)
    (results_dir / "widgets.json").write_text(
        json.dumps(
            {
                "uri": package_task["uri"],
                "sections": dict.fromkeys(package_task["prose_sections"], FILLED_PURPOSE),
            }
        ),
        encoding="utf-8",
    )
    applied_process = _run_gw(
        "scan",
        "--apply",
        "--results-dir",
        str(results_dir),
        "--short-head",
        emitted["short_head"],
        "--workspace",
        str(workspace),
    )
    assert applied_process.returncode == 0, applied_process.stderr
    assert json.loads(applied_process.stdout) == {
        "narrated": 1,
        "sections_filled": len(package_task["prose_sections"]),
        "stamped": 1,
        "entity_errors": [],
    }
    assert FILLED_PURPOSE in page_path.read_text(encoding="utf-8")
